#!/usr/bin/env python3
"""Portable, secret-safe bridge to the installed Wind MCP skill CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


class WindCliError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class WindSkillRuntime:
    root: Path
    cli: Path
    node: str


def discovery_candidates(project_root: Path | None = None) -> list[Path]:
    if project_root:
        # A monitor run with a project root is deliberately project-isolated.
        return [project_root / ".agents" / "skills" / "wind-mcp-skill"]
    roots: list[Path] = []
    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    roots.extend(
        [
            profile / ".codex" / "skills" / "wind-mcp-skill",
            profile / ".agents" / "skills" / "wind-mcp-skill",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve(strict=False)).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def discover_runtime(project_root: Path | None = None) -> WindSkillRuntime:
    node = shutil.which("node")
    if not node:
        raise WindCliError("runtime_missing", "Node.js executable was not found")
    checked = []
    for root in discovery_candidates(project_root):
        checked.append(str(root))
        skill_file = root / "SKILL.md"
        cli = root / "scripts" / "cli.mjs"
        if skill_file.is_file() and cli.is_file():
            return WindSkillRuntime(root=root.resolve(), cli=cli.resolve(), node=node)
    raise WindCliError(
        "wind_skill_missing",
        "wind-mcp-skill was not found in the project-local or supported user skill directories",
        {"checked_count": len(checked)},
    )


def _parse_stdout(stdout: str) -> Any:
    value = stdout.strip()
    if not value:
        raise WindCliError("empty_response", "Wind CLI returned an empty response")
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise WindCliError(
            "invalid_response_json",
            "Wind CLI stdout was not valid JSON",
            {"line": error.lineno, "column": error.colno},
        ) from error


def _safe_error_payload(stdout: str, stderr: str, returncode: int) -> WindCliError:
    try:
        envelope = json.loads(stdout.strip()) if stdout.strip() else {}
    except json.JSONDecodeError:
        envelope = {}
    error = envelope.get("error") if isinstance(envelope, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "wind_cli_error")
        message = str(error.get("message") or "Wind CLI call failed")
        safe_details = {
            key: error[key]
            for key in ("retry", "circuit_breaker", "correction")
            if key in error
        }
        safe_details["returncode"] = returncode
        return WindCliError(code, message, safe_details)
    # Do not echo arbitrary stderr/stdout: either can contain request diagnostics.
    return WindCliError(
        "wind_cli_error",
        "Wind CLI call failed without a structured error envelope",
        {"returncode": returncode, "stderr_present": bool(stderr.strip())},
    )


def call_wind(
    server_type: str,
    tool_name: str,
    params: dict[str, Any],
    *,
    project_root: Path | None = None,
    timeout_seconds: int = 180,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Any:
    runtime = discover_runtime(project_root)
    request_name = f"request-{uuid.uuid4().hex}.json"
    request_path = runtime.root / "scripts" / request_name
    request_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    command: Sequence[str] = (
        runtime.node,
        str(runtime.cli),
        "call",
        server_type,
        tool_name,
        f"@scripts/{request_name}",
    )
    try:
        completed = runner(
            command,
            cwd=runtime.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise WindCliError("wind_timeout", "Wind CLI call timed out", {"timeout_seconds": timeout_seconds}) from error
    except OSError as error:
        raise WindCliError("runtime_error", "Wind CLI process could not be started", {"type": type(error).__name__}) from error
    finally:
        request_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise _safe_error_payload(completed.stdout, completed.stderr, completed.returncode)
    return _parse_stdout(completed.stdout)


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("server_type")
    parser.add_argument("tool_name")
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    params = json.loads(args.params.read_text(encoding="utf-8"))
    if not isinstance(params, dict):
        raise SystemExit("params JSON must be an object")
    try:
        result = call_wind(
            args.server_type,
            args.tool_name,
            params,
            project_root=args.project_root,
            timeout_seconds=args.timeout,
        )
    except WindCliError as error:
        raise SystemExit(json.dumps(error.as_dict(), ensure_ascii=False))
    atomic_write(args.output, result)


if __name__ == "__main__":
    main()
