"""Rapport horaire : les paris notés entre LOG_MIN_SCORE et MIN_SCORE, et pourquoi ils n'ont pas déclenché d'alerte."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .scoring import fmt_usd, missed_reasons

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # pas de base de fuseaux : heure d'hiver par défaut
    PARIS = timezone(timedelta(hours=1))

TG_MAX = 3900  # Telegram coupe à 4 096 caractères


def paris(ts: int, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ts, PARIS).strftime(fmt)


def load_jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return []


def pick(candidates: list[dict], start: int, end: int) -> list[dict]:
    """Paris notés dans la fenêtre, sous le seuil d'alerte ; la dernière version d'un même pari l'emporte."""
    latest: dict[str, dict] = {}
    for c in candidates:
        if start < c.get("ts", 0) <= end:
            latest[c["key"]] = c
    return sorted((c for c in latest.values() if c.get("score", 0) < config.MIN_SCORE),
                  key=lambda c: -c.get("score", 0))


def account_line(c: dict) -> str:
    who = c.get("name") or c["wallet"][:10]
    bits = []
    if c.get("busy"):
        bits.append("très actif (plus de 1 000 opérations)")
    elif c.get("age_days") is not None:
        bits.append(f"créé il y a {c['age_days']:.0f} j")
    if c.get("markets_traded") and not c.get("busy"):
        bits.append(f"{c['markets_traded']} marché(s) tradé(s)")
    return f"Compte : {who}" + (" · " + ", ".join(bits) if bits else "")


def entry(c: dict, i: int) -> str:
    gap = int(config.MIN_SCORE) - c["score"]
    when = paris(c["first_ts"]) if c["first_ts"] == c["last_ts"] else f"{paris(c['first_ts'])}–{paris(c['last_ts'])}"
    fills = f" · {c['fills']} ordre(s)" if c.get("fills") else ""
    lines = [
        f"{i}) {c['score']}/100 (manque {gap}) — {c['title']}",
        f"Achat « {c['outcome']} » à {c['price']:.1%} · {fmt_usd(c['cash'])}{fills} · {when}",
        account_line(c),
    ]
    if c.get("price_ref") is not None and c.get("price_before") is not None:
        lines.append(f"Cote : {c['price_ref']:.1%} 24 h avant → {c['price_before']:.1%} juste avant le pari")
    parts = c.get("parts")
    if parts:
        lines.append("Points : " + " · ".join(f"{p['k']} {p['pts']:+d}" if p["k"] == "cote d'avant"
                                              else f"{p['k']} {p['pts']}/{p['max']}" for p in parts))
        missed = missed_reasons(parts, c.get("price_ref") is not None or c.get("context_checked", False))
        if missed:
            lines.append("Pas d'alerte : " + " ; ".join(missed[:4]))
    else:  # entrée d'avant le rapport détaillé
        lines.append("Pourquoi ce score : " + ", ".join(c.get("why") or []))
    lines.append(f"https://polymarket.com/event/{c.get('event_slug') or ''}")
    lines.append(f"https://polymarket.com/profile/{c['wallet']}")
    return "\n".join(lines)


def build(candidates: list[dict], alerts: list[dict], start: int, end: int) -> list[str]:
    """Messages du rapport (découpés pour Telegram). Liste vide s'il n'y a rien à signaler."""
    items = pick(candidates, start, end)
    sent = [a for a in alerts if a.get("kind") == "bet" and start < a.get("ts", 0) <= end]
    if not items and not config.REPORT_EMPTY:
        return []
    head = (f"📋 Rapport radar {paris(start)} → {paris(end)} (heure de Paris)\n"
            f"{len(items)} pari(s) noté(s) entre {config.LOG_MIN_SCORE:.0f} et {config.MIN_SCORE - 1:.0f} · "
            f"{len(sent)} alerte(s) envoyée(s)")
    if not items:
        return [head + "\nRien à signaler."]
    msgs, cur = [], head
    for i, c in enumerate(items, 1):
        block = entry(c, i)
        if len(cur) + 2 + len(block) > TG_MAX:
            msgs.append(cur)
            cur = f"📋 Rapport radar (suite)"
        cur += "\n\n" + block
    msgs.append(cur)
    return msgs
