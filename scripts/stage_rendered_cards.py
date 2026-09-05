#!/usr/bin/env python3
"""Attach renderer-produced card JSON to a durable report package unchanged."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deliver_report import atomic_write_json, card_hash, validate_card


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--cards", nargs="+", required=True, type=Path)
    args = parser.parse_args()
    package = json.loads(args.report.read_text(encoding="utf-8"))
    inputs = package.get("card_inputs") or [package]
    if len(args.cards) != len(inputs):
        raise SystemExit("rendered card count does not match report parts")
    rendered = {}
    for index, path in enumerate(args.cards, start=1):
        card = json.loads(path.read_text(encoding="utf-8"))
        validate_card(card)
        rendered[str(index)] = {"sha256": card_hash(card), "card": card}
    delivery = package.setdefault("delivery", {})
    delivery.update({
        "required_parts": len(inputs),
        "completed_parts": [],
        "rendered_cards": rendered,
        "status": "pending_send",
        "failed_part": None,
        "last_error": None,
    })
    atomic_write_json(args.report, package)
    print({"validated_parts": len(rendered), "status": "pending_send"})


if __name__ == "__main__":
    main()
