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


def get(base: str, path: str, params: dict | None = None, retries: int = 5) -> Any:
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
            try:
                return json.loads(raw)
            except ValueError:
                raise RuntimeError(f"Réponse non JSON sur {url} : {raw[:200]!r}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            body = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {e.code} sur {url} : {body}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def _pick(d: dict, *names, default=None):
    for n in names:
        if d.get(n) is not None:
            return d[n]
    return default


def _ts(v) -> int:
    try:
        t = int(float(v))
    except (TypeError, ValueError):
        return 0
    return t // 1000 if t > 10**12 else t


def _rows(res) -> tuple[list, str | None]:
    """Data API v2 : {"data": [...], "pagination": {"next_cursor": ...}} (tolère l'ancien tableau nu)."""
    if isinstance(res, list):
        return res, None
    if not isinstance(res, dict):
        return [], None
    pag = res.get("pagination") or {}
    return (res.get("data") or []), (pag.get("next_cursor") or pag.get("nextCursor"))


def normalize_trade(t: dict) -> dict:
    """Ramène une ligne de trade v2 (snake_case) au format interne."""
    return {
        "proxyWallet": _pick(t, "proxy_wallet", "proxyWallet", "wallet", default=""),
        "side": str(_pick(t, "side", default="")).upper(),
        "size": float(_pick(t, "size", default=0) or 0),
        "price": float(_pick(t, "price", default=0) or 0),
        "timestamp": _ts(_pick(t, "timestamp", "created_at", default=0)),
        "conditionId": _pick(t, "condition_id", "conditionId", default=""),
        "outcome": _pick(t, "outcome", default=""),
        "outcomeIndex": _pick(t, "outcome_index", "outcomeIndex", default=0),
        "title": _pick(t, "title", default=""),
        "slug": _pick(t, "slug", "market_slug", default=""),
        "eventSlug": _pick(t, "event_slug", "eventSlug", default=""),
        "pseudonym": _pick(t, "pseudonym", "name", default=""),
        "transactionHash": _pick(t, "transaction_hash", "transactionHash", default=""),
    }


def recent_large_trades(since_ts: int, min_cash: float, page: int = 1000, max_pages: int = 200) -> tuple[list[dict], dict]:
    """Ordres « taker » >= min_cash depuis since_ts (flux global, du plus récent au plus ancien).
    La v2 n'a plus de filtre par montant : on lit tout le flux et on filtre ici."""
    out: list[dict] = []
    cursor, pages, seen_rows, oldest = None, 0, 0, None
    sample_keys: list[str] = []
    error = None
    for _ in range(max_pages):
        try:
            res = get(DATA, "/v2/trades", {"limit": page, "cursor": cursor})
        except Exception as e:  # une page qui échoue ne doit pas faire perdre les précédentes
            if not out and pages == 0:
                raise
            error = f"{type(e).__name__}: {e}"[:300]
            break
        rows, cursor = _rows(res)
        pages += 1
        if rows and not sample_keys:
            sample_keys = sorted(rows[0].keys())
        stop = False
        for raw in rows:
            t = normalize_trade(raw)
            seen_rows += 1
            if t["timestamp"] < since_ts:
                stop = True
                continue
            oldest = t["timestamp"] if oldest is None else min(oldest, t["timestamp"])
            if t["size"] * t["price"] >= min_cash:
                out.append(t)
        if stop or not cursor or not rows:
            break
    return out, {"pages": pages, "rows": seen_rows, "oldest": oldest, "sample_keys": sample_keys,
                 "truncated": bool(cursor) and (pages >= max_pages or error is not None), "error": error}


def market_trades(condition_id: str, since_ts: int, max_pages: int = 10) -> list[dict]:
    """Trades d'un marché depuis since_ts (du plus récent au plus ancien)."""
    out: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        rows, cursor = _rows(get(DATA, "/v2/trades", {"condition": condition_id, "limit": 1000, "cursor": cursor}))
        done = False
        for raw in rows:
            t = normalize_trade(raw)
            if t["timestamp"] < since_ts:
                done = True
                continue
            out.append(t)
        if done or not cursor or not rows:
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
    """Âge du compte, nombre de marchés tradés, part du volume par marché (Data API v2 /activity)."""
    prof: dict = {"address": address, "age_days": None, "markets_traded": None, "volume_by_market": {}, "busy": False}
    try:
        res = get(DATA, "/v2/activity", {"user": address, "limit": 1000, "start": 1})
        acts, cursor = _rows(res)
    except Exception:
        return prof
    if not acts:
        return prof
    prof["busy"] = bool(cursor)  # plus de 1000 événements : compte actif, donc pas « neuf »
    if not prof["busy"]:
        first = min(_ts(_pick(a, "timestamp", default=now_ts)) for a in acts)
        prof["age_days"] = (now_ts - first) / 86400
    vol: dict[str, float] = {}
    for a in acts:
        if str(_pick(a, "type", default="")).upper() != "TRADE":
            continue
        cid = _pick(a, "condition_id", "conditionId")
        if not cid:
            continue
        usd = _pick(a, "usdc_size", "usdcSize")
        if usd is None:
            usd = float(_pick(a, "size", default=0) or 0) * float(_pick(a, "price", default=0) or 0)
        vol[cid] = vol.get(cid, 0.0) + float(usd or 0)
    prof["volume_by_market"] = vol
    prof["markets_traded"] = len(vol)
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
