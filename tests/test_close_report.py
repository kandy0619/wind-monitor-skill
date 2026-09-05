import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


close_report = load_module("build_close_report", ROOT / "scripts/build_close_report.py")
render = load_module("render_close_report", ROOT / "scripts/render_feishu_card.py")


class CloseReportTest(unittest.TestCase):
    def setUp(self):
        self.dates = ["2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]
        self.top10 = {
            "trade_date": "2026-08-13",
            "wind_data_time": "2026-08-13 15:00:05",
            "top10_main_net_inflow_yi": 1.0,
            "top10_average_change_pct": 2.0,
            "top10": [{"code": "000001.SZ", "name": "测试股", "main_net_inflow_yi": 1.0, "change_pct": 2.0, "trend": "持续加仓"}],
        }
        row = {"industry": "一级--测试行业", "net_yi": 1.0, "days": 5, "amount_yi": 2.0}
        self.industry = {
            "trade_dates": self.dates,
            "net_add_top5": [row], "net_reduce_top5": [dict(row, net_yi=-1.0)],
            "add_days_top5": [row], "reduce_days_top5": [row],
            "add_amount_top5": [row], "reduce_amount_top5": [row],
        }
        stock = {
            "code": "000001.SZ", "name": "测试股", "net_yuan": 1.0,
            "add_days": 4, "reduce_days": 1, "add_yuan": 2.0, "reduce_yuan": 1.0,
        }
        self.stock = {
            "trade_dates": self.dates,
            "net_add_top5": [stock], "net_reduce_top5": [dict(stock, net_yuan=-1.0)],
            "add_days_top5": [stock], "reduce_days_top5": [stock],
            "add_amount_top5": [stock], "reduce_amount_top5": [stock],
        }

    def test_one_close_transaction_produces_two_linked_cards(self):
        package = close_report.build_close_report(self.top10, self.industry, self.stock)
        self.assertEqual(package["planned_time"], "15:10")
        self.assertEqual(package["report_type"], "close_summary")
        self.assertEqual(
            [item["card_mode"] for item in package["card_inputs"]],
            ["close-overview", "close-stock-5d"],
        )
        self.assertEqual(package["delivery"]["required_parts"], 2)
        cards = [render.build_card(item, None) for item in package["card_inputs"]]
        self.assertIn("（1/2）", cards[0]["header"]["title"]["content"])
        self.assertIn("（2/2）", cards[1]["header"]["title"]["content"])
        for card in cards:
            serialized = str(card)
            self.assertIn(package["report_id"], serialized)
            tables = [element for element in card["elements"] if element.get("tag") == "table"]
            self.assertLessEqual(len(tables), 5)

    def test_report_id_is_deterministic_for_delivery_retry(self):
        first = close_report.build_close_report(self.top10, self.industry, self.stock)
        second = close_report.build_close_report(self.top10, self.industry, self.stock)
        self.assertEqual(first["report_id"], second["report_id"])

    def test_five_day_windows_must_match(self):
        bad_stock = dict(self.stock, trade_dates=list(reversed(self.dates)))
        with self.assertRaises(ValueError):
            close_report.build_close_report(self.top10, self.industry, bad_stock)


if __name__ == "__main__":
    unittest.main()
