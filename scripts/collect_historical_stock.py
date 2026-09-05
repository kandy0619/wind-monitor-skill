#!/usr/bin/env python3
"""Collect and verify the six Wind five-day A-share candidate directions."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from wind_cli_client import WindCliError, atomic_write, call_wind
from wind_response_adapter import (
    UNIT_FACTORS,
    discover_records,
    normalize_label,
    persist_raw_response,
    unit_from_label,
)


def date_token(value: str) -> str | None:
    match = re.search(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})", value)
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else None


def amount(value, source_key: str, units: dict[str, str]) -> float | None:
    if value in (None, "", "--", "-"):
        return None
    number = float(str(value).replace(",", ""))
    unit = units.get(source_key) or unit_from_label(source_key)
    if unit and ("百万元" in unit or "百万人民币元" in unit):
        unit = "百万元"
    if unit not in UNIT_FACTORS:
        raise ValueError(f"unsupported or missing amount unit for {source_key}")
    return number * UNIT_FACTORS[unit]


def source_key(record: dict, aliases: tuple[str, ...]) -> str | None:
    normalized = {normalize_label(key): key for key in record}
    for alias in aliases:
        if normalize_label(alias) in normalized:
            return normalized[normalize_label(alias)]
    return None


def extract_rows(raw, trade_dates: list[str]) -> list[dict]:
    records, units = discover_records(raw)
    merged: dict[str, dict] = {}
    for record in records:
        code_key = source_key(record, ("Wind代码", "证券代码", "股票代码", "代码"))
        if not code_key:
            raise ValueError("Wind stock result has no code field")
        code = str(record[code_key])
        item = merged.setdefault(code, {"code": code, "name": None, "industry": None, "daily": {}})
        name_key = source_key(record, ("证券简称", "中文简称", "股票简称", "简称", "名称"))
        industry_key = source_key(
            record,
            ("Wind行业完整名称", "所属Wind行业明细", "Wind行业", "所属行业", "行业"),
        )
        if name_key and record.get(name_key):
            item["name"] = str(record[name_key])
        if industry_key and record.get(industry_key):
            item["industry"] = str(record[industry_key])

        row_date = None
        date_key = source_key(record, ("交易日期", "交易日", "日期"))
        if date_key and record.get(date_key):
            row_date = date_token(str(record[date_key]))
        for key, value in record.items():
            label = normalize_label(key)
            if "主力净流入额" not in label:
                continue
            current_date = row_date or date_token(str(key))
            if current_date in trade_dates:
                item["daily"][current_date] = amount(value, str(key), units)
    return list(merged.values())


def ranking_questions(dates: list[str]) -> dict[str, str]:
    period = "、".join(dates)
    base = f"在全部A股范围内筛选{period}这5个交易日的逐日主力净流入额均非空的股票，"
    tail = "仅返回Top 5。返回方向内排名、证券简称、Wind代码和对应聚合指标。"
    return {
        "net-add": base + "按这5日主力净流入额代数和从高到低排序，" + tail,
        "net-reduce": base + "按这5日主力净流入额代数和从低到高排序，" + tail,
        "days-add": (
            f"筛选在{period}这5个指定交易日中每天主力净流入额均大于0的A股，"
            "按这5日主力净流入额之和从高到低排序，" + tail
        ),
        "days-reduce": (
            f"筛选在{period}这5个指定交易日中每天主力净流入额均小于0的A股，"
            "按这5日主力净流入额绝对值之和从高到低排序，" + tail
        ),
        "amount-add": (
            base + "把5个逐日字段中大于0的数值相加作为正流入累计额（小于等于0按0计），"
            "按正流入累计额从高到低排序，" + tail
        ),
        "amount-reduce": (
            f"筛选全部A股中{dates[0]}至{dates[-1]}这5个交易日主力资金净流出额累计最高的5只股票。"
            "结果必须是股票明细，每行返回Wind代码、证券简称、"
            + "、".join(dates)
            + "逐日主力净流入额和5日主力资金净流出累计额，不要返回全市场汇总。"
        ),
    }


def select_direction(request_id: str, rows: list[dict], dates: list[str]) -> list[dict]:
    def metrics(row: dict) -> tuple[float, int, float, int, float]:
        values = [row["daily"].get(date) for date in dates]
        if any(value is None for value in values):
            return (0.0, 0, 0.0, 0, 0.0)
        positive = [value for value in values if value > 0]
        negative = [value for value in values if value < 0]
        return sum(values), len(positive), sum(positive), len(negative), sum(abs(value) for value in negative)

    valid = [row for row in rows if len(row.get("daily", {})) == len(dates)]
    if request_id == "net-add":
        return sorted(valid, key=lambda row: (-metrics(row)[0], row["code"]))[:5]
    if request_id == "net-reduce":
        return sorted(valid, key=lambda row: (metrics(row)[0], row["code"]))[:5]
    if request_id == "days-add":
        return sorted(valid, key=lambda row: (-metrics(row)[1], -metrics(row)[2], row["code"]))[:5]
    if request_id == "days-reduce":
        return sorted(valid, key=lambda row: (-metrics(row)[3], -metrics(row)[4], row["code"]))[:5]
    if request_id == "amount-add":
        return sorted(valid, key=lambda row: (-metrics(row)[2], -metrics(row)[1], row["code"]))[:5]
    return sorted(valid, key=lambda row: (-metrics(row)[4], -metrics(row)[3], row["code"]))[:5]


def detail_question(codes: list[str], dates: list[str]) -> str:
    return (
        "仅查询以下A股：" + "、".join(codes) + "。返回证券简称、Wind代码、Wind行业完整名称，以及"
        + "、".join(dates)
        + "每个交易日各自的主力净流入额；每只股票一行，保留金额单位，不做排名或筛选。"
    )


def call_structured(
    server_type: str,
    tool_name: str,
    question: str,
    *,
    request_id: str,
    trade_date: str,
    project_root: Path,
    artifact_root: Path,
    attempts: int = 3,
):
    last_raw = None
    for attempt in range(1, attempts + 1):
        try:
            raw = call_wind(server_type, tool_name, {"question": question}, project_root=project_root)
        except WindCliError as error:
            persist_raw_response(
                {"error": error.as_dict()}, artifact_root, trade_date=trade_date,
                slot="15:10-simulation", request_id=f"{request_id}-attempt-{attempt}",
                metadata={"server_type": server_type, "tool_name": tool_name, "attempt": attempt},
            )
            continue
        last_raw = raw
        persist_raw_response(
            raw, artifact_root, trade_date=trade_date, slot="15:10-simulation",
            request_id=f"{request_id}-attempt-{attempt}",
            metadata={"server_type": server_type, "tool_name": tool_name, "attempt": attempt},
        )
        try:
            records, _ = discover_records(raw)
        except Exception:
            records = []
        if records:
            return raw
    raise ValueError(f"Wind returned no structured records after {attempts} attempts for {request_id}")


def call_candidates(
    question: str,
    *,
    request_id: str,
    trade_date: str,
    trade_dates: list[str],
    project_root: Path,
    artifact_root: Path,
    attempts: int = 3,
) -> list[dict]:
    cache_dir = (
        artifact_root / "a-share-monitor-raw" / trade_date.replace("-", "") / "1510-simulation"
    )
    cached = sorted(cache_dir.glob(f"{request_id}-attempt-*.json"), reverse=True)
    for path in cached:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            rows = extract_rows(envelope["response"], trade_dates)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if rows:
            return rows
    correction = (
        " 结果必须是5只具体股票的明细行，每行必须包含Wind代码和证券简称；"
        "不得返回全市场汇总、每日股票数量或每日累计金额。"
    )
    for attempt in range(1, attempts + 1):
        try:
            raw = call_wind(
                "stock_data", "search_stocks",
                {"question": question + (correction if attempt > 1 else "")},
                project_root=project_root,
            )
        except WindCliError as error:
            persist_raw_response(
                {"error": error.as_dict()}, artifact_root, trade_date=trade_date,
                slot="15:10-simulation", request_id=f"{request_id}-attempt-{attempt}",
                metadata={"server_type": "stock_data", "tool_name": "search_stocks", "attempt": attempt},
            )
            continue
        persist_raw_response(
            raw, artifact_root, trade_date=trade_date, slot="15:10-simulation",
            request_id=f"{request_id}-attempt-{attempt}",
            metadata={"server_type": "stock_data", "tool_name": "search_stocks", "attempt": attempt},
        )
        try:
            rows = extract_rows(raw, trade_dates)
        except (TypeError, ValueError):
            rows = []
        if rows:
            return rows
    raise ValueError(f"Wind returned no stock-level candidates after {attempts} attempts for {request_id}")


def collect(*, dates: list[str], project_root: Path, artifact_root: Path) -> dict:
    ranked: dict[str, list[dict]] = {}
    ranking_return_counts: dict[str, int] = {}
    for request_id, question in ranking_questions(dates).items():
        returned = call_candidates(
            question,
            request_id=f"stock-{request_id}", trade_date=dates[-1],
            trade_dates=dates, project_root=project_root, artifact_root=artifact_root,
        )
        ranking_return_counts[request_id] = len(returned)
        ranked[request_id] = select_direction(request_id, returned, dates)

    groups = {}
    for group in ("net", "days", "amount"):
        codes = sorted({row["code"] for direction in ("add", "reduce") for row in ranked[f"{group}-{direction}"]})
        raw = call_structured(
            "analytics_data", "get_financial_data", detail_question(codes, dates),
            request_id=f"stock-{group}-detail", trade_date=dates[-1],
            project_root=project_root, artifact_root=artifact_root,
        )
        details = {row["code"]: row for row in extract_rows(raw, dates)}
        missing = sorted(set(codes) - set(details))
        rows = []
        for code in codes:
            row = details.get(code)
            if not row or not row.get("industry") or any(row["daily"].get(date) is None for date in dates):
                continue
            row["daily"] = [{"trade_date": date, "main_yuan": row["daily"][date]} for date in dates]
            rows.append(row)
        groups[group] = rows
        groups[f"{group}_missing_or_incomplete"] = missing + sorted(
            code for code in codes if code in details and code not in {row["code"] for row in rows}
        )
    return {
        "schema_version": 1,
        "source": "Wind",
        "trade_dates": dates,
        "window_note": "历史演练；按Wind返回候选逐日原值复算，未直接采用返回顺序或聚合值。",
        "net_candidates": groups["net"],
        "days_candidates": groups["days"],
        "amount_candidates": groups["amount"],
        "limitations": {
            "ranking_return_counts": ranking_return_counts,
            "missing_or_incomplete": {
                group: groups[f"{group}_missing_or_incomplete"] for group in ("net", "days", "amount")
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs=5, required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = collect(
        dates=args.dates, project_root=args.project_root, artifact_root=args.artifact_root,
    )
    atomic_write(args.output, payload)
    print({
        "candidate_counts": {name: len(payload[f"{name}_candidates"]) for name in ("net", "days", "amount")},
        "incomplete_counts": {name: len(payload["limitations"]["missing_or_incomplete"][name]) for name in ("net", "days", "amount")},
    })


if __name__ == "__main__":
    main()
