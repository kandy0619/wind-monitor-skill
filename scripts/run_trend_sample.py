#!/usr/bin/env python3
"""Collect one four-board Wind trend sample and persist it through KStock."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kstock_api_client import KStockAPIError, KStockClient
from kstock_workflow import persist_and_deliver
from run_intraday import IntradayRunner, SHANGHAI, _contains_trade_date, _yi
from wind_cli_client import WindCliError
from wind_response_adapter import ADAPTER_VERSION, AdaptationError


BOARDS = ("沪市主板", "深市主板", "创业板", "科创板")


def build_question(board: str, trade_date: str, planned_time: str) -> str:
    return (
        f"仅返回股票明细表。日期为{trade_date}，股票范围严格限定为{board}A股，不得使用指数成分股替代。"
        "按当日主力净流入额降序取最高5只不同股票，恰好返回5行。"
        "每只股票唯一一行，返回板块内排名、证券简称、Wind代码、Wind行业完整名称、涨跌幅、"
        "当日主力净流入额、当日主力净流入占比、最新交易日和交易时间。"
        f"本次计划取样档位{planned_time}，唯一调用时刻{datetime.now(SHANGHAI).isoformat()}，"
        "按调用时可得累计值实时重新计算，禁止复用其它响应。"
    )


class TrendSampleRunner(IntradayRunner):
    def __init__(self, client: KStockClient, project_root: Path, trade_date: str,
                 planned_time: str, run_kind: str) -> None:
        super().__init__(client, project_root, trade_date, planned_time, run_kind, None)
        self.request_template_version = "trend-sample-v1"

    def claim(self) -> dict[str, Any]:
        self.client.capabilities()
        self.context = self.client.context()
        result = self.client.claim_run({
            "task_id": self.context["task_id"],
            "trade_date": self.trade_date,
            "planned_time": self.planned_time,
            "mode": "trend_sample",
            "run_kind": self.run_kind,
            "report_type": None,
            "triggered_at": datetime.now(SHANGHAI).isoformat(),
            "lease_owner": "wind-monitor-skill:run-trend-sample",
            "lease_seconds": 3600,
            "skill_version": "worktree",
            "adapter_version": ADAPTER_VERSION,
        })
        self.run_id = int(result["run"]["id"])
        return result

    def execute(self) -> dict[str, Any]:
        claim = self.claim()
        if claim["run"].get("status") in {"completed", "completed_with_limits"}:
            return {"status": claim["run"]["status"], "reused": True}

        details: list[list[Any]] = []
        wind_times: list[str] = []
        limitations: list[str] = []
        for board in BOARDS:
            result, raw = self.required_query(
                "board_candidate", "stock_data", "search_stocks",
                {"question": build_question(board, self.trade_date, self.planned_time)},
                "board_candidate",
            )
            if not _contains_trade_date(raw, self.trade_date):
                raise ValueError(f"board_trade_date_unverified:{board}")
            records = result.records
            unique = {row["code"] for row in records}
            if len(unique) != len(records):
                raise ValueError(f"board_duplicate_codes:{board}")
            if len(records) > 5:
                raise ValueError(f"board_row_count_exceeded:{board}:{len(records)}")
            if not records:
                raise ValueError(f"board_no_rows:{board}")
            if len(records) < 5:
                limitations.append(f"{board}仅返回{len(records)}只")
            values = [row["main_yuan"] for row in records]
            if values != sorted(values, reverse=True):
                raise ValueError(f"board_sort_mismatch:{board}")
            for fallback_rank, row in enumerate(records, 1):
                if row["trade_date"] != self.trade_date:
                    raise ValueError(f"board_stale_trade_date:{board}")
                returned_board = row.get("board")
                if returned_board and returned_board != board:
                    raise ValueError(f"board_scope_mismatch:{board}")
                wind_times.append(str(row["data_time"]))
                details.append([
                    board, row.get("rank") or fallback_rank, row["code"], row["name"],
                    row.get("industry"), _yi(row["main_yuan"]), row["change_pct"],
                    row.get("main_ratio_pct"), row["data_time"],
                ])

        normalized = {
            "trade_date": self.trade_date,
            "planned_time": self.planned_time,
            "wind_time": max(wind_times),
            "wind_data_time": max(wind_times),
            "quality_status": "partial" if limitations else "complete",
            "stock_details": details,
            "limitations": limitations,
        }
        completed = persist_and_deliver(self.client, {
            "run_id": self.run_id,
            "report_type": "trend_sample",
            "normalized_payload": normalized,
            "with_limits": bool(limitations),
        }, dispatch=False)
        return {
            "success": True,
            "status": completed.get("run", {}).get("status"),
            "wind_calls": self.call_index,
            "boards": len(BOARDS),
            "stocks": len(details),
            "limitations": len(limitations),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--planned-time", required=True)
    parser.add_argument("--run-kind", choices=("production", "preview", "historical_replay"), default="production")
    args = parser.parse_args()
    runner = TrendSampleRunner(
        KStockClient.from_environment(args.project_root), args.project_root,
        args.trade_date, args.planned_time, args.run_kind,
    )
    try:
        result = runner.execute()
    except WindCliError as error:
        if runner.run_id:
            runner.client.mark_failure(runner.run_id, "fetch", error.code, error.message)
        raise SystemExit(json.dumps(error.as_dict(), ensure_ascii=False))
    except AdaptationError as error:
        if runner.run_id:
            runner.client.mark_failure(runner.run_id, "adapt", error.code, error.message)
        raise SystemExit(json.dumps(error.as_dict(), ensure_ascii=False))
    except KStockAPIError as error:
        if runner.run_id:
            runner.client.mark_failure(runner.run_id, "persist", "kstock_error", str(error))
        raise SystemExit(json.dumps({"code": "kstock_error", "message": str(error)}, ensure_ascii=False))
    except Exception as error:
        if runner.run_id:
            runner.client.mark_failure(runner.run_id, "calculate", type(error).__name__, str(error))
        raise
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
