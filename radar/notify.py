"""Envoi des alertes : Telegram, Slack et/ou Discord selon les secrets configurés."""
from __future__ import annotations

import json
import os
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


def send(text: str) -> list[str]:
    """Envoie sur tous les canaux configurés. Renvoie la liste des canaux qui ont réussi."""
    used = []
    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        url = f"https://api.telegram.org/bot{urllib.parse.quote(tg_token, safe=':')}/sendMessage"
        if _try("Telegram", lambda: _post(url, {"chat_id": tg_chat, "text": text, "disable_web_page_preview": True})):
            used.append("telegram")
    if os.environ.get("SLACK_WEBHOOK_URL"):
        if _try("Slack", lambda: _post(os.environ["SLACK_WEBHOOK_URL"], {"text": text})):
            used.append("slack")
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        if _try("Discord", lambda: _post(os.environ["DISCORD_WEBHOOK_URL"], {"content": text[:1990]})):
            used.append("discord")
    return used
