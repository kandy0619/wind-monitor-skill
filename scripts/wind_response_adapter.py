#!/usr/bin/env python3
"""Normalize Wind CLI responses into stable business records.

The adapter is intentionally independent from any one Wind response envelope. It
first discovers record-shaped data, then maps business fields by semantic aliases.
When deterministic mapping cannot prove a field, it emits a structured fallback
request for the Codex model instead of treating the value as missing market data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ADAPTER_VERSION = "1.0.0"
SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|secret|token|authorization|recipient|receive[_-]?id|"
    r"feishu[_-]?(?:chat|user)[_-]?id)",
    re.IGNORECASE,
)
UNIT_FACTORS = {
    "元": 1.0,
    "万元": 10_000.0,
    "百万元": 1_000_000.0,
    "亿元": 100_000_000.0,
}


class AdaptationError(ValueError):
    """A classified error that is safe to persist in slot state."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    aliases: tuple[str, ...]
    kind: str = "text"
    required: bool = True


@dataclass(frozen=True)
class Profile:
    name: str
    fields: tuple[FieldSpec, ...]
    min_rows: int = 1
    max_rows: int | None = None


PROFILES = {
    "stock": Profile(
        "stock",
        (
            FieldSpec("code", ("wind代码", "windcode", "证券代码", "股票代码", "代码")),
            FieldSpec("name", ("中文简称", "证券简称", "股票简称", "简称", "名称")),
            FieldSpec("trade_date", ("最新交易日", "交易日期", "交易日", "日期")),
            FieldSpec("data_time", ("交易时间", "数据时间", "更新时间", "时间")),
            FieldSpec("change_pct", ("涨跌幅", "当日涨跌幅"), "number"),
            FieldSpec("main_yuan", ("当日主力净流入额", "主力净流入额", "主力资金净流入", "主力净额"), "amount"),
            FieldSpec("main_ratio_pct", ("当日主力净流入占比", "主力净流入占比", "主力占比"), "number", False),
            FieldSpec("institution_yuan", ("该日机构资金净流入额", "机构资金净流入额", "机构净流入"), "amount", False),
            FieldSpec("large_yuan", ("该日大户资金净流入额", "大户资金净流入额", "大户净流入"), "amount", False),
            FieldSpec("medium_yuan", ("该日中户资金净流入额", "中户资金净流入额", "中户净流入"), "amount", False),
            FieldSpec("retail_yuan", ("该日散户资金净流入额", "散户资金净流入额", "散户净流入"), "amount", False),
        ),
    ),
    "index": Profile(
        "index",
        (
            FieldSpec("code", ("wind代码", "windcode", "指数代码", "证券代码", "代码")),
            FieldSpec("name", ("中文简称", "指数简称", "简称", "名称")),
            FieldSpec("trade_date", ("最新交易日", "交易日期", "交易日", "日期")),
            FieldSpec("data_time", ("交易时间", "数据时间", "更新时间", "时间")),
            FieldSpec("main_yuan", ("当日主力净流入额", "主力净流入额", "主力资金净流入", "主力净额"), "amount"),
            FieldSpec("main_ratio_pct", ("当日主力净流入占比", "主力净流入占比", "主力占比"), "number", False),
        ),
    ),
    "industry_summary": Profile(
        "industry_summary",
        (
            FieldSpec("industry", ("wind行业完整名称", "wind行业", "行业名称", "行业")),
            FieldSpec("gross_inflow_yuan", ("主力资金流入额", "主力流入额", "资金流入额", "流入额", "inflow", "in"), "amount", False),
            FieldSpec("gross_outflow_yuan", ("主力资金流出额", "主力流出额", "资金流出额", "流出额", "outflow", "out"), "amount", False),
            FieldSpec("net_yuan", ("主力净流入额", "主力资金净流入", "资金净流入额", "净流入额", "净额", "net"), "amount"),
            FieldSpec("rank", ("排名", "名次", "rank"), "integer", False),
        ),
        max_rows=5,
    ),
    "industry_daily_full": Profile(
        "industry_daily_full",
        (
            FieldSpec("industry", ("wind行业完整名称", "wind行业", "行业名称", "行业")),
            FieldSpec("net_yuan", ("主力净流入额", "主力资金净流入", "资金净流入额", "净流入额", "净额", "net"), "amount"),
        ),
    ),
    "industry_stock": Profile(
        "industry_stock",
        (
            FieldSpec("industry", ("wind行业完整名称", "wind行业", "行业名称", "行业")),
            FieldSpec("code", ("wind代码", "windcode", "证券代码", "股票代码", "代码")),
            FieldSpec("name", ("中文简称", "证券简称", "股票简称", "简称", "名称")),
            FieldSpec("main_yuan", ("当日主力净流入额", "主力净流入额", "主力资金净流入", "净流入额", "净额"), "amount"),
            FieldSpec("change_pct", ("涨跌幅", "当日涨跌幅"), "number"),
            FieldSpec("rank", ("排名", "名次", "rank"), "integer", False),
        ),
        max_rows=3,
    ),
    "board_candidate": Profile(
        "board_candidate",
        (
            FieldSpec("board", ("上市板块", "来源板块", "板块"), required=False),
            FieldSpec("code", ("wind代码", "windcode", "证券代码", "股票代码", "代码")),
            FieldSpec("name", ("中文简称", "证券简称", "股票简称", "简称", "名称")),
            FieldSpec("trade_date", ("最新交易日", "交易日期", "交易日", "日期")),
            FieldSpec("data_time", ("交易时间", "数据时间", "更新时间", "时间")),
            FieldSpec("main_yuan", ("当日主力净流入额", "主力净流入额", "主力资金净流入", "净流入额"), "amount"),
            FieldSpec("change_pct", ("涨跌幅", "当日涨跌幅"), "number"),
            FieldSpec("main_ratio_pct", ("当日主力净流入占比", "主力净流入占比", "主力占比"), "number", False),
            FieldSpec("industry", ("wind行业完整名称", "wind行业", "所属行业", "行业"), required=False),
            FieldSpec("rank", ("排名", "名次", "rank"), "integer", False),
        ),
        max_rows=5,
    ),
}


