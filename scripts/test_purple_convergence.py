import unittest

from color_notifications import states


class PurpleConvergenceTest(unittest.TestCase):
    def test_convergence_then_breakout(self):
        bars = [
            dict(m3=98.5, m10=100, m20=101, m40=101, m60=99.5),
            dict(m3=98.5, m10=100, m20=101, m40=102, m60=99.7),
            dict(m3=98.5, m10=100, m20=100, m40=101, m60=100),
            dict(m3=101.2, m10=100, m20=100, m40=100.9, m60=100.1),
            dict(m3=101.3, m10=100, m20=100, m40=100.8, m60=100.2),
        ]
        result = states(bars)
        self.assertTrue(result[2]["purple"])
        self.assertTrue(result[3]["convergenceBreakout"])
        self.assertFalse(result[4]["convergenceBreakout"])
        bars[2]["m40"] = 101.6
        self.assertFalse(states(bars)[2]["purple"])

    def test_existing_near60_remains(self):
        bars = [dict(m3=99, m10=100, m20=101, m40=102, m60=99.5),
                dict(m3=99.5, m10=100, m20=101, m40=103, m60=100)]
        self.assertTrue(states(bars)[1]["purple"])


if __name__ == "__main__":
    unittest.main()
