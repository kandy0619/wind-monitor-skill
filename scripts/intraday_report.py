#!/usr/bin/env python3
"""Build Codex concise and audit markdown from one normalized intraday payload."""

from __future__ import annotations

from typing import Any


def _yi_from_yuan(value: Any) -> float | None:
    return None if value is None else float(value) / 100_000_000


def _amount_yi(value: Any) -> str:
    if value is None:
        return "Wind未返回"
    number = float(value)
    return f"{number:+.1f}亿"


def _amount_yuan(value: Any) -> str:
    converted = _yi_from_yuan(value)
    return _amount_yi(converted)


def _pct(value: Any) -> str:
    return "Wind未返回" if value is None else f"{float(value):+.1f}%"


def _leader_values(value: Any) -> tuple[str, str | None, Any, Any]:
    if isinstance(value, dict):
        return (
            str(value.get("name") or "Wind未返回"),
            value.get("code"),
            value.get("main_net_inflow_yi", value.get("main", value.get("net"))),
            value.get("change_pct", value.get("change")),
        )
    name = str(value[0])
    has_code = len(value) > 1 and isinstance(value[1], str) and "." in value[1]
    return (
        name,
        value[1] if has_code else None,
        value[2 if has_code else 1] if len(value) > (2 if has_code else 1) else None,
        value[3 if has_code else 2] if len(value) > (3 if has_code else 2) else None,
    )


def build_intraday_markdown(payload: dict[str, Any]) -> tuple[str, str]:
    label = payload.get("simulation_label")
    prefix = f"{label}｜" if label else ""
    title = f"# {prefix}{payload['planned_time']} 主力资金"
    total = payload.get("index_total_yuan")
    baseline = "基准建立中" if payload.get("period_delta_yuan") is None else _amount_yuan(payload["period_delta_yuan"])
    lines = [title, "", f"四个代表指数主力合计：{_amount_yuan(total)}；近10分钟：{baseline}。", ""]
    lines += ["|代表指数|主力净流入|", "|---|---:|"]
    for row in payload.get("indexes", []):
        lines.append(f"|{row[1]}|{_amount_yuan(row[3])}|")
    lines += ["", "|自选股|涨跌幅|主力净流入|", "|---|---:|---:|"]
    for row in payload.get("stocks", []):
        lines.append(f"|{row[0]}|{_pct(row[2])}|{_amount_yuan(row[3])}|")
    for heading, key in (("行业净流入 Top 5", "industry_inflow_top5"), ("行业净流出 Top 5", "industry_outflow_top5")):
        lines += ["", f"## {heading}", "", "|#|Wind行业|净额|Top 3个股|", "|---:|---|---:|---|"]
        for rank, row in enumerate(payload.get(key, []), 1):
            leaders = []
            for stock in row[4] or []:
                name, _code, amount, change = _leader_values(stock)
                leaders.append(f"{name} {_amount_yi(amount)}/{_pct(change)}")
            lines.append(f"|{rank}|{row[0]}|{_amount_yi(row[3])}|{'；'.join(leaders) or 'Wind未返回'}|")
    lines += ["", f"数据时间：{payload.get('wind_data_time')}"]
    if payload.get("data_warning"):
        lines += ["", f"数据提示：{payload['data_warning']}"]
    concise = "\n".join(lines)

    audit = ["# 完整报告（审计）", "", concise, "", "## 自选股分户资金", "",
             "|股票|代码|主力|主力占比|机构|大户|中户|散户|",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in payload.get("stocks", []):
        audit.append(
            f"|{row[0]}|{row[1]}|{_amount_yuan(row[3])}|{_pct(row[4])}|"
            f"{_amount_yuan(row[5])}|{_amount_yuan(row[6])}|{_amount_yuan(row[7])}|{_amount_yuan(row[8])}|"
        )
    audit += ["", "## 行业汇总金额与榜内个股", "",
              "|方向|排名|Wind行业完整名称|主力流入|主力流出|主力净流入|榜内个股代码|",
              "|---|---:|---|---:|---:|---:|---|"]
    for direction, key in (("流入", "industry_inflow_top5"), ("流出", "industry_outflow_top5")):
        for rank, row in enumerate(payload.get(key, []), 1):
            codes = [_leader_values(stock)[1] or "Wind未返回" for stock in row[4] or []]
            audit.append(
                f"|{direction}|{rank}|{row[0]}|{_amount_yi(row[1])}|{_amount_yi(row[2])}|"
                f"{_amount_yi(row[3])}|{'、'.join(codes)}|"
            )
    return concise, "\n".join(audit)
