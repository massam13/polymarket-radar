import unittest

from options_radar import scoring as sc
from options_radar.scoring import Contract, Flow

NOW = 1_800_000_000


def contract(**kw):
    base = dict(ticker="XYZ", side="call", strike=60.0, expiry="2026-12-18", dte=20, last=0.80, bid=0.75, ask=0.85,
                volume=8_000, oi=300, iv=0.6, spot=50.0)
    base.update(kw)
    return Contract(**base)


def flow(contracts, **kw):
    base = dict(ticker="XYZ", side="call", market_cap=4e9, spot=50.0, change_pct=0.3, earnings_ts=NOW + 80 * 86400,
                side_volume_today=12_000, side_volume_avg=900, contracts=contracts)
    base.update(kw)
    return Flow(**base)


class TestOptionsScoring(unittest.TestCase):
    def test_pari_type_fuite_de_rachat(self):
        # Calls 20 % hors de la monnaie, 3 semaines, 8 000 lots pour 300 ouverts, action plate, petite valeur
        cs = [contract(), contract(strike=55.0, last=1.6, volume=3_000, oi=150), contract(strike=65.0, last=0.3, volume=4_000, oi=50)]
        f = flow(cs)
        self.assertIsNone(sc.flow_reasons_to_skip(f, NOW))
        score, why = sc.score_flow(f)
        self.assertGreaterEqual(score, sc.MIN_SCORE, why)

    def test_activite_normale_ecartee(self):
        self.assertEqual(sc.contract_reasons_to_skip(contract(volume=800, oi=5_000)), "trop petit")
        self.assertEqual(sc.contract_reasons_to_skip(contract(volume=20_000, oi=50_000)), "volume normal vs OI")
        self.assertEqual(sc.contract_reasons_to_skip(contract(strike=51)), "dans la monnaie")
        self.assertEqual(sc.contract_reasons_to_skip(contract(dte=90)), "échéance")
        self.assertEqual(sc.contract_reasons_to_skip(contract(bid=0.3, ask=1.3)), "illiquide")

    def test_pari_sur_resultats_ecarte(self):
        f = flow([contract()], earnings_ts=NOW + 10 * 86400)
        self.assertEqual(sc.flow_reasons_to_skip(f, NOW), "résultats avant l'échéance")

    def test_info_deja_sortie_ecartee(self):
        self.assertIsNotNone(sc.flow_reasons_to_skip(flow([contract()], change_pct=9.0), NOW))
        self.assertIsNotNone(sc.flow_reasons_to_skip(flow([contract()], change_pct=3.0), NOW))   # call qui suit la hausse

    def test_grosse_valeur_sans_historique_reste_sous_le_seuil(self):
        # 300 k$ de calls sur une méga-capitalisation, OI inconnu, pas encore d'historique
        f = flow([contract(volume=3_000, last=1.0, oi=0)], market_cap=2e12, side_volume_avg=None, change_pct=1.8)
        score, why = sc.score_flow(f)
        self.assertLess(score, sc.MIN_SCORE, why)


if __name__ == "__main__":
    unittest.main()
