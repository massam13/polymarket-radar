# Polymarket Radar

Surveille Polymarket toutes les 15 minutes (GitHub Actions) et alerte sur les mouvements « étranges » liés à l'actualité, du type
[achat suspect avant le retour de Jensen Huang chez NVIDIA](https://deucalionn.vercel.app/polymarket-delit-initie-jensen-huang-nvidia/).

Lecture seule des API publiques Polymarket (`data-api`, `gamma-api`), aucune clé, aucun pari.

## Ce qui déclenche une alerte

**🚨 Pari suspect** : un compte achète une issue jugée improbable (≤ 35 %) pour ≥ 1 000 $ sur un sujet d'actualité
(sport, cours crypto, météo… exclus). Score sur 100, alerte à partir de 60 :

| Signal | Points |
|---|---|
| Issue improbable : de 30 pts (≤ 5 %) à 0 pt (40 %), en continu | 0 – 30 |
| Mise : ≥ 50 k$ / 25 k$ / 10 k$ / 3 k$ / 1 k$ — cumulée sur le sujet dans l'heure | 30 / 25 / 20 / 12 / 6 |
| Compte neuf : < 2 j / 7 j / 14 j / 30 j / 90 j | 20 / 15 / 12 / 8 / 3 |
| Part de son argent sur ce sujet : ≥ 90 % / 75 % / 50 % | 15 / 10 / 5 |
| Mise ≥ 50 % / 20 % du volume 24 h du marché | 10 / 6 |
| ≥ 3 ordres successifs (accumulation) | 5 |

Un « sujet » regroupe les marchés liés (ex. « frappes sur l'Iran avant le 20 / le 28 février / le 15 mars »).
La part est mesurée en argent et non en nombre de marchés : quelques petits paris « leurres » ailleurs ne diluent pas le signal.
Une seule alerte par compte et par sujet à chaque scan.

Rejoué sur 20 affaires connues (Iran, Biden, Maduro…), ce score repère 9 des 16 comptes rejouables, souvent plusieurs jours avant les faits.

**📈 Mouvement brusque** : la proba d'un marché d'actualité bouge de ≥ 20 points en 1 h avec ≥ 100 k$ de volume 24 h
(hors marchés qui se terminent dans les 24 h). Désactivable avec `RADAR_MOVES_ENABLED=0`.

Tous les seuils sont dans `radar/config.py` et surchargeables par variable d'environnement (`RADAR_MIN_SCORE=70`…).

## Alertes

Configurer un ou plusieurs secrets du dépôt (Settings → Secrets and variables → Actions) :

- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- `SLACK_WEBHOOK_URL`
- `DISCORD_WEBHOOK_URL`

Sans secret, les alertes apparaissent seulement dans le résumé de chaque exécution (onglet Actions).
L'historique est enregistré dans `state/alerts.jsonl`.

## Lancer à la main

```bash
python -m radar --lookback 240 --dry-run
```

(Depuis la France, les API Polymarket sont bloquées : ça ne marche que depuis GitHub Actions ou un serveur à l'étranger.)

## Tests

```bash
python -m unittest discover -s tests -t .
```
