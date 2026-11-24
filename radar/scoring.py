"""Logique pure (sans réseau) : regroupement des ordres, filtre « actualité », score de suspicion."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import config


@dataclass
class BuyGroup:
    """Tous les achats d'un même compte sur une même issue dans la fenêtre."""
    wallet: str
    condition_id: str
    outcome: str
    outcome_index: int
    title: str
    slug: str
    event_slug: str
    pseudonym: str
    cash: float = 0.0
    shares: float = 0.0
    fills: int = 0
    first_ts: int = 0
    last_ts: int = 0
    tx: list[str] = field(default_factory=list)

    @property
    def avg_price(self) -> float:
        return self.cash / self.shares if self.shares else 0.0

    @property
    def payout(self) -> float:
        return self.shares  # chaque part paie 1 $ si l'issue gagne

    @property
    def key(self) -> str:
        return f"bet:{self.wallet}:{self.condition_id}:{self.outcome_index}"


def group_buys(trades: list[dict]) -> list[BuyGroup]:
    groups: dict[tuple, BuyGroup] = {}
    for t in trades:
        if str(t.get("side", "")).upper() != "BUY":
            continue  # une vente est le plus souvent une sortie de position, pas un pari
        size, price = float(t.get("size") or 0), float(t.get("price") or 0)
        if size <= 0 or price <= 0:
            continue
        k = (t.get("proxyWallet"), t.get("conditionId"), t.get("outcomeIndex"))
        g = groups.get(k)
        ts = int(t.get("timestamp", 0))
        if g is None:
            g = groups[k] = BuyGroup(
                wallet=t.get("proxyWallet", ""), condition_id=t.get("conditionId", ""),
                outcome=str(t.get("outcome", "")), outcome_index=int(t.get("outcomeIndex") or 0),
                title=t.get("title", ""), slug=t.get("slug", ""), event_slug=t.get("eventSlug", ""),
                pseudonym=t.get("pseudonym") or t.get("name") or "", first_ts=ts, last_ts=ts,
            )
        g.cash += size * price
        g.shares += size
        g.fills += 1
        g.first_ts, g.last_ts = min(g.first_ts, ts), max(g.last_ts, ts)
        if t.get("transactionHash"):
            g.tx.append(t["transactionHash"])
    return list(groups.values())


_TITLE_RE = [re.compile(p, re.I) for p in config.EXCLUDED_TITLE_PATTERNS]


_SLUG_RE = re.compile(config.EXCLUDED_SLUG_PATTERN, re.I)


def is_news_topic(title: str, tags: set[str] | None = None, market: dict | None = None, slug: str = "") -> bool:
    if tags and tags & config.EXCLUDED_TAGS:
        return False
    if market and (market.get("sportsMarketType") or market.get("gameStartTime") or market.get("gameId")):
        return False  # Gamma marque ainsi les marchés sportifs
    if slug and _SLUG_RE.search(slug):
        return False
    return not any(r.search(title or "") for r in _TITLE_RE)


def prelim_score(g: BuyGroup) -> int:
    """Partie du score calculable sans appel réseau (sert à trier qui on examine)."""
    return _longshot_pts(g.avg_price) + _size_pts(g.cash)


def _longshot_pts(p: float) -> int:
    if p <= 0.05: return 30
    if p <= 0.10: return 25
    if p <= 0.20: return 15
    if p <= 0.35: return 5
    return 0


def _size_pts(cash: float) -> int:
    if cash >= 25000: return 25
    if cash >= 10000: return 20
    if cash >= 3000: return 12
    if cash >= 1000: return 6
    return 0


