import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = load_module("render_report_contract", ROOT / "scripts/render_feishu_card.py")


def intraday_payload(planned_time="15:00"):
    return {
        "report_type": "intraday",
        "planned_time": planned_time,
        "trade_date": "2026-08-13",
        "wind_data_time": "2026-08-13 15:00:01",
        "index_total_yuan": 1_000_000_000,
        "period_delta_yuan": 100_000_000,
        "baseline_delta_yuan": 200_000_000,
        "indexes": [
            ["上证", "上证指数", "000001.SH", 100_000_000],
            ["深证", "深证成指", "399001.SZ", 200_000_000],
            ["创业板", "创业板指", "399006.SZ", 300_000_000],
            ["科创板", "科创50", "000688.SH", 400_000_000],
        ],
        "stocks": [
            ["中芯国际", "688981.SH", 1.0, 100_000_000],
            ["新易盛", "300502.SZ", 2.0, 200_000_000],
            ["胜宏科技", "300476.SZ", -1.0, -100_000_000],
        ],
        "industry_inflow_top5": [],
        "industry_outflow_top5": [],
    }


def close_payload(planned_time="15:10"):
    dates = ["2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]
    industry = {"industry": "一级--测试行业", "net_yuan": 100_000_000, "add_days": 4, "reduce_days": 1, "add_yuan": 200_000_000, "reduce_yuan": 50_000_000}
    stock = {"code": "000001.SZ", "name": "测试股", "net_yuan": 100_000_000, "add_days": 4, "reduce_days": 1, "add_yuan": 200_000_000, "reduce_yuan": 50_000_000}
    return {
        "report_type": "close_summary",
        "planned_time": planned_time,
        "trade_date": "2026-08-13",
        "wind_data_time": "2026-08-13 15:00:05",
        "top10_main_net_inflow_yi": 1.0,
        "top10_average_change_pct": 2.0,
        "top10": [
            {
                "code": "000001.SZ",
                "name": "测试股",
                "main_net_inflow_yi": 1.0,
                "change_pct": 2.0,
                "trend": "持续加仓",
            }
        ],
        "industry_5d": {
            "trade_dates": dates,
            "net_add_top5": [industry], "net_reduce_top5": [dict(industry, net_yuan=-100_000_000)],
            "add_days_top5": [industry], "reduce_days_top5": [industry],
            "add_amount_top5": [industry], "reduce_amount_top5": [industry],
        },
        "stock_5d": {
            "trade_dates": dates,
            "net_add_top5": [stock], "net_reduce_top5": [dict(stock, net_yuan=-100_000_000)],
            "add_days_top5": [stock], "reduce_days_top5": [stock],
            "add_amount_top5": [stock], "reduce_amount_top5": [stock],
        },
    }


class ReportContractTest(unittest.TestCase):
    def test_1500_is_one_intraday_four_table_card(self):
        previous = intraday_payload("14:50")
        card = render.build_card(intraday_payload(), previous)
        title = card["header"]["title"]["content"]
        self.assertIn("15:00 主力资金", title)
        self.assertNotIn("收盘", title)
        self.assertEqual(sum(1 for item in card["elements"] if item.get("tag") == "table"), 4)

    def test_1500_rejects_close_payload_even_when_top10_exists(self):
        payload = close_payload("15:00")
        with self.assertRaisesRegex(ValueError, "requires report_type=intraday"):
            render.build_card(payload, None)

    def test_1500_intraday_rejects_close_only_fields(self):
        payload = intraday_payload()
        payload["top10"] = close_payload()["top10"]
        with self.assertRaisesRegex(ValueError, "intraday four-table layout"):
            render.build_card(payload, None)

    def test_1510_rejects_intraday_payload(self):
        payload = intraday_payload("15:10")
        with self.assertRaisesRegex(ValueError, "requires report_type=close_summary"):
            render.build_card(payload, None)

    def test_missing_report_type_is_not_inferred_from_shape(self):
        payload = deepcopy(close_payload())
        del payload["report_type"]
        with self.assertRaisesRegex(ValueError, "requires report_type=close_summary"):
            render.build_card(payload, None)

    def test_1510_close_uses_close_layout(self):
        card = render.build_card(close_payload(), None)
        title = card["header"]["title"]["content"]
        self.assertIn("15:10 收盘资金决策摘要", title)
        self.assertEqual(sum(1 for item in card["elements"] if item.get("tag") == "table"), 3)

    def test_1510_rejects_legacy_split_card_mode(self):
        payload = close_payload()
        payload["card_mode"] = "close-stock-5d"
        with self.assertRaisesRegex(ValueError, "unsupported 15:10 close card_mode"):
            render.build_card(payload, None)


if __name__ == "__main__":
    unittest.main()
