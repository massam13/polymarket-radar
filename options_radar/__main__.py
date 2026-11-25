"""Un passage du radar options : python -m options_radar [--dry-run] [--force] [--limit N] [--tickers AAPL,DECK].

Lit les chaînes d'options Yahoo Finance (différé ~15-20 min) des valeurs du S&P 1500, garde les contrats
hors de la monnaie, à échéance courte, dont le volume du jour écrase les positions ouvertes, puis note chaque action.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from radar import notify

from . import scoring as sc
from .scoring import Contract, Flow, fmt_usd

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
SEEN_FILE, HISTORY_FILE, CANDIDATES_FILE = STATE / "options_seen.json", STATE / "options_history.json", STATE / "options_candidates.jsonl"
ALERTS_FILE = STATE / "options_alerts.jsonl"
UNIVERSE_FILE = Path(__file__).resolve().parent / "universe.csv"
NY = ZoneInfo("America/New_York")
HISTORY_DAYS = 25
MIN_HISTORY_DAYS = 5
MAX_EXPIRIES = 5          # les 5 échéances les plus proches (≤ 45 j) : là où se logent les paris sur une nouvelle imminente


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def load_universe(only: list[str] | None) -> list[dict]:
    with UNIVERSE_FILE.open() as f:
        rows = list(csv.DictReader(f))
    if only:
        known = {r["ticker"]: r for r in rows}
        return [known.get(t, {"ticker": t, "name": t, "sector": "", "index": ""}) for t in only]
    return rows


_lock = threading.Lock()
_next_call = [0.0]
MIN_INTERVAL = 0.12   # ~8 requêtes/s tous fils confondus : au-delà Yahoo répond 429


def _call(fn, *a):
    """Appel Yahoo espacé, avec reprise après « Too Many Requests »."""
    for attempt in range(4):
        with _lock:
            wait = _next_call[0] - time.monotonic()
            _next_call[0] = max(_next_call[0], time.monotonic()) + MIN_INTERVAL
        if wait > 0:
            time.sleep(wait)
        try:
            return fn(*a)
        except Exception as e:
            if not any(k in type(e).__name__ for k in ("RateLimit", "Timeout")) or attempt == 3:
                raise
            with _lock:   # tout le monde fait une pause
                _next_call[0] = time.monotonic() + 30 * (attempt + 1)


def fetch(row: dict, today: date) -> dict:
    import yfinance as yf  # importé ici : les tests n'en ont pas besoin

    t = yf.Ticker(row["ticker"])
    options = _call(lambda: t.options)
    expiries = [e for e in options if 0 < (date.fromisoformat(e) - today).days <= sc.MAX_DTE][:MAX_EXPIRIES]
    contracts, side_volume, underlying = [], {"call": 0.0, "put": 0.0}, {}
    for e in expiries:
        chain = _call(t.option_chain, e)
        underlying = chain.underlying or underlying
        spot = float(underlying.get("regularMarketPrice") or 0)
        rmt = underlying.get("regularMarketTime")
        trade_day = datetime.fromtimestamp(rmt, NY).date() if rmt else today   # dernière séance (≠ aujourd'hui avec --force)
        if not spot:
            continue
        dte = (date.fromisoformat(e) - today).days
        for side, df in (("call", chain.calls), ("put", chain.puts)):
            for r in df.itertuples():
                vol = float(r.volume) if r.volume == r.volume and r.volume else 0.0   # NaN → 0
                # Ne garder que ce qui a traité dans la séance (sinon Yahoo montre le volume d'un autre jour)
                ltd = getattr(r, "lastTradeDate", None)
                if vol <= 0 or (ltd is not None and ltd.tz_convert(NY).date() != trade_day):
                    continue
                side_volume[side] += vol
                if str(getattr(r, "contractSize", "REGULAR")) != "REGULAR":
                    continue
                oi = float(r.openInterest) if r.openInterest == r.openInterest else 0.0
                contracts.append(Contract(row["ticker"], side, float(r.strike), e, dte, float(r.lastPrice or 0),
                                          float(r.bid or 0), float(r.ask or 0), vol, oi,
                                          float(r.impliedVolatility or 0), spot))
    return {"row": row, "contracts": contracts, "side_volume": side_volume, "underlying": underlying}


def market_is_live(results: list[dict], today: date) -> bool:
    """Vrai si la majorité des cotations sont du jour (jours fériés, week-end : rien à lire)."""
    stamps = [r["underlying"].get("regularMarketTime") for r in results if r["underlying"].get("regularMarketTime")]
    fresh = [s for s in stamps if datetime.fromtimestamp(s, NY).date() == today]
    return len(stamps) > 0 and len(fresh) >= 0.5 * len(stamps)


def update_history(history: dict, results: list[dict], today: str) -> None:
    for r in results:
        h = history.setdefault(r["row"]["ticker"], {})
        h[today] = [round(r["side_volume"]["call"]), round(r["side_volume"]["put"])]
        for d in sorted(h)[:-HISTORY_DAYS]:
            del h[d]


def side_average(history: dict, ticker: str, side: str, today: str) -> float | None:
    past = [v for d, v in history.get(ticker, {}).items() if d != today]
    if len(past) < MIN_HISTORY_DAYS:
        return None
    i = 0 if side == "call" else 1
    avg = sum(p[i] for p in past) / len(past)
    return avg or None


def build_flows(results: list[dict], history: dict, today: str) -> list[Flow]:
    flows = []
    for r in results:
        u, row = r["underlying"], r["row"]
        kept = [c for c in r["contracts"] if sc.contract_reasons_to_skip(c) is None]
        for side in ("call", "put"):
            cs = [c for c in kept if c.side == side]
            if not cs:
                continue
            flows.append(Flow(
                ticker=row["ticker"], side=side, name=u.get("longName") or row.get("name", ""), sector=row.get("sector", ""),
                market_cap=float(u.get("marketCap") or 0), spot=float(u.get("regularMarketPrice") or 0),
                change_pct=float(u.get("regularMarketChangePercent") or 0),
                earnings_ts=u.get("earningsTimestampStart") or u.get("earningsTimestamp"),
                side_volume_today=r["side_volume"][side], side_volume_avg=side_average(history, row["ticker"], side, today),
                contracts=cs,
            ))
    return flows


def ddmm(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}"


def message(flow: Flow, score: int, why: list[str]) -> str:
    top = flow.top
    label = "CALLS (pari à la hausse)" if flow.side == "call" else "PUTS (pari à la baisse)"
    cap = f" · {flow.market_cap / 1e9:.1f} Md$".replace(".", ",") if flow.market_cap else ""
    oi = f", positions ouvertes veille {top.oi:,.0f} (×{top.vol_oi:.0f})" if top.vol_oi else ""
    lines = [
        f"🎯 Options inhabituelles — score {score}/100",
        f"{flow.name} ({flow.ticker}){' · ' + flow.sector if flow.sector else ''}{cap}",
        f"{label} : {fmt_usd(flow.premium)} sur {len(flow.contracts)} contrat(s)",
        f"Le plus gros : {top.side} {top.strike:g} $ échéance {ddmm(top.expiry)} ({top.dte} j) — "
        f"{top.volume:,.0f} lots à {top.last:.2f} $ = {fmt_usd(top.premium)}{oi}",
        f"Action : {flow.spot:.2f} $ ({flow.change_pct:+.1f} % aujourd'hui), {top.otm:.0%} sous le prix visé"
        if flow.side == "call" else
        f"Action : {flow.spot:.2f} $ ({flow.change_pct:+.1f} % aujourd'hui), {top.otm:.0%} au-dessus du prix visé",
    ]
    if flow.earnings_ts:
        lines.append(f"Prochains résultats : {datetime.fromtimestamp(flow.earnings_ts, NY):%d/%m/%Y}")
    lines += [
        "Pourquoi : " + ", ".join(why),
        f"https://finance.yahoo.com/quote/{flow.ticker}/options",
        f"https://www.google.com/search?tbm=nws&q={flow.ticker}+stock",
        "Signal statistique à vérifier (couverture, spread, actu déjà sortie…), pas un conseil.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien, n'écrit pas l'état")
    ap.add_argument("--force", action="store_true", help="tourne même marché fermé (relit la dernière séance)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    now = datetime.now(NY)
    if not args.force and not (now.weekday() < 5 and (15, 0) <= (now.hour, now.minute) < (16, 0)):
        # Le déclencheur tourne à 19:xx et 20:xx UTC pour couvrir heure d'hiver/d'été ; un seul passage utile par jour
        print(f"::notice title=Options::hors fenêtre (New York {now:%H:%M}), rien à faire")
        return 0

    today = now.date()
    universe = load_universe([t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None)
    if args.limit:
        universe = universe[: args.limit]

    t0, results, errors = time.time(), [], []
    with ThreadPoolExecutor(args.workers) as ex:
        futures = [(row, ex.submit(fetch, row, today)) for row in universe]
        for row, f in futures:
            try:
                results.append(f.result())
            except Exception as e:
                errors.append(f"{row['ticker']}: {type(e).__name__} {str(e)[:80]}")
    print(f"::notice title=Options collecte::{len(results)}/{len(universe)} actions lues en {time.time() - t0:.0f} s, {len(errors)} erreurs")
    if errors:
        print(f"::warning title=Options erreurs::{' | '.join(errors[:8])}")
    if len(results) < 0.5 * len(universe):
        print("::error title=Options::moins de la moitié des actions lues — Yahoo bloque ou a changé")
        return 1
    if not args.force and not market_is_live(results, today):
        print("::notice title=Options::marché fermé aujourd'hui (férié ?), rien à faire")
        return 0

    history = load_json(HISTORY_FILE)
    today_s = today.isoformat()
    flows = build_flows(results, history, today_s)
    now_ts = int(time.time())

    scored, skipped = [], {}
    for fl in flows:
        reason = sc.flow_reasons_to_skip(fl, now_ts)
        if reason:
            skipped[reason.split(" ")[0]] = skipped.get(reason.split(" ")[0], 0) + 1
            continue
        score, why = sc.score_flow(fl)
        scored.append((score, fl, why))
    scored.sort(key=lambda x: -x[0])
    oi_known = sum(1 for r in results for c in r["contracts"] if c.oi > 0)
    oi_all = sum(len(r["contracts"]) for r in results) or 1
    print(f"::notice title=Options tri::{len(flows)} flux inhabituels, {len(scored)} notés, écartés {skipped}, "
          f"OI renseigné sur {oi_known / oi_all:.0%} des contrats, meilleurs : "
          + ", ".join(f"{fl.ticker} {fl.side} {s}" for s, fl, _ in scored[:8]))

    seen = load_json(SEEN_FILE)
    sent = 0
    for score, fl, why in scored:
        if score < sc.MIN_SCORE or sent >= sc.MAX_ALERTS_PER_RUN:
            break
        key = f"{fl.ticker}:{fl.side}"
        if now_ts - seen.get(key, {}).get("ts", 0) < sc.REALERT_DAYS * 86400:
            continue
        text = message(fl, score, why)
        print(text, "\n---")
        if not args.dry_run:
            notify.send(text)
            seen[key] = {"ts": now_ts, "score": score, "premium": round(fl.premium)}
            with ALERTS_FILE.open("a") as f:
                f.write(json.dumps({"date": today_s, "ticker": fl.ticker, "side": fl.side, "score": score,
                                    "premium": round(fl.premium), "spot": fl.spot, "top": fl.top.__dict__}) + "\n")
        sent += 1

    if not args.dry_run and not args.tickers and not args.limit:
        STATE.mkdir(exist_ok=True)
        update_history(history, results, today_s)
        HISTORY_FILE.write_text(json.dumps(history, separators=(",", ":"), sort_keys=True) + "\n")
        SEEN_FILE.write_text(json.dumps({k: v for k, v in seen.items() if now_ts - v["ts"] < 30 * 86400}, indent=1) + "\n")
        with CANDIDATES_FILE.open("a") as f:
            for score, fl, why in scored:
                if score >= sc.LOG_MIN_SCORE:
                    f.write(json.dumps({"date": today_s, "ticker": fl.ticker, "side": fl.side, "score": score,
                                        "premium": round(fl.premium), "spot": fl.spot, "change_pct": fl.change_pct,
                                        "why": why}, ensure_ascii=False) + "\n")
    print(f"::notice title=Options alertes::{sent} alerte(s){' (test, rien envoyé)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
