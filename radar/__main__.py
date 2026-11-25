"""Un passage du radar : python -m radar [--lookback 60] [--dry-run]."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import chain, config, notify, report, watch
from . import polymarket as pm
from .scoring import (crowd_wallets, fmt_usd, group_buys, hedged, is_longshot, is_news_topic, price_context, is_same_day_market, market_move, move_drivers,
                      prelim_score, score_bet, score_parts, theme_burst, theme_clusters, theme_keys)

STATE = Path(__file__).resolve().parent.parent / "state"
SEEN_FILE, ALERTS_FILE, CANDIDATES_FILE = STATE / "seen.json", STATE / "alerts.jsonl", STATE / "candidates.jsonl"


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m %H:%M UTC")


def load_seen() -> dict:
    try:
        return json.loads(SEEN_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_seen(seen: dict, now: int) -> None:
    STATE.mkdir(exist_ok=True)
    keep = {k: v for k, v in seen.items() if now - v.get("ts", 0) < 7 * 86400}
    SEEN_FILE.write_text(json.dumps(keep, indent=1, sort_keys=True) + "\n")


def bet_message(g, score, why, market, now: int | None = None, context: dict | None = None) -> str:
    gain = g.payout - g.cash
    who = g.pseudonym or g.wallet[:10]
    window = hhmm(g.first_ts) if g.fills == 1 else f"{hhmm(g.first_ts)} → {hhmm(g.last_ts)}"
    if now:
        window += f" (il y a {max(0, now - g.last_ts) // 60} min)"
    lines = [
        f"🚨 Pari suspect — score {score}/100",
        g.title,
        f"Achat « {g.outcome} » : {g.shares:,.0f} parts à {g.avg_price:.3f} = {fmt_usd(g.cash)}".replace(",", " "),
        f"Gain si ça tombe : +{fmt_usd(gain)} (×{g.payout / g.cash:.1f})",
        f"Compte : {who} · {g.fills} ordre(s) · {window}",
        "Pourquoi : " + ", ".join(why),
    ]
    pc = (context or {}).get("price")
    if pc:
        lines.append(f"Cote de l'issue : {pc['ref']:.1%} 24 h avant → {pc['before']:.1%} juste avant le pari"
                     if pc.get("ref") is not None else f"Cote juste avant le pari : {pc['before']:.1%}")
    crowd = (context or {}).get("crowd_new")
    if crowd is not None and not config.CROWD_PTS:
        lines.append(f"Autres comptes neufs (< {config.CROWD_NEW_DAYS:.0f} j) sur la même issue en 24 h : {crowd}")
    lines += [
        f"https://polymarket.com/event/{g.event_slug or g.slug}",
        f"https://polymarket.com/profile/{g.wallet}",
    ]
    if g.tx:
        lines.append(f"https://polygonscan.com/tx/{g.tx[0]}")
    return "\n".join(lines)


def move_message(m, d1h, outcome, drivers=None, top_profile=None) -> str:
    v24 = float(m.get("volume24hr") or 0)
    ev = (m.get("events") or [{}])[0].get("slug") or m.get("slug", "")
    lines = [
        f"📈 Mouvement brusque — {d1h * 100:+.0f} points en 1 h sur « {outcome} »",
        m.get("question", ""),
        f"Volume 24 h : {fmt_usd(v24)}",
    ]
    if drivers and drivers.get("wallets"):
        lines.append(f"Poussé par {drivers['wallets']} compte(s) pour {fmt_usd(drivers['total'])} sur 1 h")
        top = f"Le plus gros : {drivers['top_name']} — {drivers['top_share']:.0%} ({fmt_usd(drivers['top_cash'])})"
        if top_profile and top_profile.get("age_days") is not None:
            top += f", compte de {top_profile['age_days']:.0f} j"
        if top_profile and top_profile.get("markets_traded") is not None and not top_profile.get("busy"):
            top += f", {top_profile['markets_traded']} marché(s) tradé(s)"
        lines.append(top)
        if drivers["top_share"] >= 0.5:
            lines.append("⚠️ Un seul compte fait l'essentiel du mouvement")
            lines.append(f"https://polymarket.com/profile/{drivers['top_wallet']}")
    lines.append(f"https://polymarket.com/event/{ev}")
    return "\n".join(lines)


def add_themes(wallet: dict, market_cache: dict, tag_cache: dict, max_markets: int = 100) -> None:
    """Regroupe les marchés tradés par le compte en sujets (ex. 4 marchés « Venezuela » = 1 sujet).
    Au-delà de 12 marchés, pas d'appel réseau par marché ; les thèmes d'événement restent récupérés (en cache,
    dans la limite de MAX_TAG_LOOKUPS par scan)."""
    info = wallet.get("market_info") or {}
    if wallet.get("busy") or not info or len(info) > max_markets:
        return
    light = len(info) > 12
    keys = {}
    for cid, mi in info.items():
        title, ev_slug = mi.get("title") or "", mi.get("event_slug") or ""
        if (not title or not ev_slug) and not light:
            if cid not in market_cache:
                try:
                    market_cache[cid] = pm.market_by_condition(cid)
                except Exception:
                    market_cache[cid] = None
            m = market_cache[cid] or {}
            title = title or m.get("question", "")
            ev_slug = ev_slug or ((m.get("events") or [{}])[0].get("slug") or "")
        if ev_slug not in tag_cache and len(tag_cache) < config.MAX_TAG_LOOKUPS:
            try:
                tag_cache[ev_slug] = pm.event_tags(ev_slug) if ev_slug else set()
            except Exception:
                tag_cache[ev_slug] = set()
        keys[cid] = theme_keys(title, tag_cache.get(ev_slug, set())) | ({f"event:{ev_slug}"} if ev_slug else set())
    wallet["theme_of"] = theme_clusters(keys)


def bet_context(g, market: dict | None, profiles: dict, now: int) -> dict:
    """Cote de l'issue avant le pari et autres comptes neufs sur la même issue (appels réseau, bornés)."""
    ctx: dict = {}
    try:
        token = g.token_id or pm.outcome_token(market, g.outcome_index)
        if token:
            hist = pm.price_history(token, g.first_ts - int((config.RUNUP_HOURS + 1) * 3600), g.first_ts + 60)
            ctx["price"] = price_context(hist, g.first_ts)
    except Exception as e:
        print(f"::warning title=Cote KO::{type(e).__name__}: {e}"[:300])
    try:
        mt = pm.market_trades(g.condition_id, g.last_ts - int(config.CROWD_HOURS * 3600), max_pages=20)
        n_new = 0
        for w, _ in crowd_wallets(mt, g)[: config.CROWD_MAX_PROFILES]:
            if w not in profiles:
                profiles[w] = pm.wallet_profile(w, now)
            p = profiles[w]
            if not p.get("busy") and p.get("age_days") is not None and p["age_days"] < config.CROWD_NEW_DAYS:
                n_new += 1
        ctx["crowd_new"] = n_new
    except Exception as e:
        print(f"::warning title=Autres acheteurs KO::{type(e).__name__}: {e}"[:300])
    return ctx


