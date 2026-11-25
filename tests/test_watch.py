import unittest

from radar import watch

W = "0xbad77c4805acd8e7abdac9ea8365c417ddf82879"


def act(ts, cash, price, outcome="Yes", side="BUY", typ="TRADE", title="Jensen Huang announced resignation or removal again by December 31, 2026?"):
    return {"type": typ, "timestamp": ts, "usdc_size": cash, "size": cash / price, "price": price, "side": side,
            "outcome": outcome, "condition_id": "0x602b", "title": title, "event_slug": "jensen-huang-out"}


class TestComptesSurveilles(unittest.TestCase):
    def test_premier_passage_silencieux_puis_nouveaux_achats(self):
        seen, acts = {}, [act(1000, 1892, 0.0547)]
        wl = {W: {"name": "blubb22", "note": "NVIDIA"}}
        self.assertEqual(watch.check(seen, 2000, lambda w: acts, wl), [])  # l'historique n'est pas renvoyé
        acts = [act(3000, 2500, 0.06), act(3010, 600, 0.061), act(3020, 10, 0.06, typ="REWARD"), act(900, 5000, 0.05)] + acts
        [(key, text)] = watch.check(seen, 3600, lambda w: acts, wl)
        self.assertIn("blubb22", text)
        self.assertIn("Achat « Yes » 3 100 $", text.replace(" ", " "))
        self.assertEqual(watch.check(seen, 4500, lambda w: acts, wl), [])  # déjà envoyé

    def test_ventes_signalees_et_poussiere_ignoree(self):
        moves = watch.summarize([act(10, 800, 0.2, side="SELL"), act(11, 20, 0.05)], 0)
        self.assertEqual([m["side"] for m in moves], ["SELL"])


if __name__ == "__main__":
    unittest.main()
