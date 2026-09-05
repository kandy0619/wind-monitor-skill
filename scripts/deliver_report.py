#!/usr/bin/env python3
"""Render, validate and durably deliver one logical report to Feishu."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from kstock_feishu_delivery import DeliveryError, deliver_card
from render_feishu_card import build_card


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def validate_card(card: dict[str, Any]) -> None:
    if not isinstance(card, dict):
        raise ValueError("renderer did not return a JSON object")
    header = card.get("header")
    elements = card.get("elements")
    if not isinstance(header, dict) or not isinstance(elements, list) or not elements:
        raise ValueError("generated card is missing header or elements")
    table_count = sum(1 for element in elements if isinstance(element, dict) and element.get("tag") == "table")
    if table_count > 5:
        raise ValueError("generated card exceeds the five-table layout contract")


def card_hash(card: dict[str, Any]) -> str:
    value = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def deliver_report_package(
    package: dict[str, Any],
    *,
    persist: Callable[[dict[str, Any]], None],
    sender: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    inputs = package.get("card_inputs")
    if not isinstance(inputs, list) or not inputs:
        inputs = [package]
        package["card_inputs"] = inputs
    delivery = package.setdefault("delivery", {})
    completed = {int(value) for value in delivery.get("completed_parts", [])}
    rendered = delivery.setdefault("rendered_cards", {})
    delivery["required_parts"] = len(inputs)

    cards_to_send: list[tuple[int, dict[str, Any]]] = []
    for index, card_input in enumerate(inputs, start=1):
        key = str(index)
        if index in completed:
            continue
        stored = rendered.get(key)
        card = stored.get("card") if isinstance(stored, dict) else None
        stored_hash = stored.get("sha256") if isinstance(stored, dict) else None
        if not isinstance(card, dict) or card_hash(card) != stored_hash:
            try:
                card = build_card(card_input, None)
                validate_card(card)
            except Exception as error:
                delivery.update({"status": "pending_render", "failed_part": index, "last_error": {"code": "card_render_failed", "message": str(error)}})
                persist(package)
                raise
            rendered[key] = {"sha256": card_hash(card), "card": card}
            delivery.update({"status": "pending_render", "failed_part": None, "last_error": None})
            persist(package)
        else:
            validate_card(card)
        cards_to_send.append((index, card))

    delivery.update({"status": "pending_send", "failed_part": None, "last_error": None})
    persist(package)
    for index, card in cards_to_send:
        try:
            sender(card)
        except Exception as error:
            code = error.code if isinstance(error, DeliveryError) else "feishu_delivery_failed"
            delivery.update({"status": "pending_send", "failed_part": index, "last_error": {"code": code, "message": str(error)}})
            persist(package)
            raise
        completed.add(index)
        delivery["completed_parts"] = sorted(completed)
        delivery.update({"status": "delivering" if len(completed) < len(inputs) else "completed", "failed_part": None, "last_error": None})
        persist(package)
    return package


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    package = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        raise SystemExit("report JSON must be an object")

    def persist(value: dict[str, Any]) -> None:
        atomic_write_json(args.report, value)

    try:
        deliver_report_package(
            package,
            persist=persist,
            sender=lambda card: deliver_card(card, args.project_root),
        )
    except Exception as error:
        code = error.code if isinstance(error, DeliveryError) else "report_delivery_failed"
        raise SystemExit(json.dumps({"code": code, "message": str(error)}, ensure_ascii=False))
    print(json.dumps({"success": True, "report_id": package.get("report_id"), "parts": package["delivery"]["completed_parts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
