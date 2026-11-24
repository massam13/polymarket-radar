"""Seuils du radar. Tout est surchargeable par variable d'environnement (ex. RADAR_MIN_SCORE=70)."""
from __future__ import annotations

import os


def _num(name: str, default: float) -> float:
    raw = os.environ.get(f"RADAR_{name}")
    return float(raw) if raw not in (None, "") else default


# --- Collecte ---
LOOKBACK_MIN = int(_num("LOOKBACK_MIN", 60))       # fenêtre relue à chaque passage (chevauchement voulu, dédoublonné)
MIN_TRADE_CASH = _num("MIN_TRADE_CASH", 300)       # on ne récupère que les ordres >= 300 $
MAX_WALLET_LOOKUPS = int(_num("MAX_WALLET_LOOKUPS", 40))
MAX_MARKET_LOOKUPS = int(_num("MAX_MARKET_LOOKUPS", 120))

# --- Paris suspects ---
MIN_GROUP_CASH = _num("MIN_GROUP_CASH", 1000)      # mise cumulée minimale d'un compte sur une issue
MAX_PRICE = _num("MAX_PRICE", 0.35)                # au-delà, l'issue n'est plus « improbable »
MIN_SCORE = _num("MIN_SCORE", 60)                  # seuil d'alerte (0-100)
REALERT_FACTOR = _num("REALERT_FACTOR", 2.0)       # ré-alerte si la mise a été multipliée par 2 depuis

# --- Mouvements de marché ---
MOVE_MIN_1H = _num("MOVE_MIN_1H", 0.12)            # +12 points de proba en 1 h
MOVE_MIN_VOL24 = _num("MOVE_MIN_VOL24", 25000)     # sur un marché qui a au moins 25 k$ de volume 24 h
MOVE_DEDUP_HOURS = _num("MOVE_DEDUP_HOURS", 6)

# --- Sujets d'actualité : ce qu'on écarte ---
EXCLUDED_TAGS = {
    "sports", "nba", "nfl", "nhl", "mlb", "soccer", "football", "tennis", "golf", "ufc", "mma",
    "f1", "formula-1", "cricket", "boxing", "esports", "games", "ncaa", "basketball", "baseball",
    "hockey", "crypto-prices", "up-or-down", "weather", "temperature", "mentions", "tweet-markets",
}
EXCLUDED_TITLE_PATTERNS = [
    r"\bup or down\b",
    r"\bvs\.?\s",
    r"\bo/u\b",
    r"\bspread\b",
    r"highest temperature",
    r"\b(bitcoin|ethereum|btc|eth|solana|sol|xrp|doge|dogecoin)\b.*\b(above|below|reach|hit|dip|price)\b",
    r"\bprice of (bitcoin|ethereum|solana|xrp)\b",
    r"# ?of (tweets|posts)",
    r"\bwill .* say\b",
]
