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
from .scoring import fmt_usd, group_buys, theme_clusters, theme_keys

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
    return hits


def full_activity(wallet: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(20):
        rows, cursor = pm._rows(pm.get(pm.DATA, "/v2/activity", {"user": wallet, "limit": 1000, "start": 1, "cursor": cursor}))
        out.extend(rows)
        if not cursor or not rows:
            break
    return out


def replay(wallet: str, acts: list[dict], log: list[str]) -> list[str]:
    """Rejoue des scans toutes les 15 min sur 60 min, avec l'état du compte à chaque instant."""
    from .scoring import score_bet

    acts = sorted(acts, key=lambda a: pm._ts(pm._pick(a, "timestamp", default=0)))
    trades = []
    for a in acts:
        if str(pm._pick(a, "type", default="")).upper() == "TRADE":
            t = pm.normalize_trade(a)
            t["proxyWallet"] = wallet
            trades.append(t)
    if not trades:
        return ["Aucun trade trouvé dans l'activité."]
    first_ts = pm._ts(pm._pick(acts[0], "timestamp", default=0))

    tag_cache: dict[str, set] = {}
    keys: dict[str, set] = {}
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
        log.append(f"- marché « {t.get('title')} » — thèmes : {', '.join(sorted(tag_cache[ev])) or '—'}")

    lines = ["| Scan (UTC) | Marché | Mise sur 1 h | Prix moyen | Score | Alerte | Pourquoi |", "|---|---|---|---|---|---|---|"]
    seen: dict[str, float] = {}
    t0 = (trades[0]["timestamp"] // 900 + 1) * 900
    t_end = trades[-1]["timestamp"] + 3600
    for scan in range(t0, t_end + 900, 900):
        window = [t for t in trades if scan - 3600 < t["timestamp"] <= scan]
        groups = [g for g in group_buys(window) if g.cash >= config.MIN_GROUP_CASH and g.avg_price <= config.MAX_PRICE]
        if not groups:
            continue
        past = [t for t in trades if t["timestamp"] <= scan]
        vol: dict[str, float] = {}
        for t in past:
            vol[t["conditionId"]] = vol.get(t["conditionId"], 0.0) + t["size"] * t["price"]
        theme_of = theme_clusters({c: keys[c] for c in vol})
        wallet_state = {"age_days": (scan - first_ts) / 86400, "markets_traded": len(vol), "busy": False,
                        "volume_by_market": vol, "theme_of": theme_of}
        for g in groups:
            score, why = score_bet(g, wallet_state, None)
            prev = seen.get(g.key)
            fresh = prev is None or g.cash >= prev * config.REALERT_FACTOR
            alert = score >= config.MIN_SCORE and fresh
            if alert:
                seen[g.key] = g.cash
            if alert or score >= config.MIN_SCORE - 15:
                lines.append(f"| {hhmm(scan)} | {g.title[:45]} | {fmt_usd(g.cash)} | {g.avg_price:.3f} | {score} | "
                             f"{'✅' if alert else ('déjà signalé' if score >= config.MIN_SCORE else '—')} | {', '.join(why)} |")
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
