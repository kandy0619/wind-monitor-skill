from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "calculate_monitor_kstock", ROOT / "scripts" / "calculate_monitor.py",
)
calculate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(calculate)


def test_intraday_rebuilds_baseline_and_previous_from_completed_facts():
    payload = {
        "trade_date": "2026-08-13",
        "planned_time": "09:50",
        "stocks": [{"code": "000001.SZ", "name": "平安银行", "main_yuan": 140}],
        "indexes": [
            {"code": "000001.SH", "name": "上证指数", "main_yuan": 30},
            {"code": "399001.SZ", "name": "深证成指", "main_yuan": 40},
            {"code": "000688.SH", "name": "科创50", "main_yuan": 50},
            {"code": "399006.SZ", "name": "创业板指", "main_yuan": 60},
        ],
    }
    observations = [
        {"id": 1, "trade_date": "2026-08-13", "planned_time": "09:30", "entity_type": "stock", "entity_code": "000001.SZ", "entity_name": "平安银行", "main_net_inflow_yuan": "100.000000"},
        {"id": 2, "trade_date": "2026-08-13", "planned_time": "09:40", "entity_type": "stock", "entity_code": "000001.SZ", "entity_name": "平安银行", "main_net_inflow_yuan": "120.000000"},
        {"id": 3, "trade_date": "2026-08-13", "planned_time": "09:30", "entity_type": "index", "entity_code": "000001.SH", "entity_name": "上证指数", "main_net_inflow_yuan": "10"},
        {"id": 4, "trade_date": "2026-08-13", "planned_time": "09:30", "entity_type": "index", "entity_code": "399001.SZ", "entity_name": "深证成指", "main_net_inflow_yuan": "20"},
        {"id": 5, "trade_date": "2026-08-13", "planned_time": "09:40", "entity_type": "index", "entity_code": "000001.SH", "entity_name": "上证指数", "main_net_inflow_yuan": "20"},
        {"id": 6, "trade_date": "2026-08-13", "planned_time": "09:40", "entity_type": "index", "entity_code": "399001.SZ", "entity_name": "深证成指", "main_net_inflow_yuan": "30"},
        {"id": 8, "trade_date": "2026-08-13", "planned_time": "09:30", "entity_type": "index", "entity_code": "000688.SH", "entity_name": "科创50", "main_net_inflow_yuan": "30"},
        {"id": 9, "trade_date": "2026-08-13", "planned_time": "09:30", "entity_type": "index", "entity_code": "399006.SZ", "entity_name": "创业板指", "main_net_inflow_yuan": "40"},
        {"id": 10, "trade_date": "2026-08-13", "planned_time": "09:40", "entity_type": "index", "entity_code": "000688.SH", "entity_name": "科创50", "main_net_inflow_yuan": "40"},
        {"id": 11, "trade_date": "2026-08-13", "planned_time": "09:40", "entity_type": "index", "entity_code": "399006.SZ", "entity_name": "创业板指", "main_net_inflow_yuan": "50"},
        # Current-slot facts from a failed retry must not become their own baseline.
        {"id": 7, "trade_date": "2026-08-13", "planned_time": "09:50", "entity_type": "stock", "entity_code": "000001.SZ", "entity_name": "平安银行", "main_net_inflow_yuan": "999"},
    ]

    result = calculate.calculate_intraday_from_observations(payload, observations)

    stock = result["stocks"][0]
    assert stock["recent"] == {"amount_yuan": 20, "rate_pct": 20 / 120 * 100}
    assert stock["baseline"] == {"amount_yuan": 40, "rate_pct": 40. / 100 * 100}
    assert result["index_total"]["current_yuan"] == 180
    assert result["index_total"]["recent"]["amount_yuan"] == 40
    assert result["index_total"]["baseline"]["amount_yuan"] == 80


def test_first_slot_builds_baseline_without_local_state():
    payload = {
        "trade_date": "2026-08-13", "planned_time": "09:30",
        "stocks": [{"code": "000001.SZ", "name": "平安银行", "main_yuan": 100}],
        "indexes": [],
    }
    result = calculate.calculate_intraday_from_observations(payload, [])
    stock = result["stocks"][0]
    assert stock["status"] == "基准建立中"
    assert stock["baseline"]["amount_yuan"] == 0
