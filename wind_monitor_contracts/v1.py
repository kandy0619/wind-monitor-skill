"""Dependency-free contract shared by Skill tooling and KStock conformance tests."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any


API_VERSION = "1"
CONTRACT_VERSION = "1.0"
INTRADAY_TIMES = {
    "09:30", "09:40", "09:50", "10:00", "10:10", "10:20", "10:30", "10:40", "10:50",
    "11:00", "11:10", "11:20", "11:30", "13:00", "13:10", "13:20", "13:30", "13:40",
    "13:50", "14:00", "14:10", "14:20", "14:30", "14:40", "14:50", "15:00",
}
SAMPLE_TIMES = {"10:00", "10:30", "11:00", "11:15", "13:30", "13:45", "14:00", "14:30", "14:45"}


class ContractError(ValueError):
    pass


def canonical_hash(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def validate_claim(payload: dict[str, Any]) -> None:
    if payload.get("contract_version", CONTRACT_VERSION) != CONTRACT_VERSION:
        raise ContractError("unsupported contract_version")
    planned_time = payload.get("planned_time")
    mode = payload.get("mode")
    report_type = payload.get("report_type")
    if mode == "intraday":
        if planned_time not in INTRADAY_TIMES or report_type != "intraday":
            raise ContractError("intraday claim has invalid time or report_type")
    elif mode == "trend_sample":
        if planned_time not in SAMPLE_TIMES or report_type is not None:
            raise ContractError("trend_sample claim has invalid time or report_type")
    elif mode == "close":
        if planned_time != "15:10" or report_type != "close_summary":
            raise ContractError("close claim must be 15:10/close_summary")
    else:
        raise ContractError("unsupported mode")


def validate_report(payload: dict[str, Any]) -> None:
    planned_time = payload.get("planned_time")
    report_type = payload.get("report_type")
    required_parts = payload.get("required_parts", 1)
    if report_type == "intraday":
        if planned_time not in INTRADAY_TIMES:
            raise ContractError("intraday report has invalid planned_time")
        forbidden = {"top10", "stock_5d", "industry_5d", "card_mode"} & set(payload.get("normalized_payload", {}))
        if forbidden:
            raise ContractError(f"intraday report contains close fields: {sorted(forbidden)}")
        normalized = payload.get("normalized_payload", {})
        if normalized.get("report_type") != "intraday" or normalized.get("planned_time") != planned_time:
            raise ContractError("intraday normalized payload contract mismatch")
    elif report_type == "close_summary":
        if planned_time != "15:10" or required_parts != 1:
            raise ContractError("close_summary must be 15:10 and exactly one card")
        normalized = payload.get("normalized_payload", {})
        missing = {"top10", "industry_5d", "stock_5d"} - set(normalized)
        if missing:
            raise ContractError(f"close_summary missing fields: {sorted(missing)}")
        if (
            normalized.get("report_type") != "close_summary"
            or normalized.get("planned_time") != "15:10"
            or normalized.get("card_mode") != "close-summary"
        ):
            raise ContractError("close_summary normalized payload contract mismatch")
    else:
        raise ContractError("unsupported report_type")


def normalize_wind_time(value: Any, fallback_date: str | None = None) -> str | None:
    """Normalize legacy Wind time labels into a database-safe ISO timestamp."""
    if value is None:
        return None
    text = str(value).strip().replace(" ", "T", 1)
    chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日T(\d{1,2}):(\d{2}):(\d{2})", text)
    if chinese:
        year, month, day, hour, minute, second = map(int, chinese.groups())
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+08:00"
    if fallback_date and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        return f"{fallback_date}T{text}"
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except ValueError:
        prefix = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
        if prefix:
            second = prefix.group(4) or "00"
            return f"{prefix.group(1)}T{int(prefix.group(2)):02d}:{prefix.group(3)}:{second}"
        raise


_iso = normalize_wind_time


def _yi_to_yuan(value: Any) -> str | None:
    if value is None:
        return None
    converted = Decimal(str(value)) * Decimal("100000000")
    text = format(converted, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _first(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def extract_intraday_facts(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Convert the legacy normalized intraday payload into indexed facts."""
    trade_date = payload["trade_date"]
    planned_time = payload["planned_time"]
    wind_time = _iso(payload.get("wind_data_time"), trade_date)
    observations: list[dict[str, Any]] = []
    rankings: list[dict[str, Any]] = []
    for row in payload.get("stocks", []):
        if isinstance(row, dict):
            name, code = row.get("name"), _first(row, "code", "windcode")
            values = [
                _first(row, "change_pct", "change"),
                _first(row, "main_net_yuan", "main_yuan", "main"),
                _first(row, "main_ratio_pct", "ratio"),
                _first(row, "institution_yuan", "inst"),
                row.get("large_yuan", row.get("large")),
                _first(row, "medium_yuan", "mid"),
                row.get("retail_yuan", row.get("retail")),
            ]
            row_wind_time = _iso(_first(row, "wind_time", "trade_time", "time"), trade_date) or wind_time
        else:
            name, code = row[0], row[1]
            values = list(row[2:]) + [None] * 7
            row_wind_time = wind_time
        observations.append({
            "query_profile": "stock",
            "entity_type": "stock",
            "entity_code": code,
            "entity_name": name,
            "trade_date": trade_date,
            "planned_time": planned_time,
            "wind_data_time": row_wind_time,
            "change_pct": values[0],
            "main_net_inflow_yuan": values[1],
            "main_ratio_pct": values[2],
            "institution_yuan": values[3],
            "large_yuan": values[4],
            "medium_yuan": values[5],
            "retail_yuan": values[6],
        })
    for row in payload.get("indexes", []):
        if isinstance(row, dict):
            board = row.get("board")
            name, code = row.get("name"), _first(row, "code", "windcode")
            main_value = _first(row, "main_net_yuan", "main_yuan", "main")
            ratio_value = _first(row, "main_ratio_pct", "ratio")
            row_wind_time = _iso(_first(row, "wind_time", "trade_time", "time"), trade_date) or wind_time
        else:
            board, name, code = row[0], row[1], row[2]
            main_value = row[3] if len(row) > 3 else None
            ratio_value = row[4] if len(row) > 4 else None
            row_wind_time = wind_time
        observations.append({
            "query_profile": "index",
            "entity_type": "index",
            "entity_code": code,
            "entity_name": name,
            "source_board": board,
            "trade_date": trade_date,
            "planned_time": planned_time,
            "wind_data_time": row_wind_time,
            "main_net_inflow_yuan": main_value,
            "main_ratio_pct": ratio_value,
        })
    for direction, key in (("inflow", "industry_inflow_top5"), ("outflow", "industry_outflow_top5")):
        for rank, row in enumerate(payload.get(key, []), 1):
            if isinstance(row, dict):
                industry = _first(row, "name", "industry")
                inflow = _first(row, "inflow_billion", "in")
                outflow = _first(row, "outflow_billion", "out")
                net = _first(row, "net_billion", "net")
                leaders = _first(row, "stocks", "top", "leaders") or []
            else:
                industry = row[0]
                inflow = row[1] if len(row) > 1 else None
                outflow = row[2] if len(row) > 2 else None
                net = row[3] if len(row) > 3 else None
                leaders = (row[4] if len(row) > 4 else []) or []
            observations.append({
                "query_profile": "industry_summary",
                "entity_type": "industry",
                "entity_name": industry,
                "industry_full_name": industry,
                "trade_date": trade_date,
                "planned_time": planned_time,
                "wind_data_time": wind_time,
                "gross_inflow_yuan": _yi_to_yuan(inflow),
                "gross_outflow_yuan": _yi_to_yuan(outflow),
                "main_net_inflow_yuan": _yi_to_yuan(net),
            })
            rankings.append({
                "ranking_type": "industry_intraday",
                "direction": direction,
                "rank": rank,
                "entity_type": "industry",
                "entity_name": industry,
                "metric_value": _yi_to_yuan(net),
            })
            for stock_rank, stock in enumerate(leaders, 1):
                if isinstance(stock, dict):
                    stock_name = stock.get("name")
                    stock_code = stock.get("code")
                    stock_value = _first(stock, "main_net_inflow_yi", "main", "net")
                    stock_change = _first(stock, "change_pct", "change")
                else:
                    stock_name = stock[0]
                    has_code = len(stock) > 1 and isinstance(stock[1], str) and re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", stock[1])
                    stock_code = stock[1] if has_code else None
                    stock_value = stock[2 if has_code else 1] if len(stock) > (2 if has_code else 1) else None
                    stock_change = stock[3 if has_code else 2] if len(stock) > (3 if has_code else 2) else None
                rankings.append({
                    "ranking_type": f"industry_stock_top3:{canonical_hash(industry)[:12]}",
                    "direction": direction,
                    "rank": stock_rank,
                    "entity_type": "stock",
                    "entity_code": stock_code,
                    "entity_name": stock_name,
                    "metric_value": _yi_to_yuan(stock_value),
                    "extra": {"industry_full_name": industry, "change_pct": stock_change},
                })
    return {"observations": observations, "rankings": rankings}


