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
    "优先观察": "red",
    "等待确认": "orange",
    "避免追高": "orange",
    "持仓风控": "green",
    "低优先级": "neutral",
    "数据不足": "orange",
}

INTRADAY_REPORT_TYPE = "intraday"
CLOSE_REPORT_TYPE = "close_summary"
CLOSE_PLANNED_TIME = "15:10"
INTRADAY_PLANNED_TIMES = {
    "09:30", "09:40", "09:50",
    "10:00", "10:10", "10:20", "10:30", "10:40", "10:50",
    "11:00", "11:10", "11:20", "11:30",
    "13:00", "13:10", "13:20", "13:30", "13:40", "13:50",
    "14:00", "14:10", "14:20", "14:30", "14:40", "14:50",
    "15:00",
}
CLOSE_ONLY_FIELDS = {"top10", "top10_main_net_inflow_yi", "top10_average_change_pct", "stock_5d"}


def report_contract(current: dict[str, Any]) -> tuple[str, str | None]:
    """Validate the explicit slot/report contract before choosing a card layout.

    Planned time is authoritative. Payload shape is only validated after the
    report type is known; it is never used to guess whether a report is close or
    intraday.
    """
    planned_time = str(current.get("planned_time") or "")
    report_type = str(current.get("report_type") or "")
    card_mode = current.get("card_mode")

    if planned_time in INTRADAY_PLANNED_TIMES:
        if report_type != INTRADAY_REPORT_TYPE:
            raise ValueError(
                f"report contract mismatch: {planned_time} requires report_type=intraday"
            )
        forbidden = sorted(field for field in CLOSE_ONLY_FIELDS if field in current)
        if card_mode is not None or forbidden:
            details = f"card_mode={card_mode!r}, close_fields={forbidden}"
            raise ValueError(
                f"report contract mismatch: {planned_time} must use the intraday four-table layout ({details})"
            )
        return INTRADAY_REPORT_TYPE, None

    if planned_time == CLOSE_PLANNED_TIME:
        if report_type != CLOSE_REPORT_TYPE:
            raise ValueError(
                "report contract mismatch: 15:10 requires report_type=close_summary"
            )
        if card_mode in (None, "close-summary"):
            missing = [key for key in ("top10", "industry_5d", "stock_5d") if key not in current]
            if missing:
                raise ValueError(f"15:10 close summary payload is missing: {', '.join(missing)}")
            return CLOSE_REPORT_TYPE, "close-summary"
        raise ValueError(f"unsupported 15:10 close card_mode: {card_mode!r}")

    raise ValueError(f"unsupported report planned_time: {planned_time!r}")


def validate_previous_contract(previous: dict[str, Any] | None) -> None:
    if previous is None:
        return
    report_type = previous.get("report_type")
    planned_time = str(previous.get("planned_time") or "")
    if report_type != INTRADAY_REPORT_TYPE or planned_time not in INTRADAY_PLANNED_TIMES:
        raise ValueError("previous payload must be a successful intraday report slice")


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


def industry_net_yi(item: dict[str, Any]) -> float:
    if item.get("net_yi") is not None:
        return float(item["net_yi"])
    return float(item["net_yuan"]) / 100_000_000


def industry_days(item: dict[str, Any], direction: str) -> int:
    if item.get("days") is not None:
        return int(item["days"])
    return int(item["add_days" if direction == "add" else "reduce_days"])


def industry_accumulated_yi(item: dict[str, Any], direction: str) -> float:
    if item.get("amount_yi") is not None:
        return float(item["amount_yi"])
    return float(item["add_yuan" if direction == "add" else "reduce_yuan"]) / 100_000_000


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


def status_tags(*values: str) -> list[dict[str, str]]:
    return [status_tag(value)[0] for value in values]


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
    report_type, _ = report_contract(current)
    if report_type != INTRADAY_REPORT_TYPE:
        raise ValueError("intraday renderer received a non-intraday payload")
    validate_previous_contract(previous)
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


def close_action(stock: dict[str, Any]) -> str:
    """Return a transparent next-session action label from close-only facts."""
    trend = str(stock.get("trend") or "样本不足")
    change_pct = stock.get("change_pct")
    if trend in {"持续加仓", "波动加仓"} and change_pct is not None and float(change_pct) >= 7.0:
        return "避免追高"
    if trend == "持续加仓":
        return "优先观察"
    if trend == "波动加仓":
        return "等待确认"
    if trend in {"持续减仓", "波动减仓"}:
        return "持仓风控"
    if trend == "基本稳定":
        return "低优先级"
    return "数据不足"


