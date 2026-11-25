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
        self.assertFalse(is_news_topic("Will FC Porto win on 2026-11-24? AND Will FC Bayern win on 2026-11-24?"))
        self.assertTrue(is_news_topic("Will J.D. Vance win the 2028 Republican presidential nomination?"))
        self.assertFalse(is_news_topic("Exact Score: Galatasaray SK 0 - 0 Aston Villa FC?"))
        self.assertFalse(is_news_topic("Some match", slug="ucl-gal-ast-2026-11-24-exact-score"))
        self.assertFalse(is_news_topic("Will X win?", market={"sportsMarketType": "moneyline"}))
        self.assertTrue(is_news_topic("Will Jensen Huang rejoin NVIDIA?", slug="jensen-huang-rejoin-nvidia-by-december-31"))
        self.assertTrue(is_news_topic("Fed decision in December?", slug="fed-decision-in-december"))
        self.assertTrue(is_news_topic("Will Trump meet Xi in 2026?", slug="will-trump-meet-xi-in-2026"))
        self.assertFalse(is_news_topic("Will Bitcoin reach $150,000 in December?"))
        self.assertFalse(is_news_topic("Fed decision in December?", {"sports"}))

    def test_mouvement_de_marche(self):
        m = {"oneHourPriceChange": 0.25, "volume24hr": 150_000, "outcomes": '["Yes", "No"]'}
        self.assertEqual(market_move(m), (0.25, "Yes"))
        self.assertIsNone(market_move({"oneHourPriceChange": 0.12, "volume24hr": 150_000}))
        self.assertIsNone(market_move({"oneHourPriceChange": 0.29, "volume24hr": 26_948}))  # la dinde

    def test_marche_du_jour(self):
        from radar.scoring import is_same_day_market
        now = 1795550000  # 24/11/2026
        self.assertTrue(is_same_day_market({"question": "Will a turkey audibly gobble during the Thanksgiving Turkey Pardon on November 24?"}, now))
        self.assertTrue(is_same_day_market({"question": "X?", "endDate": "2026-11-25T05:00:00Z"}, now))
        self.assertFalse(is_same_day_market({"question": "Will Jensen Huang rejoin NVIDIA by December 31?", "endDate": "2026-12-31T23:59:00Z"}, now))

    def test_auteurs_du_mouvement(self):
        from radar.scoring import move_drivers
        trades = [{"proxyWallet": "0xa", "side": "BUY", "outcomeIndex": 0, "size": 10_000, "price": 0.5, "pseudonym": "whale"},
                  {"proxyWallet": "0xb", "side": "BUY", "outcomeIndex": 0, "size": 1_000, "price": 0.5},
                  {"proxyWallet": "0xc", "side": "SELL", "outcomeIndex": 1, "size": 1_000, "price": 0.5},
                  {"proxyWallet": "0xd", "side": "BUY", "outcomeIndex": 1, "size": 9_999, "price": 0.5}]
        d = move_drivers(trades, 0.25)
        self.assertEqual(d["wallets"], 3)
        self.assertEqual(d["top_name"], "whale")
        self.assertAlmostEqual(d["top_share"], 5000 / 6000)


if __name__ == "__main__":
    unittest.main()


