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
    token_id: str = ""
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
                pseudonym=t.get("pseudonym") or t.get("name") or "", token_id=str(t.get("tokenId") or ""),
                first_ts=ts, last_ts=ts,
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


def price_context(history: list[tuple[int, float]], first_ts: int, hours: float | None = None) -> dict | None:
    """Cote de l'issue juste avant le 1er achat, et `hours` plus tôt."""
    hours = config.RUNUP_HOURS if hours is None else hours
    before = [p for t, p in history if t <= first_ts]
    if not before:
        return None
    ref = [p for t, p in history if t <= first_ts - hours * 3600]
    return {"before": before[-1], "ref": ref[-1] if ref else None, "hours": hours}


def runup_points(ctx: dict | None) -> tuple[int, str | None]:
    """Un initié achète avant que la cote bouge. Si elle a déjà monté, le pari suit probablement une info publique."""
    if not ctx or ctx.get("ref") is None:
        return 0, None
    ref, before = ctx["ref"], ctx["before"]
    rise, ratio = before - ref, before / max(ref, 0.005)
    span = f"de {ref:.1%} à {before:.1%} en {ctx['hours']:.0f} h avant le pari"
    for min_rise, min_ratio, pts in (config.RUNUP_STRONG, config.RUNUP_MILD):
        if rise >= min_rise and ratio >= min_ratio:
            return pts, f"cote déjà montée {span}"
    if rise <= max(0.01, 0.1 * ref):
        trend = "en baisse" if rise < -0.01 else "stable"
        return int(config.STABLE_PTS), f"cote {trend} avant le pari ({ref:.1%} → {before:.1%})"
    return 0, None


def crowd_wallets(trades: list[dict], g: BuyGroup) -> list[tuple[str, float]]:
    """Autres comptes qui achètent la même issue (mise ≥ CROWD_MIN_CASH, issue toujours improbable)."""
    others = [x for x in group_buys([t for t in trades if t.get("proxyWallet") != g.wallet])
              if x.condition_id == g.condition_id and x.outcome_index == g.outcome_index
              and x.cash >= config.CROWD_MIN_CASH and is_longshot(x.avg_price)]
    return sorted(((x.wallet, x.cash) for x in others), key=lambda kv: -kv[1])


def crowd_points(n_new: int | None) -> tuple[int, str | None]:
    if not n_new:
        return 0, None
    what = f"{n_new} autre(s) compte(s) neuf(s) sur la même issue en {config.CROWD_HOURS:.0f} h"
    pts = config.CROWD_PTS if n_new >= 2 else config.CROWD_PTS / 2
    return int(pts), what


def _days(d: float) -> str:
    return f"{d / 365:.1f} an".replace(".0 an", " an").replace(".", ",") + ("s" if d >= 730 else "") if d >= 365 \
        else f"{d / 30.4:.0f} mois" if d >= 60 else f"{d:.0f} j"


