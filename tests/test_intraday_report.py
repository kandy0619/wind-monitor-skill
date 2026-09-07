from scripts.intraday_report import build_intraday_markdown
from scripts.render_feishu_card import build_card
from scripts.run_intraday import apply_comparison_deltas


def payload():
    leaders = [{"name": "寒武纪", "code": "688256.SH", "main_net_inflow_yi": 2.2, "change_pct": -2.5}]
    return {
        "trade_date": "2026-09-04", "planned_time": "15:00", "report_type": "intraday",
        "simulation_label": "历史预览 2026-09-04", "wind_data_time": "2026-09-04T15:00:00+08:00",
        "index_total_yuan": 100_000_000, "period_delta_yuan": None, "baseline_delta_yuan": None,
        "indexes": [["上证", "上证指数", "000001.SH", 100_000_000, 1.0]],
        "stocks": [["中芯国际", "688981.SH", 1.0, 100_000_000, 2.0, 1, 2, 3, 4]],
        "industry_inflow_top5": [["信息技术--半导体产品", 3.0, 1.0, 2.0, leaders]],
        "industry_outflow_top5": [["材料--钢铁", 1.0, 2.0, -1.0, leaders]],
    }


def test_structured_industry_leader_keeps_code_in_audit_but_card_stays_compact():
    concise, audit = build_intraday_markdown(payload())
    assert "寒武纪 +2.2亿/-2.5%" in concise
    assert "688256.SH" in audit
    card = build_card(payload(), None)
    text = str(card)
    assert "寒武纪" in text
    assert "688256.SH" not in text


def test_production_comparison_reuses_previous_baseline():
    current = {"index_total_yuan": 130}
    previous = {"index_total_yuan": 100, "baseline_delta_yuan": 20}
    apply_comparison_deltas(current, previous)
    assert current["period_delta_yuan"] == 30
    assert current["baseline_delta_yuan"] == 50
