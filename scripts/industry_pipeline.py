#!/usr/bin/env python3
"""Deterministic helpers for the intraday Wind industry two-stage workflow.

Wind semantic queries sometimes resolve a requested leaf industry to its
parent level.  These helpers never blur that boundary: batch results are only
accepted on an exact full-path match, while a scoped fallback explicitly asks
Wind for the concrete hierarchy level and still validates the returned leaf.
"""

from __future__ import annotations

from typing import Any, Iterable


_CHINESE_LEVELS = {
    1: "一级",
    2: "二级",
    3: "三级",
    4: "四级",
    5: "五级",
    6: "六级",
}


class IndustryScopeError(ValueError):
    pass


def industry_path_parts(full_name: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in str(full_name).split("--") if part.strip())
    if not parts:
        raise IndustryScopeError("Wind行业完整名称为空")
    return parts


def industry_leaf(full_name: str) -> str:
    return industry_path_parts(full_name)[-1]


def industry_level(full_name: str) -> str:
    depth = len(industry_path_parts(full_name))
    try:
        return _CHINESE_LEVELS[depth]
    except KeyError as error:
        raise IndustryScopeError(f"不支持的Wind行业层级深度: {depth}") from error


def merge_industry_amounts(
    a1_records: Iterable[dict[str, Any]],
    a2_records: Iterable[dict[str, Any]] = (),
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Merge direct Wind industry amounts without fuzzy hierarchy matching.

    A1 occasionally returns gross inflow/outflow even when only net flow was
    requested.  Those are valid direct industry-summary values and are kept.
    A2 can override/fill them only when it returns the exact same full path.
    Parent or shortened labels are deliberately ignored because their amounts
    represent a different aggregation scope.
    """
    base = {str(row["industry"]): dict(row) for row in a1_records}
    exact_a2 = {
        str(row["industry"]): row
        for row in a2_records
        if str(row.get("industry")) in base
    }
    missing: list[str] = []
    for full_name, row in base.items():
        supplement = exact_a2.get(full_name, {})
        for field in ("gross_inflow_yuan", "gross_outflow_yuan"):
            if supplement.get(field) is not None:
                row[field] = supplement[field]
        if row.get("gross_inflow_yuan") is None or row.get("gross_outflow_yuan") is None:
            missing.append(full_name)
    return base, missing


def scoped_industry_amount(
    full_name: str, records: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Accept an explicitly scoped amount response for the exact leaf only.

    A scoped Wind query can return the requested leaf label instead of its
    full hierarchy path.  The query itself pins the hierarchy level, so the
    exact leaf is safe to canonicalize.  Parent and sibling labels remain
    rejected.
    """
    leaf = industry_leaf(full_name)
    accepted = [
        dict(row) for row in records
        if str(row.get("industry") or "").strip() in {full_name, leaf}
    ]
    if len(accepted) != 1:
        return None
    accepted[0]["returned_industry"] = accepted[0].get("industry")
    accepted[0]["industry"] = full_name
    return accepted[0]


def exact_batch_industry_stocks(
    industry_names: Iterable[str],
    records: Iterable[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Accept only exact full-path matches from a multi-industry B request."""
    names = tuple(industry_names)
    allowed = set(names)
    grouped = {name: [] for name in names}
    seen: dict[str, set[str]] = {name: set() for name in names}
    for source in records:
        full_name = str(source.get("industry") or "")
        code = str(source.get("code") or "")
        if full_name not in allowed or not code or code in seen[full_name]:
            continue
        grouped[full_name].append(dict(source))
        seen[full_name].add(code)
    for full_name in names:
        grouped[full_name].sort(key=lambda row: float(row["main_yuan"]), reverse=True)
        grouped[full_name] = grouped[full_name][:3]
    missing = [name for name in names if len(grouped[name]) < 3]
    return grouped, missing


def scoped_industry_stocks(
    full_name: str,
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate a one-leaf fallback response and return its top three rows."""
    leaf = industry_leaf(full_name)
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in records:
        returned = str(source.get("industry") or "").strip()
        code = str(source.get("code") or "").strip()
        if returned not in {full_name, leaf} or not code or code in seen:
            continue
        item = dict(source)
        # Preserve Wind's returned label in provenance while keeping the A1
        # full path as the canonical relationship key.
        item["returned_industry"] = returned
        item["industry"] = full_name
        accepted.append(item)
        seen.add(code)
    accepted.sort(key=lambda row: float(row["main_yuan"]), reverse=True)
    return accepted[:3]


def build_scoped_stock_question(full_name: str, trade_date: str) -> str:
    leaf = industry_leaf(full_name)
    level = industry_level(full_name)
    return (
        f"在全部A股中，仅筛选Wind{level}行业名称为“{leaf}”的股票。"
        f"日期严格为{trade_date}，先按Wind{level}行业精确过滤，再按当日主力净流入额降序取前3只不同股票。"
        f"不得扩大到上级行业或用其它行业补足数量。返回Wind代码、证券简称、所属Wind{level}行业、"
        "行业内排名、当日主力净流入额和涨跌幅。"
    )


def build_scoped_amount_question(full_name: str, trade_date: str) -> str:
    leaf = industry_leaf(full_name)
    level = industry_level(full_name)
    return (
        f"仅返回行业汇总数据，禁止返回个股。日期严格为{trade_date}。"
        f"范围严格限定为Wind{level}行业“{leaf}”，完整路径为“{full_name}”。"
        "返回Wind行业完整名称、主力流入额、主力流出额和主力净流入额；"
        "不得用上级行业汇总值代替。"
    )
