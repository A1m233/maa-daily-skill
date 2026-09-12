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

# These ProcessTask failures are boolean probes in MaaCore, not failed actions.
# See safety-and-results.md for upstream source and the contextual proof required.
CONDITIONAL_PROBES = {
    ("Mall", "CreditShop-NoMoney"): "CreditShoppingTask",
    ("Infrast", "UnlockClues"): "InfrastReceptionTask",
    ("Infrast", "EndOfClueExchange"): "InfrastReceptionTask",
}


def classify_conditional_errors(lines: list[str], start_line: int = 1) -> dict:
    """Keep unknown errors blocking; qualify only complete same-lane scopes.

    A node name alone never qualifies. A completed enclosing handler and chain
    are required, and any unknown error in that chain invalidates its candidates.
    Missing/ambiguous lifecycle or malformed callbacks disable all exemptions.
    """
    chains, active, parents, pending, qualified = {}, {}, {}, {}, []
    raw, invalid = [], False
    for number, line in enumerate(lines, start_line):
        if CALLBACK_MARKER not in line:
            continue
        event, _, payload = line.split(CALLBACK_MARKER, 1)[1].partition(" ")
        if event == "SubTaskError":
            raw.append(number)
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("callback must be object")
            chain, taskid = value.get("taskchain"), value.get("taskid")
            if not isinstance(chain, str) or type(taskid) is not int:
                if event in TASKCHAIN_EVENTS or event == "SubTaskError":
                    invalid = True
                continue
            key = (chain, taskid)
            match = re.search(r"\[(P[^]]+)\]\[(T[^]]+)\]", line)
            lane = match.groups() if match else None
            if event == "TaskChainStart":
                if key in chains:
                    invalid = True
                chains[key] = {"complete": False, "bad": False, "items": []}
                active[key] = lane
                continue
            if key not in active or lane is None or lane != active[key]:
                if event in TASKCHAIN_EVENTS or event == "SubTaskError":
                    invalid = True
                continue
            state = chains[key]
            subtask = value.get("subtask")
            parent = "CreditShoppingTask" if chain == "Mall" else "InfrastReceptionTask" if chain == "Infrast" else None
            if parent and subtask == parent:
                if value.get("class") != "asst::" + parent:
                    invalid = True
                if event == "SubTaskStart":
                    if key in parents:
                        invalid = True
                    parents[key] = parent
                    pending[key] = []
                elif event == "SubTaskCompleted":
                    if parents.pop(key, None) != parent:
                        invalid = True
                    state["items"].extend(pending.pop(key, []))
            if event == "SubTaskError":
                first = value.get("first")
                node = first[0] if isinstance(first, list) and len(first) == 1 and isinstance(first[0], str) else None
                expected = CONDITIONAL_PROBES.get((chain, node))
                if (expected and parents.get(key) == expected
                        and subtask == "ProcessTask" and value.get("class") == "asst::ProcessTask"
                        and value.get("details") == {} and value.get("pre_task") == ""):
                    pending[key].append({"line": number, "taskchain": chain,
                                         "taskid": taskid, "node": node,
                                         "basis": "conditional_probe_completed_scope"})
                else:
                    state["bad"] = True
            if event in {"TaskChainCompleted", "TaskChainError"}:
                state["complete"] = event == "TaskChainCompleted" and key not in parents
                state["bad"] |= event == "TaskChainError"
                active.pop(key)
        except (ValueError, TypeError):
            invalid = True
    if not invalid and not active:
        for state in chains.values():
            if state["complete"] and not state["bad"]:
                qualified.extend(state["items"])
    allowed = {item["line"] for item in qualified}
    return {"conditional_subtask_errors": sorted(qualified, key=lambda item: item["line"]),
            "blocking_subtask_error_lines": [n for n in raw if n not in allowed]}


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
        **classify_conditional_errors(text.splitlines(), start_line),
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
        or evidence.get("blocking_subtask_error_lines", evidence.get("subtask_error_lines", []))
        or evidence.get("callback_parse_error_lines")
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
