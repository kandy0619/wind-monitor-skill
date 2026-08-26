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


def calculate_industry_5d(payload):
    aggregated = {}
    for day in payload.get("days", []):
        trade_date = day.get("trade_date")
        for row in day.get("industries", []):
            name = row["industry"]
            value = row.get("main_yuan")
            if value is None:
                continue
            item = aggregated.setdefault(name, {
                "industry": name,
                "observed_days": 0,
                "add_days": 0,
                "reduce_days": 0,
                "add_yuan": 0,
                "reduce_yuan": 0,
                "net_yuan": 0,
                "daily": [],
            })
            item["observed_days"] += 1
            item["net_yuan"] += value
            if value > 0:
                item["add_days"] += 1
                item["add_yuan"] += value
            elif value < 0:
                item["reduce_days"] += 1
                item["reduce_yuan"] += abs(value)
            item["daily"].append({"trade_date": trade_date, "main_yuan": value})

    rows = list(aggregated.values())
    expected_days = len(payload.get("trade_dates", []))
    comparable = [x for x in rows if x["observed_days"] == expected_days]
    result = {
        "trade_dates": payload.get("trade_dates", []),
        "industry_count": len(rows),
        "comparable_industry_count": len(comparable),
        "method": {
            "add_day": "当日主力净流入额大于0",
            "reduce_day": "当日主力净流入额小于0",
            "add_yuan": "5日内正值逐日相加",
            "reduce_yuan": "5日内负值绝对值逐日相加",
            "net_yuan": "5日主力净流入额代数和",
        },
        "net_add_top5": sorted((x for x in comparable if x["net_yuan"] > 0), key=lambda x: (-x["net_yuan"], x["industry"]))[:5],
        "net_reduce_top5": sorted((x for x in comparable if x["net_yuan"] < 0), key=lambda x: (x["net_yuan"], x["industry"]))[:5],
        "add_days_top5": sorted(comparable, key=lambda x: (-x["add_days"], -x["add_yuan"], x["industry"]))[:5],
        "reduce_days_top5": sorted(comparable, key=lambda x: (-x["reduce_days"], -x["reduce_yuan"], x["industry"]))[:5],
        "add_amount_top5": sorted(comparable, key=lambda x: (-x["add_yuan"], -x["add_days"], x["industry"]))[:5],
        "reduce_amount_top5": sorted(comparable, key=lambda x: (-x["reduce_yuan"], -x["reduce_days"], x["industry"]))[:5],
    }
    return result


def _stock_candidate(row, trade_dates):
    daily_source = row.get("daily", [])
    if isinstance(daily_source, dict):
        daily_map = daily_source
    else:
        daily_map = {item.get("trade_date"): item.get("main_yuan") for item in daily_source}
    values = [daily_map.get(trade_date) for trade_date in trade_dates]
    if len(trade_dates) != 5 or any(value is None for value in values):
        return None
    add_values = [value for value in values if value > 0]
    reduce_values = [value for value in values if value < 0]
    return {
        "code": row["code"],
        "name": row.get("name"),
        "industry": row.get("industry"),
        "observed_days": len(values),
        "add_days": len(add_values),
        "reduce_days": len(reduce_values),
        "add_yuan": sum(add_values),
        "reduce_yuan": sum(abs(value) for value in reduce_values),
        "net_yuan": sum(values),
        "daily": [
            {"trade_date": trade_date, "main_yuan": value}
            for trade_date, value in zip(trade_dates, values)
        ],
    }


def _unique_stock_candidates(rows, trade_dates):
    candidates = {}
    rejected = []
    for row in rows:
        item = _stock_candidate(row, trade_dates)
        code = row.get("code")
        if item is None:
            rejected.append(code)
            continue
        previous = candidates.get(code)
        if previous is not None and previous["daily"] != item["daily"]:
            raise ValueError(f"conflicting daily values for {code}")
        candidates[code] = item
    return list(candidates.values()), rejected


def calculate_stock_5d(payload):
    trade_dates = payload.get("trade_dates", [])
    net_rows, net_rejected = _unique_stock_candidates(payload.get("net_candidates", []), trade_dates)
    days_rows, days_rejected = _unique_stock_candidates(payload.get("days_candidates", []), trade_dates)
    amount_rows, amount_rejected = _unique_stock_candidates(payload.get("amount_candidates", []), trade_dates)
    return {
        "trade_dates": trade_dates,
        "window_note": payload.get("window_note"),
        "method": {
            "eligibility": "5个指定交易日逐日主力净流入额均非空",
            "add_day": "当日主力净流入额大于0",
            "reduce_day": "当日主力净流入额小于0",
            "add_yuan": "5日内正值逐日相加",
            "reduce_yuan": "5日内负值绝对值逐日相加",
            "net_yuan": "5日主力净流入额代数和",
        },
        "candidate_counts": {
            "net": len(net_rows), "days": len(days_rows), "amount": len(amount_rows),
        },
        "rejected_codes": sorted(set(net_rejected + days_rejected + amount_rejected)),
        "net_add_top5": sorted(
            (row for row in net_rows if row["net_yuan"] > 0),
            key=lambda row: (-row["net_yuan"], row["code"]),
        )[:5],
        "net_reduce_top5": sorted(
            (row for row in net_rows if row["net_yuan"] < 0),
            key=lambda row: (row["net_yuan"], row["code"]),
        )[:5],
        "add_days_top5": sorted(
            days_rows, key=lambda row: (-row["add_days"], -row["add_yuan"], row["code"]),
        )[:5],
        "reduce_days_top5": sorted(
            days_rows, key=lambda row: (-row["reduce_days"], -row["reduce_yuan"], row["code"]),
        )[:5],
        "add_amount_top5": sorted(
            amount_rows, key=lambda row: (-row["add_yuan"], -row["add_days"], row["code"]),
        )[:5],
        "reduce_amount_top5": sorted(
            amount_rows, key=lambda row: (-row["reduce_yuan"], -row["reduce_days"], row["code"]),
        )[:5],
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
    industry_5d = sub.add_parser("industry-5d")
    industry_5d.add_argument("--input", required=True)
    industry_5d.add_argument("--output", required=True)
    stock_5d = sub.add_parser("stock-5d")
    stock_5d.add_argument("--input", required=True)
    stock_5d.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = read_json(args.input)
    if args.command == "intraday":
        state_path = Path(args.state)
        state = read_json(state_path) if state_path.exists() else {}
        result, updated = calculate_intraday(payload, state)
        write_json(args.state, updated)
        write_json(args.output, result)
    elif args.command == "close-trend":
        write_json(args.output, calculate_close(payload))
    elif args.command == "industry-5d":
        write_json(args.output, calculate_industry_5d(payload))
    else:
        write_json(args.output, calculate_stock_5d(payload))


if __name__ == "__main__":
    main()
