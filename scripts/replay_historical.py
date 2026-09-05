#!/usr/bin/env python3
"""Offline replay of historical KStock monitor slices without external calls."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from calculate_monitor import calculate_close, calculate_intraday
from deliver_report import card_hash, deliver_report_package, validate_card
from kstock_feishu_delivery import DeliveryError
from monitor_runtime import SlotStateStore, SlotTask, empty_manifest, plan_poll
from render_feishu_card import build_card
from wind_response_adapter import atomic_write_json


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def reconstruct_pre_slot_state(final_state: dict[str, Any], previous_normalized: dict[str, Any]) -> dict[str, Any]:
    """Keep daily baselines but restore previous values from the preceding slice."""
    state = copy.deepcopy(final_state)
    layouts = {"stocks": (1, 3), "indexes": (2, 3)}
    for group, (code_index, value_index) in layouts.items():
        for row in previous_normalized.get(group, []):
            code = row[code_index]
            saved = state.get(group, {}).get(code)
            if saved is None:
                continue
            saved["previous_yuan"] = row[value_index]
            saved["previous_time"] = previous_normalized.get("wind_data_time")
    return state


def _require(paths: list[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("historical replay inputs are missing: " + ", ".join(missing))


def replay(kstock_root: Path, trade_date: str, output_root: Path) -> dict[str, Any]:
    token = trade_date.replace("-", "")
    state_root = kstock_root / ".codex" / "automation-state"
    previous_normalized_path = state_root / "a-share-monitor-normalized" / f"{token}-1450.json"
    current_normalized_path = state_root / "a-share-monitor-normalized" / f"{token}-1500.json"
    current_input_path = state_root / "a-share-monitor-input" / f"{token}-1500.json"
    final_state_path = state_root / "a-share-watchlist-main-flow-10m.json"
    expected_intraday_path = state_root / "a-share-monitor-calculated" / f"{token}-1500.json"
    close_input_path = state_root / "a-share-monitor-input" / f"{token}-close-trend.json"
    expected_close_trend_path = state_root / "a-share-monitor-calculated" / f"{token}-close-trend.json"
    close_normalized_path = state_root / "a-share-monitor-normalized" / f"{token}-close.json"
    close_top10_path = state_root / "a-share-close-main-add-top10" / f"{token}.json"
    required = [
        previous_normalized_path, current_normalized_path, current_input_path,
        final_state_path, expected_intraday_path, close_input_path,
        expected_close_trend_path, close_normalized_path, close_top10_path,
    ]
    _require(required)

    output_root.mkdir(parents=True, exist_ok=True)
    previous_normalized = read_json(previous_normalized_path)
    current_normalized = read_json(current_normalized_path)
    current_input = read_json(current_input_path)
    seeded_state = reconstruct_pre_slot_state(read_json(final_state_path), previous_normalized)
    intraday_result, advanced_state = calculate_intraday(current_input, seeded_state)
    expected_intraday = read_json(expected_intraday_path)
    intraday_matches = intraday_result == expected_intraday
    atomic_write_json(output_root / "intraday-calculated.json", intraday_result)
    atomic_write_json(output_root / "intraday-state.json", advanced_state)

    intraday_card = build_card(current_normalized, previous_normalized)
    validate_card(intraday_card)
    atomic_write_json(output_root / "intraday-card.json", intraday_card)

    close_trend = calculate_close(read_json(close_input_path))
    expected_close_trend = read_json(expected_close_trend_path)
    close_trend_matches = close_trend == expected_close_trend
    atomic_write_json(output_root / "close-trend-calculated.json", close_trend)

    close_normalized = read_json(close_normalized_path)
    close_card = build_card(close_normalized, None)
    validate_card(close_card)
    atomic_write_json(output_root / "close-card-legacy.json", close_card)

    # Exercise pending_send -> retry -> completed using the historical close card.
    delivery_package = {
        "report_id": f"historical-{token}-close",
        "card_inputs": [close_normalized],
        "delivery": {},
    }
    delivery_path = output_root / "close-delivery-package.json"
    persist_delivery = lambda value: atomic_write_json(delivery_path, value)
    first_attempt_failed = False
    try:
        deliver_report_package(
            delivery_package,
            persist=persist_delivery,
            sender=lambda card: (_ for _ in ()).throw(DeliveryError("simulated_failure", "simulated")),
        )
    except DeliveryError:
        first_attempt_failed = True
    first_delivery_status = delivery_package["delivery"].get("status")
    delivered_cards: list[dict[str, Any]] = []
    deliver_report_package(
        delivery_package,
        persist=persist_delivery,
        sender=lambda card: delivered_cards.append(copy.deepcopy(card)) or {"success": True},
    )

    intraday_plan = plan_poll(datetime.fromisoformat(f"{trade_date}T15:00:00+08:00"), empty_manifest(trade_date))
    close_manifest = empty_manifest(trade_date)
    close_manifest["slots"]["15:00:intraday"] = {
        "planned_time": "15:00", "mode": "intraday", "status": "completed"
    }
    close_plan = plan_poll(datetime.fromisoformat(f"{trade_date}T15:10:00+08:00"), close_manifest)

    industry_5d_path = state_root / "a-share-monitor-calculated" / f"{token}-industry-5d.json"
    stock_5d_path = state_root / "a-share-monitor-calculated" / f"{token}-stock-5d.json"
    missing_close_components = [
        name for name, path in (("industry_5d", industry_5d_path), ("stock_5d", stock_5d_path))
        if not path.is_file()
    ]
    close_top10 = read_json(close_top10_path)

    slot_store = SlotStateStore(output_root / "run-slots" / f"{token}.json", trade_date)
    intraday_task = SlotTask("15:00", "intraday")
    slot_store.begin(intraday_task, datetime.fromisoformat(f"{trade_date}T15:00:00+08:00"))
    slot_store.complete(
        intraday_task,
        datetime.fromisoformat(f"{trade_date}T15:00:01+08:00"),
        wind_data_time=current_normalized.get("wind_data_time"),
        delivery="dry_run_success",
        artifacts={"card_sha256": card_hash(intraday_card)},
    )
    close_task = SlotTask("15:10", "close")
    slot_store.begin(close_task, datetime.fromisoformat(f"{trade_date}T15:10:00+08:00"))
    if missing_close_components:
        slot_store.fail(
            close_task,
            datetime.fromisoformat(f"{trade_date}T15:10:01+08:00"),
            "data",
            {"code": "missing_historical_components", "components": missing_close_components},
            {"legacy_card_sha256": card_hash(close_card)},
        )
    summary = {
        "simulation": True,
        "external_wind_called": False,
        "external_feishu_called": False,
        "trade_date": trade_date,
        "intraday": {
            "planned_time": "15:00",
            "route_modes": [item["mode"] for item in intraday_plan.get("tasks", [])],
            "calculation_matches_saved_result": intraday_matches,
            "card_valid": True,
            "card_sha256": card_hash(intraday_card),
            "dry_run_delivery": "success",
            "index_total_yuan": intraday_result.get("index_total", {}).get("current_yuan"),
        },
        "close": {
            "planned_time": "15:10",
            "route_modes": [item["mode"] for item in close_plan.get("tasks", [])],
            "top10_count": len(close_top10.get("top10", [])),
            "trend_calculation_matches_saved_result": close_trend_matches,
            "legacy_card_valid": True,
            "legacy_card_sha256": card_hash(close_card),
            "delivery_retry": {
                "first_attempt_failed": first_attempt_failed,
                "status_after_failure": first_delivery_status,
                "final_status": delivery_package["delivery"].get("status"),
                "retry_sent_count": len(delivered_cards),
                "retried_card_unchanged": bool(delivered_cards) and delivered_cards[0] == close_card,
            },
            "unified_close_status": "ready" if not missing_close_components else "pending_missing_historical_components",
            "missing_components": missing_close_components,
        },
        "artifacts": {
            "intraday_calculated": str(output_root / "intraday-calculated.json"),
            "intraday_card": str(output_root / "intraday-card.json"),
            "close_trend_calculated": str(output_root / "close-trend-calculated.json"),
            "close_card_legacy": str(output_root / "close-card-legacy.json"),
            "close_delivery_package": str(delivery_path),
            "run_slots": str(output_root / "run-slots" / f"{token}.json"),
        },
    }
    atomic_write_json(output_root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kstock-root", required=True, type=Path)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    summary = replay(args.kstock_root, args.trade_date, args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
