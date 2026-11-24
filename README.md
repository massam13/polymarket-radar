# Polymarket Radar

Surveille Polymarket toutes les 15 minutes (GitHub Actions) et alerte sur les mouvements « étranges » liés à l'actualité, du type
[achat suspect avant le retour de Jensen Huang chez NVIDIA](https://deucalionn.vercel.app/polymarket-delit-initie-jensen-huang-nvidia/).

Lecture seule des API publiques Polymarket (`data-api`, `gamma-api`), aucune clé, aucun pari.

## Ce qui déclenche une alerte

**🚨 Pari suspect** : un compte achète une issue jugée improbable (≤ 35 %) pour ≥ 1 000 $ sur un sujet d'actualité
(sport, cours crypto, météo… exclus). Score sur 100, alerte à partir de 60 :

| Signal | Points |
|---|---|
| Issue improbable : ≤ 5 % / 10 % / 20 % / 35 % | 30 / 25 / 15 / 5 |
| Mise : ≥ 25 k$ / 10 k$ / 3 k$ / 1 k$ | 25 / 20 / 12 / 6 |
| Compte neuf : < 2 j / 7 j / 30 j | 20 / 15 / 7 |
| Peu diversifié : 1 / ≤ 3 / ≤ 10 marchés tradés | 15 / 10 / 4 |
| ≥ 80 % de son volume sur ce marché | 5 |
| Mise ≥ 50 % / 20 % du volume 24 h du marché | 10 / 6 |
| ≥ 3 ordres successifs (accumulation) | 5 |

Le cas Huang (4 ordres à 0,065, 4 649 $, compte de 4 jours, mono-marché) obtient 82/100.

**📈 Mouvement brusque** : la proba d'un marché d'actualité bouge de ≥ 12 points en 1 h avec ≥ 25 k$ de volume 24 h.

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