def extract_close_facts(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    trade_date = payload["trade_date"]
    planned_time = payload.get("planned_time", "15:10")
    wind_time = _iso(payload.get("wind_data_time"), trade_date)
    observations: list[dict[str, Any]] = []
    rankings: list[dict[str, Any]] = []
    for fallback_rank, raw_item in enumerate(payload.get("top10", []), 1):
        if isinstance(raw_item, dict):
            item = raw_item
        else:
            item = {
                "rank": raw_item[0], "code": raw_item[1], "name": raw_item[2],
                "board": raw_item[3], "board_rank": raw_item[4], "industry": raw_item[5],
                "change_pct": raw_item[6], "main_net_inflow_yi": raw_item[7],
                "main_ratio_pct": raw_item[8], "trend": raw_item[9],
            }
        code = _first(item, "code", "wind_code")
        industry = _first(item, "industry", "wind_industry")
        sources = item.get("sources")
        source_board = (sources or [None])[0] if isinstance(sources, list) else _first(item, "source_board", "board")
        main_ratio = _first(item, "main_ratio_pct", "main_inflow_ratio_pct")
        main_yi = item.get("main_net_inflow_yi")
        if main_yi is None and item.get("main_yuan") is not None:
            main_yi = Decimal(str(item["main_yuan"])) / Decimal("100000000")
        valid_sample_count = item.get("valid_sample_count")
        if valid_sample_count is None and isinstance(item.get("trend_samples"), list):
            valid_sample_count = len(item["trend_samples"])
        rank = item.get("rank", fallback_rank)
        observations.append({
            "query_profile": "board_candidate",
            "entity_type": "stock",
            "entity_code": code,
            "entity_name": item["name"],
            "industry_full_name": industry,
            "source_board": source_board,
            "trade_date": trade_date,
            "planned_time": planned_time,
            "wind_data_time": wind_time,
            "main_net_inflow_yuan": _yi_to_yuan(main_yi),
            "change_pct": item.get("change_pct"),
            "main_ratio_pct": main_ratio,
        })
        rankings.append({
            "ranking_type": "close_top10",
            "direction": "inflow",
            "rank": rank,
            "entity_type": "stock",
            "entity_code": code,
            "entity_name": item["name"],
            "source_board": source_board,
            "metric_value": _yi_to_yuan(main_yi),
            "decision_label": item.get("decision_label"),
            "decision_rule_version": item.get("decision_rule_version"),
            "extra": {
                "trend": item.get("trend"),
                "change_pct": item.get("change_pct"),
                "valid_sample_count": valid_sample_count,
            },
        })
    return {"observations": observations, "rankings": rankings}


def extract_trend_sample_facts(trade_date: str, sample: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Convert one legacy four-board trend sample into indexed stock facts."""
    planned_time = sample["planned_time"]
    wind_time = _iso(sample.get("wind_time"), trade_date)
    details = sample.get("stock_details") or []
    rows: list[dict[str, Any]] = []
    if details:
        for item in details:
            rows.append({
                "board": item[0], "rank": item[1], "code": item[2], "name": item[3],
                "industry": item[4], "main_yi": item[5], "change_pct": item[6],
                "main_ratio_pct": item[7], "wind_time": item[8] if len(item) > 8 else wind_time,
            })
    else:
        for board, value in (sample.get("boards") or {}).items():
            items = value.get("stocks", []) if isinstance(value, dict) else value
            for item in items or []:
                if isinstance(item, dict):
                    main_yi = _first(item, "main_net_raw", "main_net_inflow_raw")
                    if main_yi is None and item.get("main_yuan") is not None:
                        main_yi = Decimal(str(item["main_yuan"])) / Decimal("100000000")
                    rows.append({
                        "board": board, "rank": _first(item, "rank", "board_rank"),
                        "code": _first(item, "code", "wind_code", "windcode"),
                        "name": item.get("name"),
                        "industry": _first(item, "industry", "wind_industry"),
                        "main_yi": main_yi, "change_pct": item.get("change_pct"),
                        "main_ratio_pct": _first(item, "main_ratio_pct", "main_inflow_ratio"),
                        "wind_time": item.get("wind_time") or wind_time,
                    })
                else:
                    rows.append({
                        "board": board, "rank": item[0], "code": item[1], "name": item[2],
                        "industry": None, "main_yi": item[3], "change_pct": item[4],
                        "main_ratio_pct": item[5], "wind_time": wind_time,
                    })
    observations = [{
        "query_profile": "board_candidate",
        "entity_type": "stock",
        "entity_code": item["code"],
        "entity_name": item["name"],
        "industry_full_name": item["industry"],
        "source_board": item["board"],
        "trade_date": trade_date,
        "planned_time": planned_time,
        "wind_data_time": _iso(item["wind_time"], trade_date),
        "main_net_inflow_yuan": _yi_to_yuan(item["main_yi"]),
        "change_pct": item["change_pct"],
        "main_ratio_pct": item["main_ratio_pct"],
    } for item in rows]
    rankings = [{
        "ranking_type": f"board_sample:{item['board']}",
        "direction": "inflow",
        "rank": item["rank"],
        "entity_type": "stock",
        "entity_code": item["code"],
        "entity_name": item["name"],
        "source_board": item["board"],
        "metric_value": _yi_to_yuan(item["main_yi"]),
    } for item in rows]
    return {"observations": observations, "rankings": rankings}
