#!/usr/bin/env python3
"""只读公招动作与标签保留证据，不计算干员组合或操作游戏。"""
import argparse
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_with_evidence import classify_execution
from log_evidence import read_evidence

MARKER = "Assistant::append_callback | "


def inspect_report(report):
    result = {"status": "unknown", "reason": "invalid_evidence", "chains": [],
              "reminder_required": True, "observed_at": report.get("ended_at")}
    try:
        e = report["evidence"]
        if e.get("state") != "bounded":
            return result
        try:
            data = read_evidence(e)
        except ValueError:
            return {**result, "reason": "log_interval_changed_or_unhashed"}
        lines = data.decode("utf-8").splitlines()
        chains = {}
        for number, line in enumerate(lines, 1):
            if MARKER not in line:
                continue
            kind, _, body = line.split(MARKER, 1)[1].partition(" ")
            v = json.loads(body)
            if not kind.startswith(("TaskChain", "SubTask")):
                continue
            if v.get("taskchain") != "Recruit":
                continue
            identity = v.get("taskid")
            if type(identity) is not int:
                raise ValueError("missing_taskid")
            if kind == "TaskChainStart":
                if identity in chains:
                    raise ValueError("reused_taskid")
                chains[identity] = {"taskid": identity, "status": "running", "rounds": [],
                                    "confirmed_actions": 0, "confirmed_recruitments": 0,
                                    "failed_confirmations": 0, "refresh_actions": 0, "errors": []}
                continue
            c = chains[identity]
            if c["status"] != "running":
                raise ValueError("event_outside_chain")
            d = v.get("details", {})
            if kind in {"TaskChainCompleted", "TaskChainError", "TaskChainStopped"}:
                c["status"] = "completed" if kind == "TaskChainCompleted" else "failed"
            elif kind == "SubTaskError":
                c["errors"].append(number)
                if (c["rounds"] and "RecruitConfirm" in v.get("first", [])
                        and c["rounds"][-1]["outcome"] in {"unverified", "confirmation_attempted"}):
                    c["rounds"][-1].update(outcome="confirmation_failed", failure_line=number)
                    c["failed_confirmations"] += 1
            elif kind == "SubTaskExtraInfo" and v.get("what") == "RecruitTagsDetected":
                tags = d.get("tags")
                if not isinstance(tags, list) or not tags or not all(isinstance(t, str) for t in tags):
                    raise ValueError("invalid_tags")
                c["rounds"].append({"tags": tags, "interval_line": number, "outcome": "unverified"})
            elif kind == "SubTaskExtraInfo" and v.get("what") in {
                    "RecruitPreservedTag", "RecruitTagsRefreshed", "RecruitResult"}:
                r = c["rounds"][-1]
                if v["what"] == "RecruitPreservedTag":
                    if d.get("tag") not in r["tags"] or d.get("tags") != r["tags"]:
                        raise ValueError("unmatched_preserved_tag")
                    r.update(outcome="preserved", preserved_tag=d["tag"], reason="maa_preserved_tag")
                elif v["what"] == "RecruitTagsRefreshed":
                    if r["outcome"] != "unverified":
                        raise ValueError("conflicting_round_outcome")
                    r["outcome"] = "refreshed"
                    c["refresh_actions"] += 1  # count 字段可能每槽重置，不取全局最大值。
                else:
                    r["level"] = d.get("level")
            elif (kind == "SubTaskCompleted" and d.get("task", "").split("@")[-1] == "RecruitConfirm"
                  and d.get("action") in {"ClickSelf", "ClickRect"}):
                r = c["rounds"][-1]
                if r["outcome"] != "unverified":
                    raise ValueError("ambiguous_confirm")
                r["outcome"] = "confirmation_attempted"
                r["confirmation_line"] = number
                c["confirmed_actions"] += 1
            elif (kind == "SubTaskStart" and d.get("task", "").split("@")[-1] == "RecruitFlag"
                  and d.get("action") == "Stop" and v.get("first") == ["RecruitConfirm"]):
                r = c["rounds"][-1]
                if r["outcome"] != "confirmation_attempted":
                    raise ValueError("unmatched_confirmation_terminal")
                r.update(outcome="confirmed", confirmation_terminal_line=number)
                c["confirmed_recruitments"] += 1
        result["chains"] = list(chains.values())
        execution = classify_execution(lines)["status"]
        if (not chains or any(c["status"] != "completed" or not c["rounds"] for c in chains.values())
                or execution != "completed" or report.get("child_exit_code") != 0
                or report.get("wrapper_exit_code") != 0 or report.get("runner_error")):
            return {**result, "reason": "execution_incomplete"}
        pending = [r for c in chains.values() for r in c["rounds"]
                   if r["outcome"] in {"preserved", "unverified", "confirmation_attempted"}]
        return {**result, "status": "evaluated", "reason": "actions_and_preserved_tags",
                "confirmed_actions": sum(c["confirmed_actions"] for c in chains.values()),
                "confirmed_recruitments": sum(c["confirmed_recruitments"] for c in chains.values()),
                "failed_confirmations": sum(c["failed_confirmations"] for c in chains.values()),
                "refresh_actions": sum(c["refresh_actions"] for c in chains.values()),
                "pending": pending,
                "reminder_required": bool(pending or any(c["errors"] for c in chains.values()))}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {**result, "reason": "unreadable_or_ambiguous_evidence"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_report(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        result = {"status": "unknown", "reason": "unreadable_report", "reminder_required": True}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
