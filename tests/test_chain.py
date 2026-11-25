import unittest

from radar import chain
from radar.scoring import group_buys

EXCHANGE = "0xe111180000d2663c0091e4f400237545b87b996b"
YES, NO = "4872670756822880674944283038882529075210700927252109488954070437890542145542", "995154256881621"


def word(n):
    return f"{n:064x}"


def log(maker, taker, side, token, maker_amt, taker_amt):
    return {"address": EXCHANGE,
            "topics": [chain.ORDER_FILLED, "0x" + "ab" * 32, "0x" + "0" * 24 + maker[2:], "0x" + "0" * 24 + taker[2:]],
            "data": "0x" + "".join(word(x) for x in (side, int(token), maker_amt, taker_amt, 0, 0, 0))}


class TestMakers(unittest.TestCase):
    # Dernier achat de ljhggk (23/11 03:28) : dans le flux, seul le taker 0x2974… apparaît (achat « No » à 93,5 %)
    feed = {"proxyWallet": "0x2974bd0059e48f215c391882976e0f1b4c8c9c23", "side": "BUY", "size": 40642.58,
            "price": 0.935, "timestamp": 1795742900, "conditionId": "0xc1", "outcome": "No", "outcomeIndex": 1,
            "title": "Will Jensen Huang rejoin NVIDIA by December 31, 2026?", "slug": "huang", "eventSlug": "huang",
            "transactionHash": "0xd59a", "tokenId": NO}
    receipt = {"logs": [
        log("0xdf8616e18da2ad636713f4bf4367794e810606bb", "0x2974bd0059e48f215c391882976e0f1b4c8c9c23", 0, YES,
            2_589_147_408, 39_833_037_046),
        log("0xf1f2b3c6c5bea42a6664fe3a8395addc1ab28c8d", "0x2974bd0059e48f215c391882976e0f1b4c8c9c23", 1, NO,
            9_380_000, 8_760_920),  # un maker qui vend : ignoré
        log("0x2974bd0059e48f215c391882976e0f1b4c8c9c23", EXCHANGE, 0, NO, 37_999_999_998, 40_642_577_046),  # le taker
    ]}

    def test_le_taker_qui_achete_le_favori_cache_un_maker_sur_l_outsider(self):
        self.assertAlmostEqual(chain.maker_side_cash(self.feed), 40642.58 * 0.065, places=0)
        self.assertEqual(chain.maker_side_cash(dict(self.feed, price=0.5)), 0)

    def test_retrouve_ljhggk(self):
        [t] = chain.decode_maker_buys(self.receipt, self.feed)
        self.assertEqual(t["proxyWallet"], "0xdf8616e18da2ad636713f4bf4367794e810606bb")
        self.assertEqual((t["outcome"], t["outcomeIndex"]), ("Yes", 0))
        self.assertAlmostEqual(t["price"], 0.065, places=3)
        [g] = group_buys([t])
        self.assertAlmostEqual(g.cash, 2589.15, places=1)
        self.assertEqual(g.token_id, YES)


if __name__ == "__main__":
    unittest.main()


class TestPositionsCouvertes(unittest.TestCase):
    def test_echange_entre_complices_ecarte(self):
        # TraeDing (Soudan, 25/11) : achète « Non » à 0,90 et « Oui » à 0,10, autant de parts, puis fusionne
        from radar.scoring import hedged
        from tests.test_scoring import trade
        groups = group_buys([trade(proxyWallet="0xfb83", outcomeIndex=1, outcome="No", size=1800, price=0.9),
                             trade(proxyWallet="0xfb83", outcomeIndex=0, outcome="Yes", size=1800, price=0.1)])
        yes = [g for g in groups if g.outcome_index == 0][0]
        self.assertTrue(hedged(yes, groups))

    def test_pari_dans_un_seul_sens_garde(self):
        from radar.scoring import hedged
        from tests.test_scoring import trade
        groups = group_buys([trade(size=71_500, price=0.065), trade(proxyWallet="0xautre", outcomeIndex=1, size=40_000, price=0.935),
                             trade(outcomeIndex=1, outcome="No", size=1_000, price=0.935)])  # petite couverture : 1,4 % des parts
        main = [g for g in groups if g.wallet == "0xabc" and g.outcome_index == 0][0]
        self.assertFalse(hedged(main, groups))
