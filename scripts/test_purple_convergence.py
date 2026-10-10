import unittest

from color_notifications import states


class PurpleConvergenceTest(unittest.TestCase):
    def test_convergence_then_breakout(self):
        bars = [
            dict(m3=98.5, m10=100, m20=101, m40=101, m60=99.5),
            dict(m3=98.5, m10=100, m20=101, m40=102, m60=99.7),
            dict(m3=100, m10=100, m20=100, m40=101, m60=100),
            dict(m3=101.2, m10=100, m20=100, m40=100.9, m60=100.1),
            dict(m3=101.3, m10=100, m20=100, m40=100.8, m60=100.2),
        ]
        result = states(bars)
        self.assertTrue(result[2]["purple"])
        self.assertTrue(result[3]["convergenceBreakout"])
        self.assertFalse(result[4]["convergenceBreakout"])
        bars[2]["m20"] = 98.3
        self.assertFalse(states(bars)[2]["purple"])

    def test_existing_near60_remains(self):
        bars = [dict(m3=99, m10=100, m20=101, m40=102, m60=99.5),
                dict(m3=99.5, m10=100, m20=101, m40=103, m60=100)]
        self.assertTrue(states(bars)[1]["purple"])

    def test_convergence_above_ma20_and_inclusive_threshold(self):
        bars = [dict(m3=98, m10=100, m20=100, m40=101, m60=98),
                dict(m3=98, m10=100, m20=100, m40=102, m60=98.2),
                dict(m3=100.2, m10=100, m20=100.6, m40=102, m60=100),
                dict(m3=101.8, m10=100.2, m20=101.6, m40=101.9, m60=100.1)]
        self.assertTrue(states(bars)[3]["purple"])

    def test_ma60_must_be_lowest_and_ma40_highest(self):
        bars = [dict(m3=98, m10=100, m20=100, m40=101, m60=98),
                dict(m3=98, m10=100, m20=100, m40=102, m60=98.2),
                dict(m3=100.5, m10=100.6, m20=101, m40=101.9, m60=100.1)]
        self.assertTrue(states(bars)[2]["purple"])
        bars[2]["m10"] = 99
        self.assertFalse(states(bars)[2]["purple"])
        bars[2]["m10"] = 102.5
        self.assertFalse(states(bars)[2]["purple"])

    def test_flat_ma40_is_not_downturn(self):
        bars = [dict(m3=98, m10=100, m20=100, m40=101, m60=98),
                dict(m3=98, m10=100, m20=100, m40=102, m60=98.2),
                dict(m3=100.5, m10=100.6, m20=101, m40=102, m60=100.1)]
        self.assertFalse(states(bars)[2]["purple"])


if __name__ == "__main__":
    unittest.main()
