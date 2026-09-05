import sys
import unittest
from copy import deepcopy
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from deliver_report import deliver_report_package, validate_card
from kstock_feishu_delivery import DeliveryError


def minimal_input(title):
    dates = ["2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]
    industry = {"industry": "一级--测试行业", "net_yuan": 1.0, "add_days": 4, "reduce_days": 1, "add_yuan": 2.0, "reduce_yuan": 1.0}
    stock = {"code": "000001.SZ", "name": title, "net_yuan": 1.0, "add_days": 4, "reduce_days": 1, "add_yuan": 2.0, "reduce_yuan": 1.0}
    return {
        "report_type": "close_summary",
        "planned_time": "15:10",
        "trade_date": "2026-08-13",
        "slot": "15:10",
        "wind_data_time": "15:00:00",
        "top10": [{"rank": 1, "name": title, "code": "000001.SZ", "main_net_inflow_yi": 1.0, "change_pct": 1.0, "trend": "持续加仓"}],
        "top10_main_net_inflow_yi": 1.0,
        "top10_average_change_pct": 1.0,
        "industry_5d": {
            "trade_dates": dates,
            "net_add_top5": [industry], "net_reduce_top5": [industry],
            "add_days_top5": [industry], "reduce_days_top5": [industry],
            "add_amount_top5": [industry], "reduce_amount_top5": [industry],
        },
        "stock_5d": {
            "trade_dates": dates,
            "net_add_top5": [stock], "net_reduce_top5": [stock],
            "add_days_top5": [stock], "reduce_days_top5": [stock],
            "add_amount_top5": [stock], "reduce_amount_top5": [stock],
        },
    }


class DeliverReportTests(unittest.TestCase):
    def test_two_cards_are_persisted_and_sent_in_order(self):
        package = {"report_id": "r1", "card_inputs": [minimal_input("one"), minimal_input("two")], "delivery": {}}
        snapshots = []
        sent = []
        result = deliver_report_package(
            package,
            persist=lambda value: snapshots.append(deepcopy(value)),
            sender=lambda card: sent.append(card) or {"success": True},
        )
        self.assertEqual(len(sent), 2)
        self.assertEqual(result["delivery"]["status"], "completed")
        self.assertEqual(result["delivery"]["completed_parts"], [1, 2])
        self.assertEqual(set(result["delivery"]["rendered_cards"]), {"1", "2"})
        self.assertGreaterEqual(len(snapshots), 4)

    def test_retry_skips_already_delivered_part(self):
        package = {"report_id": "r1", "card_inputs": [minimal_input("one"), minimal_input("two")], "delivery": {}}
        calls = []

        def first_sender(card):
            calls.append(card)
            if len(calls) == 2:
                raise DeliveryError("feishu_delivery_failed", "temporary")
            return {"success": True}

        with self.assertRaises(DeliveryError):
            deliver_report_package(package, persist=lambda value: None, sender=first_sender)
        self.assertEqual(package["delivery"]["completed_parts"], [1])
        persisted_second = deepcopy(package["delivery"]["rendered_cards"]["2"]["card"])
        package["card_inputs"][1]["top10"][0]["name"] = "changed-after-render"
        retry_calls = []
        deliver_report_package(package, persist=lambda value: None, sender=lambda card: retry_calls.append(card) or {"success": True})
        self.assertEqual(len(retry_calls), 1)
        self.assertEqual(retry_calls[0], persisted_second)
        self.assertEqual(package["delivery"]["completed_parts"], [1, 2])

    def test_invalid_card_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_card({"elements": []})


if __name__ == "__main__":
    unittest.main()
