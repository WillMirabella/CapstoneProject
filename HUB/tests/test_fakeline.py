"""The replay's defect model: real defects are usually caught by every camera."""

import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from fakeline import make_events, parse_rates  # noqa: E402

CAMS = ("c1", "keyence", "c2")


def flag_counts(events):
    return Counter(sum(bool(e[c]) for c in CAMS) for e in events if e["e"] == "exit" and e["rejected"])


class DefectModelTest(unittest.TestCase):
    def test_real_defects_are_flagged_by_all_cameras(self):
        events = make_events(1000, parse_rates("0", 0), defect_rate=0.05,
                             detect=parse_rates("1", 0), seed=3, cans_per_second=10)
        counts = flag_counts(events)
        self.assertGreater(counts[3], 20)
        self.assertEqual(set(counts), {3})

    def test_false_flags_come_from_one_camera(self):
        events = make_events(1000, parse_rates("telodyne=0.05", 0), seed=3, cans_per_second=10)
        exits = [e for e in events if e["e"] == "exit" and e["rejected"]]
        self.assertTrue(exits)
        self.assertTrue(all(e["c1"] and not e["keyence"] and not e["c2"] for e in exits))

    def test_parse_rates_accepts_a_bare_number(self):
        self.assertEqual(parse_rates("0.9", 0), {"telodyne": 0.9, "keyence": 0.9, "elp": 0.9})
        self.assertEqual(parse_rates("keyence=0.6", 0.9)["keyence"], 0.6)
        self.assertEqual(parse_rates("", 0.002)["elp"], 0.002)


if __name__ == "__main__":
    unittest.main()
