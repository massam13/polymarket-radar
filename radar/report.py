"""Rapport horaire : les paris notés entre LOG_MIN_SCORE et MIN_SCORE, et pourquoi ils n'ont pas déclenché d'alerte.
Envoyé au premier scan de chaque heure, de 8 h à 23 h (heure de Paris). La nuit, rien ; à 8 h, un rapport de nuit."""
from __future__ import annotations

import html
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


def local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, PARIS)


def paris(ts: int, fmt: str = "%H:%M") -> str:
    return local(ts).strftime(fmt)


def due(now: int, last: int) -> bool:
    """Premier scan d'une nouvelle heure, hors nuit (rapport de nuit inclus à l'heure du réveil)."""
    if not config.REPORT_EVERY_MIN:
        return False
    h = local(now).hour
    if not (config.REPORT_FIRST_HOUR <= h <= config.REPORT_LAST_HOUR):
        return False
    return local(last).strftime("%Y%m%d%H") != local(now).strftime("%Y%m%d%H")


def window_start(now: int, last: int) -> int:
    """Depuis le dernier rapport, sans remonter au-delà de la nuit (premier rapport ou longue panne)."""
    longest = (24 - config.REPORT_LAST_HOUR + config.REPORT_FIRST_HOUR + 1) * 3600
    return max(last, now - longest)


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


def _pct(p: float) -> str:
    return (f"{p:.0%}" if p >= 0.095 else f"{p:.1%}").replace(".", ",")


def entry(c: dict, i: int) -> str:
    e = html.escape
    when = paris(c["first_ts"]) if c["first_ts"] == c["last_ts"] else f"{paris(c['first_ts'])}–{paris(c['last_ts'])}"
    links = (f'<a href="https://polymarket.com/event/{e(c.get("event_slug") or "")}">marché</a> · '
             f'<a href="https://polymarket.com/profile/{e(c["wallet"])}">compte</a>')
    lines = [f"<b>{c['score']}/100</b> · {e(c['title'])}",
             f"« {e(c['outcome'])} » à {_pct(c['price'])} · {fmt_usd(c['cash'])} · {when} · {links}"]
    parts = c.get("parts")
    if parts:
        checked = c.get("price_ref") is not None or c.get("context_checked", False)
        missed = missed_reasons(parts, checked)
        lines.append("Écarté : " + e(" · ".join(missed[: config.REPORT_MAX_REASONS])))
    else:  # entrée d'avant le rapport détaillé
        lines.append("Pourquoi ce score : " + e(", ".join(c.get("why") or [])))
    return "\n".join(lines)


def build(candidates: list[dict], alerts: list[dict], start: int, end: int, night: bool = False) -> list[str]:
    """Messages du rapport, en HTML Telegram, découpés à 4 096 caractères. Vide s'il n'y a rien à signaler."""
    items = pick(candidates, start, end)
    sent = [a for a in alerts if a.get("kind") == "bet" and start < a.get("ts", 0) <= end]
    if not items and not (night or config.REPORT_EMPTY):
        return []
    title = "🌙 Rapport de nuit" if night else "📋 Radar"
    head = (f"<b>{title} {paris(start)} → {paris(end)}</b>\n"
            f"{len(items)} pari(s) entre {config.LOG_MIN_SCORE:.0f} et {config.MIN_SCORE - 1:.0f} · "
            f"{len(sent)} alerte(s) envoyée(s)")
    if not items:
        return [head + "\nRien à signaler."]
    msgs, cur = [], head
    for i, c in enumerate(items, 1):
        block = entry(c, i)
        if len(cur) + 2 + len(block) > TG_MAX:
            msgs.append(cur)
            cur = f"<b>{title} (suite)</b>"
        cur += "\n\n" + block
    msgs.append(cur)
    return msgs
