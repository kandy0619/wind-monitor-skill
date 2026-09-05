import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calculate = load_module("calculate_monitor", ROOT / "scripts/calculate_monitor.py")
render = load_module("render_feishu_card", ROOT / "scripts/render_feishu_card.py")


class IndustryFiveDayTest(unittest.TestCase):
    @staticmethod
    def stock_5d(dates):
        row = {"code": "000001.SZ", "name": "测试股", "net_yuan": 1.0, "add_days": 4, "reduce_days": 1, "add_yuan": 2.0, "reduce_yuan": 1.0}
        return {
            "trade_dates": dates,
            "net_add_top5": [row], "net_reduce_top5": [row],
            "add_days_top5": [row], "reduce_days_top5": [row],
            "add_amount_top5": [row], "reduce_amount_top5": [row],
        }

    def test_calculation_and_complete_coverage(self):
        dates = [f"2026080{i}" for i in range(1, 6)]
        payload = {
            "trade_dates": dates,
            "days": [
                {
                    "trade_date": date,
                    "industries": [
                        {"industry": "行业A", "main_yuan": 100 + index},
                        {"industry": "行业B", "main_yuan": -200 - index},
                        {"industry": "行业C", "main_yuan": 50 if index % 2 == 0 else -25},
                    ] + ([{"industry": "缺失行业", "main_yuan": 999}] if index < 4 else []),
                }
                for index, date in enumerate(dates)
            ],
        }
        result = calculate.calculate_industry_5d(payload)
        self.assertEqual(result["industry_count"], 4)
        self.assertEqual(result["comparable_industry_count"], 3)
        self.assertEqual(result["net_add_top5"][0]["industry"], "行业A")
        self.assertEqual(result["net_reduce_top5"][0]["industry"], "行业B")
        self.assertNotIn("缺失行业", {row["industry"] for row in result["add_amount_top5"]})

    def test_close_card_has_three_decision_tables(self):
        dates = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"]
        row = {
            "industry": "一级--二级--测试行业",
            "net_yi": 1.0,
            "days": 5,
            "amount_yi": 2.0,
            "observed_days": 5,
        }
        payload = {
            "report_type": "close_summary",
            "planned_time": "15:10",
            "wind_data_time": "2026-08-26 15:00",
            "top10_main_net_inflow_yi": 1.0,
            "top10_average_change_pct": 1.0,
            "top10": [{"name": "测试股", "main_net_inflow_yi": 1.0, "change_pct": 1.0, "trend": "基本稳定"}],
            "industry_5d": {
                "trade_dates": dates,
                "net_add_top5": [row], "net_reduce_top5": [dict(row, net_yi=-1.0)],
                "add_days_top5": [row], "reduce_days_top5": [row],
                "add_amount_top5": [row], "reduce_amount_top5": [row],
            },
            "stock_5d": self.stock_5d(dates),
        }
        card = render.build_close_card(payload)
        tables = [element for element in card["elements"] if element.get("tag") == "table"]
        self.assertEqual(len(tables), 3)

    def test_close_card_accepts_calculator_native_yuan_schema(self):
        dates = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"]
        row = {
            "industry": "一级--测试行业",
            "net_yuan": 100_000_000,
            "add_days": 4,
            "reduce_days": 1,
            "add_yuan": 200_000_000,
            "reduce_yuan": 50_000_000,
        }
        payload = {
            "report_type": "close_summary",
            "planned_time": "15:10",
            "wind_data_time": "2026-08-26 15:00",
            "top10_main_net_inflow_yi": 1.0,
            "top10_average_change_pct": 1.0,
            "top10": [{"name": "测试股", "main_net_inflow_yi": 1.0, "change_pct": 1.0, "trend": "基本稳定"}],
            "industry_5d": {
                "trade_dates": dates,
                "net_add_top5": [row], "net_reduce_top5": [dict(row, net_yuan=-100_000_000)],
                "add_days_top5": [row], "reduce_days_top5": [row],
                "add_amount_top5": [row], "reduce_amount_top5": [row],
            },
            "stock_5d": self.stock_5d(dates),
            "simulation_label": "历史演练 2026-08-26",
        }
        card = render.build_card(payload, None)
        self.assertIn("历史演练", card["header"]["title"]["content"])
        self.assertEqual(sum(1 for element in card["elements"] if element.get("tag") == "table"), 3)


if __name__ == "__main__":
    unittest.main()
