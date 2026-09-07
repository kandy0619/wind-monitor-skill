#!/usr/bin/env python3
"""Persist one Skill stage through KStock without local production state files.

The CLI reads a JSON object from stdin. Credentials remain in environment
variables and never appear in command-line arguments or output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from kstock_api_client import KStockAPIError, KStockClient
    from render_feishu_card import build_card
    from deliver_report import validate_card
except ModuleNotFoundError:  # imported as scripts.kstock_workflow
    from scripts.kstock_api_client import KStockAPIError, KStockClient
    from scripts.render_feishu_card import build_card
    from scripts.deliver_report import validate_card

try:
    from wind_monitor_contracts import extract_close_facts, extract_intraday_facts, extract_trend_sample_facts
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from wind_monitor_contracts import extract_close_facts, extract_intraday_facts, extract_trend_sample_facts


def facts_from_normalized(report_type: str, normalized: dict[str, Any]) -> dict[str, Any]:
    if report_type == "intraday":
        facts = extract_intraday_facts(normalized)
    elif report_type == "close_summary":
        facts = extract_close_facts(normalized)
    elif report_type == "trend_sample":
        facts = normalized.get("facts")
        if facts is None and normalized.get("observations") is not None:
            facts = {"observations": normalized.get("observations", []), "rankings": normalized.get("rankings", [])}
        if facts is None:
            facts = extract_trend_sample_facts(normalized["trade_date"], normalized)
    else:
        raise ValueError(f"unsupported report_type: {report_type}")
    return {
        "wind_data_time": normalized.get("wind_data_time"),
        "quality_status": normalized.get("quality_status", "complete"),
        "observations": facts.get("observations", []),
        "rankings": facts.get("rankings", []),
    }


def render_card(normalized: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    card = build_card(normalized, previous)
    validate_card(card)
    return card


def persist_and_deliver(
    client: KStockClient,
    bundle: dict[str, Any],
    *,
    dispatch: bool = True,
) -> dict[str, Any]:
    """Commit facts/report/card and deliver the exact persisted card.

    Wind request attempts must be recorded before calling this function. A
    failed render or delivery deliberately leaves the run incomplete so a
    later stateless invocation can resume from KStock.
    """
    run_id = int(bundle["run_id"])
    report_type = bundle["report_type"]
    normalized = bundle["normalized_payload"]
    client.commit_facts(run_id, facts_from_normalized(report_type, normalized))

    if report_type == "trend_sample":
        return client.complete_run(run_id, with_limits=bundle.get("with_limits", False))

    report_id = bundle["report_id"]
    report_payload = {
        "report_id": report_id,
        "report_type": report_type,
        "planned_time": normalized["planned_time"],
        "normalized_payload": normalized,
        "calculated_payload": bundle.get("calculated_payload", {}),
        "concise_markdown": bundle.get("concise_markdown"),
        "audit_markdown": bundle.get("audit_markdown"),
        "required_parts": 1,
        "quality_status": normalized.get("quality_status", "complete"),
        "card_contract_version": bundle.get("card_contract_version", "1.0"),
    }
    client.commit_report(run_id, report_payload)
    previous = bundle.get("previous_successful_payload") if report_type == "intraday" else None
    try:
        card = render_card(normalized, previous)
    except Exception as error:
        client.mark_failure(run_id, "render", "card_render_failed", str(error))
        raise
    client.commit_card(
        report_id,
        card,
        recipient_config_version=bundle.get("recipient_config_version", "current"),
        part_index=0,
    )
    if not dispatch:
        return {"success": True, "report_id": report_id, "status": "pending_send"}
    try:
        sent = client.dispatch_feishu(report_id, 0)
    except KStockAPIError as error:
        client.mark_failure(run_id, "send", "feishu_dispatch_uncertain", str(error))
        raise
    if not sent.get("success"):
        client.mark_failure(run_id, "send", "feishu_dispatch_failed", "飞书卡片发送失败")
        raise KStockAPIError(None, "飞书卡片发送失败，已保留持久化数据等待重试", retryable=True)
    completed = client.complete_run(run_id, with_limits=bundle.get("with_limits", False))
    return {
        "success": True,
        "report_id": report_id,
        "status": completed.get("run", {}).get("status"),
    }


def resume_report(client: KStockClient, run_id: int) -> dict[str, Any]:
    """Resume rendering/delivery from KStock without another Wind request."""
    aggregate = client.run(run_id)
    run = aggregate["run"]
    if run.get("status") in {"completed", "completed_with_limits"}:
        return {"success": True, "report_id": None, "status": run["status"], "reused": True}
    report = aggregate.get("report")
    if not report:
        raise KStockAPIError(409, "该运行尚无可恢复报告", retryable=False)
    report_id = report["report_id"]
    deliveries = report.get("deliveries") or []
    if not deliveries:
        previous = None
        if report["report_type"] == "intraday":
            prior = client.previous_report(run["task_id"], run["trade_date"], run["planned_time"])
            previous = prior.get("normalized_payload") if prior else None
        try:
            card = render_card(report["normalized_payload"], previous)
        except Exception as error:
            client.mark_failure(run_id, "render", "card_render_failed", str(error))
            raise
        context = client.context()
        client.commit_card(
            report_id, card,
            recipient_config_version=context["recipient_config_version"], part_index=0,
        )
    try:
        sent = client.dispatch_feishu(report_id, 0)
    except KStockAPIError as error:
        client.mark_failure(run_id, "send", "feishu_dispatch_uncertain", str(error))
        raise
    if not sent.get("success"):
        client.mark_failure(run_id, "send", "feishu_dispatch_failed", "飞书卡片发送失败")
        raise KStockAPIError(None, "飞书卡片发送失败，已保留持久化数据等待重试", retryable=True)
    completed = client.complete_run(run_id, with_limits=report.get("quality_status") != "complete")
    return {
        "success": True, "report_id": report_id,
        "status": completed.get("run", {}).get("status"), "reused": True,
    }


def _stdin_object() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("stdin JSON must be an object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Persist wind-monitor-skill stages through KStock")
    parser.add_argument(
        "command",
        choices=("claim", "attempt", "facts", "incident", "failure", "report", "resume", "dispatch", "complete"),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-dispatch", action="store_true", help="persist immutable card for outbox delivery")
    args = parser.parse_args()
    payload = _stdin_object()
    client = KStockClient.from_environment(args.project_root)
    client.capabilities()
    if args.command == "claim":
        result = client.claim_run(payload)
    elif args.command == "attempt":
        result = client.record_attempt(int(payload.pop("run_id")), payload)
    elif args.command == "facts":
        result = client.commit_facts(int(payload["run_id"]), facts_from_normalized(payload["report_type"], payload["normalized_payload"]))
    elif args.command == "incident":
        result = client.record_adapter_incident(int(payload.pop("run_id")), payload)
    elif args.command == "failure":
        result = client.mark_failure(
            int(payload["run_id"]), payload["stage"], payload["error_code"], payload.get("detail_redacted"),
        )
    elif args.command == "report":
        result = persist_and_deliver(client, payload, dispatch=not args.no_dispatch)
    elif args.command == "resume":
        result = resume_report(client, int(payload["run_id"]))
    elif args.command == "dispatch":
        result = client.dispatch_feishu(payload["report_id"], int(payload.get("part_index", 0)))
    else:
        result = client.complete_run(int(payload["run_id"]), with_limits=bool(payload.get("with_limits", False)))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
