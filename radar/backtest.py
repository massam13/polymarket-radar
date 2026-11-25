"""Enquête / rejeu : retrouver un portefeuille et rejouer le radar sur son historique.

python -m radar.backtest research/request.json   → écrit research/<nom>.md
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config
from . import polymarket as pm
from .scoring import fmt_usd, group_buys, is_longshot, theme_burst, theme_clusters, theme_keys

ROOT = Path(__file__).resolve().parent.parent


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def find_markets(queries: list[str], slugs: list[str], log: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for slug in slugs:
        try:
            for m in pm.get(pm.GAMMA, "/markets", {"slug": slug}) or []:
                found[m.get("conditionId")] = m
            log.append(f"- slug `{slug}` : {len(found)} marché(s) au total")
        except Exception as e:
            log.append(f"- slug `{slug}` KO : {e}")
    for q in queries:
        try:
            res = pm.get(pm.GAMMA, "/public-search", {"q": q, "limit_per_type": 20, "events_status": "all"})
            evs = (res or {}).get("events") or []
            for ev in evs:
                for m in ev.get("markets") or []:
                    if m.get("conditionId"):
                        m.setdefault("events", [ev])
                        found[m["conditionId"]] = m
            log.append(f"- recherche « {q} » : {len(evs)} événement(s)")
        except Exception as e:
            log.append(f"- recherche « {q} » KO : {e}")
    return list(found.values())


def wallets_in_market(cid: str, prefix: str, suffix: str, log: list[str], max_pages: int = 10) -> list[dict]:
    hits = []
    for status in ("CLOSED", "REDEEMABLE", None):
        cursor = None
        for _ in range(max_pages):
            try:
                res = pm.get(pm.DATA, "/v2/positions", {"condition": cid, "status": status, "sort_by": "REALIZED_PNL",
                                                         "limit": 1000, "cursor": cursor})
            except Exception as e:
                log.append(f"  - positions {status} KO : {e}")
                break
            rows, cursor = pm._rows(res)
            for r in rows:
                w = str(pm._pick(r, "proxy_wallet", "proxyWallet", default="")).lower()
                if w.startswith(prefix.lower()) and w.endswith(suffix.lower()):
                    hits.append({"wallet": w, "status": status, "realized_pnl": pm._pick(r, "realized_pnl", "realizedPnl"),
                                 "total_pnl": pm._pick(r, "total_pnl"), "title": pm._pick(r, "title")})
            if not cursor or not rows:
                break
    if hits:
        return hits
    # Les positions d'un compte supprimé n'apparaissent plus : on cherche alors dans les trades du marché
    cursor, seen = None, set()
    for _ in range(max_pages * 20):
        try:
            rows, cursor = pm._rows(pm.get(pm.DATA, "/v2/trades", {"condition": cid, "limit": 1000, "cursor": cursor}))
        except Exception as e:
            log.append(f"  - trades KO : {e}")
            break
        for r in rows:
            w = str(pm._pick(r, "proxy_wallet", "proxyWallet", default="")).lower()
            if w.startswith(prefix.lower()) and w.endswith(suffix.lower()) and w not in seen:
                seen.add(w)
                hits.append({"wallet": w, "status": "trades", "realized_pnl": None, "total_pnl": None, "title": None})
        if not cursor or not rows:
            break
    return hits


def full_activity(wallet: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(20):
        rows, cursor = pm._rows(pm.get(pm.DATA, "/v2/activity", {"user": wallet, "limit": 1000, "start": 1, "cursor": cursor}))
        out.extend(rows)
        if not cursor or not rows:
            break
    return out


class MarketTape:
    """Trades passés d'un marché, lus à reculons depuis le plus récent (l'API ne filtre pas par date), en cache."""

    def __init__(self, cid: str, max_pages: int = 400):
        self.cid, self.max_pages, self.pages = cid, max_pages, 0
        self.trades: list[dict] = []
        self.cursor, self.done = None, False

    def window(self, start: int, end: int) -> list[dict] | None:
        while not self.done and (not self.trades or self.trades[-1]["timestamp"] >= start):
            if self.pages >= self.max_pages:
                return None  # trop loin dans le passé : on ne sait pas
            rows, self.cursor = pm._rows(pm.get(pm.DATA, "/v2/trades", {"condition": self.cid, "limit": 1000,
                                                                        "cursor": self.cursor}))
            self.pages += 1
            self.trades += [pm.normalize_trade(r) for r in rows]
            self.done = not self.cursor or not rows
        return [t for t in self.trades if start <= t["timestamp"] <= end]


def first_seen(wallet: str, cache: dict) -> tuple[int | None, int]:
    """Date de la 1re activité d'un compte et nombre d'événements de la 1re page (1000 = compte actif)."""
    if wallet not in cache:
        try:
            rows, _ = pm._rows(pm.get(pm.DATA, "/v2/activity", {"user": wallet, "limit": 1000, "start": 1}))
            ts = [pm._ts(pm._pick(r, "timestamp", default=0)) for r in rows]
            cache[wallet] = (min(ts) if ts else None, len(rows))
        except Exception:
            cache[wallet] = (None, 0)
    return cache[wallet]


def replay_context(g, scan: int, tapes: dict, price_cache: dict, seen_cache: dict, crowd: bool) -> dict:
    from .scoring import crowd_wallets, price_context
    ctx: dict = {}
    if g.token_id:
        k = (g.token_id, g.first_ts)
        if k not in price_cache:
            try:
                price_cache[k] = pm.price_history(g.token_id, g.first_ts - int((config.RUNUP_HOURS + 1) * 3600), g.first_ts + 60)
            except Exception:
                price_cache[k] = []
        ctx["price"] = price_context(price_cache[k], g.first_ts)
    if crowd:
        tape = tapes.setdefault(g.condition_id, MarketTape(g.condition_id))
        trades = tape.window(g.last_ts - int(config.CROWD_HOURS * 3600), scan)
        if trades is not None:
            n_new = 0
            for w, _ in crowd_wallets(trades, g)[: config.CROWD_MAX_PROFILES]:
                first, n = first_seen(w, seen_cache)
                if first is not None and scan - first < config.CROWD_NEW_DAYS * 86400:
                    n_new += 1
            ctx["crowd_new"] = n_new
    return ctx


def replay_scans(wallet: str, acts: list[dict], crowd: bool = True, tapes: dict | None = None,
                 price_cache: dict | None = None, seen_cache: dict | None = None,
                 visible_tx: set[str] | None = None) -> tuple[list[dict], dict]:
    """Rejoue des scans toutes les 15 min sur 60 min, avec l'état du compte à chaque instant.
    visible_tx : si fourni, seuls les ordres de ces transactions sont visibles (ex. rejeu « takers seulement »).
    Fidèle au radar : ordres < MIN_TRADE_CASH invisibles, cote avant le pari et autres comptes neufs (si crowd), filtre « actualité », compte « actif » au-delà de 1000 événements,
    dédoublonnage (nouvelle alerte seulement si la mise double). Le poids dans le volume du marché n'est pas rejoué."""
    import bisect
    from .scoring import is_news_topic, score_bet

    acts = sorted(acts, key=lambda a: pm._ts(pm._pick(a, "timestamp", default=0)))
    act_ts = [pm._ts(pm._pick(a, "timestamp", default=0)) for a in acts]
    trades = []
    for a in acts:
        if str(pm._pick(a, "type", default="")).upper() == "TRADE":
            t = pm.normalize_trade(a)
            t["proxyWallet"] = wallet
            trades.append(t)
    info = {"trades": len(trades), "activities": len(acts), "first_ts": act_ts[0] if act_ts else None, "markets": {}}
    if not trades:
        return [], info
    first_ts = act_ts[0]
    tag_cache: dict[str, set] = {}
    keys: dict[str, set] = {}
    news: dict[str, bool] = {}
    for t in trades:
        cid = t["conditionId"]
        if cid in keys:
            continue
        ev = t.get("eventSlug") or ""
        if ev not in tag_cache:
            try:
                tag_cache[ev] = pm.event_tags(ev) if ev else set()
            except Exception:
                tag_cache[ev] = set()
        keys[cid] = theme_keys(t.get("title", ""), tag_cache[ev]) | ({f"event:{ev}"} if ev else set())
        news[cid] = is_news_topic(t.get("title", ""), tag_cache[ev], None, ev or t.get("slug", ""))
        info["markets"][cid] = {"title": t.get("title"), "news": news[cid], "tags": sorted(tag_cache[ev])}

    visible = [t for t in trades if t["size"] * t["price"] >= config.MIN_TRADE_CASH and news[t["conditionId"]]
               and (visible_tx is None or t.get("transactionHash") in visible_tx)]  # ex. ordres taker seulement
    vis_ts = [t["timestamp"] for t in visible]
    tr_ts = [t["timestamp"] for t in trades]
    rows: list[dict] = []
    seen: dict[str, float] = {}
    tapes = {} if tapes is None else tapes
    price_cache = {} if price_cache is None else price_cache
    seen_cache = {} if seen_cache is None else seen_cache
    scan_times = sorted({(ts // 900 + k) * 900 for ts in vis_ts for k in range(1, 5)})
    for scan in scan_times:
        lo, hi = bisect.bisect_right(vis_ts, scan - 3600), bisect.bisect_right(vis_ts, scan)
        window = group_buys(visible[lo:hi])
        groups = [g for g in window if g.cash >= config.MIN_GROUP_CASH and is_longshot(g.avg_price)]
        if not groups:
            continue
        past = trades[: bisect.bisect_right(tr_ts, scan)]
        vol: dict[str, float] = {}
        for t in past:
            vol[t["conditionId"]] = vol.get(t["conditionId"], 0.0) + t["size"] * t["price"]
        busy = bisect.bisect_right(act_ts, scan) >= 1000
        state = {"age_days": None if busy else (scan - first_ts) / 86400, "markets_traded": len(vol), "busy": busy,
                 "volume_by_market": vol, "theme_of": {} if busy else theme_clusters({c: keys[c] for c in vol})}
        for g in groups:
            burst = theme_burst(g, window, state["theme_of"])
            score, why = score_bet(g, state, None, burst)
            ctx = None
            if score >= config.CONTEXT_MIN_SCORE:
                ctx = replay_context(g, scan, tapes, price_cache, seen_cache, crowd)
                score, why = score_bet(g, state, None, burst, ctx)
            prev = seen.get(g.key)
            alert = score >= config.MIN_SCORE and (prev is None or g.cash >= prev * config.REALERT_FACTOR)
            if alert:
                seen[g.key] = g.cash
            rows.append({"scan": scan, "title": g.title, "cash": g.cash, "price": g.avg_price, "score": score,
                         "alert": alert, "why": why, "age": state["age_days"], "n_markets": len(vol), "ctx": ctx})
    return rows, info


def replay(wallet: str, acts: list[dict], log: list[str]) -> list[str]:
    rows, info = replay_scans(wallet, acts)
    for m in info["markets"].values():
        log.append(f"- marché « {m['title']} » — actualité : {'oui' if m['news'] else 'non'} — thèmes : {', '.join(m['tags']) or '—'}")
    lines = ["| Scan (UTC) | Marché | Mise sur 1 h | Prix moyen | Score | Alerte | Pourquoi |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["alert"] or r["score"] >= config.MIN_SCORE - 15:
            lines.append(f"| {hhmm(r['scan'])} | {r['title'][:45]} | {fmt_usd(r['cash'])} | {r['price']:.3f} | {r['score']} | "
                         f"{'✅' if r['alert'] else ('déjà signalé' if r['score'] >= config.MIN_SCORE else '—')} | {', '.join(r['why'])} |")
    return lines


def main() -> int:
    req_path = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "research" / "request.json")
    req = json.loads(req_path.read_text())
    name = req.get("name", "enquete")
    log: list[str] = [f"# Enquête : {name}", "", f"_Générée le {hhmm(int(datetime.now(timezone.utc).timestamp()))}_", "", "## Recherche du marché"]
    wallets = [w.lower() for w in req.get("wallets", [])]
    if not wallets:
        markets = find_markets(req.get("queries", []), req.get("slugs", []), log)
        must = [s.lower() for s in req.get("title_contains", [])]
        markets = [m for m in markets if all(s in (m.get("question") or "").lower() for s in must)]
        log += ["", "## Marchés retenus"] + [f"- {m.get('question')} — `{m.get('conditionId')}`" for m in markets]
        log += ["", "## Portefeuilles correspondants"]
        for m in markets:
            for h in wallets_in_market(m["conditionId"], req["prefix"], req["suffix"], log):
                log.append(f"- `{h['wallet']}` sur « {m.get('question')} » ({h['status']}) — gain réalisé {h['realized_pnl']}")
                if h["wallet"] not in wallets:
                    wallets.append(h["wallet"])
        if not wallets:
            log.append("- aucun")
    for w in wallets:
        log += ["", f"## Rejeu du radar sur `{w}`", ""]
        try:
            acts = full_activity(w)
            log.append(f"{len(acts)} événements d'activité.")
            log += [""] + replay(w, acts, log)
        except Exception as e:
            log.append(f"Rejeu KO : {type(e).__name__}: {e}")
    out = ROOT / "research" / f"{name}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(log) + "\n")
    print("\n".join(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
