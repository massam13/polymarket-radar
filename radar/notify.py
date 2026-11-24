"""Envoi des alertes : Telegram, Slack et/ou Discord selon les secrets configurés."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def _post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def send(text: str) -> list[str]:
    """Envoie sur tous les canaux configurés. Renvoie la liste des canaux utilisés."""
    used = []
    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        _post(f"https://api.telegram.org/bot{urllib.parse.quote(tg_token, safe=':')}/sendMessage",
              {"chat_id": tg_chat, "text": text, "disable_web_page_preview": True})
        used.append("telegram")
    if os.environ.get("SLACK_WEBHOOK_URL"):
        _post(os.environ["SLACK_WEBHOOK_URL"], {"text": text})
        used.append("slack")
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        _post(os.environ["DISCORD_WEBHOOK_URL"], {"content": text[:1990]})
        used.append("discord")
    return used
