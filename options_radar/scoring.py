"""Détection et score des prises de position inhabituelles sur options US. Fonctions pures, testables sans réseau."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _num(name: str, default: float) -> float:
    raw = os.environ.get(f"OPTIONS_{name}")
    return float(raw) if raw not in (None, "") else default


# --- Filtres d'un contrat (tous doivent passer) ---
MAX_DTE = int(_num("MAX_DTE", 45))           # au-delà, ce n'est plus un pari sur une nouvelle imminente
MIN_DTE = int(_num("MIN_DTE", 1))            # 0DTE = spéculation intraday, bruit
MIN_OTM = _num("MIN_OTM", 0.03)              # hors de la monnaie d'au moins 3 % : on cherche des paris sur un mouvement
MIN_VOLUME = _num("MIN_VOLUME", 300)
MIN_PREMIUM = _num("MIN_PREMIUM", 100_000)   # $ engagés sur le contrat dans la journée
MIN_VOL_OI = _num("MIN_VOL_OI", 3)           # volume du jour ≥ 3 × positions ouvertes de la veille
MAX_SPREAD = _num("MAX_SPREAD", 0.35)        # écart achat/vente > 35 % du milieu : contrat trop illiquide pour conclure

# --- Filtres du sous-jacent ---
MAX_UNDERLYING_MOVE = _num("MAX_UNDERLYING_MOVE", 4.0)   # l'action a déjà bougé de ±4 % : l'info est sortie
EARNINGS_BUFFER_DAYS = _num("EARNINGS_BUFFER_DAYS", 2)   # résultats avant l'échéance (+2 j) : pari sur les résultats, pas une fuite

# --- Alertes ---
MIN_SCORE = _num("MIN_SCORE", 70)
MAX_ALERTS_PER_RUN = int(_num("MAX_ALERTS", 3))
LOG_MIN_SCORE = _num("LOG_MIN_SCORE", 40)
REALERT_DAYS = _num("REALERT_DAYS", 5)


@dataclass
class Contract:
    ticker: str
    side: str            # "call" | "put"
    strike: float
    expiry: str          # AAAA-MM-JJ
    dte: int
    last: float
    bid: float
    ask: float
    volume: float
    oi: float            # 0 = inconnu (Yahoo ne le renseigne pas toujours)
    iv: float
    spot: float
    multiplier: int = 100

    @property
    def premium(self) -> float:
        return self.volume * self.last * self.multiplier

    @property
    def otm(self) -> float:
        """Distance hors de la monnaie (0,10 = 10 %). Négatif si dans la monnaie."""
        if self.side == "call":
            return self.strike / self.spot - 1
        return 1 - self.strike / self.spot

    @property
    def vol_oi(self) -> float | None:
        return self.volume / self.oi if self.oi > 0 else None

    @property
    def spread(self) -> float | None:
        if self.bid <= 0 or self.ask <= 0:
            return None
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid if mid else None

    @property
    def at_ask(self) -> bool | None:
        """Dernier prix dans la moitié haute de la fourchette : plutôt un acheteur pressé."""
        if self.bid <= 0 or self.ask <= 0:
            return None
        return self.last >= (self.bid + self.ask) / 2


def contract_reasons_to_skip(c: Contract) -> str | None:
    if not (MIN_DTE <= c.dte <= MAX_DTE):
        return "échéance"
    if c.otm < MIN_OTM:
        return "dans la monnaie"
    if c.volume < MIN_VOLUME or c.premium < MIN_PREMIUM:
        return "trop petit"
    if c.oi > 0 and c.vol_oi < MIN_VOL_OI:
        return "volume normal vs OI"
    if c.spread is not None and c.spread > MAX_SPREAD:
        return "illiquide"
    return None


@dataclass
class Flow:
    """Contrats inhabituels d'une même action et d'un même sens (calls ou puts)."""
    ticker: str
    side: str
    name: str = ""
    sector: str = ""
    market_cap: float = 0
    spot: float = 0
    change_pct: float = 0
    earnings_ts: int | None = None
    side_volume_today: float = 0           # volume total calls (ou puts) de l'action sur les échéances lues
    side_volume_avg: float | None = None   # moyenne des jours précédents (historique du radar)
    contracts: list[Contract] = field(default_factory=list)

    @property
    def premium(self) -> float:
        return sum(c.premium for c in self.contracts)

    @property
    def top(self) -> Contract:
        return max(self.contracts, key=lambda c: c.premium)


def earnings_before_expiry(flow: Flow, now_ts: int) -> bool:
    if not flow.earnings_ts or flow.earnings_ts < now_ts - 86400:
        return False
    last_dte = max(c.dte for c in flow.contracts)
    return flow.earnings_ts <= now_ts + (last_dte + EARNINGS_BUFFER_DAYS) * 86400


def flow_reasons_to_skip(flow: Flow, now_ts: int) -> str | None:
    if abs(flow.change_pct) >= MAX_UNDERLYING_MOVE:
        return f"action déjà à {flow.change_pct:+.1f} %"
    if earnings_before_expiry(flow, now_ts):
        return "résultats avant l'échéance"
    # Un call qui monte avec l'action (ou un put qui baisse) suit le marché, ne l'anticipe pas
    if flow.side == "call" and flow.change_pct >= 2.5 or flow.side == "put" and flow.change_pct <= -2.5:
        return "suit le mouvement du jour"
    return None


def _tier(value: float, tiers: list[tuple[float, int]]) -> int:
    for threshold, pts in tiers:
        if value >= threshold:
            return pts
    return 0


def score_flow(flow: Flow) -> tuple[int, list[str]]:
    pts, why = 0, []
    top = flow.top

    p = _tier(flow.premium, [(5e6, 30), (2e6, 25), (1e6, 20), (5e5, 14), (2.5e5, 8), (1e5, 4)])
    pts += p
    why.append(f"{fmt_usd(flow.premium)} engagés")

    ratios = [c.vol_oi for c in flow.contracts if c.vol_oi is not None]
    if ratios:
        best = max(ratios)
        r = _tier(best, [(20, 20), (10, 15), (5, 10), (3, 5)])
        pts += r
        if r:
            why.append(f"volume {best:.0f}× les positions ouvertes")
    elif any(c.oi == 0 for c in flow.contracts):
        why.append("positions ouvertes de la veille inconnues")

    if flow.side_volume_avg:
        mult = flow.side_volume_today / flow.side_volume_avg
        m = _tier(mult, [(10, 20), (5, 15), (3, 10), (2, 5)])
        pts += m
        if m:
            why.append(f"{'calls' if flow.side == 'call' else 'puts'} ×{mult:.0f} vs habitude")

    o = _tier(top.otm, [(0.15, 10), (0.08, 7), (0.04, 4)])
    pts += o
    d = 10 if top.dte <= 14 else 6 if top.dte <= 30 else 3
    pts += d
    if o >= 7 and top.dte <= 30:
        why.append(f"pari à {top.otm:.0%} du cours, échéance {top.dte} j")

    if abs(flow.change_pct) < 1.5:
        pts += 5
        why.append("action encore stable")
    aggressive = [c.at_ask for c in flow.contracts if c.at_ask is not None]
    if aggressive and sum(aggressive) / len(aggressive) >= 0.6:
        pts += 5
        why.append("achats côté vendeur (pressés)")
    if len({c.strike for c in flow.contracts}) >= 3:
        pts += 5
        why.append(f"{len(flow.contracts)} prix d'exercice visés")
    if 0 < flow.market_cap < 10e9:
        pts += 5   # les fuites (rachats surtout) touchent surtout des petites et moyennes valeurs

    return min(pts, 100), why


def fmt_usd(x: float) -> str:
    if x >= 1e6:
        return f"{x / 1e6:.1f} M$".replace(".", ",")
    if x >= 1e3:
        return f"{x / 1e3:.0f} k$"
    return f"{x:.0f} $"