@dataclass
class AdaptationResult:
    profile: str
    records: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    adapter_mode: str = "deterministic"
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "adapter_version": ADAPTER_VERSION,
            "adapter_mode": self.adapter_mode,
            "profile": self.profile,
            "records": self.records,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def response_hash(value: Any) -> str:
    payload = json.dumps(redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def persist_raw_response(
    raw: Any,
    root: Path,
    *,
    trade_date: str,
    slot: str,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    safe = redact(raw)
    envelope = {
        "schema_version": 1,
        "trade_date": trade_date,
        "planned_slot": slot,
        "request_id": request_id,
        "response_hash": response_hash(safe),
        "metadata": redact(metadata or {}),
        "response": safe,
    }
    target = root / "a-share-monitor-raw" / trade_date.replace("-", "") / slot.replace(":", "") / f"{request_id}.json"
    atomic_write_json(target, envelope)
    return target


def _parse_embedded_json(value: str) -> Any:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def unwrap_response(raw: Any) -> Any:
    current = raw
    for _ in range(8):
        if isinstance(current, dict) and isinstance(current.get("content"), list):
            texts = [item.get("text") for item in current["content"] if isinstance(item, dict) and item.get("text")]
            if texts:
                parsed = _parse_embedded_json(texts[0])
                if parsed is not texts[0]:
                    current = parsed
                    continue
        if isinstance(current, dict):
            for key in ("data", "result", "results", "records", "items"):
                candidate = current.get(key)
                if candidate not in (None, [], {}):
                    current = candidate
                    break
            else:
                return current
            continue
        return current
    return current


def discover_records(raw: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if isinstance(raw, dict) and isinstance(raw.get("content"), list):
        texts = [
            item.get("text")
            for item in raw["content"]
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if texts and isinstance(_parse_embedded_json(texts[0]), str):
            message = texts[0].strip().lower()
            if message in {"没找到数据", "暂无数据", "无数据", "no data", "not found"}:
                return [], {}
            raise AdaptationError("shape_mismatch", "Wind returned non-structured text content")
    value = unwrap_response(raw)
    units: dict[str, str] = {}
    if isinstance(value, list):
        if not value:
            return [], units
        if all(
            isinstance(item, dict)
            and isinstance(item.get("columns") or item.get("fields"), list)
            and isinstance(item.get("rows") or item.get("values"), list)
            for item in value
        ):
            records: list[dict[str, Any]] = []
            for table in value:
                table_records, table_units = discover_records(table)
                records.extend(table_records)
                units.update(table_units)
            return records, units
        if value and all(isinstance(row, dict) for row in value):
            return list(value), units
        raise AdaptationError("shape_mismatch", "Wind result list is not record-shaped")
    if not isinstance(value, dict):
        raise AdaptationError("shape_mismatch", "Wind response does not contain structured records")

    columns = value.get("columns") or value.get("fields")
    rows = value.get("rows") or value.get("values")
    if isinstance(columns, list) and isinstance(rows, list):
        names = []
        for column in columns:
            if isinstance(column, dict):
                name = column.get("name") or column.get("display_name") or column.get("title")
                unit = column.get("unit")
                if name and unit:
                    units[str(name)] = str(unit)
            else:
                name = column
            names.append(str(name))
        records = []
        for row in rows:
            if isinstance(row, dict):
                records.append(row)
            elif isinstance(row, (list, tuple)) and len(row) == len(names):
                records.append(dict(zip(names, row)))
            else:
                raise AdaptationError("shape_mismatch", "Wind table row does not match its columns")
        return records, units

    dict_lists = {key: item for key, item in value.items() if isinstance(item, list)}
    lengths = {len(item) for item in dict_lists.values()}
    if dict_lists and len(lengths) == 1:
        size = lengths.pop()
        return [{key: item[index] for key, item in dict_lists.items()} for index in range(size)], units
    if value and all(not isinstance(item, (dict, list)) for item in value.values()):
        return [value], units
    raise AdaptationError("shape_mismatch", "Wind object has no unambiguous row collection")


def normalize_label(value: str) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[（(].*?[)）]", "", text)
    return re.sub(r"[\s_\-—:：/]+", "", text)


def unit_from_label(value: str) -> str | None:
    match = re.search(r"(亿元|百万元|万元|元)", str(value))
    return match.group(1) if match else None


def canonical_unit(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    if "亿元" in text:
        return "亿元"
    if "百万元" in text or "百万人民币元" in text:
        return "百万元"
    if "万元" in text:
        return "万元"
    if "元" in text:
        return "元"
    return None


def _source_key(record: dict[str, Any], spec: FieldSpec) -> str | None:
    normalized = {normalize_label(key): str(key) for key in record}
    matches = []
    for alias in spec.aliases:
        candidate = normalized.get(normalize_label(alias))
        if candidate and candidate not in matches:
            matches.append(candidate)
    if not matches:
        for normalized_key, source_key in normalized.items():
            if spec.name == "gross_inflow_yuan" and "净流入" in normalized_key:
                continue
            if spec.name == "gross_outflow_yuan" and "净流出" in normalized_key:
                continue
            if any(normalized_key.endswith(normalize_label(alias)) for alias in spec.aliases):
                matches.append(source_key)
    if len(matches) > 1:
        raise AdaptationError(
            "field_ambiguous", f"multiple source fields match {spec.name}", {"matches": matches}
        )
    return matches[0] if matches else None


def _number(value: Any, *, integer: bool = False) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise AdaptationError("type_mismatch", "boolean is not a numeric Wind value")
    if isinstance(value, (int, float)):
        return int(value) if integer else float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"--", "-", "null", "None", "N/A"}:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise AdaptationError("type_mismatch", f"cannot parse numeric value: {value}") from exc
    return int(parsed) if integer else parsed


def _convert(value: Any, spec: FieldSpec, source_key: str, units: dict[str, str]) -> Any:
    if spec.kind == "text":
        return None if value is None else str(value)
    if spec.kind == "integer":
        return _number(value, integer=True)
    number = _number(value)
    if number is None or spec.kind == "number":
        return number
    unit = canonical_unit(units.get(source_key)) or unit_from_label(source_key)
    if unit not in UNIT_FACTORS:
        raise AdaptationError(
            "unit_ambiguous",
            f"amount field {source_key} has no supported unit metadata",
            {"field": spec.name, "source_key": source_key},
        )
    return number * UNIT_FACTORS[unit]


def _mapping_from_candidate(candidate: dict[str, Any], profile: Profile) -> dict[str, dict[str, Any]]:
    if candidate.get("profile") != profile.name:
        raise AdaptationError("candidate_invalid", "candidate profile does not match requested profile")
    fields = candidate.get("fields")
    if not isinstance(fields, dict):
        raise AdaptationError("candidate_invalid", "candidate must contain a fields object")
    allowed = {spec.name for spec in profile.fields}
    unknown = set(fields) - allowed
    if unknown:
        raise AdaptationError("candidate_invalid", "candidate contains unknown target fields", {"fields": sorted(unknown)})
    return fields


def _records_from_candidate(raw: Any, candidate: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not candidate or "record_path" not in candidate:
        return discover_records(raw)
    path = candidate["record_path"]
    if not isinstance(path, list) or any(not isinstance(part, (str, int)) for part in path):
        raise AdaptationError("candidate_invalid", "candidate record_path must be a string/integer array")
    current = raw
    try:
        for part in path:
            current = current[part]
    except (KeyError, IndexError, TypeError) as error:
        raise AdaptationError("candidate_invalid", "candidate record_path does not exist", {"record_path": path}) from error
    return discover_records(current)


def adapt_response(raw: Any, profile_name: str, candidate: dict[str, Any] | None = None) -> AdaptationResult:
    if profile_name not in PROFILES:
        raise AdaptationError("profile_unknown", f"unknown profile: {profile_name}")
    profile = PROFILES[profile_name]
    records, units = _records_from_candidate(raw, candidate)
    if not records:
        raise AdaptationError("no_data", "Wind returned an empty record set")
    candidate_fields = _mapping_from_candidate(candidate, profile) if candidate else {}
    normalized_records = []
    provenance = []

    for row_index, record in enumerate(records):
        normalized: dict[str, Any] = {}
        evidence: dict[str, Any] = {}
        for spec in profile.fields:
            proposal = candidate_fields.get(spec.name, {})
            source_key = proposal.get("source_key") if isinstance(proposal, dict) else None
            if source_key is not None and source_key not in record:
                raise AdaptationError(
                    "candidate_invalid", f"candidate source key does not exist: {source_key}", {"row": row_index}
                )
            source_key = source_key or _source_key(record, spec)
            if source_key is None:
                if spec.required:
                    raise AdaptationError(
                        "field_missing", f"required field cannot be mapped: {spec.name}",
                        {"row": row_index, "available_fields": list(record)},
                    )
                normalized[spec.name] = None
                evidence[spec.name] = {"status": "not_returned"}
                continue
            local_units = dict(units)
            if isinstance(proposal, dict) and proposal.get("unit"):
                local_units[source_key] = str(proposal["unit"])
            raw_value = record[source_key]
            value = _convert(raw_value, spec, source_key, local_units)
            if spec.required and value is None:
                raise AdaptationError(
                    "field_missing", f"required Wind field is null: {spec.name}",
                    {"row": row_index, "source_key": source_key},
                )
            normalized[spec.name] = value
            evidence[spec.name] = {
                "status": "returned" if value is not None else "null_returned",
                "source_key": source_key,
                "raw_value": raw_value,
                "unit": local_units.get(source_key) or unit_from_label(source_key),
            }
        normalized_records.append(normalized)
        provenance.append({"row": row_index, "fields": evidence})

    warnings = []
    if len(normalized_records) == 100:
        if profile.name == "industry_daily_full":
            warnings.append(
                {
                    "code": "possible_truncation",
                    "message": "Wind returned exactly 100 rows; union with the reverse-sorted query before use",
                    "row_count": 100,
                }
            )
        else:
            raise AdaptationError(
                "truncated",
                "Wind returned exactly 100 rows; split the query before using the result",
                {"row_count": 100},
            )
    if profile.max_rows is not None and len(normalized_records) > profile.max_rows:
        raise AdaptationError(
            "row_count_mismatch",
            f"profile {profile.name} returned more than {profile.max_rows} rows",
            {"actual_rows": len(normalized_records)},
        )
    if len(normalized_records) < profile.min_rows:
        raise AdaptationError("no_data", f"profile {profile.name} returned no usable rows")
    return AdaptationResult(
        profile=profile.name,
        records=normalized_records,
        provenance=provenance,
        adapter_mode="llm_fallback" if candidate else "deterministic",
        warnings=warnings,
    )


def fallback_request(raw: Any, profile_name: str, error: AdaptationError) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    try:
        records, _ = discover_records(raw)
        available = sorted({str(key) for row in records for key in row})
    except AdaptationError:
        available = []
    return {
        "request_type": "wind_mapping_fallback",
        "adapter_version": ADAPTER_VERSION,
        "profile": profile_name,
        "response_hash": response_hash(raw),
        "failure": error.as_dict(),
        "available_fields": available,
        "target_fields": [
            {"name": spec.name, "kind": spec.kind, "required": spec.required, "aliases": list(spec.aliases)}
            for spec in profile.fields
        ],
        "candidate_contract": {
            "profile": profile_name,
            "fields": {"target_field": {"source_key": "exact raw field name", "unit": "元|万元|百万元|亿元 when needed"}},
            "rule": "Only map fields that are explicitly present. Never infer a missing value.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--fallback-request", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8")) if args.candidate else None
    try:
        result = adapt_response(raw, args.profile, candidate)
    except AdaptationError as error:
        if args.fallback_request:
            atomic_write_json(args.fallback_request, fallback_request(raw, args.profile, error))
        raise SystemExit(json.dumps(error.as_dict(), ensure_ascii=False))
    atomic_write_json(args.output, result.as_dict())


if __name__ == "__main__":
    main()