def score_parts(g: BuyGroup, wallet: dict, market: dict | None, theme_cash: float | None = None,
                context: dict | None = None) -> list[dict]:
    """Détail du score, critère par critère : points obtenus, maximum, raison affichée (why) et explication (note)."""
    parts: list[dict] = []

    def add(k: str, pts: int, mx: int, why: str | None, note: str) -> None:
        parts.append({"k": k, "pts": int(pts), "max": mx, "why": why if pts else None, "note": note})

    lp = _longshot_pts(g.avg_price)
    add("cote", lp, 30, f"issue jugée improbable ({g.avg_price:.1%})", f"issue à {g.avg_price:.1%}")

    cash = max(g.cash, theme_cash or 0)
    sp = _size_pts(cash)
    add("mise", sp, 30, f"mise de {fmt_usd(g.cash)}" if cash <= g.cash + 1 else f"{fmt_usd(cash)} misés sur ce sujet en 1 h",
        f"mise moyenne ({fmt_usd(cash)})" if sp <= 12 else f"{fmt_usd(cash)} misés")

    busy = bool(wallet.get("busy"))
    since = wallet.get("since_days")
    busy_note = "compte très actif (plus de 1 000 opérations" + (f", créé il y a {_days(since)})" if since else ")")
    age = wallet.get("age_days")
    if age is not None:
        ap = _age_pts(age)
        add("âge", ap, 20, f"compte créé il y a {age * 24:.0f} h" if age < 2 else f"compte de {age:.0f} j",
            f"compte ancien (créé il y a {_days(age)})" if age >= 90 else f"compte pas tout neuf ({age:.0f} j)" if age >= 30
            else f"compte récent ({age:.0f} j)")
    else:
        add("âge", 0, 20, None, busy_note if busy else "historique du compte illisible (API)")

    # Concentration pondérée par l'argent : quelques petits paris « leurres » ailleurs ne diluent plus le signal
    vol = wallet.get("volume_by_market") or {}
    total = sum(vol.values())
    if total > 0 and not busy:
        theme_of = wallet.get("theme_of") or {}
        mine = theme_of.get(g.condition_id)
        same = (sum(v for c, v in vol.items() if theme_of.get(c) == mine) if mine is not None
                else vol.get(g.condition_id, 0))
        share = min(same / total, 1.0)
        cp = 15 if share >= 0.9 else 10 if share >= 0.75 else 5 if share >= 0.5 else 0
        n = wallet.get("markets_traded") or len(vol)
        detail = "aucun autre marché tradé" if n <= 1 else f"{share:.0%} de son argent sur ce sujet ({n} marchés tradés)"
        add("concentration", cp, 15, detail,
            detail if share >= 0.5 else f"argent réparti sur {n} marchés ({share:.0%} sur ce sujet)")
    else:
        add("concentration", 0, 15, None, busy_note if busy else "historique du compte illisible (API)")

    v24 = float((market or {}).get("volume24hr") or 0)
    if v24 > 0:
        s = g.cash / v24
        vp = 10 if s >= 0.5 else 6 if s >= 0.2 else 0
        add("volume", vp, 10, f"{s:.0%} du volume 24 h du marché", f"pèse peu sur le marché ({s:.0%} du volume 24 h)")
    else:
        add("volume", 0, 10, None, "volume 24 h du marché inconnu")

    add("ordres", 5 if g.fills >= 3 else 0, 5, f"{g.fills} ordres successifs",
        "un seul ordre" if g.fills == 1 else f"{g.fills} ordres" if g.fills < 3 else f"{g.fills} ordres successifs")

    if context:
        rp, rw = runup_points(context.get("price"))
        if context.get("price") and context["price"].get("ref") is not None:
            add("cote d'avant", rp, int(config.STABLE_PTS), rw, rw or "cote en légère hausse avant le pari")
        else:
            add("cote d'avant", 0, int(config.STABLE_PTS), None, "historique de cote indisponible")
        cp_, cw = crowd_points(context.get("crowd_new"))
        if config.CROWD_PTS:
            add("autres comptes", cp_, int(config.CROWD_PTS), cw, cw or "aucun autre compte neuf")
    return parts


def score_bet(g: BuyGroup, wallet: dict, market: dict | None, theme_cash: float | None = None,
              context: dict | None = None) -> tuple[int, list[str]]:
    parts = score_parts(g, wallet, market, theme_cash, context)
    pts = sum(p["pts"] for p in parts)
    return max(0, min(pts, 100)), [p["why"] for p in parts if p["why"]]


def missed_reasons(parts: list[dict], context_checked: bool) -> list[str]:
    """Ce qui a manqué pour atteindre le seuil, du plus coûteux au moins coûteux.
    Un même motif (ex. compte très actif) qui coûte plusieurs critères n'est cité qu'une fois."""
    groups: dict[str, list[dict]] = {}
    for p in parts:
        if p["pts"] < p["max"]:
            groups.setdefault(p["note"], []).append(p)
    rows = []
    for note, ps in groups.items():
        pts, mx = sum(p["pts"] for p in ps), sum(p["max"] for p in ps)
        if len(ps) > 1:
            text = f"{note} → {' et '.join(p['k'] for p in ps)} non comptés ({pts}/{mx})"
        elif ps[0]["pts"] < 0:
            text = f"{note} ({pts})"
        elif ps[0]["k"] in ("âge", "concentration") and ps[0]["pts"] == 0 and ("très actif" in note or "illisible" in note):
            text = f"{note} → {ps[0]['k']} non compté ({pts}/{mx})"
        else:
            text = f"{note} ({pts}/{mx})"
        rows.append((pts - mx, text))
    out = [t for _, t in sorted(rows, key=lambda r: r[0])]
    if not context_checked:
        out.append(f"cote d'avant non vérifiée (seulement à partir de {config.CONTEXT_MIN_SCORE:.0f}/100)")
    return out


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
