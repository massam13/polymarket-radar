"""Envoi des alertes : Telegram, Slack et/ou Discord selon les secrets configurés."""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def _post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def _try(name: str, fn) -> bool:
    for attempt in range(3):
        try:
            fn()
            return True
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode("utf-8", "replace")
            print(f"::warning title=Envoi {name} KO::HTTP {e.code} {body}")
            if e.code < 500 and e.code != 429:
                return False
        except Exception as e:
            print(f"::warning title=Envoi {name} KO::{type(e).__name__}: {e}")
        time.sleep(3 * (attempt + 1))
    return False


def plain(text: str) -> str:
    """HTML Telegram → texte brut (liens affichés en clair) pour Slack / Discord."""
    text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"\2 : \1", text)
    return html.unescape(re.sub(r"</?b>", "", text))


def send(text: str, as_html: bool = False) -> list[str]:
    """Envoie sur tous les canaux configurés. Renvoie la liste des canaux qui ont réussi.
    as_html : le texte est en HTML Telegram (<b>, <a href>), converti en texte brut pour les autres canaux."""
    used = []
    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        url = f"https://api.telegram.org/bot{urllib.parse.quote(tg_token, safe=':')}/sendMessage"
        payload = {"chat_id": tg_chat, "text": text, "disable_web_page_preview": True}
        if as_html:
            payload["parse_mode"] = "HTML"
        if _try("Telegram", lambda: _post(url, payload)):
            used.append("telegram")
    text = plain(text) if as_html else text
    if os.environ.get("SLACK_WEBHOOK_URL"):
        if _try("Slack", lambda: _post(os.environ["SLACK_WEBHOOK_URL"], {"text": text})):
            used.append("slack")
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        if _try("Discord", lambda: _post(os.environ["DISCORD_WEBHOOK_URL"], {"content": text[:1990]})):
            used.append("discord")
    return used
