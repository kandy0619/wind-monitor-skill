#!/usr/bin/env python3
"""Execute one complete Wind intraday report through KStock.

This is the deterministic implementation used by the Skill after slot routing.
It owns Wind retrieval, raw-attempt persistence, strict industry reconciliation,
normalization, report/card persistence and idempotent Feishu dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from industry_pipeline import (
        build_scoped_amount_question,
        build_scoped_stock_question,
        exact_batch_industry_stocks,
        merge_industry_amounts,
        scoped_industry_amount,
        scoped_industry_stocks,
    )
    from intraday_report import build_intraday_markdown
    from kstock_api_client import KStockAPIError, KStockClient
    from kstock_workflow import persist_and_deliver
    from wind_cli_client import WindCliError, call_wind
    from wind_response_adapter import ADAPTER_VERSION, AdaptationError, adapt_response, response_hash
except ModuleNotFoundError:
    from scripts.industry_pipeline import (
        build_scoped_amount_question,
        build_scoped_stock_question,
        exact_batch_industry_stocks,
        merge_industry_amounts,
        scoped_industry_amount,
        scoped_industry_stocks,
    )
    from scripts.intraday_report import build_intraday_markdown
    from scripts.kstock_api_client import KStockAPIError, KStockClient
    from scripts.kstock_workflow import persist_and_deliver
    from scripts.wind_cli_client import WindCliError, call_wind
    from scripts.wind_response_adapter import ADAPTER_VERSION, AdaptationError, adapt_response, response_hash


SHANGHAI = ZoneInfo("Asia/Shanghai")
STOCK_CODES = ("688981.SH", "300502.SZ", "300476.SZ")
INDEXES = (
    ("上证", "000001.SH"),
    ("深证", "399001.SZ"),
    ("创业板", "399006.SZ"),
    ("科创板", "000688.SH"),
)
STOCK_FIELDS = (
    "最新交易日,交易时间,中文简称,涨跌幅,当日主力净流入额,当日主力净流入占比,"
    "该日机构资金净流入额,该日大户资金净流入额,该日中户资金净流入额,该日散户资金净流入额"
)
INDEX_FIELDS = "最新交易日,交易时间,中文简称,当日主力净流入额,当日主力净流入占比"


def _contains_trade_date(raw: Any, trade_date: str) -> bool:
    text = json.dumps(raw, ensure_ascii=False)
    compact = trade_date.replace("-", "")
    year, month, day = (int(part) for part in trade_date.split("-"))
    return any(value in text for value in (trade_date, compact, f"{year}年{month}月{day}日"))


def _yi(value: Any) -> float | None:
    return None if value is None else float(value) / 100_000_000


def apply_comparison_deltas(
    current: dict[str, Any], previous: dict[str, Any] | None,
) -> None:
    if not previous:
        current["period_delta_yuan"] = None
        current["baseline_delta_yuan"] = None
        return
    current_total = float(current["index_total_yuan"])
    previous_total = float(previous["index_total_yuan"])
    current["period_delta_yuan"] = current_total - previous_total
    previous_baseline_delta = previous.get("baseline_delta_yuan")
    baseline_total = (
        previous_total
        if previous_baseline_delta is None
        else previous_total - float(previous_baseline_delta)
    )
    current["baseline_delta_yuan"] = current_total - baseline_total


class IntradayRunner:
    def __init__(
        self,
        client: KStockClient,
        project_root: Path,
        trade_date: str,
        planned_time: str,
        run_kind: str,
        simulation_label: str | None,
    ) -> None:
        self.client = client
        self.project_root = project_root
        self.trade_date = trade_date
        self.planned_time = planned_time
        self.run_kind = run_kind
        self.simulation_label = simulation_label
        self.run_id = 0
        self.context: dict[str, Any] = {}
        self.call_index = 0
        self.limitations: list[str] = []
        self.request_template_version = "intraday-v2"

    def claim(self) -> dict[str, Any]:
        self.client.capabilities()
        self.context = self.client.context()
        result = self.client.claim_run({
            "task_id": self.context["task_id"],
            "trade_date": self.trade_date,
            "planned_time": self.planned_time,
            "mode": "intraday",
            "run_kind": self.run_kind,
            "report_type": "intraday",
            "triggered_at": datetime.now(SHANGHAI).isoformat(),
            "lease_owner": "wind-monitor-skill:run-intraday",
            "lease_seconds": 3600,
            "skill_version": "worktree",
            "adapter_version": ADAPTER_VERSION,
        })
        self.run_id = int(result["run"]["id"])
        return result

    def query(
        self,
        query_profile: str,
        server_type: str,
        tool_name: str,
        params: dict[str, Any],
        adapter_profile: str,
        attempt_no: int = 1,
    ):
        self.call_index += 1
        # A stateless retry can legitimately call Wind again for the same run.
        # A random suffix prevents collision with an earlier persisted attempt
        # whose response hash may differ as the market advances.
        request_id = f"{query_profile}-{self.call_index:03d}-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        contract = {"server_type": server_type, "tool_name": tool_name, "params": params}
        try:
            raw = call_wind(server_type, tool_name, params, project_root=self.project_root)
        except WindCliError as error:
            self.client.record_attempt(self.run_id, {
                "request_id": request_id,
                "query_profile": query_profile,
                "request_kind": tool_name,
                "attempt_no": attempt_no,
                "request_contract": contract,
                "error_code": error.code,
                "error_detail_redacted": error.message,
                "adapter_version": ADAPTER_VERSION,
                "duration_ms": int((time.monotonic() - started) * 1000),
            })
            raise
        saved = self.client.record_attempt(self.run_id, {
            "request_id": request_id,
            "query_profile": query_profile,
            "request_kind": tool_name,
            "attempt_no": attempt_no,
            "request_contract": contract,
            "request_template_version": self.request_template_version,
            "raw_response": raw,
            "response_hash": response_hash(raw),
            "response_metadata": {"called_at": datetime.now(SHANGHAI).isoformat()},
            "adapter_version": ADAPTER_VERSION,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        try:
            normalized = adapt_response(raw, adapter_profile)
        except AdaptationError as error:
            self.client.record_adapter_incident(self.run_id, {
                "request_attempt_id": saved["attempt_id"],
                "query_profile": query_profile,
                "error_code": error.code,
                "validation_result": error.as_dict(),
                "status": "pending",
            })
            raise
        return normalized, raw

    def required_query(
        self,
        query_profile: str,
        server_type: str,
        tool_name: str,
        params: dict[str, Any],
        adapter_profile: str,
    ):
        """Retry a required Wind query at most three times."""
        last_error: Exception | None = None
        for attempt_no in range(1, 4):
            try:
                return self.query(
                    query_profile, server_type, tool_name, params,
                    adapter_profile, attempt_no,
                )
            except (WindCliError, AdaptationError) as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def _industry_a1(self):
        call_time = datetime.now(SHANGHAI).isoformat()
        question = (
            f"仅返回行业汇总表，禁止返回任何个股。日期为{self.trade_date}，范围为全部A股，采用Wind行业分类。"
            "按当日主力净流入额降序取最高5个行业，再按当日主力净流入额升序取最低5个行业，按此顺序恰好返回10行。"
            "每个行业唯一一行，只返回Wind行业完整名称和主力净流入额，不返回证券简称或Wind代码。"
            f"本次计划档位{self.planned_time}，唯一调用时刻{call_time}，按调用时可得累计值实时重新计算，禁止复用其它响应。"
        )
        result, raw = self.required_query(
            "industry_a1", "analytics_data", "get_financial_data",
            {"question": question}, "industry_summary",
        )
        if not _contains_trade_date(raw, self.trade_date):
            raise ValueError("industry_a1_trade_date_unverified")
        records = result.records
        if len(records) != 10 or len({row["industry"] for row in records}) != 10:
            raise ValueError("industry_a1_row_count_mismatch")
        if [row["net_yuan"] for row in records[:5]] != sorted((row["net_yuan"] for row in records[:5]), reverse=True):
            raise ValueError("industry_a1_inflow_sort_mismatch")
        if [row["net_yuan"] for row in records[5:]] != sorted(row["net_yuan"] for row in records[5:]):
            raise ValueError("industry_a1_outflow_sort_mismatch")
        return records

    def _complete_amounts(self, a1_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        amounts, missing = merge_industry_amounts(a1_records)
        for full_name in missing:
            last_error: Exception | None = None
            for attempt_no in range(1, 4):
                question = build_scoped_amount_question(full_name, self.trade_date)
                try:
                    result, raw = self.query(
                        "industry_a2_scoped", "analytics_data", "get_financial_data",
                        {"question": question}, "industry_summary", attempt_no,
                    )
                    if not _contains_trade_date(raw, self.trade_date):
                        raise ValueError("industry_a2_trade_date_unverified")
                    scoped = scoped_industry_amount(full_name, result.records)
                    amounts, remaining = merge_industry_amounts(amounts.values(), [scoped] if scoped else [])
                    if full_name not in remaining:
                        last_error = None
                        break
                    last_error = ValueError(f"industry_amount_incomplete:{full_name}")
                except (WindCliError, AdaptationError, ValueError) as error:
                    last_error = error
            if last_error is not None:
                self.limitations.append(full_name)
        return amounts

    def _complete_industry_stocks(self, industry_names: list[str]) -> dict[str, list[dict[str, Any]]]:
        call_time = datetime.now(SHANGHAI).isoformat()
        question = (
            f"仅返回个股明细表，禁止返回行业汇总。日期为{self.trade_date}，股票范围严格限定为以下Wind行业完整成分："
            f"{'；'.join(industry_names)}。对每个行业分别按当日主力净流入额降序取最高3只不同股票。"
            "每只股票唯一一行，返回Wind行业完整名称、行业内排名、证券简称、Wind代码、主力净流入额和涨跌幅。"
            f"本次计划档位{self.planned_time}，唯一调用时刻{call_time}，禁止复用其它响应。"
        )
        batch, raw = self.required_query(
            "industry_b_batch", "analytics_data", "get_financial_data",
            {"question": question}, "industry_stock",
        )
        if not _contains_trade_date(raw, self.trade_date):
            raise ValueError("industry_b_trade_date_unverified")
        grouped, missing = exact_batch_industry_stocks(industry_names, batch.records)
        for full_name in missing:
            accepted: list[dict[str, Any]] = []
            for attempt_no in range(1, 4):
                scoped_question = build_scoped_stock_question(full_name, self.trade_date)
                scoped_question += (
                    f" 本次计划档位{self.planned_time}，唯一调用时刻{datetime.now(SHANGHAI).isoformat()}，"
                    "按调用时可得累计值实时重新计算，禁止复用其它响应。"
                )
                try:
                    result, scoped_raw = self.query(
                        "industry_b_scoped", "stock_data", "search_stocks",
                        {"question": scoped_question}, "industry_stock", attempt_no,
                    )
                except (WindCliError, AdaptationError):
                    continue
                if not _contains_trade_date(scoped_raw, self.trade_date):
                    raise ValueError("industry_b_scoped_trade_date_unverified")
                accepted = scoped_industry_stocks(full_name, result.records)
                if len(accepted) == 3:
                    break
            if len(accepted) != 3:
                raise ValueError(f"industry_stock_top3_incomplete:{full_name}:{len(accepted)}")
            grouped[full_name] = accepted
        return grouped

    def execute(self, dispatch: bool) -> dict[str, Any]:
        claim = self.claim()
        if claim["run"].get("status") in {"completed", "completed_with_limits"}:
            return {"status": claim["run"]["status"], "reused": True}
        stock_result, _ = self.required_query(
            "intraday_stock", "stock_data", "get_stock_price_indicators",
            {"windcode": ",".join(STOCK_CODES), "indexes": STOCK_FIELDS}, "stock",
        )
        index_result, _ = self.required_query(
            "intraday_index", "index_data", "get_index_price_indicators",
            {"windcode": ",".join(code for _, code in INDEXES), "indexes": INDEX_FIELDS}, "index",
        )
        if {row["code"] for row in stock_result.records} != set(STOCK_CODES):
            raise ValueError("stock_row_count_mismatch")
        if {row["code"] for row in index_result.records} != {code for _, code in INDEXES}:
            raise ValueError("index_row_count_mismatch")
        returned_dates = {row["trade_date"] for row in stock_result.records + index_result.records}
        if returned_dates != {self.trade_date}:
            raise ValueError("stale_trade_date")

        a1 = self._industry_a1()
        industry_names = [row["industry"] for row in a1]
        amounts = self._complete_amounts(a1)
        leaders = self._complete_industry_stocks(industry_names)
        stock_by_code = {row["code"]: row for row in stock_result.records}
        index_by_code = {row["code"]: row for row in index_result.records}
        times = [str(row["data_time"]) for row in stock_result.records + index_result.records if row.get("data_time")]
        wind_time = max(times)
        current: dict[str, Any] = {
            "trade_date": self.trade_date,
            "planned_time": self.planned_time,
            "report_type": "intraday",
            "wind_data_time": wind_time,
            "quality_status": "complete",
            "stocks": [], "indexes": [],
            "industry_inflow_top5": [], "industry_outflow_top5": [],
        }
        if self.simulation_label:
            current["simulation_label"] = self.simulation_label
        for code in STOCK_CODES:
            row = stock_by_code[code]
            current["stocks"].append([
                row["name"], code, row["change_pct"], row["main_yuan"], row.get("main_ratio_pct"),
                row.get("institution_yuan"), row.get("large_yuan"), row.get("medium_yuan"), row.get("retail_yuan"),
            ])
        for label, code in INDEXES:
            row = index_by_code[code]
            current["indexes"].append([label, row["name"], code, row["main_yuan"], row.get("main_ratio_pct")])
        current["index_total_yuan"] = sum(float(row[3]) for row in current["indexes"])
        previous = None
        if self.run_kind == "production":
            previous_report = self.client.previous_report(
                self.context["task_id"], self.trade_date, self.planned_time,
            )
            previous = previous_report.get("normalized_payload") if previous_report else None
        apply_comparison_deltas(current, previous)
        if self.limitations:
            current["quality_status"] = "partial"
            current["data_warning"] = (
                "部分行业主力流入/流出额经3次Wind补查仍未返回；净额、排名和个股Top 3不受影响，"
                "审计表缺失字段标注Wind未返回：" + "；".join(self.limitations)
            )
        for source, key in ((a1[:5], "industry_inflow_top5"), (a1[5:], "industry_outflow_top5")):
            for item in source:
                amount = amounts[item["industry"]]
                top3 = [{
                    "name": row["name"], "code": row["code"],
                    "main_net_inflow_yi": _yi(row["main_yuan"]), "change_pct": row["change_pct"],
                } for row in leaders[item["industry"]]]
                current[key].append([
                    item["industry"], _yi(amount.get("gross_inflow_yuan")),
                    _yi(amount.get("gross_outflow_yuan")), _yi(item["net_yuan"]), top3,
                ])
        concise, audit = build_intraday_markdown(current)
        result = persist_and_deliver(self.client, {
            "run_id": self.run_id,
            "report_id": f"wind-{self.run_kind}-{self.trade_date.replace('-', '')}-{self.planned_time.replace(':', '')}-v2",
            "report_type": "intraday",
            "normalized_payload": current,
            "calculated_payload": {
                "index_total_yuan": current["index_total_yuan"],
                "period_delta_yuan": None,
                "baseline_delta_yuan": None,
                "all_required_data_complete": not self.limitations,
            },
            "concise_markdown": concise,
            "audit_markdown": audit,
            "recipient_config_version": self.context["recipient_config_version"],
            "with_limits": bool(self.limitations),
            "previous_successful_payload": previous,
        }, dispatch=dispatch)
        return {
            **result,
            "wind_calls": self.call_index,
            "stocks": len(current["stocks"]),
            "indexes": len(current["indexes"]),
            "industries": len(industry_names),
            "industry_stocks": sum(len(row[4]) for key in ("industry_inflow_top5", "industry_outflow_top5") for row in current[key]),
            "concise_markdown": concise,
            "audit_markdown": audit,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--planned-time", required=True)
    parser.add_argument("--run-kind", choices=("production", "preview", "historical_replay"), default="production")
    parser.add_argument("--simulation-label")
    parser.add_argument("--dispatch", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    runner = IntradayRunner(
        KStockClient.from_environment(args.project_root), args.project_root,
        args.trade_date, args.planned_time, args.run_kind, args.simulation_label,
    )
    try:
        result = runner.execute(args.dispatch)
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
            try:
                aggregate = runner.client.run(runner.run_id)
                run = aggregate["run"]
                if (
                    run.get("status") not in {"completed", "completed_with_limits"}
                    and run.get("failure_stage") != "send"
                ):
                    runner.client.mark_failure(runner.run_id, "send", "kstock_delivery_error", str(error))
            except KStockAPIError:
                # The durable server state is authoritative and will be
                # reconciled by the next fixed-slot invocation.
                pass
        raise SystemExit(json.dumps({"code": "kstock_error", "message": str(error)}, ensure_ascii=False))
    except Exception as error:
        if runner.run_id:
            runner.client.mark_failure(runner.run_id, "calculate", type(error).__name__, str(error))
        raise
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
