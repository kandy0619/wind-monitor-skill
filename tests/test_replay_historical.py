import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from replay_historical import reconstruct_pre_slot_state


class ReplayHistoricalTests(unittest.TestCase):
    def test_reconstructs_previous_values_without_changing_baseline(self):
        final = {
            "stocks": {"A": {"open_baseline_yuan": 10, "previous_yuan": 30}},
            "indexes": {"I": {"open_baseline_yuan": 100, "previous_yuan": 300}},
        }
        previous = {
            "wind_data_time": "14:50",
            "stocks": [["股票", "A", 1.0, 20]],
            "indexes": [["板块", "指数", "I", 200]],
        }
        result = reconstruct_pre_slot_state(final, previous)
        self.assertEqual(result["stocks"]["A"]["previous_yuan"], 20)
        self.assertEqual(result["indexes"]["I"]["previous_yuan"], 200)
        self.assertEqual(result["stocks"]["A"]["open_baseline_yuan"], 10)
        self.assertEqual(final["stocks"]["A"]["previous_yuan"], 30)


if __name__ == "__main__":
    unittest.main()
