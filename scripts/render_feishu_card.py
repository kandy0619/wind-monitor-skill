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


def yuan(value: float | None, missing: str = "Wind未返回") -> str:
    if value is None:
        return f"<font color='orange'>⚠ {missing}</font>"
    return signed(value / 100_000_000, "亿")


def industry_amount(value: float | None) -> str:
    if value is None:
        return "<font color='orange'>⚠ Wind未返回</font>"
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


def stock_text(name: str, amount: float | None, change_pct: float | None) -> str:
    if amount is None or change_pct is None:
        return f"{name} Wind未返回"
    amount_text = f"↑+{amount:.1f}亿" if amount > 0 else f"↓{amount:.1f}亿" if amount < 0 else "→0.0亿"
    pct_text = f"↑+{change_pct:.1f}%" if change_pct > 0 else f"↓{change_pct:.1f}%" if change_pct < 0 else "→0.0%"
    return f"{name} {amount_text}/{pct_text}"


def build_intraday_card(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    previous = previous or {}
    prev_indexes = previous_map(previous.get("indexes", []), 2, 3)
    prev_stocks = previous_map(previous.get("stocks", []), 1, 3)

    total = float(current["index_total_yuan"])
    delta_raw = current.get("period_delta_yuan")
    baseline_raw = current.get("baseline_delta_yuan")
    delta = float(delta_raw) if delta_raw is not None else None
    baseline_delta = float(baseline_raw) if baseline_raw is not None else None
    slot = str(current["planned_time"])
    if delta is None:
        core = "基准建立中"
    elif total >= 0 and delta > 0:
        core = "净流入增强"
    elif total >= 0 and delta < 0:
        core = "净流入收窄"
    elif total < 0 and delta > 0:
        core = "净流出收窄"
    elif total < 0 and delta < 0:
        core = "净流出扩大"
    else:
        core = "基本持平"
    header_color = "orange" if delta is None else "red" if total > 0 else "green" if total < 0 else "blue"

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
            name = terminal_industry(str(row[0]))
            net = float(row[3]) if row[3] is not None else None
            stocks = row[4] or []
            rendered.append({
                "rank": str(rank),
                "industry": name,
                "net": industry_amount(net),
                "stocks": "\n".join(
                    stock_text(
                        str(stock[0]),
                        float(stock[1]) if stock[1] is not None else None,
                        float(stock[2]) if stock[2] is not None else None,
                    )
                    for stock in stocks[:3]
                ) or "Wind未返回",
            })
        return rendered

    strongest = max(index_values)[1]
    weakest = min(index_values)[1]
    if delta is None:
        summary = f"🔎 四个代表指数合计{'净流入' if total >= 0 else '净流出'}；首次预览正在建立比较基准，{strongest}最强，{weakest}相对偏弱。"
    else:
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
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**⏱ 近10分钟**\n{yuan(delta, '基准建立中')}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**🏁 较日内基准**\n{yuan(baseline_delta, '基准建立中')}"}},
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
    ]
    if current.get("data_warning"):
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"<font color='orange'>⚠️ 数据提示：{current['data_warning']}</font>"},
        })
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据时间：{current['wind_data_time']}"}]})
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
    ]
    industry_5d = current.get("industry_5d")
    if industry_5d:
        net_rows = []
        for direction, key in (("加仓", "net_add_top5"), ("减仓", "net_reduce_top5")):
            for rank, item in enumerate(industry_5d.get(key, []), 1):
                net_rows.append({
                    "direction": direction,
                    "rank": str(rank),
                    "industry": terminal_industry(str(item["industry"])),
                    "amount": industry_amount(float(item["net_yi"])),
                })
        pair_rows = []
        add_days = industry_5d.get("add_days_top5", [])
        reduce_days = industry_5d.get("reduce_days_top5", [])
        for rank in range(max(len(add_days), len(reduce_days))):
            add = add_days[rank] if rank < len(add_days) else None
            reduce = reduce_days[rank] if rank < len(reduce_days) else None
            pair_rows.append({
                "rank": str(rank + 1),
                "add": f"{terminal_industry(str(add['industry']))}｜{add['days']}天" if add else "Wind未返回",
                "reduce": f"{terminal_industry(str(reduce['industry']))}｜{reduce['days']}天" if reduce else "Wind未返回",
            })
        amount_rows = []
        add_amount = industry_5d.get("add_amount_top5", [])
        reduce_amount = industry_5d.get("reduce_amount_top5", [])
        for rank in range(max(len(add_amount), len(reduce_amount))):
            add = add_amount[rank] if rank < len(add_amount) else None
            reduce = reduce_amount[rank] if rank < len(reduce_amount) else None
            amount_rows.append({
                "rank": str(rank + 1),
                "add": f"{terminal_industry(str(add['industry']))}｜+{add['amount_yi']:.1f}亿" if add else "Wind未返回",
                "reduce": f"{terminal_industry(str(reduce['industry']))}｜-{reduce['amount_yi']:.1f}亿" if reduce else "Wind未返回",
            })
        elements.extend([
            {"tag": "hr"},
            section("📊 近5日净加减仓 Top 5"),
            table([
                {"name": "direction", "display_name": "方向", "data_type": "text", "width": "15%"},
                {"name": "rank", "display_name": "#", "data_type": "text", "width": "8%"},
                {"name": "industry", "display_name": "行业", "data_type": "text", "width": "47%"},
                {"name": "amount", "display_name": "5日净额", "data_type": "lark_md", "width": "30%"},
            ], net_rows),
            {"tag": "hr"},
            section("📅 近5日加减仓天数 Top 5"),
            table([
                {"name": "rank", "display_name": "#", "data_type": "text", "width": "8%"},
                {"name": "add", "display_name": "加仓行业｜天数", "data_type": "text", "width": "46%"},
                {"name": "reduce", "display_name": "减仓行业｜天数", "data_type": "text", "width": "46%"},
            ], pair_rows),
            {"tag": "hr"},
            section("💰 近5日加减仓金额 Top 5"),
            table([
                {"name": "rank", "display_name": "#", "data_type": "text", "width": "8%"},
                {"name": "add", "display_name": "累计加仓", "data_type": "text", "width": "46%"},
                {"name": "reduce", "display_name": "累计减仓", "data_type": "text", "width": "46%"},
            ], amount_rows),
        ])
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据时间：{current['wind_data_time']}"}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_color,
            "title": {"tag": "plain_text", "content": f"📊 {slot} 收盘主力榜｜{leading_trend}"},
        },
        "elements": elements,
    }


