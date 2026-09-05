#!/usr/bin/env python3
"""Validate and run a model-generated mapping adapter with restricted builtins.

Generated code may only inspect the supplied response and return a mapping
candidate. It never returns market values directly; the deterministic adapter
re-reads all values from the original Wind response and performs final checks.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from wind_response_adapter import AdaptationError, adapt_response, atomic_write_json


FORBIDDEN_NODES = (
    ast.AsyncFunctionDef,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.Global,
    ast.Import,
    ast.ImportFrom,
    ast.Lambda,
    ast.Nonlocal,
    ast.Raise,
    ast.Try,
    ast.While,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)
FORBIDDEN_NAMES = {
    "__import__", "breakpoint", "compile", "eval", "exec", "globals", "input",
    "locals", "open", "vars",
}
SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


def validate_source(source: str) -> ast.Module:
    tree = ast.parse(source, mode="exec")
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != "adapt":
        raise ValueError("generated adapter must define exactly one function named adapt")
    if any(not isinstance(node, (ast.FunctionDef, ast.Expr)) for node in tree.body):
        raise ValueError("generated adapter may not contain module-level statements")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            raise ValueError(f"forbidden syntax in generated adapter: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ValueError(f"forbidden name in generated adapter: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("private and dunder attribute access is forbidden")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "adapt":
            raise ValueError("recursive generated adapters are forbidden")
    return tree


def execute_candidate(source: str, raw: Any) -> dict[str, Any]:
    tree = validate_source(source)
    namespace: dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
    exec(compile(tree, "<generated-wind-adapter>", "exec"), namespace, namespace)
    candidate = namespace["adapt"](raw)
    if not isinstance(candidate, dict):
        raise ValueError("generated adapter must return a mapping candidate object")
    return candidate


def validate_generated_adapter(source: str, raw: Any, profile: str) -> dict[str, Any]:
    candidate = execute_candidate(source, raw)
    result = adapt_response(raw, profile, candidate)
    payload = result.as_dict()
    payload["generated_adapter_validated"] = True
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.adapter.read_text(encoding="utf-8")
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        result = validate_generated_adapter(source, raw, args.profile)
    except (AdaptationError, ValueError) as error:
        payload = error.as_dict() if isinstance(error, AdaptationError) else {"code": "adapter_unsafe", "message": str(error)}
        raise SystemExit(json.dumps(payload, ensure_ascii=False))
    atomic_write_json(args.output, result)


if __name__ == "__main__":
    main()
