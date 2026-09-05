#!/usr/bin/env python3
"""Build one auditable 15:10 close-report package and its decision card input."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _require(value: dict[str, Any], key: str, component: str) -> Any:
    if key not in value or value[key] is None:
        raise ValueError(f"{component} is missing required field: {key}")
    return value[key]


def _report_id(trade_date: str, wind_data_time: str, top10: list[dict[str, Any]]) -> str:
    identity = {
        "trade_date": trade_date,
        "wind_data_time": wind_data_time,
        "top10": [
            [row.get("code") or row.get("wind_code") or row.get("name"), row.get("main_net_inflow_yi")]
            for row in top10
        ],
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"{trade_date.replace('-', '')}-close-{digest}"


def build_close_report(
    close_top10: dict[str, Any],
    industry_5d: dict[str, Any],
    stock_5d: dict[str, Any],
    simulation_label: str | None = None,
) -> dict[str, Any]:
    trade_date = str(_require(close_top10, "trade_date", "close_top10"))
    wind_data_time = str(_require(close_top10, "wind_data_time", "close_top10"))
    top10 = list(_require(close_top10, "top10", "close_top10"))
    if not 1 <= len(top10) <= 10:
        raise ValueError("close_top10.top10 must contain between 1 and 10 rows")
    industry_dates = list(_require(industry_5d, "trade_dates", "industry_5d"))
    stock_dates = list(_require(stock_5d, "trade_dates", "stock_5d"))
    if len(industry_dates) != 5 or len(stock_dates) != 5:
        raise ValueError("industry_5d and stock_5d must each contain exactly five trade dates")
    if industry_dates != stock_dates:
        raise ValueError("industry and stock five-day windows must be identical")

    report_id = _report_id(trade_date, wind_data_time, top10)
    limitations = []
    for component in (close_top10, industry_5d, stock_5d):
        limitations.extend(component.get("limitations") or component.get("warnings") or [])
    quality_status = "completed_with_limits" if limitations else "completed"

    close_card_input = dict(close_top10)
    close_card_input.update({
        "report_type": "close_summary",
        "report_id": report_id,
        "card_mode": "close-summary",
        "planned_time": "15:10",
        "industry_5d": industry_5d,
        "stock_5d": stock_5d,
    })
    if simulation_label:
        close_card_input["simulation_label"] = simulation_label
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "close_summary",
        "report_id": report_id,
        "trade_date": trade_date,
        "planned_time": "15:10",
        "wind_data_time": wind_data_time,
        "quality_status": quality_status,
        "simulation_label": simulation_label,
        "limitations": limitations,
        "components": {
            "close_top10": close_top10,
            "industry_5d": industry_5d,
            "stock_5d": stock_5d,
        },
        "card_inputs": [close_card_input],
        "delivery": {
            "required_parts": 1,
            "completed_parts": [],
            "status": "pending_render",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top10", required=True, type=Path)
    parser.add_argument("--industry-5d", required=True, type=Path)
    parser.add_argument("--stock-5d", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--simulation-label")
    args = parser.parse_args()
    report = build_close_report(
        read_json(args.top10), read_json(args.industry_5d), read_json(args.stock_5d), args.simulation_label
    )
    atomic_write_json(args.output, report)


if __name__ == "__main__":
    main()