def run(lookback_min: int, dry_run: bool) -> int:
    now = int(time.time())
    since = now - lookback_min * 60
    seen = load_seen()
    alerts: list[dict] = []
    stats = {"trades": 0, "makers": 0, "groups": 0, "news_candidates": 0, "wallets_checked": 0, "markets_scanned": 0}

    # 1) Paris suspects : gros achats sur des issues improbables
    trades, feed = pm.recent_large_trades(since, config.MIN_TRADE_CASH,
                                           max_pages=max(200, lookback_min * 2))  # ~50 000 trades/h
    stats["trades"] = len(trades)
    feed_msg = (f"{feed['rows']} trades lus sur {feed['pages']} page(s), plus ancien "
                f"{hhmm(feed['oldest']) if feed['oldest'] else '—'}{' (TRONQUÉ)' if feed['truncated'] else ''}")
    print(f"::notice title=Flux::{feed_msg}")
    if feed.get("error"):
        print(f"::warning title=Flux interrompu::{feed['error']}")
    # Le flux ne montre que les takers : on relit sur Polygon les échanges où un maker a pu acheter une issue improbable
    try:
        makers, mk = chain.maker_trades(trades, lambda t: is_news_topic(t["title"], set(), None,
                                                                       t.get("eventSlug") or t.get("slug")))
        trades += makers
        stats["makers"] = mk["fills"]
        print(f"::notice title=Makers::{mk['read']}/{mk['tx']} transactions relues sur Polygon, "
              f"{mk['fills']} achat(s) maker ajouté(s)")
    except Exception as e:
        print(f"::warning title=Makers KO::{type(e).__name__}: {e}"[:300])
    all_groups = group_buys(trades)
    groups = [g for g in all_groups if g.cash >= config.MIN_GROUP_CASH and is_longshot(g.avg_price)]
    covered = [g for g in groups if hedged(g, all_groups)]
    if covered:
        print("::notice title=Positions couvertes écartées::" + " · ".join(
            f"{g.pseudonym or g.wallet[:10]} {fmt_usd(g.cash)} sur « {g.outcome} » + l'issue opposée ({g.title[:40]})"
            for g in covered[:5]))
    groups = [g for g in groups if g not in covered]
    stats["groups"] = len(groups)

    # Le flux v2 ne donne plus slug / event / tags : on complète via Gamma (en cache, nombre de lookups borné)
    market_cache: dict[str, dict | None] = {}
    tag_cache: dict[str, set] = {}
    candidates = []
    for g in sorted(groups, key=prelim_score, reverse=True):
        if g.condition_id not in market_cache:
            if len(market_cache) >= config.MAX_MARKET_LOOKUPS:
                break
            try:
                market_cache[g.condition_id] = pm.market_by_condition(g.condition_id)
            except Exception:
                market_cache[g.condition_id] = None
        m = market_cache[g.condition_id] or {}
        ev = (m.get("events") or [{}])[0]
        if not g.outcome:  # achat maker sur l'issue opposée d'un marché qui n'est pas Oui/Non
            outs = m.get("outcomes")
            outs = json.loads(outs) if isinstance(outs, str) else outs
            if outs and g.outcome_index < len(outs):
                g.outcome = outs[g.outcome_index]
        g.title = g.title or m.get("question", "")
        g.slug = g.slug or m.get("slug", "")
        g.event_slug = g.event_slug or ev.get("slug", "")
        if g.event_slug not in tag_cache:
            tags = {str(t.get("slug", "")).lower() for t in (ev.get("tags") or []) if isinstance(t, dict)}
            if not tags:
                try:
                    tags = pm.event_tags(g.event_slug)
                except Exception:
                    tags = set()
            tag_cache[g.event_slug] = tags
        if is_news_topic(g.title, tag_cache[g.event_slug], market_cache.get(g.condition_id), g.event_slug or g.slug):
            candidates.append(g)
    stats["news_candidates"] = len(candidates)

    scored: list[tuple] = []
    candidates_log: list[dict] = []
    profiles: dict[str, dict] = {}
    best_by_theme: dict[tuple, dict] = {}  # une seule alerte par compte et par sujet dans un même scan
    for g in candidates[: config.MAX_WALLET_LOOKUPS]:
        prev = seen.get(g.key)
        if prev and g.cash < prev.get("cash", 0) * config.REALERT_FACTOR:
            continue
        if g.wallet not in profiles:
            profiles[g.wallet] = pm.wallet_profile(g.wallet, now)
            add_themes(profiles[g.wallet], market_cache, tag_cache)
            stats["wallets_checked"] += 1
        wallet = profiles[g.wallet]
        theme_of = wallet.get("theme_of") or {}
        burst = theme_burst(g, all_groups, theme_of)
        market = market_cache[g.condition_id]
        score, why = score_bet(g, wallet, market, burst)
        ctx = None
        if score >= config.CONTEXT_MIN_SCORE:
            ctx = bet_context(g, market, profiles, now)
            score, why = score_bet(g, wallet, market, burst, ctx)
        parts = score_parts(g, wallet, market, burst, ctx)
        print(f"  {score:3d}  {fmt_usd(g.cash):>10}  @{g.avg_price:.3f}  {g.title[:70]}")
        scored.append((score, g.cash, g.avg_price, g.title))
        log_key = "log:" + g.key
        if score >= config.LOG_MIN_SCORE and (log_key not in seen or g.cash >= seen[log_key].get("cash", 0) * 1.5):
            pc = (ctx or {}).get("price") or {}
            candidates_log.append({"ts": now, "key": g.key, "score": score, "cash": round(g.cash, 2),
                                   "theme_cash": round(burst, 2), "price": round(g.avg_price, 4), "title": g.title,
                                   "outcome": g.outcome, "wallet": g.wallet, "condition_id": g.condition_id,
                                   "event_slug": g.event_slug, "first_ts": g.first_ts, "last_ts": g.last_ts,
                                   "age_days": wallet.get("age_days"), "price_ref": pc.get("ref"),
                                   "price_before": pc.get("before"), "crowd_new": (ctx or {}).get("crowd_new"),
                                   "why": why, "name": g.pseudonym, "fills": g.fills, "busy": wallet.get("busy"), "since_days": wallet.get("since_days"),
                                   "markets_traded": wallet.get("markets_traded"), "context_checked": ctx is not None,
                                   "parts": [{k: p[k] for k in ("k", "pts", "max", "note")} for p in parts]})
            seen[log_key] = {"ts": now, "cash": g.cash}
        if score >= config.MIN_SCORE:
            text = bet_message(g, score, why, market, now, ctx)
            theme = (g.wallet, theme_of.get(g.condition_id, g.event_slug or g.condition_id))
            other = best_by_theme.get(theme)
            if other and other["score"] >= score:
                seen[g.key] = {"ts": now, "cash": g.cash}  # couvert par l'alerte du même sujet
                continue
            if other:
                alerts.remove(other)
                seen[other["key"]] = {"ts": now, "cash": other["cash"]}
            alerts.append({"kind": "bet", "key": g.key, "score": score, "cash": round(g.cash, 2),
                           "price": round(g.avg_price, 4), "title": g.title, "outcome": g.outcome,
                           "wallet": g.wallet, "condition_id": g.condition_id, "why": why,
                           "first_ts": g.first_ts, "last_ts": g.last_ts, "tx": g.tx[:10], "text": text})
            best_by_theme[theme] = alerts[-1]

    top = sorted(scored, reverse=True)[:5]
    if top:
        print("::notice title=Meilleurs scores::" + " · ".join(
            f"{sc}/100 {fmt_usd(c)} @{p:.0%} {t[:50]}" for sc, c, p, t in top))
    # 2) Mouvements brusques de prix sur les marchés d'actualité les plus liquides
    try:
        markets = pm.top_markets(500) if config.MOVES_ENABLED else []
    except Exception as e:
        print(f"top_markets KO : {e}", file=sys.stderr)
        markets = []
    stats["markets_scanned"] = len(markets)
    for m in markets:
        mv = market_move(m)
        if not mv:
            continue
        ev = (m.get("events") or [{}])[0]
        tags = {str(t.get("slug", "")).lower() for t in (ev.get("tags") or [])}
        ev_slug = ev.get("slug") or m.get("slug", "")
        if not tags and ev_slug:
            try:
                tags = pm.event_tags(ev_slug)
            except Exception:
                tags = set()
        if not is_news_topic(m.get("question", ""), tags, m, ev_slug) or is_same_day_market(m, now):
            continue
        key = f"move:{m.get('conditionId')}"
        if key in seen and now - seen[key]["ts"] < config.MOVE_DEDUP_HOURS * 3600:
            continue
        d1h, outcome = mv
        drivers, top_profile = None, None
        try:
            drivers = move_drivers(pm.market_trades(m.get("conditionId"), now - 3600), d1h)
            if drivers.get("wallets") and drivers["top_share"] >= 0.3:
                top_profile = pm.wallet_profile(drivers["top_wallet"], now)
        except Exception as e:
            print(f"::warning title=Auteurs du mouvement KO::{type(e).__name__}: {e}"[:500])
        alerts.append({"kind": "move", "key": key, "delta_1h": d1h, "title": m.get("question"),
                       "condition_id": m.get("conditionId"), "volume24hr": m.get("volume24hr"),
                       "drivers": drivers, "text": move_message(m, d1h, outcome, drivers, top_profile)})

    # 3) Comptes surveillés : tout achat ou vente, quel que soit le score
    for key, text in watch.check(seen, now):
        alerts.append({"kind": "watch", "key": f"{key}:{now}", "text": text, "html": True})

    # 4) Envoi + mémoire
    for a in alerts:
        print("\n" + a["text"])
        channels = [] if dry_run else notify.send(a["text"], as_html=a.get("html", False))
        a["sent_to"], a["ts"] = channels, now
        if a["kind"] != "watch":
            seen[a["key"]] = {"ts": now, "cash": a.get("cash", 0)}
    if not dry_run:
        save_seen(seen, now)
        if candidates_log:
            with CANDIDATES_FILE.open("a") as f:
                for c in candidates_log:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
        if alerts:
            with ALERTS_FILE.open("a") as f:
                for a in alerts:
                    f.write(json.dumps({k: v for k, v in a.items() if k != "text"}, ensure_ascii=False) + "\n")

    # 5) Rapport horaire des paris sous le seuil (à heure fixe, rien la nuit, rapport de nuit au réveil)
    last = seen.get("report:hourly", {}).get("ts", 0)
    if not dry_run and report.due(now, last):
        start = report.window_start(now, last)
        night = report.local(now).hour == config.REPORT_FIRST_HOUR
        logged = report.load_jsonl(CANDIDATES_FILE)
        for text in report.build(logged, report.load_jsonl(ALERTS_FILE), start, now, night):
            print("\n" + text)
            notify.send(text, as_html=True)
        print(f"::notice title=Rapport horaire::{len(report.pick(logged, start, now))} pari(s) entre 30 et 59 "
              f"depuis {report.paris(start)} (heure de Paris)")
        seen["report:hourly"] = {"ts": now}
        save_seen(seen, now)
    elif dry_run and candidates_log:  # test : aperçu du rapport pour les paris de ce passage
        print("\n" + "\n\n".join(report.build(candidates_log, [], now - 1, now)))

    summary = (f"Fenêtre {lookback_min} min · {stats['trades']} ordres + {stats['makers']} achats maker · "
               f"{stats['groups']} paris sur issues improbables · {stats['news_candidates']} sur l'actualité · "
               f"{stats['wallets_checked']} comptes examinés · {stats['markets_scanned']} marchés surveillés · "
               f"**{len(alerts)} alerte(s)**")
    print("\n" + summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary + "\n\n" + "\n\n".join("```\n" + a["text"] + "\n```" for a in alerts) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=config.LOOKBACK_MIN, help="minutes à relire")
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien et ne touche pas à state/")
    ap.add_argument("--report", type=int, metavar="MIN", help="affiche le rapport des MIN dernières minutes, sans scanner")
    args = ap.parse_args()
    if args.report:
        now = int(time.time())
        msgs = report.build(report.load_jsonl(CANDIDATES_FILE), report.load_jsonl(ALERTS_FILE), now - args.report * 60, now,
                            night=args.report > 120)
        print("\n\n".join(msgs) or "Rien à signaler.")
        return 0
    try:
        return run(args.lookback, args.dry_run)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        # Annotation lisible publiquement (les logs complets exigent d'être connecté à GitHub)
        ours = [line.strip() for line in tb.splitlines() if "/radar/" in line]
        last = " | ".join(ours[-3:] + [tb.strip().splitlines()[-1]])
        print(f"::error title=Scan KO::{type(e).__name__}: {e} || {last}".replace("\n", " ")[:1500])
        return 1


if __name__ == "__main__":
    sys.exit(main())