def stock_label(item: dict[str, Any] | None) -> str:
    if not item:
        return "Wind未返回"
    return f"{item.get('name') or 'Wind未返回'}（{item['code']}）"


def build_close_stock_5d_card(current: dict[str, Any]) -> dict[str, Any]:
    stock_5d = current.get("stock_5d") or current
    trade_dates = stock_5d.get("trade_dates", [])
    if len(trade_dates) != 5:
        raise ValueError("stock 5d payload must contain exactly five trade dates")

    net_rows = []
    for direction, key in (("加仓", "net_add_top5"), ("减仓", "net_reduce_top5")):
        for rank, item in enumerate(stock_5d.get(key, []), 1):
            net_rows.append({
                "direction": direction,
                "rank": str(rank),
                "stock": stock_label(item),
                "amount": yuan(float(item["net_yuan"])),
            })

    def paired_rows(add_key, reduce_key, formatter):
        add_rows = stock_5d.get(add_key, [])
        reduce_rows = stock_5d.get(reduce_key, [])
        rows = []
        for rank in range(max(len(add_rows), len(reduce_rows))):
            add = add_rows[rank] if rank < len(add_rows) else None
            reduce = reduce_rows[rank] if rank < len(reduce_rows) else None
            rows.append({
                "rank": str(rank + 1),
                "add": formatter(add, True) if add else "Wind未返回",
                "reduce": formatter(reduce, False) if reduce else "Wind未返回",
            })
        return rows

    days_rows = paired_rows(
        "add_days_top5", "reduce_days_top5",
        lambda item, add: f"{stock_label(item)}｜{item['add_days' if add else 'reduce_days']}天",
    )
    amount_rows = paired_rows(
        "add_amount_top5", "reduce_amount_top5",
        lambda item, add: f"{stock_label(item)}｜{'+' if add else '-'}{item['add_yuan' if add else 'reduce_yuan'] / 100_000_000:.1f}亿",
    )
    window = f"{trade_dates[0]} 至 {trade_dates[-1]}"
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**统计区间：** {window}\n仅纳入5个交易日逐日资金均完整的A股。"}},
        {"tag": "hr"},
        section("📊 个股近5日净加减仓 Top 5"),
        table([
            {"name": "direction", "display_name": "方向", "data_type": "text", "width": "13%"},
            {"name": "rank", "display_name": "#", "data_type": "text", "width": "7%"},
            {"name": "stock", "display_name": "股票", "data_type": "text", "width": "50%"},
            {"name": "amount", "display_name": "5日净额", "data_type": "lark_md", "width": "30%"},
        ], net_rows),
        {"tag": "hr"},
        section("📅 个股近5日加减仓天数 Top 5"),
        table([
            {"name": "rank", "display_name": "#", "data_type": "text", "width": "7%"},
            {"name": "add", "display_name": "加仓个股｜天数", "data_type": "text", "width": "46%"},
            {"name": "reduce", "display_name": "减仓个股｜天数", "data_type": "text", "width": "47%"},
        ], days_rows),
        {"tag": "hr"},
        section("💰 个股近5日加减仓金额 Top 5"),
        table([
            {"name": "rank", "display_name": "#", "data_type": "text", "width": "7%"},
            {"name": "add", "display_name": "累计加仓", "data_type": "text", "width": "46%"},
            {"name": "reduce", "display_name": "累计减仓", "data_type": "text", "width": "47%"},
        ], amount_rows),
    ]
    if stock_5d.get("window_note"):
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": stock_5d["window_note"]}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "📊 15:10 收盘｜近5日个股资金统计"},
        },
        "elements": elements,
    }


def build_card(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if current.get("card_mode") == "close-stock-5d" or ("stock_5d" in current and "top10" not in current):
        return build_close_stock_5d_card(current)
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