def consensus_rankings(data: dict[str, Any], *, entity_type: str, direction: str) -> list[dict[str, Any]]:
    """Rank entities appearing across net, day-count and amount Top-5 lists."""
    if direction not in {"add", "reduce"}:
        raise ValueError(f"unsupported consensus direction: {direction}")
    keys = [
        (f"net_{direction}_top5", "净额"),
        (f"{direction}_days_top5", "天数"),
        (f"{direction}_amount_top5", "金额"),
    ]
    ranked: dict[str, dict[str, Any]] = {}
    for key, dimension in keys:
        for rank, item in enumerate(data.get(key, [])[:5], 1):
            if entity_type == "industry":
                identity = str(item.get("industry") or "")
                name = terminal_industry(identity)
                code = ""
            else:
                code = str(item.get("code") or "")
                name = str(item.get("name") or code or "Wind未返回")
                identity = code or name
            if not identity:
                continue
            entry = ranked.setdefault(identity, {
                "identity": identity, "name": name, "code": code,
                "score": 0, "dimensions": [],
            })
            entry["score"] += 6 - rank
            if dimension not in entry["dimensions"]:
                entry["dimensions"].append(dimension)
    return sorted(
        ranked.values(),
        key=lambda item: (-item["score"], -len(item["dimensions"]), item["identity"]),
    )


def consensus_text(item: dict[str, Any] | None, *, show_code: bool) -> str:
    if not item:
        return "Wind未返回"
    hits = len(item["dimensions"])
    strength = "强共振" if hits == 3 else "中共振" if hits == 2 else "单项领先"
    identity = item["name"]
    if show_code and item.get("code"):
        identity = f"{identity}（{item['code']}）"
    evidence = "·".join(item["dimensions"])
    return f"**{identity}**｜{strength}\n{evidence}｜{item['score']}分"


def consensus_rows(data: dict[str, Any], *, entity_type: str) -> list[dict[str, Any]]:
    add = consensus_rankings(data, entity_type=entity_type, direction="add")[:3]
    reduce = consensus_rankings(data, entity_type=entity_type, direction="reduce")[:3]
    return [
        {
            "rank": str(rank + 1),
            "add": consensus_text(add[rank] if rank < len(add) else None, show_code=entity_type == "stock"),
            "reduce": consensus_text(reduce[rank] if rank < len(reduce) else None, show_code=entity_type == "stock"),
        }
        for rank in range(max(len(add), len(reduce), 1))
    ]


