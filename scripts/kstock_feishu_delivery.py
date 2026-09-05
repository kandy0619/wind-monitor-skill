#!/usr/bin/env python3
"""Deliver generated cards through KStock without exposing the chat identifier."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


PREFERRED_TASK_NAME = "监控-神龙7-全盘"


class DeliveryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def select_chat_task(tasks: Iterable[Any]) -> Any:
    eligible = [task for task in tasks if bool(getattr(task, "enabled", False)) and str(getattr(task, "feishu_chat_id", "") or "").strip()]
    preferred = [task for task in eligible if getattr(task, "name", None) == PREFERRED_TASK_NAME]
    candidates = preferred or eligible
    if not candidates:
        raise DeliveryError("recipient_missing", "No enabled MonitorTask has a Feishu chat configured")
    distinct = {str(getattr(task, "feishu_chat_id")).strip() for task in candidates}
    if len(distinct) != 1:
        raise DeliveryError("recipient_ambiguous", "Multiple eligible Feishu chats are configured")
    return candidates[0]


class _RecipientRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if "receive_id=%s" in str(record.msg) and isinstance(record.args, tuple) and record.args:
            record.args = ("<redacted>", *record.args[1:])
        return True


@contextmanager
def _redact_backend_logger(logger: logging.Logger):
    redactor = _RecipientRedactionFilter()
    logger.addFilter(redactor)
    try:
        yield
    finally:
        logger.removeFilter(redactor)


def _load_kstock(project_root: Path):
    backend = (project_root / "backend").resolve()
    if not (backend / "database.py").is_file() or not (backend / "models.py").is_file():
        raise DeliveryError("kstock_runtime_missing", "KStock backend runtime was not found")
    sys.path.insert(0, str(backend))
    try:
        database = importlib.import_module("database")
        models = importlib.import_module("models")
        feishu_bot = importlib.import_module("services.feishu_bot")
    except Exception as error:
        raise DeliveryError("kstock_import_failed", f"KStock backend import failed: {type(error).__name__}") from error
    return database, models, feishu_bot


def deliver_card(card: dict[str, Any], project_root: Path) -> dict[str, Any]:
    database, models, feishu_bot = _load_kstock(project_root)
    try:
        with database.get_db_session() as session:
            tasks = session.query(models.MonitorTask).filter(models.MonitorTask.enabled.is_(True)).all()
            task = select_chat_task(tasks)
            chat_id = str(task.feishu_chat_id).strip()
        with _redact_backend_logger(feishu_bot.logger):
            result = feishu_bot.send_card(chat_id, card, receive_id_type="chat_id")
    except DeliveryError:
        raise
    except Exception as error:
        raise DeliveryError("delivery_runtime_failed", f"Feishu delivery failed: {type(error).__name__}") from error
    if not isinstance(result, dict) or not result.get("success"):
        code = result.get("code") if isinstance(result, dict) else None
        suffix = f" (code={code})" if code is not None else ""
        raise DeliveryError("feishu_delivery_failed", f"Feishu rejected the card{suffix}")
    return {"success": True, "receive_id_type": "chat_id"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--card", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    card = json.loads(args.card.read_text(encoding="utf-8"))
    if not isinstance(card, dict):
        raise SystemExit("card JSON must be an object")
    try:
        result = deliver_card(card, args.project_root)
    except DeliveryError as error:
        raise SystemExit(json.dumps(error.as_dict(), ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
