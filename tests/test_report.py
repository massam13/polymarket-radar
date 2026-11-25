import unittest

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
        self.assertEqual(missed[0], "compte très actif (plus de 1 000 opérations) : âge et concentration non comptés (0/35)")
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
        self.assertIn("1 pari(s) noté(s)", msgs[0])
        self.assertIn("52/100 (manque 8)", msgs[0])
        self.assertIn("très actif", msgs[0])
        self.assertEqual(report.build(logged, [], 100, 200), [])  # heure vide : rien d'envoyé

    def test_decoupage_telegram(self):
        base = {"title": "x" * 300, "outcome": "Yes", "price": 0.05, "cash": 2000, "wallet": "0x1", "first_ts": 1,
                "last_ts": 1, "why": ["a"], "score": 40, "ts": 5}
        msgs = report.build([dict(base, key=str(i)) for i in range(30)], [], 0, 10)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))


if __name__ == "__main__":
    unittest.main()
