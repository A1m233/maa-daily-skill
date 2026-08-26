#!/usr/bin/env python3
"""Run one maa-cli process and emit a bounded MaaCore evidence report."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


REPORT_PREFIX = "MAA_EVIDENCE_JSON="
EVIDENCE_UNAVAILABLE_EXIT = 74
TASKCHAIN_ERROR_EXIT = 75
CALLBACK_MARKER = "Assistant::append_callback | "
TASKCHAIN_EVENTS = {"TaskChainStart", "TaskChainCompleted", "TaskChainError"}
KNOWN_SUBCOMMANDS = {
    "startup",
    "closedown",
    "run",
    "fight",
    "copilot",
    "ssscopilot",
    "paradoxcopilot",
    "roguelike",
    "reclamation",
}
LEVEL_PATTERN = re.compile(r"\]\[(TRC|DBG|INF|WRN|ERR|CRT)\]\[")
TAIL_WINDOW = 512


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _count_newlines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            count += chunk.count(b"\n")
    return count


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "size": 0,
            "newline_count": 0,
            "identity": None,
            "tail_start": 0,
            "tail_sha256": None,
        }

    stat = path.stat()
    tail_start = max(stat.st_size - TAIL_WINDOW, 0)
    with path.open("rb") as handle:
        handle.seek(tail_start)
        tail_sha256 = hashlib.sha256(handle.read()).hexdigest()
    return {
        "exists": True,
        "size": stat.st_size,
        "newline_count": _count_newlines(path),
        "identity": [stat.st_dev, stat.st_ino],
        "tail_start": tail_start,
        "tail_sha256": tail_sha256,
    }


def _discover_core_log(executable: str) -> Path:
    result = subprocess.run(
        [executable, "dir", "log", "--batch"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("maa dir log failed")

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("maa dir log returned no path")
    return Path(lines[-1]) / "asst.log"


def _read_appended(path: Path, start: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read()


def _safe_subcommand(command: Sequence[str]) -> str | None:
    for argument in command[1:]:
        if argument in KNOWN_SUBCOMMANDS:
            return argument
    return None


def _parse_callbacks(appended: bytes, start_line: int) -> dict[str, Any]:
    callback_counts: Counter[str] = Counter()
    extra_info_types: Counter[str] = Counter()
    level_counts: Counter[str] = Counter()
    internal_error_lines: list[int] = []
    subtask_error_lines: list[int] = []
    task_chains: list[dict[str, Any]] = []
    callback_parse_error_lines: list[int] = []

    text = appended.decode("utf-8", errors="replace")
    for line_number, line in enumerate(text.splitlines(), start=start_line):
        level_match = LEVEL_PATTERN.search(line)
        if level_match:
            level = level_match.group(1)
            level_counts[level] += 1
            if level in {"ERR", "CRT"}:
                internal_error_lines.append(line_number)

        if CALLBACK_MARKER not in line:
            continue

        event_and_payload = line.split(CALLBACK_MARKER, 1)[1]
        event, separator, payload_text = event_and_payload.partition(" ")
        callback_counts[event] += 1
        if event == "SubTaskError":
            subtask_error_lines.append(line_number)

        if not separator:
            callback_parse_error_lines.append(line_number)
            continue

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            callback_parse_error_lines.append(line_number)
            continue

        if event in TASKCHAIN_EVENTS:
            task_chains.append(
                {
                    "event": event,
                    "taskchain": payload.get("taskchain"),
                    "taskid": payload.get("taskid"),
                    "line": line_number,
                }
            )
        elif event == "SubTaskExtraInfo":
            what = payload.get("what")
            if isinstance(what, str) and what:
                extra_info_types[what] += 1

    return {
        "level_counts": dict(sorted(level_counts.items())),
        "internal_error_lines": internal_error_lines,
        "callback_counts": dict(sorted(callback_counts.items())),
        "callback_parse_error_lines": callback_parse_error_lines,
        "subtask_error_lines": subtask_error_lines,
        "task_chains": task_chains,
        "extra_info_types": dict(sorted(extra_info_types.items())),
    }


def _collect_evidence(
    path: Path, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "log_file": str(path),
        "before_size": before["size"],
        "after_size": after["size"],
        "start_line": None,
        "end_line": None,
        "state": "missing",
        "level_counts": {},
        "internal_error_lines": [],
        "callback_counts": {},
        "callback_parse_error_lines": [],
        "subtask_error_lines": [],
        "task_chains": [],
        "extra_info_types": {},
    }
    if not after["exists"]:
        return base

    identity_changed = (
        before["exists"]
        and before["identity"] is not None
        and after["identity"] is not None
        and before["identity"] != after["identity"]
    )
    prior_tail_changed = False
    if before["exists"] and before["size"]:
        with path.open("rb") as handle:
            handle.seek(before["tail_start"])
            prior_tail = handle.read(before["size"] - before["tail_start"])
        prior_tail_changed = (
            hashlib.sha256(prior_tail).hexdigest() != before["tail_sha256"]
        )
    if identity_changed or after["size"] < before["size"] or prior_tail_changed:
        base["state"] = "rotated"
        return base
    if after["size"] == before["size"]:
        base["state"] = "unchanged"
        return base

    appended = _read_appended(path, before["size"])
    start_line = before["newline_count"] + 1
    parsed = _parse_callbacks(appended, start_line)
    base.update(parsed)
    base.update(
        {
            "state": "bounded",
            "start_line": start_line,
            "end_line": start_line + max(len(appended.splitlines()) - 1, 0),
        }
    )
    return base


def _emit_report(report: dict[str, Any], report_file: Path | None) -> int:
    if report_file is not None:
        try:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            report["report_file_error"] = type(error).__name__
            if report["wrapper_exit_code"] == 0:
                report["wrapper_exit_code"] = EVIDENCE_UNAVAILABLE_EXIT
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(f"{REPORT_PREFIX}{payload}", flush=True)
    return int(report["wrapper_exit_code"])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one maa-cli process and bound its MaaCore log evidence."
    )
    parser.add_argument(
        "--core-log",
        type=Path,
        help="Override the MaaCore asst.log path; otherwise use `maa dir log`.",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        help="Optionally write the machine-readable report to this local path.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a maa-cli command is required after --")

    started_at = _utc_now()
    command_summary = {
        "executable": Path(command[0]).name,
        "subcommand": _safe_subcommand(command),
    }
    try:
        core_log = args.core_log or _discover_core_log(command[0])
        before = _snapshot(core_log)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        report = {
            "schema_version": 1,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "command": command_summary,
            "child_exit_code": None,
            "wrapper_exit_code": EVIDENCE_UNAVAILABLE_EXIT,
            "runner_error": type(error).__name__,
            "evidence": {"state": "unavailable"},
            "business_result": "not_evaluated",
        }
        return _emit_report(report, args.report_file)

    runner_error: str | None = None
    try:
        child_exit_code = subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        child_exit_code = 130
        runner_error = "KeyboardInterrupt"
    except OSError as error:
        child_exit_code = 127
        runner_error = type(error).__name__

    try:
        after = _snapshot(core_log)
        evidence = _collect_evidence(core_log, before, after)
    except OSError as error:
        evidence = {"state": "unavailable", "log_file": str(core_log)}
        runner_error = type(error).__name__

    wrapper_exit_code = child_exit_code
    if child_exit_code == 0 and evidence["state"] != "bounded":
        wrapper_exit_code = EVIDENCE_UNAVAILABLE_EXIT
    elif child_exit_code == 0 and (
        evidence.get("callback_counts", {}).get("TaskChainError", 0)
        or evidence.get("callback_counts", {}).get("SubTaskError", 0)
    ):
        wrapper_exit_code = TASKCHAIN_ERROR_EXIT

    report = {
        "schema_version": 1,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "command": command_summary,
        "child_exit_code": child_exit_code,
        "wrapper_exit_code": wrapper_exit_code,
        "runner_error": runner_error,
        "evidence": evidence,
        "business_result": "not_evaluated",
    }
    return _emit_report(report, args.report_file)


if __name__ == "__main__":
    raise SystemExit(main())
