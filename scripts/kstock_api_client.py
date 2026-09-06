#!/usr/bin/env python3
"""HTTP persistence client for KStock's Wind monitor service.

Secrets are read from environment variables and are never accepted as CLI
arguments so they do not appear in process listings or automation prompts.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


try:
    from wind_monitor_contracts import CONTRACT_VERSION, validate_claim, validate_report
except ModuleNotFoundError:  # direct execution from scripts/
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from wind_monitor_contracts import CONTRACT_VERSION, validate_claim, validate_report


DEFAULT_BASE_URL = "http://127.0.0.1:8002/api/v1/internal/wind-monitor"


class KStockAPIError(RuntimeError):
    def __init__(self, status: int | None, message: str, *, retryable: bool):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class KStockClient:
    base_url: str
    token: str | None = None
    timeout_seconds: float = 20.0

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> "KStockClient":
        base_url = os.getenv("KSTOCK_WIND_MONITOR_URL", "").strip()
        if not base_url and project_root:
            config_path = project_root / "config" / "config.json"
            if config_path.is_file():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                base_url = str((config.get("wind_monitor") or {}).get("service_url") or "").strip()
        token = os.getenv("KSTOCK_AGENT_TOKEN", "").strip() or None
        return cls((base_url or DEFAULT_BASE_URL).rstrip("/"), token)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read()
                return json.loads(content.decode("utf-8")) if content else {}
        except urllib.error.HTTPError as error:
            detail = "KStock API请求失败"
            try:
                decoded = json.loads(error.read().decode("utf-8"))
                detail = str(decoded.get("detail") or detail)
            except Exception:
                pass
            raise KStockAPIError(error.code, detail, retryable=error.code >= 500 or error.code in {408, 409, 429}) from error
        except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
            raise KStockAPIError(None, "KStock服务不可用", retryable=True) from error

    def capabilities(self) -> dict[str, Any]:
        result = self._request("GET", "/capabilities")
        if CONTRACT_VERSION not in result.get("contract_versions", []):
            raise KStockAPIError(409, "KStock不支持当前Skill存储契约", retryable=False)
        if not result.get("database_ready"):
            raise KStockAPIError(503, "KStock数据库未就绪", retryable=True)
        return result

    def context(self, preferred_name: str = "监控-神龙7-全盘") -> dict[str, Any]:
        query = urllib.parse.urlencode({"preferred_name": preferred_name})
        return self._request("GET", f"/context?{query}")

    def pending_runs(self, task_id: int, trade_date: str, run_kind: str = "production") -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"task_id": task_id, "trade_date": trade_date, "run_kind": run_kind})
        return self._request("GET", f"/runs/pending?{query}").get("runs", [])

    def day_runs(self, task_id: int, trade_date: str, run_kind: str = "production") -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"task_id": task_id, "trade_date": trade_date, "run_kind": run_kind})
        return self._request("GET", f"/runs/day?{query}").get("runs", [])

    def previous_report(self, task_id: int, trade_date: str, before_time: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({
            "task_id": task_id, "trade_date": trade_date,
            "before_time": before_time, "report_type": "intraday",
        })
        return self._request("GET", f"/reports/previous?{query}").get("report")

    def observations(self, trade_date_from: str, *, trade_date_to: str | None = None,
                     entity_type: str | None = None, entity_code: str | None = None,
                     query_profile: str | None = None, run_kind: str = "production",
                     completed_only: bool = True, limit: int = 1000) -> list[dict[str, Any]]:
        params = {
            "trade_date_from": trade_date_from, "run_kind": run_kind,
            "completed_only": str(completed_only).lower(), "limit": limit,
        }
        params.update({key: value for key, value in {
            "trade_date_to": trade_date_to, "entity_type": entity_type,
            "entity_code": entity_code, "query_profile": query_profile,
        }.items() if value})
        return self._request("GET", f"/observations?{urllib.parse.urlencode(params)}").get("observations", [])

    def rankings(self, trade_date_from: str, *, trade_date_to: str | None = None,
                 ranking_type: str | None = None, entity_code: str | None = None,
                 run_kind: str = "production", completed_only: bool = True,
                 limit: int = 1000) -> list[dict[str, Any]]:
        params = {
            "trade_date_from": trade_date_from, "run_kind": run_kind,
            "completed_only": str(completed_only).lower(), "limit": limit,
        }
        params.update({key: value for key, value in {
            "trade_date_to": trade_date_to, "ranking_type": ranking_type,
            "entity_code": entity_code,
        }.items() if value})
        return self._request("GET", f"/rankings?{urllib.parse.urlencode(params)}").get("rankings", [])

    def claim_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        value.setdefault("contract_version", CONTRACT_VERSION)
        validate_claim(value)
        key = f"claim:{value['task_id']}:{value['trade_date']}:{value['planned_time']}:{value['mode']}:{value.get('run_kind','production')}"
        return self._request("POST", "/runs/claim", value, idempotency_key=key)

    def renew_lease(self, run_id: int, lease_token: str, lease_seconds: int = 900) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/lease/renew", {
            "lease_token": lease_token,
            "lease_seconds": lease_seconds,
        })

    def run(self, run_id: int) -> dict[str, Any]:
        return self._request("GET", f"/runs/{run_id}")

    def record_attempt(self, run_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        key = f"attempt:{run_id}:{payload['request_id']}:{payload.get('attempt_no', 1)}"
        return self._request("POST", f"/runs/{run_id}/attempts", payload, idempotency_key=key)

    def commit_facts(self, run_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/facts:commit", payload, idempotency_key=f"facts:{run_id}")

    def record_adapter_incident(self, run_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/adapter-incidents", payload)

    def mark_failure(self, run_id: int, stage: str, error_code: str,
                     detail_redacted: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/fail", {
            "stage": stage, "error_code": error_code, "detail_redacted": detail_redacted,
        })

    def commit_report(self, run_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        validate_report(payload)
        return self._request("POST", f"/runs/{run_id}/report:commit", payload, idempotency_key=f"report:{run_id}")

    def commit_card(self, report_id: str, card_payload: dict[str, Any], *,
                    recipient_config_version: str = "current", part_index: int = 0) -> dict[str, Any]:
        return self._request("POST", f"/reports/{urllib.parse.quote(report_id, safe='')}/card:commit", {
            "card_payload": card_payload,
            "channel": "feishu",
            "part_index": part_index,
            "recipient_config_version": recipient_config_version,
        }, idempotency_key=f"card:{report_id}:{part_index}:{recipient_config_version}")

    def dispatch_feishu(self, report_id: str, part_index: int = 0) -> dict[str, Any]:
        query = urllib.parse.urlencode({"part_index": part_index})
        return self._request("POST", f"/reports/{urllib.parse.quote(report_id, safe='')}/feishu:dispatch?{query}")

    def complete_run(self, run_id: int, *, with_limits: bool = False) -> dict[str, Any]:
        return self._request("POST", f"/runs/{run_id}/complete", {"with_limits": with_limits}, idempotency_key=f"complete:{run_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the KStock Wind monitor persistence service")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=("capabilities", "context"))
    args = parser.parse_args()
    client = KStockClient.from_environment(args.project_root)
    result = client.capabilities() if args.command == "capabilities" else client.context()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
