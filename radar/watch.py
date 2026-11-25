"""Comptes surveillés : chaque nouvel achat ou vente d'un compte de watchlist.json est envoyé, quel que soit le score.

watchlist.json (racine du dépôt) : {"0xadresse": {"name": "pseudo", "note": "pourquoi on le suit"}, ...}
Au premier passage, l'historique existant n'est pas envoyé : on part de l'instant présent.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import config
from . import polymarket as pm
from .report import paris
from .scoring import fmt_usd

WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.json"


def load(path: Path = WATCHLIST) -> dict[str, dict]:
    try:
        return {k.lower(): v for k, v in json.loads(path.read_text()).items() if k.startswith("0x")}
    except (OSError, ValueError):
        return {}


def summarize(acts: list[dict], since: int) -> list[dict]:
    """Trades postérieurs à since, regroupés par marché, issue et sens (les petits ordres d'un même achat ensemble)."""
    out: dict[tuple, dict] = {}
    for a in acts:
        ts = pm._ts(pm._pick(a, "timestamp", default=0))
        if ts <= since or str(pm._pick(a, "type", default="")).upper() != "TRADE":
            continue
        cid = pm._pick(a, "condition_id", "conditionId", default="")
        side = str(pm._pick(a, "side", default="")).upper()
        outcome = pm._pick(a, "outcome", default="")
        cash = float(pm._pick(a, "usdc_size", "usdcSize", default=0) or 0)
        shares = float(pm._pick(a, "size", default=0) or 0)
        k = (cid, outcome, side)
        g = out.setdefault(k, {"title": pm._pick(a, "title", default=""), "outcome": outcome, "side": side,
                               "event_slug": pm._pick(a, "event_slug", "eventSlug", default=""),
                               "cash": 0.0, "shares": 0.0, "fills": 0, "first": ts, "last": ts})
        g["cash"] += cash
        g["shares"] += shares
        g["fills"] += 1
        g["first"], g["last"] = min(g["first"], ts), max(g["last"], ts)
    return sorted((g for g in out.values() if g["cash"] >= config.WATCH_MIN_CASH), key=lambda g: g["first"])


def message(wallet: str, info: dict, moves: list[dict]) -> str:
    e = html.escape
    who = info.get("name") or wallet[:10]
    lines = [f"👁 <b>Compte surveillé : {e(who)}</b>" + (f" ({e(info['note'])})" if info.get("note") else "")]
    for g in moves:
        verb = "Achat" if g["side"] == "BUY" else "Vente"
        price = g["cash"] / g["shares"] if g["shares"] else 0
        a, b = paris(g["first"]), paris(g["last"])
        when = a if a == b else f"{a}–{b}"
        lines.append(f"{verb} « {e(g['outcome'])} » {fmt_usd(g['cash'])} à {price:.1%} · {when} · "
                     f'<a href="https://polymarket.com/event/{e(g["event_slug"])}">{e(g["title"])}</a>')
    lines.append(f'<a href="https://polymarket.com/profile/{e(wallet)}">Voir le compte</a>')
    return "\n".join(lines)


def check(seen: dict, now: int, fetch=None, watchlist: dict | None = None) -> list[tuple[str, str]]:
    """(clé, message) pour chaque compte surveillé qui a bougé depuis le dernier passage. Met à jour seen."""
    fetch = fetch or (lambda w: pm._rows(pm.get(pm.DATA, "/v2/activity", {"user": w, "limit": 200}))[0])
    out = []
    for wallet, info in (load() if watchlist is None else watchlist).items():
        key = f"watch:{wallet}"
        last = seen.get(key, {}).get("last")
        try:
            acts = fetch(wallet)
        except Exception as e:
            print(f"::warning title=Compte surveillé KO::{info.get('name') or wallet[:10]} : {type(e).__name__}: {e}"[:300])
            continue
        newest = max([pm._ts(pm._pick(a, "timestamp", default=0)) for a in acts] + [last or 0])
        if last is not None:
            moves = summarize(acts, last)
            if moves:
                out.append((key, message(wallet, info, moves)))
        seen[key] = {"ts": now, "last": newest if last is not None else max(newest, now)}
    return out
