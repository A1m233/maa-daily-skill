#!/usr/bin/env python3
"""Read-only Infrast evidence classifier; never claims that clicks clear all work."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

MARKER = "Assistant::append_callback | "
CATEGORIES = {"可收获": "products", "订单交付": "orders", "干员信赖": "trust"}


def inspect_report(report: dict) -> dict:
    result = {"status": "unknown", "reason": "invalid_evidence", "chains": [],
              "reminder_required": True, "all_work_completed": "unknown",
              "observed_at": report.get("ended_at")}
    evidence = report.get("evidence", {})
    if evidence.get("state") != "bounded":
        return result
    start, end = evidence.get("before_size"), evidence.get("after_size")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end or end - start > 64 * 1024 * 1024):
        return result
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(start)
        data = handle.read(end - start)
    if len(data) != end - start or hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        result["reason"] = "log_interval_changed_or_unhashed"
        return result
    chains = {}
    errors = []
    for line_number, line in enumerate(data.decode("utf-8", errors="strict").splitlines(), 1):
        if re.search(r"\[(ERR|CRT)\]", line):
            errors.append(line_number)
        if MARKER not in line:
            continue
        event, _, payload = line.split(MARKER, 1)[1].partition(" ")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("callback must be an object")
        if event in {"TaskChainError", "SubTaskError"}:
            errors.append(line_number)
        if value.get("taskchain") != "Infrast":
            continue
        taskid = value.get("taskid")
        if type(taskid) is not int:
            raise ValueError("missing Infrast taskid")
        chain = chains.setdefault(taskid, {"taskid": taskid, "chain_status": "unknown",
            "collection": {key: [] for key in (*CATEGORIES.values(), "unclassified")},
            "rotation": [], "trim": [], "facility_subtasks": [],
            "notification_lines": [], "exit_lines": [], "started": False})
        if event == "TaskChainStart":
            if chain["started"]:
                raise ValueError("reused taskid: ambiguous run boundary")
            chain["started"] = True
        elif event == "TaskChainCompleted":
            if not chain["started"] or chain["chain_status"] != "unknown":
                raise ValueError("invalid chain lifecycle")
            chain["chain_status"] = "completed"
        elif event == "SubTaskCompleted":
            if not chain["started"] or chain["chain_status"] != "unknown":
                raise ValueError("subtask outside active chain")
            details = value.get("details", {})
            task = details.get("task")
            if task == "InfrastNotification":
                chain["notification_lines"].append(line_number)
            if task == "InfrastExitReward":
                chain["exit_lines"].append(line_number)
            if details.get("action") in {"ClickSelf", "ClickRect"}:
                item = {"interval_line": line_number, "node": task}
                if task == "InfrastReward":
                    match = details.get("result", {})
                    category = CATEGORIES.get(match.get("text"), "unclassified")
                    score = match.get("score")
                    if type(score) not in {int, float} or not 0.8 <= score <= 1:
                        category = "unclassified"
                    chain["collection"][category].append(item)
                elif task == "InfrastRotationClick":
                    chain["rotation"].append(item)
                elif task == "InfrastTrimClick":
                    chain["trim"].append(item)
            # Expose completed facility handlers, not a guessed room/count result.
            subtask = value.get("subtask", "")
            if isinstance(subtask, str) and subtask.startswith("Infrast"):
                chain["facility_subtasks"].append({"subtask": subtask,
                    "facility": details.get("facility"), "index": details.get("index"),
                    "interval_line": line_number})
    for chain in chains.values():
        chain.pop("started")
        has_collection = any(chain["collection"].values())
        chain["collection_status"] = "action_observed" if has_collection else "unknown"
        chain["possible_collection_skip"] = bool(chain["notification_lines"] and chain["exit_lines"] and not has_collection)
        chain["rotation_status"] = "action_observed" if chain["rotation"] else "unknown"
        chain["trim_status"] = "action_observed" if chain["trim"] else "unknown"
        chain["all_work_completed"] = "unknown"
    result["chains"] = list(chains.values())
    result["error_interval_lines"] = sorted(set(errors))
    result["status"] = "evaluated"
    result["reason"] = "actions_only_not_final_state"
    if (report.get("wrapper_exit_code") != 0 or report.get("child_exit_code") != 0
            or errors or evidence.get("callback_parse_error_lines")
            or evidence.get("internal_error_lines")):
        result.update(status="unknown", reason="run_failed_or_has_errors")
    elif not chains or any(c["chain_status"] != "completed" for c in chains.values()):
        result.update(status="unknown", reason="no_complete_infrast_chain")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读核验本轮基建日志；不启动 MAA，不证明全部待办清空。")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("inspect")
    command.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect_report(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result = {"status": "unknown", "reason": "unreadable_or_invalid_evidence",
                  "error_type": type(error).__name__, "reminder_required": True,
                  "all_work_completed": "unknown", "chains": []}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
