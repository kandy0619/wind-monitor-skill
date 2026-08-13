#!/usr/bin/env python3
"""Render deterministic Feishu intraday and close cards for wind-monitor-skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


STATUS_COLORS = {
    "持续流入": "red",
    "持续加仓": "red",
    "流出收窄": "orange",
    "波动加仓": "orange",
    "流入转流出": "green",
    "流出扩大": "green",
    "持续减仓": "green",
    "波动减仓": "green",
    "基本持平": "neutral",
    "基本稳定": "neutral",
    "基准建立中": "orange",
    "样本不足": "orange",
    "Wind未返回": "orange",
}


def signed(value: float, suffix: str) -> str:
    if value > 0:
        return f"<font color='red'>↑ +{value:.1f}{suffix}</font>"
    if value < 0:
        return f"<font color='green'>↓ {value:.1f}{suffix}</font>"
    return f"<font color='grey'>→ 0.0{suffix}</font>"


def yuan(value: float) -> str:
    return signed(value / 100_000_000, "亿")


def industry_amount(value: float) -> str:
    return signed(value, "亿")


def terminal_industry(name: str) -> str:
    parts = [part.strip() for part in str(name).split("--") if part.strip()]
    return parts[-1] if parts else str(name)


def state(current: float, previous: float | None) -> str:
    if previous is None:
        return "基准建立中"
    if current == previous:
        return "基本持平"
    if previous >= 0 and current >= 0:
        return "持续流入"
    if previous >= 0 and current < 0:
        return "流入转流出"
    if current >= previous:
        return "流出收窄"
    return "流出扩大"


def status_tag(value: str) -> list[dict[str, str]]:
    return [{"text": value, "color": STATUS_COLORS.get(value, "neutral")}]


def table(
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    *,
    row_height: str = "low",
) -> dict[str, Any]:
    return {
        "tag": "table",
        "page_size": len(rows),
        "row_height": row_height,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "default",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def section(title: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": f"**{title}**"}}


def previous_map(rows: list[list[Any]], code_index: int, value_index: int) -> dict[str, float]:
    return {str(row[code_index]): float(row[value_index]) for row in rows}


def stock_text(name: str, amount: float, change_pct: float) -> str:
    amount_text = f"↑+{amount:.1f}亿" if amount > 0 else f"↓{amount:.1f}亿" if amount < 0 else "→0.0亿"
    pct_text = f"↑+{change_pct:.1f}%" if change_pct > 0 else f"↓{change_pct:.1f}%" if change_pct < 0 else "→0.0%"
    return f"{name} {amount_text}/{pct_text}"


def build_intraday_card(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    previous = previous or {}
    prev_indexes = previous_map(previous.get("indexes", []), 2, 3)
    prev_stocks = previous_map(previous.get("stocks", []), 1, 3)

    total = float(current["index_total_yuan"])
    delta = float(current["period_delta_yuan"])
    baseline_delta = float(current["baseline_delta_yuan"])
    slot = str(current["planned_time"])
    if total >= 0 and delta >= 0:
        core = "净流入增强"
    elif total >= 0:
        core = "净流入收窄"
    elif delta >= 0:
        core = "净流出收窄"
    else:
        core = "净流出扩大"
    header_color = "red" if total > 0 else "green" if total < 0 else "blue"

    index_rows = []
    index_values = []
    for row in current["indexes"]:
        label, name, code, value = str(row[0]), str(row[1]), str(row[2]), float(row[3])
        previous_value = prev_indexes.get(code)
        change = value - previous_value if previous_value is not None else 0.0
        current_state = state(value, previous_value)
        index_values.append((value, name))
        index_rows.append({
            "name": label,
            "current": yuan(value),
            "delta": yuan(change) if previous_value is not None else "<font color='orange'>⚠ 基准建立中</font>",
            "status": status_tag(current_state),
        })

    stock_rows = []
    for row in current["stocks"]:
        name, code = str(row[0]), str(row[1])
        change_pct, value = float(row[2]), float(row[3])
        previous_value = prev_stocks.get(code)
        change = value - previous_value if previous_value is not None else 0.0
        current_state = state(value, previous_value)
        stock_rows.append({
            "name": name,
            "pct": signed(change_pct, "%"),
            "main": yuan(value),
            "delta": yuan(change) if previous_value is not None else "<font color='orange'>⚠ 基准建立中</font>",
            "status": status_tag(current_state),
        })

    def industry_rows(key: str) -> list[dict[str, Any]]:
        rendered = []
        for rank, row in enumerate(current[key], 1):
            name, net, stocks = terminal_industry(str(row[0])), float(row[3]), row[4]
            rendered.append({
                "rank": str(rank),
                "industry": name,
                "net": industry_amount(net),
                "stocks": "\n".join(
                    stock_text(str(stock[0]), float(stock[1]), float(stock[2]))
                    for stock in stocks[:3]
                ),
            })
        return rendered

    strongest = max(index_values)[1]
    weakest = min(index_values)[1]
    direction = "增强" if delta > 0 else "收窄" if delta < 0 else "持平"
    summary = f"🔎 四个代表指数合计{'净流入' if total >= 0 else '净流出'}，近10分钟{direction}；{strongest}最强，{weakest}相对偏弱。"

    market_columns = [
        {"name": "name", "display_name": "指数", "data_type": "text", "width": "auto"},
        {"name": "current", "display_name": "当前主力", "data_type": "lark_md", "width": "auto"},
        {"name": "delta", "display_name": "近10分钟", "data_type": "lark_md", "width": "auto"},
        {"name": "status", "display_name": "状态", "data_type": "options", "width": "auto"},
    ]
    watch_columns = [
        {"name": "name", "display_name": "股票", "data_type": "text", "width": "auto"},
        {"name": "pct", "display_name": "涨跌", "data_type": "lark_md", "width": "auto"},
        {"name": "main", "display_name": "主力", "data_type": "lark_md", "width": "auto"},
        {"name": "delta", "display_name": "近10分钟", "data_type": "lark_md", "width": "auto"},
        {"name": "status", "display_name": "状态", "data_type": "options", "width": "auto"},
    ]
    industry_columns = [
        {
            "name": "rank",
            "display_name": "#",
            "data_type": "text",
            "width": "7%",
            "horizontal_align": "center",
            "vertical_align": "top",
        },
        {
            "name": "industry",
            "display_name": "行业",
            "data_type": "text",
            "width": "24%",
            "vertical_align": "top",
        },
        {
            "name": "net",
            "display_name": "净额",
            "data_type": "lark_md",
            "width": "20%",
            "vertical_align": "top",
        },
        {
            "name": "stocks",
            "display_name": "Top 3 个股",
            "data_type": "lark_md",
            "width": "49%",
            "vertical_align": "top",
        },
    ]
    elements = [
        {"tag": "div", "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**💰 当前合计**\n{yuan(total)}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**⏱ 近10分钟**\n{yuan(delta)}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**🏁 较日内基准**\n{yuan(baseline_delta)}"}},
        ]},
        {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
        {"tag": "hr"},
        section("📈 代表指数"),
        table(market_columns, index_rows),
        {"tag": "hr"},
        section("⭐ 自选股"),
        table(watch_columns, stock_rows),
        {"tag": "hr"},
        section("🔥 行业净流入 Top 5"),
        table(industry_columns, industry_rows("industry_inflow_top5"), row_height="88px"),
        {"tag": "hr"},
        section("🧊 行业净流出 Top 5"),
        table(industry_columns, industry_rows("industry_outflow_top5"), row_height="88px"),
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据时间：{current['wind_data_time']}"}]},
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_color,
            "title": {"tag": "plain_text", "content": f"📊 {slot} 主力资金｜{core}"},
        },
        "elements": elements,
    }


def build_close_card(current: dict[str, Any]) -> dict[str, Any]:
    top10 = list(current.get("top10", []))
    if not top10:
        raise ValueError("close payload has no top10 rows")

    total = float(current["top10_main_net_inflow_yi"])
    average_pct = float(current["top10_average_change_pct"])
    leading_trend = str(top10[0].get("trend") or "样本不足")
    slot = str(current.get("planned_time") or "15:10")
    header_color = "red" if total > 0 else "green" if total < 0 else "blue"

    rows = []
    for rank, stock in enumerate(top10, 1):
        trend = str(stock.get("trend") or "样本不足")
        rows.append({
            "rank": f"**{rank}**" if rank <= 5 else str(rank),
            "stock": str(stock.get("name") or "Wind未返回"),
            "main": industry_amount(float(stock["main_net_inflow_yi"])),
            "pct": signed(float(stock["change_pct"]), "%"),
            "trend": status_tag(trend),
        })

    columns = [
        {
            "name": "rank", "display_name": "#", "data_type": "lark_md",
            "width": "8%", "horizontal_align": "center",
        },
        {"name": "stock", "display_name": "股票", "data_type": "text", "width": "22%"},
        {"name": "main", "display_name": "主力净流入", "data_type": "lark_md", "width": "23%"},
        {"name": "pct", "display_name": "涨跌", "data_type": "lark_md", "width": "17%"},
        {"name": "trend", "display_name": "全日趋势", "data_type": "options", "width": "30%"},
    ]
    summary = (
        f"🔎 四板块各 Top 5 候选合并后取前 {len(top10)} 名；"
        f"榜首为{top10[0].get('name', 'Wind未返回')}，全日趋势为{leading_trend}。"
    )
    elements = [
        {"tag": "div", "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**💰 Top 10 合计**\n{industry_amount(total)}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**📈 平均涨跌**\n{signed(average_pct, '%')}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**🏆 榜首趋势**\n{leading_trend}"}},
        ]},
        {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
        {"tag": "hr"},
        section("📋 四板块各Top 5候选合并榜"),
        table(columns, rows, row_height="middle"),
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据时间：{current['wind_data_time']}"}]},
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_color,
            "title": {"tag": "plain_text", "content": f"📊 {slot} 收盘主力榜｜{leading_trend}"},
        },
        "elements": elements,
    }


def build_card(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if "top10" in current:
        return build_close_card(current)
    return build_intraday_card(current, previous)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    current = json.loads(args.input.read_text(encoding="utf-8"))
    previous = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
    card = build_card(current, previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
