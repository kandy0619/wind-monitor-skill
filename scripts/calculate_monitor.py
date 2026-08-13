#!/usr/bin/env python3
"""Deterministic calculations for wind-monitor-skill.

Inputs are normalized JSON with monetary values in yuan. The script preserves raw
precision and leaves display rounding to the report renderer.
"""

import argparse
import json
from pathlib import Path


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def change(current, reference):
    if current is None or reference is None:
        return {"amount_yuan": None, "rate_pct": None}
    amount = current - reference
    rate = None if reference == 0 else amount / abs(reference) * 100
    return {"amount_yuan": amount, "rate_pct": rate}


def flow_status(current, previous):
    if current is None or previous is None:
        return "基准建立中"
    if current == previous:
        return "基本持平"
    if previous >= 0 and current >= 0:
        return "持续流入"
    if previous >= 0 and current < 0:
        return "流入转流出"
    if previous < 0 and current >= previous:
        return "流出收窄"
    return "流出扩大"


def calculate_intraday(payload, state):
    trade_date = payload["trade_date"]
    new_day = state.get("trade_date") != trade_date
    if new_day:
        state = {
            "trade_date": trade_date,
            "baseline_type": "first_available",
            "baseline_note": payload.get("baseline_note", "使用当天首个成功采样作为基准"),
            "stocks": {},
            "indexes": {},
        }

    result = {"trade_date": trade_date, "stocks": [], "indexes": []}
    for group in ("stocks", "indexes"):
        saved_group = state.setdefault(group, {})
        for row in payload.get(group, []):
            code = row["code"]
            current = row.get("main_yuan")
            saved = saved_group.get(code, {})
            baseline = saved.get("open_baseline_yuan", current)
            previous = saved.get("previous_yuan")
            calculated = dict(row)
            calculated["recent"] = change(current, previous)
            calculated["baseline"] = change(current, baseline)
            calculated["status"] = flow_status(current, previous)
            result[group].append(calculated)
            saved_group[code] = {
                "name": row.get("name"),
                "open_baseline_yuan": baseline,
                "previous_yuan": current,
                "previous_time": row.get("data_time"),
            }

    index_values = [row.get("main_yuan") for row in payload.get("indexes", [])]
    if len(index_values) == 4 and all(value is not None for value in index_values):
        current_sum = sum(index_values)
        baseline_values = []
        for row in payload["indexes"]:
            saved = state["indexes"][row["code"]]
            baseline_values.append(saved.get("open_baseline_yuan"))
        # Previous sum is supplied explicitly because state has already advanced.
        previous_sum = payload.get("previous_index_sum_yuan")
        baseline_sum = sum(baseline_values) if all(x is not None for x in baseline_values) else None
        result["index_total"] = {
            "current_yuan": current_sum,
            "recent": change(current_sum, previous_sum),
            "baseline": change(current_sum, baseline_sum),
        }
    else:
        result["index_total"] = None
    return result, state


def classify_trend(samples):
    valid = [s for s in samples if s.get("main_yuan") is not None]
    valid.sort(key=lambda x: x["time"])
    if len(valid) < 3:
        return {"trend": "样本不足", "change_yuan": None, "samples": valid, "increments_yuan": []}
    values = [s["main_yuan"] for s in valid]
    increments = [b - a for a, b in zip(values, values[1:])]
    positive = sum(x > 0 for x in increments)
    negative = sum(x < 0 for x in increments)
    count = len(increments)
    start, end = values[0], values[-1]
    if positive / count >= 0.8 and end > start:
        trend = "持续加仓"
    elif negative / count >= 0.8 and end < start:
        trend = "持续减仓"
    elif positive and negative and end > start:
        trend = "波动加仓"
    elif positive and negative and end < start:
        trend = "波动减仓"
    elif end == start or (start != 0 and abs(end - start) <= abs(start) * 0.02):
        trend = "基本稳定"
    else:
        trend = "基本稳定"
    return {
        "trend": trend,
        "change_yuan": end - start,
        "samples": valid,
        "increments_yuan": increments,
        "positive_interval_ratio": positive / count,
        "negative_interval_ratio": negative / count,
    }


def calculate_close(payload):
    return {
        "trade_date": payload.get("trade_date"),
        "stocks": [
            {"code": row["code"], "name": row.get("name"), **classify_trend(row.get("samples", []))}
            for row in payload.get("stocks", [])
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    intraday = sub.add_parser("intraday")
    intraday.add_argument("--input", required=True)
    intraday.add_argument("--state", required=True)
    intraday.add_argument("--output", required=True)
    close = sub.add_parser("close-trend")
    close.add_argument("--input", required=True)
    close.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = read_json(args.input)
    if args.command == "intraday":
        state_path = Path(args.state)
        state = read_json(state_path) if state_path.exists() else {}
        result, updated = calculate_intraday(payload, state)
        write_json(args.state, updated)
        write_json(args.output, result)
    else:
        write_json(args.output, calculate_close(payload))


if __name__ == "__main__":
    main()
