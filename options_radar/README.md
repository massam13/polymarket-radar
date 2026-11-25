# Options Radar (US)

Une fois par jour de bourse, ~45 min avant la clôture de New York, lit les chaînes d'options Yahoo Finance
(gratuit, différé ~15-20 min) des ~1 500 valeurs du S&P 500 / 400 / 600 et alerte sur Telegram quand
quelqu'un engage beaucoup d'argent sur des options **hors de la monnaie, à échéance courte, que personne ne détenait la veille**,
alors que l'action n'a pas encore bougé et qu'aucune publication de résultats n'explique le pari.
C'est le profil typique des fuites avant un rachat (OPA) ou une annonce.

Lecture seule, aucune clé, aucun ordre.

## Ce qui est retenu

Un contrat passe si **tout** est vrai :

- échéance entre 1 et 45 jours (5 échéances les plus proches) ;
- au moins 3 % hors de la monnaie ;
- ≥ 300 lots et ≥ 100 k$ de prime dans la journée ;
- volume ≥ 3 × positions ouvertes de la veille (quand Yahoo les donne) ;
- écart achat/vente ≤ 35 % (sinon trop illiquide pour conclure).

Puis, par action et par sens (calls / puts), l'ensemble est écarté si :

- l'action a déjà bougé de ± 4 % (l'info est sortie), ou de + 2,5 % pour des calls / − 2,5 % pour des puts (le flux suit le marché) ;
- des résultats tombent avant l'échéance (pari sur les résultats, pas une fuite).

## Score (sur 100, alerte à partir de 70, 3 alertes max par jour, une seule par action et par sens sur 5 jours)

| Signal | Points |
|---|---|
| Prime totale : ≥ 5 M$ / 2 M$ / 1 M$ / 500 k$ / 250 k$ / 100 k$ | 30 / 25 / 20 / 14 / 8 / 4 |
| Volume / positions ouvertes veille : ≥ 20 / 10 / 5 / 3 | 20 / 15 / 10 / 5 |
| Volume calls (ou puts) de l'action vs sa moyenne des jours précédents : ≥ ×10 / ×5 / ×3 / ×2 | 20 / 15 / 10 / 5 |
| Distance au cours du plus gros contrat : ≥ 15 % / 8 % / 4 % | 10 / 7 / 4 |
| Échéance : ≤ 14 j / ≤ 30 j / ≤ 45 j | 10 / 6 / 3 |
| Action stable (< 1,5 %) | 5 |
| Majorité des contrats traités côté vendeur (acheteur pressé) | 5 |
| ≥ 3 prix d'exercice visés | 5 |
| Capitalisation < 10 Md$ | 5 |

La moyenne « habituelle » se construit au fil des jours dans `state/options_history.json` (5 séances minimum) :
la première semaine, ce critère vaut 0 et les alertes sont donc plus rares.
Tout ce qui est noté ≥ 40 va dans `state/options_candidates.jsonl` pour vérifier après coup ce qui a précédé une annonce.

Seuils surchargeables par variable d'environnement `OPTIONS_<NOM>` (ex. `OPTIONS_MIN_SCORE=80`), voir `scoring.py`.

## Limites assumées

- Pas de données transaction par transaction (sweeps, blocs, sens réel) : ça demande un flux OPRA payant.
  On n'a qu'une photo du jour : volume, positions ouvertes de la veille, dernier prix, fourchette.
- Un gros volume peut être une couverture, une jambe de spread ou une fermeture : l'alerte est un signal à vérifier.
- Yahoo n'est pas une API officielle : si elle bloque, le job sort en erreur (annotation) sans rien envoyer.

## Lancer

```bash
pip install -r options_radar/requirements.txt
python -m options_radar --dry-run --force --tickers AAPL,DECK
```
