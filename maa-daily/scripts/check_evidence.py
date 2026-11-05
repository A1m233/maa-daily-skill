"""Evidence contract for one native Custom check, separate from business meaning."""
from __future__ import annotations

import json
from log_evidence import read_evidence


def error_scope(data: bytes, start_line: int = 1) -> dict:
    from run_with_evidence import CALLBACK_MARKER, LEVEL_PATTERN
    result = {"before_chain": [], "within_chain": [], "after_chain": [], "critical": []}
    active, started = False, False
    for number, line in enumerate(data.decode("utf-8").splitlines(), start_line):
        if CALLBACK_MARKER in line:
            event = line.split(CALLBACK_MARKER, 1)[1].partition(" ")[0]
            if event == "TaskChainStart":
                active = started = True
        level = LEVEL_PATTERN.search(line)
        if level and level[1] in {"ERR", "CRT"}:
            result["within_chain" if active else "after_chain" if started else "before_chain"].append(number)
            if level[1] == "CRT":
                result["critical"].append(number)
        if CALLBACK_MARKER in line and event in {"TaskChainCompleted", "TaskChainError", "TaskChainStopped"}:
            active = False
    return result


def read_check(report: dict) -> tuple[bytes, dict]:
    """Only boundary-external ERR can coexist with completed business checks.

    These remain diagnostics, not proven-harmless errors. No task-name whitelist.
    Reparse the sealed interval rather than trusting cached error arrays.
    """
    from run_with_evidence import CALLBACK_MARKER, classify_execution
    evidence = report.get("evidence", {})
    if (report.get("child_exit_code") != 0 or report.get("wrapper_exit_code") != 0
            or report.get("runner_error") or evidence.get("state") != "bounded"):
        raise ValueError("check_process_or_evidence_invalid")
    data = read_evidence(evidence)
    lines = data.decode("utf-8").splitlines()
    if classify_execution(lines)["status"] != "completed":
        raise ValueError("check_execution_incomplete")
    events = [(kind, json.loads(payload)) for line in lines if CALLBACK_MARKER in line
              for kind, _, payload in [line.split(CALLBACK_MARKER, 1)[1].partition(" ")]]
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Custom")
            or chains[1] != ("TaskChainCompleted", "Custom", chains[0][2])):
        raise ValueError("check_chain_ambiguous")
    active = False
    for kind, value in events:
        if kind == "TaskChainStart":
            active = True
        elif kind.startswith("TaskChain"):
            active = False
        if kind.startswith("SubTask") and (not active or kind in {"SubTaskError", "SubTaskStopped"}
                or (value.get("taskchain"), value.get("taskid")) != chains[0][1:]):
            raise ValueError("check_subtask_failed_or_unattributed")
    diagnostics = error_scope(data, evidence.get("start_line", 1))
    if diagnostics["within_chain"] or diagnostics["critical"]:
        raise ValueError("check_active_or_critical_error")
    return data, diagnostics