def score_bet(g: BuyGroup, wallet: dict, market: dict | None) -> tuple[int, list[str]]:
    pts, why = 0, []

    lp = _longshot_pts(g.avg_price)
    if lp:
        pts += lp
        why.append(f"issue jugée improbable ({g.avg_price:.1%})")

    sp = _size_pts(g.cash)
    if sp:
        pts += sp
        why.append(f"mise de {fmt_usd(g.cash)}")

    age = wallet.get("age_days")
    if age is not None:
        if age < 2:
            pts += 20; why.append(f"compte créé il y a {age * 24:.0f} h")
        elif age < 7:
            pts += 15; why.append(f"compte créé il y a {age:.0f} j")
        elif age < 30:
            pts += 7; why.append(f"compte récent ({age:.0f} j)")

    n = wallet.get("markets_traded")
    if n is not None and not wallet.get("busy"):
        if n <= 1:
            pts += 15; why.append("aucun autre marché tradé")
        elif n <= 3:
            pts += 10; why.append(f"seulement {n} marchés tradés")
        elif n <= 10:
            pts += 4
    vol = wallet.get("volume_by_market") or {}
    total = sum(vol.values())
    if total > 0 and vol.get(g.condition_id, 0) / total >= 0.8 and (n or 0) > 1:
        pts += 5; why.append("80 %+ de son volume sur ce marché")

    if market:
        v24 = float(market.get("volume24hr") or 0)
        if v24 > 0:
            share = g.cash / v24
            if share >= 0.5:
                pts += 10; why.append(f"{share:.0%} du volume 24 h du marché")
            elif share >= 0.2:
                pts += 6; why.append(f"{share:.0%} du volume 24 h du marché")

    if g.fills >= 3:
        pts += 5; why.append(f"{g.fills} ordres successifs")

    return min(pts, 100), why


def market_move(m: dict) -> tuple[float, str] | None:
    """Saut de proba sur 1 h d'un marché liquide. Renvoie (variation, issue concernée)."""
    try:
        d1h = float(m.get("oneHourPriceChange") or 0)
        v24 = float(m.get("volume24hr") or 0)
    except (TypeError, ValueError):
        return None
    if abs(d1h) < config.MOVE_MIN_1H or v24 < config.MOVE_MIN_VOL24:
        return None
    outcomes = m.get("outcomes")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except ValueError:
            outcomes = None
    first = outcomes[0] if outcomes else "Yes"
    return d1h, first


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]


def is_same_day_market(m: dict, now_ts: int) -> bool:
    """Marché qui se termine bientôt ou porte la date du jour : souvent un événement suivi en direct."""
    from datetime import datetime, timedelta, timezone
    end = m.get("endDate") or m.get("end_date")
    if end:
        try:
            end_ts = datetime.fromisoformat(str(end).replace("Z", "+00:00")).timestamp()
            if end_ts - now_ts < config.MOVE_MIN_HOURS_TO_END * 3600:
                return True
        except ValueError:
            pass
    title = (m.get("question") or "").lower()
    for delta in (-1, 0, 1):
        d = datetime.fromtimestamp(now_ts, timezone.utc) + timedelta(days=delta)
        if re.search(rf"\b{_MONTHS[d.month - 1].lower()} {d.day}\b", title):
            return True
    return False


def move_drivers(trades: list[dict], d1h: float) -> dict:
    """Qui a poussé le prix ? Acheteurs de l'issue qui monte (ou vendeurs de l'issue opposée)."""
    up_idx = 0 if d1h > 0 else 1
    cash_by_wallet: dict[str, float] = {}
    names: dict[str, str] = {}
    for t in trades:
        idx, side, size, price = int(t.get("outcomeIndex") or 0), t.get("side"), t["size"], t["price"]
        if side == "BUY" and idx == up_idx:
            cash = size * price
        elif side == "SELL" and idx != up_idx:
            cash = size * (1 - price)
        else:
            continue
        w = t.get("proxyWallet") or ""
        cash_by_wallet[w] = cash_by_wallet.get(w, 0.0) + cash
        names.setdefault(w, t.get("pseudonym") or w[:10])
    total = sum(cash_by_wallet.values())
    if not total:
        return {"wallets": 0, "total": 0.0}
    top_w, top_cash = max(cash_by_wallet.items(), key=lambda kv: kv[1])
    return {"wallets": len(cash_by_wallet), "total": total, "top_wallet": top_w,
            "top_name": names.get(top_w, ""), "top_cash": top_cash, "top_share": top_cash / total}


def fmt_usd(x: float) -> str:
    return f"{x:,.0f} $".replace(",", " ")
