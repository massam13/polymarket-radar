import unittest

from radar.scoring import group_buys, is_news_topic, market_move, score_bet


def trade(**kw):
    base = {"side": "BUY", "proxyWallet": "0xabc", "conditionId": "0xc1", "outcome": "Yes",
            "outcomeIndex": 0, "title": "Will Jensen Huang rejoin NVIDIA by December 31, 2026?",
            "slug": "huang", "eventSlug": "huang", "pseudonym": "ljhggk", "timestamp": 1_000}
    base.update(kw)
    return base


class TestScoring(unittest.TestCase):
    def test_cas_huang_declenche_une_alerte(self):
        # 4 achats à 0,065 pour ~4 650 $ depuis un compte de 4 jours, un seul marché
        trades = [trade(size=17_879.45, price=0.065, timestamp=1_000 + i * 1000, transactionHash=f"0x{i}")
                  for i in range(4)]
        [g] = group_buys(trades)
        self.assertEqual(g.fills, 4)
        self.assertAlmostEqual(g.cash, 4648.66, places=0)
        wallet = {"age_days": 4, "markets_traded": 1, "volume_by_market": {"0xc1": 4648}, "busy": False}
        score, why = score_bet(g, wallet, {"volume24hr": 9000})
        self.assertGreaterEqual(score, 80, why)

    def test_baleine_diversifiee_ne_declenche_pas(self):
        [g] = group_buys([trade(size=100_000, price=0.08, title="Will the Fed cut rates in December?")])
        wallet = {"age_days": None, "markets_traded": 420, "volume_by_market": {}, "busy": True}
        score, _ = score_bet(g, wallet, {"volume24hr": 3_000_000})
        self.assertLess(score, 60)

    def test_les_ventes_sont_ignorees(self):
        self.assertEqual(group_buys([trade(side="SELL", size=1000, price=0.05)]), [])

    def test_filtre_actualite(self):
        self.assertTrue(is_news_topic("Will Jensen Huang rejoin NVIDIA by December 31, 2026?"))
        self.assertFalse(is_news_topic("Bitcoin Up or Down - November 24, 3PM ET"))
        self.assertFalse(is_news_topic("Lakers vs. Celtics"))
        self.assertFalse(is_news_topic("Will Bitcoin reach $150,000 in December?"))
        self.assertFalse(is_news_topic("Fed decision in December?", {"sports"}))

    def test_mouvement_de_marche(self):
        m = {"oneHourPriceChange": 0.2, "volume24hr": 80_000, "outcomes": '["Yes", "No"]'}
        self.assertEqual(market_move(m), (0.2, "Yes"))
        self.assertIsNone(market_move({"oneHourPriceChange": 0.03, "volume24hr": 80_000}))


if __name__ == "__main__":
    unittest.main()
