#!/usr/bin/env python3
"""Import legacy normalized monitor files into KStock MySQL.

The command is dry-run by default. It never sends Feishu messages and marks
imported runs as ``legacy_import`` with ``raw_missing`` data quality because
historical normalized files do not contain a complete raw Wind envelope.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    from kstock_api_client import KStockClient
except ModuleNotFoundError:
    from scripts.kstock_api_client import KStockClient

try:
    from wind_monitor_contracts import (
        extract_close_facts,
        extract_intraday_facts,
        extract_trend_sample_facts,
    )
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from wind_monitor_contracts import (
        extract_close_facts,
        extract_intraday_facts,
        extract_trend_sample_facts,
    )


SHANGHAI = ZoneInfo("Asia/Shanghai")
INTRADAY_FILE = re.compile(r"^(\d{8})-(\d{4})\.json$")


@dataclass(frozen=True)
class LegacyRecord:
    trade_date: str
    planned_time: str
    mode: str
    report_type: str | None
    wind_data_time: str | None
    payload: dict[str, Any]
    facts: dict[str, list[dict[str, Any]]]
    source_name: str


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"legacy file is not an object: {path.name}")
    return value


def discover_records(state_root: Path) -> list[LegacyRecord]:
    records: list[LegacyRecord] = []
    normalized_root = state_root / "a-share-monitor-normalized"
    if normalized_root.is_dir():
        for path in sorted(normalized_root.glob("*.json")):
            match = INTRADAY_FILE.match(path.name)
            if not match:
                continue
            payload = _read_object(path)
            planned_time = payload.get("planned_time") or f"{match.group(2)[:2]}:{match.group(2)[2:]}"
            if planned_time == "15:10" or "stocks" not in payload:
                continue
            records.append(LegacyRecord(
                trade_date=payload["trade_date"], planned_time=planned_time,
                mode="intraday", report_type="intraday",
                wind_data_time=payload.get("wind_data_time"), payload=payload,
                facts=extract_intraday_facts(payload), source_name=path.name,
            ))

    samples_root = state_root / "a-share-close-main-add-samples"
    if samples_root.is_dir():
        for path in sorted(samples_root.glob("*.json")):
            payload = _read_object(path)
            trade_date = payload["trade_date"]
            rich_samples = []
            flat_groups: dict[str, dict[str, Any]] = {}
            for sample in payload.get("samples", []):
                code = sample.get("windcode") or sample.get("wind_code")
                if not code:
                    rich_samples.append(sample)
                    continue
                planned_time = sample["planned_time"]
                grouped = flat_groups.setdefault(planned_time, {
                    "planned_time": planned_time,
                    "executed_at": sample.get("executed_at"),
                    "wind_time": sample.get("wind_time"),
                    "stock_details": [],
                })
                raw_value = sample.get("main_net_raw")
                if raw_value is None:
                    raw_value = sample.get("main_net_inflow_yuan")
                unit = str(sample.get("main_net_unit") or sample.get("unit") or ("元" if "main_net_inflow_yuan" in sample else "亿元"))
                if raw_value is None:
                    value_yi = None
                elif unit == "元":
                    value_yi = float(raw_value) / 100_000_000
                elif unit == "万元":
                    value_yi = float(raw_value) / 10_000
                elif unit == "百万元":
                    value_yi = float(raw_value) / 100
                else:
                    value_yi = raw_value
                grouped["stock_details"].append([
                    sample.get("board") or sample.get("source_board"),
                    sample.get("rank") or sample.get("board_rank"), code,
                    sample.get("name"), sample.get("wind_industry"), value_yi,
                    sample.get("change_pct"), sample.get("main_ratio_pct", sample.get("main_inflow_ratio")),
                    sample.get("wind_time"),
                ])
            for sample in [*rich_samples, *flat_groups.values()]:
                records.append(LegacyRecord(
                    trade_date=trade_date, planned_time=sample["planned_time"],
                    mode="trend_sample", report_type=None,
                    wind_data_time=sample.get("wind_time"), payload=sample,
                    facts=extract_trend_sample_facts(trade_date, sample),
                    source_name=f"{path.name}#{sample['planned_time']}",
                ))

    close_root = state_root / "a-share-close-main-add-top10"
    if close_root.is_dir():
        for path in sorted(close_root.glob("*.json")):
            payload = _read_object(path)
            if not payload.get("top10"):
                continue
            records.append(LegacyRecord(
                trade_date=payload["trade_date"], planned_time="15:10",
                mode="close", report_type="close_summary",
                wind_data_time=payload.get("wind_data_time"), payload=payload,
                facts=extract_close_facts(payload), source_name=path.name,
            ))
    return sorted(records, key=lambda item: (item.trade_date, item.planned_time, item.mode))


def select_records(records: Iterable[LegacyRecord], start: str | None, end: str | None) -> list[LegacyRecord]:
    return [record for record in records if (not start or record.trade_date >= start) and (not end or record.trade_date <= end)]


def import_records(client: KStockClient, task_id: int, records: Iterable[LegacyRecord]) -> dict[str, int]:
    counts = {"imported": 0, "skipped": 0, "observations": 0, "rankings": 0}
    for record in records:
        triggered = datetime.fromisoformat(f"{record.trade_date}T{record.planned_time}:00").replace(tzinfo=SHANGHAI)
        claimed = client.claim_run({
            "task_id": task_id,
            "trade_date": record.trade_date,
            "planned_time": record.planned_time,
            "mode": record.mode,
            "run_kind": "legacy_import",
            "report_type": record.report_type,
            "triggered_at": triggered.isoformat(),
            "lease_owner": "legacy-import-v1",
            "skill_version": "legacy-import-v1",
            "adapter_version": "legacy-normalized-v1",
        })
        run = claimed["run"]
        if run.get("status") in {"completed", "completed_with_limits"}:
            counts["skipped"] += 1
            continue
        run_id = int(run["id"])
        client.record_attempt(run_id, {
            "request_id": f"legacy:{record.source_name}",
            "query_profile": "legacy_normalized",
            "request_kind": record.mode,
            "request_contract": {"source_kind": "legacy_normalized_file"},
            "response_metadata": {"source_name": record.source_name},
            "error_code": "raw_missing",
            "error_detail_redacted": "历史文件不包含完整脱敏Wind原始回包",
            "adapter_version": "legacy-normalized-v1",
        })
        committed = client.commit_facts(run_id, {
            "wind_data_time": record.wind_data_time,
            "quality_status": "raw_missing",
            "observations": record.facts["observations"],
            "rankings": record.facts["rankings"],
        })
        client.complete_run(run_id, with_limits=True)
        counts["imported"] += 1
        counts["observations"] += int(committed.get("observations", 0))
        counts["rankings"] += int(committed.get("rankings", 0))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy wind-monitor normalized state into KStock")
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--apply", action="store_true", help="write to KStock; otherwise only scan")
    args = parser.parse_args()
    records = select_records(discover_records(args.state_root), args.start, args.end)
    summary: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "records": len(records),
        "intraday": sum(record.mode == "intraday" for record in records),
        "trend_sample": sum(record.mode == "trend_sample" for record in records),
        "close": sum(record.mode == "close" for record in records),
    }
    if args.apply:
        client = KStockClient.from_environment(args.project_root)
        client.capabilities()
        context = client.context()
        summary.update(import_records(client, int(context["task_id"]), records))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
