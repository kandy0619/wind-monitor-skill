import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calculate = load_module("calculate_monitor_stock", ROOT / "scripts/calculate_monitor.py")
render = load_module("render_feishu_stock", ROOT / "scripts/render_feishu_card.py")


class StockFiveDayTest(unittest.TestCase):
    def row(self, code, values):
        return {
            "code": code,
            "name": f"股票{code}",
            "industry": "一级--末级",
            "daily": [
                {"trade_date": date, "main_yuan": value}
                for date, value in zip(self.dates, values)
            ],
        }

    def setUp(self):
        self.dates = [f"2026-08-{day:02d}" for day in (20, 21, 24, 25, 26)]

    def test_recalculates_and_rejects_incomplete_stock(self):
        complete = self.row("000001.SZ", [1, 2, -1, 3, 4])
        negative = self.row("000002.SZ", [-1, -2, 1, -3, -4])
        incomplete = self.row("000003.SZ", [1, 2, 3, 4, None])
        payload = {
            "trade_dates": self.dates,
            "net_candidates": [complete, negative, incomplete],
            "days_candidates": [complete, negative, incomplete],
            "amount_candidates": [complete, negative, incomplete],
        }
        result = calculate.calculate_stock_5d(payload)
        self.assertEqual(result["net_add_top5"][0]["net_yuan"], 9)
        self.assertEqual(result["net_reduce_top5"][0]["net_yuan"], -9)
        self.assertEqual(result["add_days_top5"][0]["add_days"], 4)
        self.assertEqual(result["reduce_days_top5"][0]["reduce_days"], 4)
        self.assertEqual(result["rejected_codes"], ["000003.SZ"])

    def test_second_close_card_has_three_tables(self):
        row = calculate._stock_candidate(self.row("000001.SZ", [1, 2, -1, 3, 4]), self.dates)
        payload = {
            "report_type": "close_summary",
            "planned_time": "15:10",
            "card_mode": "close-stock-5d",
            "stock_5d": {
                "trade_dates": self.dates,
                "net_add_top5": [row], "net_reduce_top5": [row],
                "add_days_top5": [row], "reduce_days_top5": [row],
                "add_amount_top5": [row], "reduce_amount_top5": [row],
            },
        }
        card = render.build_card(payload, None)
        tables = [element for element in card["elements"] if element.get("tag") == "table"]
        self.assertEqual(len(tables), 3)
        self.assertIn("近5日个股资金统计", card["header"]["title"]["content"])


if __name__ == "__main__":
    unittest.main()
