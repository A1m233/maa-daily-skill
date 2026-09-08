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


def classify_errors(lines: list[str]) -> dict:
    """Attribute raw errors only to a unique active chain on the same log lane.

    Callback identity wins over timing. Pre-chain and other-thread messages
    stay unassigned; no error-name allowlist or guessing from message text.
    """
    active = {}
    result = {"infrast": [], "other_chains": [], "unassigned": []}
    task_error = False
    for number, line in enumerate(lines, 1):
        lane_match = re.search(r"\[(P[^]]+)\]\[(T[^]]+)\]", line)
        lane = lane_match.groups() if lane_match else None
        event, value = None, {}
        if MARKER in line:
            event, _, payload = line.split(MARKER, 1)[1].partition(" ")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("callback must be an object")
        identity = (value.get("taskchain"), value.get("taskid"))
        identified = isinstance(identity[0], str) and type(identity[1]) is int
        if event == "TaskChainStart":
            if not identified or identity in active:
                raise ValueError("ambiguous chain lifecycle")
            active[identity] = lane
        callback_error = event in {"TaskChainError", "SubTaskError"}
        if callback_error or re.search(r"\[(ERR|CRT)\]", line):
            task_error = task_error or callback_error
            owner = identity if identified else None
            basis = "callback_identity" if owner else "unassigned"
            if owner is None:
                candidates = [key for key, location in active.items() if location == lane]
                if len(candidates) == 1:
                    owner, basis = candidates[0], "active_chain_same_log_lane"
            item = {"interval_line": number, "basis": basis}
            if owner:
                item.update(taskchain=owner[0], taskid=owner[1])
            bucket = "unassigned" if owner is None else "infrast" if owner[0] == "Infrast" else "other_chains"
            result[bucket].append(item)
        if event in {"TaskChainCompleted", "TaskChainError"} and identified:
            active.pop(identity, None)
    return {"groups": result, "task_error": task_error}


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
    lines = data.decode("utf-8", errors="strict").splitlines()
    attribution = classify_errors(lines)
    chains = {}
    for line_number, line in enumerate(lines, 1):
        if MARKER not in line:
            continue
        event, _, payload = line.split(MARKER, 1)[1].partition(" ")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("callback must be an object")
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
        chain["error_interval_lines"] = [e["interval_line"] for e in attribution["groups"]["infrast"]
                                         if e["taskid"] == chain["taskid"]]
        chain["evidence_status"] = ("evaluated" if chain["chain_status"] == "completed"
                                    and not chain["error_interval_lines"] else "unknown")
    result["chains"] = list(chains.values())
    result["error_groups"] = attribution["groups"]
    result["error_interval_lines"] = sorted({e["interval_line"] for group in attribution["groups"].values() for e in group})
    run_failed = (report.get("wrapper_exit_code") != 0 or report.get("child_exit_code") != 0
                  or attribution["task_error"])
    result["run_status"] = "failed" if run_failed else "warnings" if result["error_interval_lines"] else "clean"
    result["status"] = "evaluated"
    result["reason"] = "actions_only_not_final_state"
    if evidence.get("callback_parse_error_lines"):
        result.update(status="unknown", reason="callback_parse_errors")
    elif attribution["groups"]["infrast"]:
        result.update(status="unknown", reason="infrast_has_errors")
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
    return 0 if result["status"] == "evaluated" and result.get("run_status") != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
