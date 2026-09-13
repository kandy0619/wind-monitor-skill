from scripts.industry_pipeline import (
    build_scoped_stock_question,
    exact_batch_industry_stocks,
    industry_leaf,
    industry_level,
    merge_industry_amounts,
    scoped_industry_amount,
    scoped_industry_stocks,
)


FULL = "信息技术--半导体与半导体生产设备--半导体产品与半导体设备--半导体产品"
SIBLING = "信息技术--半导体与半导体生产设备--半导体产品与半导体设备--半导体设备"


def test_industry_hierarchy_is_explicit():
    assert industry_leaf(FULL) == "半导体产品"
    assert industry_level(FULL) == "四级"
    question = build_scoped_stock_question(FULL, "2026-09-04")
    assert "Wind四级行业名称为“半导体产品”" in question
    assert "不得扩大到上级行业" in question


def test_a1_direct_gross_amounts_remove_need_for_a2():
    merged, missing = merge_industry_amounts([{
        "industry": FULL,
        "gross_inflow_yuan": 12,
        "gross_outflow_yuan": 7,
        "net_yuan": 5,
    }])
    assert missing == []
    assert merged[FULL]["gross_inflow_yuan"] == 12


def test_shortened_or_parent_a2_amount_is_never_applied_to_leaf():
    merged, missing = merge_industry_amounts(
        [{"industry": FULL, "gross_inflow_yuan": None, "gross_outflow_yuan": None, "net_yuan": 5}],
        [{"industry": "半导体产品与半导体设备", "gross_inflow_yuan": 99, "gross_outflow_yuan": 88}],
    )
    assert missing == [FULL]
    assert merged[FULL]["gross_inflow_yuan"] is None


def test_scoped_amount_accepts_exact_leaf_but_rejects_parent_or_sibling():
    accepted = scoped_industry_amount(FULL, [{
        "industry": "半导体产品", "gross_inflow_yuan": 12,
        "gross_outflow_yuan": 7, "net_yuan": 5,
    }])
    assert accepted is not None
    assert accepted["industry"] == FULL
    assert accepted["returned_industry"] == "半导体产品"
    assert scoped_industry_amount(FULL, [{
        "industry": "半导体产品与半导体设备", "gross_inflow_yuan": 99,
    }]) is None
    assert scoped_industry_amount(FULL, [{
        "industry": "半导体设备", "gross_inflow_yuan": 99,
    }]) is None


def test_batch_drops_parent_rows_and_scoped_result_accepts_only_target_leaf():
    rows = [
        {"industry": "半导体产品与半导体设备", "code": "000001.SZ", "main_yuan": 99},
        {"industry": FULL, "code": "000002.SZ", "main_yuan": 8},
    ]
    grouped, missing = exact_batch_industry_stocks([FULL], rows)
    assert [row["code"] for row in grouped[FULL]] == ["000002.SZ"]
    assert missing == [FULL]

    scoped = scoped_industry_stocks(FULL, [
        {"industry": "半导体产品", "code": "000003.SZ", "main_yuan": 7},
        {"industry": SIBLING, "code": "000004.SZ", "main_yuan": 100},
        {"industry": "半导体设备", "code": "000005.SZ", "main_yuan": 90},
    ])
    assert [row["code"] for row in scoped] == ["000003.SZ"]
    assert scoped[0]["industry"] == FULL
    assert scoped[0]["returned_industry"] == "半导体产品"
