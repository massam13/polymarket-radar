"""Seuils du radar. Tout est surchargeable par variable d'environnement (ex. RADAR_MIN_SCORE=70)."""
from __future__ import annotations

import os


def _num(name: str, default: float) -> float:
    raw = os.environ.get(f"RADAR_{name}")
    return float(raw) if raw not in (None, "") else default


# --- Collecte ---
LOOKBACK_MIN = int(_num("LOOKBACK_MIN", 60))       # fenêtre relue à chaque passage (chevauchement voulu, dédoublonné)
MIN_TRADE_CASH = _num("MIN_TRADE_CASH", 0)         # pas de seuil par ordre : on regroupe d'abord (achats fractionnés)
MAX_WALLET_LOOKUPS = int(_num("MAX_WALLET_LOOKUPS", 40))
MAX_MARKET_LOOKUPS = int(_num("MAX_MARKET_LOOKUPS", 120))
MAX_TAG_LOOKUPS = int(_num("MAX_TAG_LOOKUPS", 400))  # thèmes d'événements récupérés par scan

# --- Paris suspects ---
MIN_GROUP_CASH = _num("MIN_GROUP_CASH", 1000)      # mise cumulée minimale d'un compte sur une issue
MAX_PRICE = _num("MAX_PRICE", 0.35)                # au-delà, l'issue n'est plus « improbable »
MIN_PRICE = _num("MIN_PRICE", 0.01)                # en dessous, le marché est déjà joué : billet de loterie, pas un pari informé
MIN_SCORE = _num("MIN_SCORE", 60)                  # seuil d'alerte (0-100)
REALERT_FACTOR = _num("REALERT_FACTOR", 2.0)       # ré-alerte si la mise a été multipliée par 2 depuis
LOG_MIN_SCORE = _num("LOG_MIN_SCORE", 30)          # paris notés à partir de 30 gardés dans state/candidates.jsonl
CONTEXT_MIN_SCORE = _num("CONTEXT_MIN_SCORE", 45)  # au-dessus, on regarde la cote d'avant et les autres acheteurs

# --- Rapport horaire (paris notés sous le seuil d'alerte, avec ce qui leur a manqué) ---
REPORT_EVERY_MIN = _num("REPORT_EVERY_MIN", 60)    # 0 pour couper le rapport
REPORT_EMPTY = _num("REPORT_EMPTY", 0) > 0         # 1 pour envoyer aussi « rien à signaler » en journée
REPORT_FIRST_HOUR = int(_num("REPORT_FIRST_HOUR", 8))   # heure de Paris : rapport de nuit à 8 h…
REPORT_LAST_HOUR = int(_num("REPORT_LAST_HOUR", 23))    # …dernier rapport horaire à 23 h, rien entre les deux
REPORT_MAX_REASONS = int(_num("REPORT_MAX_REASONS", 3))  # raisons d'écartement affichées par pari

# --- Cote avant le pari (un initié achète avant que le marché bouge) ---
RUNUP_HOURS = _num("RUNUP_HOURS", 24)
RUNUP_STRONG = (0.05, 1.5, -15)    # +5 pts et x1,5 en 24 h avant le 1er achat : -15
RUNUP_MILD = (0.03, 1.25, -7)      # +3 pts et x1,25 : -7
STABLE_PTS = _num("STABLE_PTS", 5)  # cote stable avant le pari : +5

# --- Autres comptes neufs sur la même issue ---
CROWD_HOURS = _num("CROWD_HOURS", 24)
CROWD_MIN_CASH = _num("CROWD_MIN_CASH", 500)
CROWD_NEW_DAYS = _num("CROWD_NEW_DAYS", 14)
CROWD_MAX_PROFILES = int(_num("CROWD_MAX_PROFILES", 6))  # comptes examinés par pari
# Testé sur 20 affaires : autant de comptes neufs sur les marchés populaires (Fed, élections) que chez les initiés.
# Affiché dans l'alerte, mais 0 point par défaut. RADAR_CROWD_PTS=10 pour le réactiver.
CROWD_PTS = _num("CROWD_PTS", 0)

# --- Mouvements de marché ---
MOVES_ENABLED = _num("MOVES_ENABLED", 1) > 0      # RADAR_MOVES_ENABLED=0 pour couper ces alertes
MOVE_MIN_1H = _num("MOVE_MIN_1H", 0.20)            # +20 points de proba en 1 h
MOVE_MIN_VOL24 = _num("MOVE_MIN_VOL24", 100000)    # sur un marché qui a au moins 100 k$ de volume 24 h
MOVE_MIN_HOURS_TO_END = _num("MOVE_MIN_HOURS_TO_END", 24)  # écarte les marchés « du jour » (événements en direct)
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
    r"\bwin on \d{4}-\d{2}-\d{2}\b",      # matchs du jour (« Will FC Porto win on 2026-11-24? »)
    r"\bAND Will\b",                        # combinés de paris sportifs
    r"\b(end in a draw|both teams to score|total (goals|points))\b",
    r"\bexact score\b",
    r"\b(halftime|first half|second half|1st half|2nd half|to qualify|clean sheet|anytime goalscorer)\b",
    r"\b\d+\s*-\s*\d+\b.*\?$",              # « … 0 - 0 Aston Villa FC? »
    r"\b(FC|SK|CF|AFC)\b.*\b(win|beat|score|draw)\b",
]
# Préfixes de slug des événements sportifs (ucl-gal-ast-2026-11-24, nba-lal-bos-…, epl-…)
EXCLUDED_SLUG_PATTERN = (
    r"^(ucl|uel|uecl|epl|lal|laliga|bun|bundesliga|sea|serie-?a|fl1|ligue-?1|mls|nba|wnba|nfl|nhl|mlb|ncaa[a-z]*|cbb|cfb|"
    r"atp|wta|ufc|f1|ipl|cs2|lol|dota2?|val|copa|wc|fifa|afc|caf|kbo|npb|csl|tur|ere|por|sco|bra|arg|mex)-"
    r"|^[a-z0-9]{2,6}-[a-z0-9]{2,4}-[a-z0-9]{2,4}-\d{4}-\d{2}-\d{2}"   # ligue-équipe-équipe-date
)

# --- Regroupement par sujet (plusieurs marchés sur le même thème = un seul sujet) ---
GENERIC_TAGS = {
    "politics", "geopolitics", "world", "us-politics", "trump", "trump-presidency", "elections", "global-elections",
    "economy", "business", "finance", "tech", "technology", "crypto", "middle-east", "featured", "breaking-news",
    "news", "all", "recurring", "culture", "pop-culture", "science", "ai", "latin-america", "europe", "asia", "usa",
    "us-news", "world-affairs", "foreign-policy", "military", "war", "international", "companies", "earnings",
    "courts", "legal", "white-house", "congress", "midterms", "2026", "2027", "2028",
}
THEME_STOPWORDS = {
    "will", "the", "before", "after", "end", "yes", "which", "who", "what", "when", "how", "does", "next", "new",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "trump", "donald", "biden", "u.s", "us", "usa", "united", "states", "america", "american", "president",
    "presidential", "election", "government", "house", "senate", "congress", "war", "republican", "democrat",
    "democratic", "party", "announced", "announce", "out", "invade", "forces", "between", "above", "below",
}