class TestSujets(unittest.TestCase):
    def test_cas_maduro_un_seul_sujet(self):
        from radar.scoring import theme_clusters, theme_keys
        titles = {
            "m1": ("Maduro out by January 31, 2026?", {"politics", "venezuela", "maduro"}),
            "m2": ("Will the U.S. invade Venezuela by January 31, 2026?", {"politics", "venezuela"}),
            "m3": ("Trump invokes War Powers against Venezuela by January 31, 2026?", {"geopolitics"}),
            "m4": ("US forces in Venezuela by January 31, 2026?", set()),
            "x": ("Fed decision in January?", {"economy", "fed"}),
        }
        clusters = theme_clusters({m: theme_keys(t, tags) for m, (t, tags) in titles.items()})
        self.assertEqual(len({clusters[m] for m in ("m1", "m2", "m3", "m4")}), 1)
        self.assertNotEqual(clusters["x"], clusters["m1"])

    def test_trump_seul_ne_relie_pas(self):
        from radar.scoring import theme_clusters, theme_keys
        c = theme_clusters({"a": theme_keys("Will Trump pardon Ghislaine Maxwell?"),
                            "b": theme_keys("Will Trump visit China in 2026?")})
        self.assertNotEqual(c["a"], c["b"])

    def test_score_maduro_30_decembre(self):
        # 30/12 : ~1 323 $ à 0,09 sur « Maduro out », compte de 3-4 jours, 2 marchés Venezuela tradés
        trades = [trade(conditionId="m1", title="Maduro out by January 31, 2026?", size=13_769, price=0.09),
                  trade(conditionId="m1", title="Maduro out by January 31, 2026?", size=850, price=0.10)]
        [g] = group_buys(trades)
        wallet = {"age_days": 3.5, "markets_traded": 2, "busy": False,
                  "volume_by_market": {"m1": 1323, "m4": 96}, "theme_of": {"m1": 0, "m4": 0}}
        score, why = score_bet(g, wallet, None)
        self.assertGreaterEqual(score, 60, why)
        self.assertIn("100% de son argent sur ce sujet (2 marchés tradés)", why)

    def test_paris_leurres_ne_diluent_pas(self):
        # whopperlover : 10 k$ sur l'Iran + quelques petits paris sur 5 autres sujets
        [g] = group_buys([trade(conditionId="iran", title="US strikes Iran by February 28, 2026?", size=80_000, price=0.13)])
        vol = {"iran": 10_400, "groenland": 200, "taiwan": 150, "nobel": 100, "fed": 120, "oscars": 90}
        wallet = {"age_days": 0.5, "markets_traded": 6, "busy": False, "volume_by_market": vol,
                  "theme_of": {c: i for i, c in enumerate(vol)}}
        score, why = score_bet(g, wallet, None)
        self.assertGreaterEqual(score, 60, why)

    def test_mises_reparties_sur_plusieurs_echeances(self):
        from radar.scoring import fmt_usd, theme_burst
        gs = group_buys([trade(conditionId=c, title=f"US strikes Iran by {d}?", size=30_000, price=0.12)
                         for c, d in (("f20", "February 20"), ("f28", "February 28"), ("m15", "March 15"))])
        theme_of = {"f20": 0, "f28": 0, "m15": 0, "autre": 1}
        self.assertAlmostEqual(theme_burst(gs[0], gs, theme_of), 10_800)
        wallet = {"age_days": 1.5, "markets_traded": 3, "busy": False,
                  "volume_by_market": {"f20": 3600, "f28": 3600, "m15": 3600}, "theme_of": theme_of}
        seul, _ = score_bet(gs[0], wallet, None)
        cumul, why = score_bet(gs[0], wallet, None, theme_burst(gs[0], gs, theme_of))
        self.assertGreater(cumul, seul)
        self.assertIn(f"{fmt_usd(10_800)} misés sur ce sujet en 1 h", why)

    def test_courbe_continue_de_probabilite(self):
        from radar.scoring import _longshot_pts
        self.assertEqual([_longshot_pts(p) for p in (0.03, 0.05, 0.30, 0.40, 0.6)], [30, 30, 9, 0, 0])


class TestApiV2(unittest.TestCase):
    def test_normalisation_trade_v2(self):
        from radar.polymarket import _rows, normalize_trade
        res = {"data": [{"proxy_wallet": "0xw", "side": "BUY", "token_id": "1", "condition_id": "0xc",
                         "size": 100, "price": 0.07, "timestamp": 1791473091, "outcome": "Yes",
                         "outcome_index": 0, "transaction_hash": "0xt"}],
               "pagination": {"has_more": True, "next_cursor": "abc"}}
        rows, cursor = _rows(res)
        self.assertEqual(cursor, "abc")
        t = normalize_trade(rows[0])
        self.assertEqual((t["proxyWallet"], t["conditionId"], t["outcomeIndex"], t["transactionHash"]),
                         ("0xw", "0xc", 0, "0xt"))
        self.assertEqual(normalize_trade({"timestamp": 1791473091000})["timestamp"], 1791473091)
