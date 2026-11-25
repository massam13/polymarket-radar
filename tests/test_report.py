import unittest
from datetime import datetime

from radar import report
from radar.scoring import group_buys, missed_reasons, score_bet, score_parts
from tests.test_scoring import trade


class TestRapport(unittest.TestCase):
    def setUp(self):
        [self.g] = group_buys([trade(size=130_000, price=0.046, timestamp=1_000 + i) for i in range(4)])
        self.busy = {"age_days": None, "markets_traded": 11, "volume_by_market": {"0xc1": 1}, "busy": True}
        self.ctx = {"price": {"ref": 0.039, "before": 0.043, "hours": 24}, "crowd_new": 1}

    def test_le_detail_donne_le_meme_score(self):
        parts = score_parts(self.g, self.busy, {"volume24hr": 400_000}, None, self.ctx)
        score, _ = score_bet(self.g, self.busy, {"volume24hr": 400_000}, None, self.ctx)
        self.assertEqual(sum(p["pts"] for p in parts), score)

    def test_compte_tres_actif_explique(self):
        parts = score_parts(self.g, self.busy, {"volume24hr": 400_000}, None, self.ctx)
        missed = missed_reasons(parts, True)
        self.assertEqual(missed[0], "compte très actif (plus de 1 000 opérations) → âge et concentration non comptés (0/35)")
        self.assertEqual(sum("très actif" in m for m in missed), 1)

    def test_cote_d_avant_non_verifiee(self):
        parts = score_parts(self.g, self.busy, None)
        self.assertIn("cote d'avant non vérifiée", missed_reasons(parts, False)[-1])

    def test_rapport_sous_le_seuil_seulement(self):
        parts = [{"k": "cote", "pts": 30, "max": 30, "note": "issue à 4.6%"}]
        base = {"title": "Fed", "outcome": "Yes", "price": 0.046, "cash": 6000, "wallet": "0x7ad3", "first_ts": 100,
                "last_ts": 200, "parts": parts, "busy": True}
        logged = [dict(base, ts=50, key="a", score=42), dict(base, ts=60, key="a", score=52),
                  dict(base, ts=60, key="b", score=75), dict(base, ts=10, key="c", score=40)]
        msgs = report.build(logged, [], 20, 100)
        self.assertEqual(len(msgs), 1)
        self.assertIn("1 pari(s) entre 30 et 59", msgs[0])
        self.assertIn("<b>52/100</b>", msgs[0])
        self.assertIn('<a href="https://polymarket.com/profile/0x7ad3">compte</a>', msgs[0])
        self.assertEqual(report.build(logged, [], 100, 200), [])  # heure vide : rien d'envoyé

    def test_decoupage_telegram(self):
        base = {"title": "x" * 300, "outcome": "Yes", "price": 0.05, "cash": 2000, "wallet": "0x1", "first_ts": 1,
                "last_ts": 1, "why": ["a"], "score": 40, "ts": 5}
        msgs = report.build([dict(base, key=str(i)) for i in range(30)], [], 0, 10)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))


class TestHoraires(unittest.TestCase):
    def ts(self, d, h, m=1):
        return int(datetime(2026, 11, d, h, m, tzinfo=report.PARIS).timestamp())

    def test_une_fois_par_heure_a_heure_fixe(self):
        self.assertTrue(report.due(self.ts(25, 14, 1), self.ts(25, 13, 1)))
        self.assertFalse(report.due(self.ts(25, 14, 16), self.ts(25, 14, 1)))  # déjà envoyé cette heure-ci
        self.assertTrue(report.due(self.ts(25, 14, 16), self.ts(25, 13, 1)))   # scan de 14 h raté : rattrapé à 14 h 15

    def test_silence_la_nuit_puis_rapport_de_nuit(self):
        self.assertTrue(report.due(self.ts(25, 23, 1), self.ts(25, 22, 1)))
        for h in range(0, 8):
            self.assertFalse(report.due(self.ts(26, h, 1), self.ts(25, 23, 1)))
        self.assertTrue(report.due(self.ts(26, 8, 1), self.ts(25, 23, 1)))
        self.assertEqual(report.window_start(self.ts(26, 8, 1), self.ts(25, 23, 1)), self.ts(25, 23, 1))

    def test_rapport_de_nuit_meme_vide(self):
        msgs = report.build([], [], 0, 10, night=True)
        self.assertIn("Rapport de nuit", msgs[0])
        self.assertIn("Rien à signaler", msgs[0])

    def test_compte_ancien_explique(self):
        from tests.test_scoring import trade
        [g] = group_buys([trade(size=40_000, price=0.03)])
        wallet = {"age_days": 400, "markets_traded": 85, "busy": False, "volume_by_market": {"0xc1": 1200, "0xc2": 90_000}}
        missed = missed_reasons(score_parts(g, wallet, {"volume24hr": 900_000}), False)
        self.assertIn("compte ancien (créé il y a 1,1 an) (0/20)", missed)
        self.assertIn("argent réparti sur 85 marchés (1% sur ce sujet) (0/15)", missed)


if __name__ == "__main__":
    unittest.main()
