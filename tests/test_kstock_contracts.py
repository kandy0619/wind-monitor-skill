import json
from pathlib import Path

import pytest

from wind_monitor_contracts import (
    ContractError,
    extract_close_facts,
    extract_intraday_facts,
    validate_claim,
    validate_report,
)


def test_1500_and_1510_contracts_are_distinct():
    validate_claim({
        "planned_time": "15:00", "mode": "intraday", "report_type": "intraday", "contract_version": "1.0",
    })
    validate_claim({
        "planned_time": "15:10", "mode": "close", "report_type": "close_summary", "contract_version": "1.0",
    })
    with pytest.raises(ContractError):
        validate_claim({
            "planned_time": "15:00", "mode": "close", "report_type": "close_summary", "contract_version": "1.0",
        })
    with pytest.raises(ContractError):
        validate_claim({
            "planned_time": "15:10", "mode": "intraday", "report_type": "intraday", "contract_version": "1.0",
        })


def test_close_report_requires_one_card_and_all_sections():
    validate_report({
        "planned_time": "15:10",
        "report_type": "close_summary",
        "required_parts": 1,
        "normalized_payload": {
            "report_type": "close_summary", "planned_time": "15:10",
            "card_mode": "close-summary", "top10": [],
            "industry_5d": {}, "stock_5d": {},
        },
    })
    with pytest.raises(ContractError):
        validate_report({
            "planned_time": "15:10",
            "report_type": "close_summary",
            "required_parts": 2,
            "normalized_payload": {"top10": [], "industry_5d": {}, "stock_5d": {}},
        })


def test_extract_intraday_facts_preserves_money_precision_and_rankings():
    facts = extract_intraday_facts({
        "trade_date": "2026-08-13",
        "planned_time": "15:00",
        "wind_data_time": "2026-08-13T15:00:00+08:00",
        "stocks": [["中芯国际", "688981.SH", 1.2, 123456789.123456, 2.3, None, None, None, None]],
        "indexes": [["科创板", "科创50", "000688.SH", -100000000.25, -0.1]],
        "industry_inflow_top5": [["信息技术--半导体", 20.1, 10.2, 9.9, [["中芯国际", 1.5, 1.2]]]],
        "industry_outflow_top5": [],
    })
    assert len(facts["observations"]) == 3
    assert facts["observations"][0]["main_net_inflow_yuan"] == 123456789.123456
    assert facts["rankings"][0]["ranking_type"] == "industry_intraday"
    assert facts["rankings"][0]["metric_value"] == "990000000"


def test_extract_close_facts_builds_researchable_top10():
    facts = extract_close_facts({
        "trade_date": "2026-08-13",
        "planned_time": "15:10",
        "wind_data_time": "2026-08-13T15:00:00+08:00",
        "top10": [{
            "rank": 1, "code": "688981.SH", "name": "中芯国际", "sources": ["科创板"],
            "industry": "信息技术--半导体", "main_net_inflow_yi": 2.5, "change_pct": 1.2,
            "trend": "持续加仓", "valid_sample_count": 9,
        }],
    })
    assert facts["rankings"][0]["ranking_type"] == "close_top10"
    assert facts["rankings"][0]["metric_value"] == "250000000"


def test_published_http_contract_matches_python_contract():
    contract = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / "kstock-storage-v1.json").read_text(encoding="utf-8")
    )
    assert contract["contract_version"] == "1.0"
    assert contract["report_contracts"]["15:00"]["report_type"] == "intraday"
    assert contract["report_contracts"]["15:10"]["required_parts"] == 1
    assert contract["recipient_identifiers_in_responses"] is False


def test_extract_intraday_accepts_legacy_aliases_without_losing_units():
    facts = extract_intraday_facts({
        "trade_date": "2026-08-12", "planned_time": "11:00",
        "wind_data_time": "2026-08-12 10:59—11:00",
        "stocks": [{
            "code": "688981.SH", "name": "中芯国际", "change": 1.2,
            "main": 123, "ratio": 2.1, "inst": 1, "large": 2, "mid": 3, "retail": 4,
            "time": "2026年8月12日 10:59:59",
        }],
        "indexes": [{"code": "000688.SH", "name": "科创50", "main": 456, "ratio": 1.0}],
        "industry_inflow_top5": [{
            "industry": "信息技术--半导体", "in": 5, "out": 2, "net": 3,
            "leaders": [["中芯国际", "688981.SH", 1.5, 1.2]],
        }],
        "industry_outflow_top5": [],
    })
    assert facts["observations"][0]["wind_data_time"] == "2026-08-12T10:59:59+08:00"
    assert facts["rankings"][1]["entity_code"] == "688981.SH"
    assert facts["rankings"][1]["metric_value"] == "150000000"
