#!/usr/bin/env python3
"""Collect complete historical Wind industry slices with bidirectional paging."""

from __future__ import annotations

import argparse
from pathlib import Path

from wind_cli_client import WindCliError, atomic_write, call_wind
from wind_response_adapter import AdaptationError, adapt_response, persist_raw_response


def question(trade_date: str, direction: str) -> str:
    prefix = (
        f"仅返回{trade_date}全部A股的Wind末级行业汇总表，禁止返回个股。"
    )
    if direction == "asc":
        return (
            prefix
            + f"按{trade_date}当日主力净流入额从低到高升序返回，"
            + "每个Wind末级行业唯一一行，返回Wind行业完整名称和当日主力净流入额，不筛选Top行业。"
        )
    return (
        prefix
        + f"每个Wind末级行业唯一一行，返回Wind行业完整名称和{trade_date}当日主力净流入额，不筛选Top行业。"
    )


def collect_day(trade_date: str, *, project_root: Path, artifact_root: Path) -> dict:
    merged: dict[str, float] = {}
    counts: dict[str, int] = {}
    for direction in ("desc", "asc"):
        result = None
        for attempt in range(1, 4):
            try:
                raw = call_wind(
                    "analytics_data", "get_financial_data",
                    {"question": question(trade_date, direction)}, project_root=project_root,
                )
            except WindCliError as error:
                persist_raw_response(
                    {"error": error.as_dict()}, artifact_root, trade_date=trade_date,
                    slot="15:10-simulation", request_id=f"industry-{direction}-attempt-{attempt}",
                    metadata={"server_type": "analytics_data", "tool_name": "get_financial_data", "attempt": attempt},
                )
                continue
            persist_raw_response(
                raw, artifact_root, trade_date=trade_date, slot="15:10-simulation",
                request_id=f"industry-{direction}-attempt-{attempt}",
                metadata={"server_type": "analytics_data", "tool_name": "get_financial_data", "attempt": attempt},
            )
            try:
                result = adapt_response(raw, "industry_daily_full")
            except AdaptationError as error:
                if error.code == "no_data":
                    continue
                raise
            break
        if result is None:
            raise ValueError(f"Wind returned no usable industry rows after 3 attempts: {trade_date} {direction}")
        counts[direction] = len(result.records)
        for row in result.records:
            name = row["industry"]
            amount = row["net_yuan"]
            if name in merged and merged[name] != amount:
                raise ValueError(f"conflicting Wind values for industry: {name}")
            merged[name] = amount
    if len(merged) <= max(counts.values()):
        raise ValueError(
            "bidirectional Wind queries did not expand the 100-row result; completeness is unproven"
        )
    return {
        "trade_date": trade_date,
        "source_rows": counts,
        "industries": [
            {"industry": name, "main_yuan": amount}
            for name, amount in sorted(merged.items())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs=5, required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    days = [
        collect_day(date, project_root=args.project_root, artifact_root=args.artifact_root)
        for date in args.dates
    ]
    atomic_write(
        args.output,
        {
            "schema_version": 1,
            "source": "Wind",
            "trade_dates": args.dates,
            "days": days,
        },
    )
    print({"days": len(days), "industry_counts": [len(day["industries"]) for day in days]})


if __name__ == "__main__":
    main()
