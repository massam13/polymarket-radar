"""Client minimal des API publiques Polymarket (lecture seule, sans clé)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
UA = "polymarket-radar/0.1"


def get(base: str, path: str, params: dict | None = None, retries: int = 3) -> Any:
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read()
            try:
                return json.loads(raw)
            except ValueError:
                raise RuntimeError(f"Réponse non JSON sur {url} : {raw[:200]!r}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            body = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {e.code} sur {url} : {body}") from e
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def recent_large_trades(since_ts: int, min_cash: float, page: int = 500, max_pages: int = 20) -> list[dict]:
    """Ordres « taker » >= min_cash depuis since_ts, du plus récent au plus ancien."""
    out: list[dict] = []
    for i in range(max_pages):
        batch = get(DATA, "/trades", {
            "limit": page, "offset": i * page, "takerOnly": "true",
            "filterType": "CASH", "filterAmount": int(min_cash),
        })
        if not batch:
            break
        out.extend(t for t in batch if int(t.get("timestamp", 0)) >= since_ts)
        if len(batch) < page or int(batch[-1].get("timestamp", 0)) < since_ts:
            break
    return out


def market_by_condition(condition_id: str) -> dict | None:
    res = get(GAMMA, "/markets", {"condition_ids": condition_id, "limit": 1})
    return res[0] if res else None


def event_tags(event_slug: str) -> set[str]:
    if not event_slug:
        return set()
    res = get(GAMMA, "/events", {"slug": event_slug, "limit": 1})
    if not res:
        return set()
    return {str(t.get("slug", "")).lower() for t in (res[0].get("tags") or []) if t.get("slug")}


def wallet_profile(address: str, now_ts: int) -> dict:
    """Âge du compte, nombre de marchés tradés, part du volume par marché."""
    prof: dict = {"address": address, "age_days": None, "markets_traded": None, "volume_by_market": {}, "busy": False}
    try:
        acts = get(DATA, "/activity", {"user": address, "limit": 500, "sortBy": "TIMESTAMP", "sortDirection": "DESC"}) or []
    except Exception:
        acts = []
    if acts:
        prof["busy"] = len(acts) >= 500  # historique tronqué : compte actif, donc pas « neuf »
        if not prof["busy"]:
            first = min(int(a.get("timestamp", now_ts)) for a in acts)
            prof["age_days"] = (now_ts - first) / 86400
        vol: dict[str, float] = {}
        for a in acts:
            if a.get("type") == "TRADE" and a.get("conditionId"):
                vol[a["conditionId"]] = vol.get(a["conditionId"], 0.0) + float(a.get("usdcSize") or 0)
        prof["volume_by_market"] = vol
        prof["markets_traded"] = len(vol)
    try:
        traded = get(DATA, "/traded", {"user": address})
        if isinstance(traded, dict) and traded.get("traded") is not None:
            prof["markets_traded"] = int(traded["traded"])
    except Exception:
        pass
    return prof


def top_markets(limit: int = 500) -> list[dict]:
    out: list[dict] = []
    for offset in range(0, limit, 100):
        batch = get(GAMMA, "/markets", {
            "active": "true", "closed": "false", "order": "volume24hr", "ascending": "false",
            "limit": 100, "offset": offset,
        })
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
    return out
