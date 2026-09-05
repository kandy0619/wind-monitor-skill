#!/usr/bin/env python3
"""Deterministic slot routing and durable state for wind-monitor-skill."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 2
INTRADAY_TIMES = (
    "09:30", "09:40", "09:50",
    "10:00", "10:10", "10:20", "10:30", "10:40", "10:50",
    "11:00", "11:10", "11:20", "11:30",
    "13:00", "13:10", "13:20", "13:30", "13:40", "13:50",
    "14:00", "14:10", "14:20", "14:30", "14:40", "14:50",
    "15:00",
)
SAMPLE_TIMES = ("10:00", "10:30", "11:00", "11:15", "13:30", "13:45", "14:00", "14:30", "14:45")
PURE_SAMPLE_TIMES = {"11:15", "13:45", "14:45"}
TRIGGER_TIMES = tuple(sorted(set(INTRADAY_TIMES) | set(SAMPLE_TIMES) | {"15:10"}))
TERMINAL_STATUSES = {"completed", "completed_with_limits"}


@dataclass(frozen=True)
class SlotTask:
    planned_time: str
    mode: str

    def __post_init__(self) -> None:
        if self.mode == "intraday" and self.planned_time not in INTRADAY_TIMES:
            raise ValueError(f"invalid intraday slot: {self.planned_time}")
        if self.mode == "close" and self.planned_time != "15:10":
            raise ValueError(f"close mode is reserved for 15:10, got {self.planned_time}")
        if self.planned_time == "15:10" and self.mode != "close":
            raise ValueError(f"15:10 is reserved for close mode, got {self.mode}")
        if self.mode == "trend_sample" and self.planned_time not in SAMPLE_TIMES:
            raise ValueError(f"invalid trend sample slot: {self.planned_time}")
        if self.mode not in {"intraday", "close", "trend_sample"}:
            raise ValueError(f"unsupported slot mode: {self.mode}")

    @property
    def key(self) -> str:
        return f"{self.planned_time}:{self.mode}"

    def as_dict(self) -> dict[str, Any]:
        value = {"key": self.key, "planned_time": self.planned_time, "mode": self.mode}
        if self.mode == "intraday":
            value["report_type"] = "intraday"
        elif self.mode == "close":
            value["report_type"] = "close_summary"
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


def tasks_for_trigger(planned_time: str) -> list[SlotTask]:
    if planned_time == "15:10":
        return [SlotTask(planned_time, "close")]
    tasks = []
    if planned_time in INTRADAY_TIMES:
        tasks.append(SlotTask(planned_time, "intraday"))
    if planned_time in SAMPLE_TIMES:
        tasks.append(SlotTask(planned_time, "trend_sample"))
    return tasks


def parse_now(value: str | None = None) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("--at must include a timezone offset")
        return parsed.astimezone(TIMEZONE)
    return datetime.now(TIMEZONE)


def trigger_for_now(now: datetime) -> str | None:
    local = now.astimezone(TIMEZONE)
    current = local.strftime("%H:%M")
    return current if current in TRIGGER_TIMES else None


def empty_manifest(trade_date: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "timezone": "Asia/Shanghai",
        "slots": {},
    }


def _migrate_entry(key: str, value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    planned_time = value.get("planned_time") or key.split(":", 1)[0]
    mode = value.get("mode") or (key.split(":", 1)[1] if ":" in key else "intraday")
    mode = {"sample": "trend_sample", "intraday_sample": "intraday"}.get(mode, mode)
    status = value.get("status")
    if not status:
        status = "completed" if value.get("completed") or value.get("completed_at") else "pending"
    entry = {
        "planned_time": planned_time,
        "mode": mode,
        "status": status,
        "triggered_at": value.get("triggered_at"),
        "last_attempt_at": value.get("last_attempt_at"),
        "completed_at": value.get("completed_at"),
        "wind_data_time": value.get("wind_data_time"),
        "failure_stage": value.get("failure_stage"),
        "last_error": value.get("last_error"),
        "artifacts": value.get("artifacts", {}),
        "delivery": value.get("delivery") or ("feishu_success" if value.get("feishu_delivered") else None),
    }
    return f"{planned_time}:{mode}", entry


def migrate_manifest(raw: dict[str, Any], trade_date: str) -> dict[str, Any]:
    if raw.get("schema_version") == SCHEMA_VERSION and isinstance(raw.get("slots"), dict):
        return raw
    migrated = empty_manifest(raw.get("trade_date") or trade_date)
    slots = raw.get("slots")
    if isinstance(slots, dict):
        for planned_time, modes in slots.items():
            if not isinstance(modes, dict):
                continue
            for mode, value in modes.items():
                if isinstance(value, dict):
                    key, entry = _migrate_entry(f"{planned_time}:{mode}", value)
                    migrated["slots"][key] = entry
    else:
        for key, value in raw.items():
            if key == "pending" or not isinstance(value, dict) or ":" not in key:
                continue
            migrated_key, entry = _migrate_entry(key, value)
            migrated["slots"][migrated_key] = entry
    return migrated


class SlotStateStore:
    def __init__(self, path: Path, trade_date: str):
        self.path = path
        self.trade_date = trade_date

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_manifest(self.trade_date)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return migrate_manifest(raw, self.trade_date)

    def save(self, manifest: dict[str, Any]) -> None:
        atomic_write_json(self.path, manifest)

    def begin(self, task: SlotTask, now: datetime) -> dict[str, Any]:
        manifest = self.load()
        existing = manifest["slots"].get(task.key)
        if existing and existing.get("status") in TERMINAL_STATUSES:
            return existing
        entry = existing or {"planned_time": task.planned_time, "mode": task.mode, "artifacts": {}}
        entry.update({"status": "fetching", "triggered_at": entry.get("triggered_at") or now.isoformat(), "last_attempt_at": now.isoformat()})
        manifest["slots"][task.key] = entry
        self.save(manifest)
        return entry

    def fail(self, task: SlotTask, now: datetime, stage: str, error: dict[str, Any], artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest = self.load()
        entry = manifest["slots"].setdefault(task.key, {"planned_time": task.planned_time, "mode": task.mode, "artifacts": {}})
        entry.update({
            "status": f"pending_{stage}",
            "last_attempt_at": now.isoformat(),
            "failure_stage": stage,
            "last_error": error,
        })
        entry.setdefault("artifacts", {}).update(artifacts or {})
        self.save(manifest)
        return entry

    def complete(
        self,
        task: SlotTask,
        now: datetime,
        *,
        wind_data_time: str | None,
        delivery: str | None,
        artifacts: dict[str, Any] | None = None,
        with_limits: bool = False,
    ) -> dict[str, Any]:
        manifest = self.load()
        entry = manifest["slots"].setdefault(task.key, {"planned_time": task.planned_time, "mode": task.mode, "artifacts": {}})
        entry.update({
            "status": "completed_with_limits" if with_limits else "completed",
            "completed_at": now.isoformat(),
            "wind_data_time": wind_data_time,
            "delivery": delivery,
            "failure_stage": None,
            "last_error": None,
        })
        entry.setdefault("artifacts", {}).update(artifacts or {})
        self.save(manifest)
        return entry


def pending_tasks(manifest: dict[str, Any]) -> list[SlotTask]:
    pending = []
    for entry in manifest.get("slots", {}).values():
        if str(entry.get("status", "")).startswith("pending"):
            pending.append(SlotTask(entry["planned_time"], entry["mode"]))
    return sorted(pending, key=lambda task: (task.planned_time, task.mode))


def plan_poll(now: datetime, manifest: dict[str, Any]) -> dict[str, Any]:
    local = now.astimezone(TIMEZONE)
    if local.weekday() >= 5:
        return {"action": "silent", "reason": "weekend", "tasks": []}
    current_trigger = trigger_for_now(local)
    if current_trigger is None:
        return {"action": "silent", "reason": "invalid_time", "tasks": []}
    pending = pending_tasks(manifest)
    current = tasks_for_trigger(current_trigger) if current_trigger else []
    planned = []
    seen = set()
    for task in pending + current:
        entry = manifest.get("slots", {}).get(task.key, {})
        if task.key in seen or entry.get("status") in TERMINAL_STATUSES:
            continue
        planned.append(task)
        seen.add(task.key)
    if not planned:
        return {"action": "silent", "reason": "no_due_or_already_completed", "tasks": []}
    return {
        "action": "run",
        "trade_date": local.date().isoformat(),
        "timezone": "Asia/Shanghai",
        "requires_wind_trading_day_confirmation": True,
        "tasks": [task.as_dict() for task in planned],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=Path(".codex/automation-state"))
    parser.add_argument("--at", help="timezone-aware ISO timestamp; intended for tests and replay")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("poll")
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--trade-date")
    args = parser.parse_args()
    now = parse_now(args.at)
    trade_date = args.trade_date if args.command == "migrate" and args.trade_date else now.date().isoformat()
    path = args.state_root / "a-share-monitor-run-slots" / f"{trade_date.replace('-', '')}.json"
    store = SlotStateStore(path, trade_date)
    manifest = store.load()
    if args.command == "migrate":
        store.save(manifest)
        print(json.dumps({"status": "migrated", "path": str(path), "schema_version": SCHEMA_VERSION}, ensure_ascii=False))
    else:
        print(json.dumps(plan_poll(now, manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