def build_close_card(current: dict[str, Any]) -> dict[str, Any]:
    report_type, card_mode = report_contract(current)
    if report_type != CLOSE_REPORT_TYPE or card_mode != "close-summary":
        raise ValueError("close summary renderer received an incompatible payload")
    top10 = list(current.get("top10", []))
    if not top10:
        raise ValueError("close payload has no top10 rows")

    industry_5d = current["industry_5d"]
    stock_5d = current["stock_5d"]
    industry_dates = list(industry_5d.get("trade_dates", []))
    stock_dates = list(stock_5d.get("trade_dates", []))
    if len(industry_dates) != 5 or industry_dates != stock_dates:
        raise ValueError("close summary requires one identical five-day industry and stock window")

    total = float(current["top10_main_net_inflow_yi"])
    average_pct = float(current["top10_average_change_pct"])
    slot = str(current["planned_time"])
    header_color = "red" if total > 0 else "green" if total < 0 else "blue"
    additive = sum(1 for stock in top10 if stock.get("trend") in {"持续加仓", "波动加仓"})

    rows = []
    actions: dict[str, list[str]] = {}
    for rank, stock in enumerate(top10, 1):
        trend = str(stock.get("trend") or "样本不足")
        action = close_action(stock)
        name = str(stock.get("name") or "Wind未返回")
        actions.setdefault(action, []).append(name)
        rows.append({
            "rank": f"**{rank}**" if rank <= 5 else str(rank),
            "stock": name,
            "main": industry_amount(float(stock["main_net_inflow_yi"])),
            "pct": signed(float(stock["change_pct"]), "%"),
            "decision": status_tags(trend, action),
        })

    focus = (actions.get("优先观察") or [])[:3]
    wait = (actions.get("等待确认") or [])[:3]
    risk = (actions.get("避免追高") or [])[:3] + (actions.get("持仓风控") or [])[:3]
    decision_lines = [
        f"🎯 **优先观察：** {'、'.join(focus) if focus else '暂无'}",
        f"⏳ **等待确认：** {'、'.join(wait) if wait else '暂无'}",
        f"⚠️ **风险提示：** {'、'.join(risk[:4]) if risk else '暂无'}",
    ]
    summary = (
        f"🔎 Top {len(top10)}合计{'净流入' if total >= 0 else '净流出'}，"
        f"其中{additive}只呈现加仓趋势；次日先看资金延续，再结合价格确认，避免仅凭单日排名追涨。"
    )
    columns = [
        {"name": "rank", "display_name": "#", "data_type": "lark_md", "width": "7%", "horizontal_align": "center"},
        {"name": "stock", "display_name": "股票", "data_type": "text", "width": "20%"},
        {"name": "main", "display_name": "主力净流入", "data_type": "lark_md", "width": "22%"},
        {"name": "pct", "display_name": "涨跌", "data_type": "lark_md", "width": "16%"},
        {"name": "decision", "display_name": "趋势 / 次日参考", "data_type": "options", "width": "35%"},
    ]
    consensus_columns = [
        {"name": "rank", "display_name": "#", "data_type": "text", "width": "7%"},
        {"name": "add", "display_name": "加仓共振", "data_type": "lark_md", "width": "46%"},
        {"name": "reduce", "display_name": "减仓共振", "data_type": "lark_md", "width": "47%"},
    ]
    window = f"{industry_dates[0]} 至 {industry_dates[-1]}"
    elements = [
        {"tag": "div", "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**💰 Top 10 合计**\n{industry_amount(total)}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**📈 平均涨跌**\n{signed(average_pct, '%')}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**🔥 加仓趋势**\n{additive}/{len(top10)}"}},
        ]},
        {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(decision_lines)}},
        {"tag": "hr"},
        section("📋 收盘 Top 10｜趋势与次日参考"),
        table(columns, rows, row_height="middle"),
        {"tag": "hr"},
        section("🏭 近5日行业三维共振 Top 3"),
        table(consensus_columns, consensus_rows(industry_5d, entity_type="industry"), row_height="middle"),
        {"tag": "hr"},
        section("🎯 近5日个股三维共振 Top 3"),
        table(consensus_columns, consensus_rows(stock_5d, entity_type="stock"), row_height="middle"),
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"5日区间：{window}。共振分=三类Top5名次积分之和（第1至5名为5至1分）。"}]},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "行动标签仅基于资金趋势与当日涨跌：涨幅≥7%的加仓股标记避免追高；不是无条件买卖指令。"}]},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"数据时间：{current['wind_data_time']}"}]},
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": header_color,
            "title": {"tag": "plain_text", "content": f"📊 {slot} 收盘资金决策摘要"},
        },
        "elements": elements,
    }


def annotate_report_part(card: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Bind multiple Feishu cards to one logical close-report transaction."""
    simulation_label = current.get("simulation_label")
    if simulation_label:
        title = card["header"]["title"]["content"]
        card["header"]["title"]["content"] = f"🧪 {simulation_label}｜{title}"
        card["elements"].insert(0, {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "历史数据流程演练，不代表当前市场状态。"}],
        })
    if current.get("report_type") != CLOSE_REPORT_TYPE:
        return card
    report_id = current.get("report_id")
    part = current.get("card_part")
    part_count = current.get("card_part_count")
    if not report_id:
        return card
    if part and part_count:
        title = card["header"]["title"]["content"]
        card["header"]["title"]["content"] = f"{title}（{part}/{part_count}）"
    card["elements"].append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"收盘总结编号：{report_id}"}],
    })
    return card


def build_card(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    report_type, card_mode = report_contract(current)
    if report_type == INTRADAY_REPORT_TYPE:
        card = build_intraday_card(current, previous)
    elif card_mode == "close-summary":
        if previous is not None:
            raise ValueError("15:10 close report must not receive --previous")
        card = build_close_card(current)
    else:
        raise ValueError("report contract did not select a supported card layout")
    return annotate_report_part(card, current)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--part", type=int, choices=(1,))
    args = parser.parse_args()
    current = json.loads(args.input.read_text(encoding="utf-8"))
    if current.get("report_type") == "close_summary" and "card_inputs" in current:
        if args.part is None:
            raise ValueError("a close_summary package requires --part 1")
        current = current["card_inputs"][args.part - 1]
    elif args.part is not None:
        raise ValueError("--part is only valid for a close_summary package")
    previous = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
    card = build_card(current, previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
