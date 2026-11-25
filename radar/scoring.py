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


def is_longshot(price: float) -> bool:
    """Issue jugée improbable, sans être un marché déjà tranché (< 1 %)."""
    return config.MIN_PRICE <= price <= config.MAX_PRICE


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


_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z.'\-]{2,}")


def theme_keys(title: str, tags: set[str] | None = None) -> set[str]:
    """Indices de sujet d'un marché : thèmes Polymarket spécifiques + noms propres du titre."""
    keys = {f"tag:{t}" for t in (tags or set()) if t and t not in config.GENERIC_TAGS}
    for tok in _TOKEN_RE.findall(title or ""):
        w = tok.lower().strip(".'-")
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) >= 3 and w not in config.THEME_STOPWORDS:
            keys.add(f"w:{w}")
    return keys


def theme_clusters(keys_by_market: dict[str, set[str]]) -> dict[str, int]:
    """Regroupe les marchés qui partagent au moins un indice. Renvoie {marché: n° de sujet}."""
    parent = {m: m for m in keys_by_market}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    for m, keys in keys_by_market.items():
        for k in keys:
            if k in owner:
                parent[find(m)] = find(owner[k])
            else:
                owner[k] = m
    roots: dict[str, int] = {}
    return {m: roots.setdefault(find(m), len(roots)) for m in keys_by_market}


def prelim_score(g: BuyGroup) -> int:
    """Partie du score calculable sans appel réseau (sert à trier qui on examine)."""
    return _longshot_pts(g.avg_price) + _size_pts(g.cash)


def _longshot_pts(p: float) -> int:
    """Continu : 30 pts à 5 % ou moins, 0 pt à 40 %."""
    if p <= 0.05:
        return 30
    if p >= 0.40:
        return 0
    return round(30 * (0.40 - p) / 0.35)


def _size_pts(cash: float) -> int:
    for lim, pts in ((50000, 30), (25000, 25), (10000, 20), (3000, 12), (1000, 6)):
        if cash >= lim:
            return pts
    return 0


def _age_pts(age: float) -> int:
    for lim, pts in ((2, 20), (7, 15), (14, 12), (30, 8), (90, 3)):
        if age < lim:
            return pts
    return 0


def theme_burst(g: BuyGroup, groups: list[BuyGroup], theme_of: dict[str, int]) -> float:
    """Mises cumulées du même compte sur le même sujet dans la fenêtre (ex. « frappes avant le 20 / le 28 / le 15 mars »)."""
    mine = theme_of.get(g.condition_id)
    total = 0.0
    for x in groups:
        if x.wallet != g.wallet or not is_longshot(x.avg_price):
            continue
        same = (theme_of.get(x.condition_id) == mine) if mine is not None else (
            x.condition_id == g.condition_id or (g.event_slug and x.event_slug == g.event_slug))
        if same:
            total += x.cash
    return max(total, g.cash)


def score_bet(g: BuyGroup, wallet: dict, market: dict | None, theme_cash: float | None = None) -> tuple[int, list[str]]:
    pts, why = 0, []

    lp = _longshot_pts(g.avg_price)
    if lp:
        pts += lp
        why.append(f"issue jugée improbable ({g.avg_price:.1%})")

    cash = max(g.cash, theme_cash or 0)
    sp = _size_pts(cash)
    if sp:
        pts += sp
        why.append(f"mise de {fmt_usd(g.cash)}" if cash <= g.cash + 1
                   else f"{fmt_usd(cash)} misés sur ce sujet en 1 h")

    age = wallet.get("age_days")
    if age is not None:
        ap = _age_pts(age)
        if ap:
            pts += ap
            why.append(f"compte créé il y a {age * 24:.0f} h" if age < 2 else f"compte de {age:.0f} j")

    # Concentration pondérée par l'argent : quelques petits paris « leurres » ailleurs ne diluent plus le signal
    vol = wallet.get("volume_by_market") or {}
    total = sum(vol.values())
    if total > 0 and not wallet.get("busy"):
        theme_of = wallet.get("theme_of") or {}
        mine = theme_of.get(g.condition_id)
        same = (sum(v for c, v in vol.items() if theme_of.get(c) == mine) if mine is not None
                else vol.get(g.condition_id, 0))
        share = min(same / total, 1.0)
        cp = 15 if share >= 0.9 else 10 if share >= 0.75 else 5 if share >= 0.5 else 0
        if cp:
            pts += cp
            n = wallet.get("markets_traded") or len(vol)
            why.append("aucun autre marché tradé" if n <= 1 else f"{share:.0%} de son argent sur ce sujet ({n} marchés tradés)")

    if market:
        v24 = float(market.get("volume24hr") or 0)
        if v24 > 0:
            s = g.cash / v24
            if s >= 0.5:
                pts += 10; why.append(f"{s:.0%} du volume 24 h du marché")
            elif s >= 0.2:
                pts += 6; why.append(f"{s:.0%} du volume 24 h du marché")

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
