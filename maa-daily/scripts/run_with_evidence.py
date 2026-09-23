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
TASKCHAIN_EVENTS = {"TaskChainStart", "TaskChainCompleted", "TaskChainError", "TaskChainStopped"}
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
LOG_LANE = re.compile(r"\[(P[^\]]+)\]\[(T[^\]]+)\]")


def _callback_context(line: str, value: dict) -> tuple:
    lane = LOG_LANE.search(line)
    uuid = value.get("uuid")
    return (lane.groups() if lane else None, uuid if isinstance(uuid, str) and uuid else None)


def _context_conflicts(left: tuple, right: tuple) -> bool:
    return any(a is not None and b is not None and a != b for a, b in zip(left, right))


def classify_execution(lines: list[str], start_line: int = 1) -> dict:
    """Judge the execution boundary, never infer business success from errors.

    SubTaskError remains diagnostic even when the enclosing chain completes.
    Default-ID children need a unique active chain in the same log/device context.
    This attributes diagnostics, not a hidden exception tree or business success.
    """
    chains, active, issues = {}, set(), []
    contexts, attributions = {}, []
    failed = False
    for number, line in enumerate(lines, start_line):
        if CALLBACK_MARKER not in line:
            continue
        event, _, payload = line.split(CALLBACK_MARKER, 1)[1].partition(" ")
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("callback must be object")
            if event in {"InternalError", "InitFailed"}:
                failed = True
            if event not in TASKCHAIN_EVENTS and event != "SubTaskError":
                continue
            chain, taskid = value.get("taskchain"), value.get("taskid")
            if not isinstance(chain, str) or not chain or type(taskid) is not int or taskid < 0:
                issues.append({"line": number, "reason": "missing_chain_identity"})
                continue
            key = (chain, taskid)
            context = _callback_context(line, value)
            if event == "TaskChainStart":
                if key in chains:
                    issues.append({"line": number, "reason": "reused_chain_identity"})
                chains[key] = "running"
                contexts[key] = context
                active.add(key)
                continue
            if event == "SubTaskError":
                owner, basis = None, "callback_identity"
                if key in active and not _context_conflicts(contexts[key], context):
                    owner = key
                elif taskid == 0 and key not in active and all(part is not None for part in context):
                    candidates = [k for k in active if k[0] == chain and k[1] > 0
                                  and contexts[k] == context]
                    if len(candidates) == 1:
                        owner, basis = candidates[0], "default_id_unique_active_chain"
                    elif len(candidates) > 1:
                        issues.append({"line": number, "reason": "ambiguous_subtask_parent"})
                        continue
                if owner is None:
                    issues.append({"line": number, "reason": "event_outside_active_chain"})
                else:
                    attributions.append({"line": number, "taskchain": chain,
                                         "reported_taskid": taskid, "parent_taskid": owner[1], "basis": basis})
                continue
            if key not in active:
                issues.append({"line": number, "reason": "event_outside_active_chain"})
            elif _context_conflicts(contexts[key], context):
                issues.append({"line": number, "reason": "chain_context_mismatch"})
            if event in {"TaskChainError", "TaskChainStopped"}:
                failed = True
            if event in TASKCHAIN_EVENTS:
                chains[key] = "completed" if event == "TaskChainCompleted" else "failed"
                active.discard(key)
        except (ValueError, TypeError):
            issues.append({"line": number, "reason": "invalid_callback"})
    status = "failed" if failed else "unknown" if issues or active or not chains else "completed"
    return {"policy": "execution-boundary-v2", "status": status, "issues": issues,
            "subtask_error_attributions": attributions,
            "incomplete_chains": [{"taskchain": c, "taskid": i} for c, i in sorted(active)],
            "business_result": "not_evaluated"}


def inspect_execution_report(report: dict) -> dict:
    """Reassess a sealed byte interval without rewriting it or authorizing resume."""
    result = {"status": "unknown", "reason": "invalid_evidence", "business_result": "not_evaluated",
              "continuation": "blocked_execution", "original_wrapper_exit_code": report.get("wrapper_exit_code")}
    evidence = report.get("evidence", {})
    start, end, first_line = evidence.get("before_size"), evidence.get("after_size"), evidence.get("start_line")
    child_exit = report.get("child_exit_code")
    if (report.get("schema_version") not in (1, 2) or type(report.get("wrapper_exit_code")) is not int
            or evidence.get("state") != "bounded" or type(start) is not int or type(end) is not int
            or not 0 <= start < end or end - start > 64 * 1024 * 1024
            or type(first_line) is not int or first_line < 1 or type(child_exit) is not int):
        return result
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(start)
        data = handle.read(end - start)
    if len(data) != end - start or hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        result["reason"] = "log_interval_changed_or_unhashed"
        return result
    parsed = _parse_callbacks(data, first_line)
    execution = parsed["execution"]
    if child_exit != 0:
        code = child_exit
    elif report.get("runner_error") or parsed["callback_parse_error_lines"]:
        code = EVIDENCE_UNAVAILABLE_EXIT
    elif execution["status"] == "failed":
        code = TASKCHAIN_ERROR_EXIT
    elif execution["status"] != "completed":
        code = EVIDENCE_UNAVAILABLE_EXIT
    else:
        code = 0
    result.update(status="evaluated", reason="execution_only_not_business_success", execution=execution,
                  reassessed_wrapper_exit_code=code, runner_error=report.get("runner_error"),
                  child_exit_code=child_exit, interval_sha256=evidence["interval_sha256"],
                  subtask_error_lines=parsed["subtask_error_lines"],
                  internal_error_lines=parsed["internal_error_lines"],
                  callback_parse_error_lines=parsed["callback_parse_error_lines"],
                  continuation="requires_business_preconditions" if code == 0 else "blocked_execution")
    return result


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
            if not isinstance(payload, dict):
                raise ValueError("callback must be an object")
        except ValueError:
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
        "execution": classify_execution(text.splitlines(), start_line),
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
            "interval_sha256": hashlib.sha256(appended).hexdigest(),
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
    parser.add_argument("--inspect-report", type=Path,
                        help="只读校验原报告日志区间并复核执行边界，不启动 MAA、不改写报告")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.inspect_report is not None:
        if args.command or args.core_log is not None or args.report_file is not None:
            parser.error("--inspect-report cannot be combined with a command or output/log overrides")
        try:
            value = inspect_execution_report(json.loads(args.inspect_report.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            value = {"status": "unknown", "reason": "unreadable_or_invalid_evidence",
                     "error_type": type(error).__name__, "business_result": "not_evaluated",
                     "continuation": "blocked_execution"}
        print(json.dumps(value, ensure_ascii=False))
        return int(value.get("reassessed_wrapper_exit_code", EVIDENCE_UNAVAILABLE_EXIT))
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
            "schema_version": 2,
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
    elif child_exit_code == 0 and evidence.get("execution", {}).get("status") == "failed":
        wrapper_exit_code = TASKCHAIN_ERROR_EXIT
    elif child_exit_code == 0 and evidence.get("execution", {}).get("status") != "completed":
        wrapper_exit_code = EVIDENCE_UNAVAILABLE_EXIT

    report = {
        "schema_version": 2,
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
