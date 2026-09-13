#!/usr/bin/env python3
"""Read-only expiring-medicine recognition; orchestration belongs to drain_sanity."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess

from daily_checks import stage_cost
from drain_sanity import Runtime, callbacks, navigate, read_probe, write_json
from stage_runtime import normalize_stage, probe_task
from medicine_policy import load_policy
from reward_check import OCR_ITEM

MP = "MaaDailyMedicine@"


def native_expire_days(text: str) -> int | None:
    """Native MedicineExpiringTime replacements + MedicineCounter's day + 1."""
    match = re.fullmatch(r"\D*(\d+)天", text)
    if match:
        return int(match[1]) + 1
    if re.fullmatch(r"\D*\d+(小时|分钟)", text) or re.fullmatch(r"\d+(小|分)", text):
        return 1
    return None


def read_scan(report: dict, days: int, entry: str) -> dict:
    events = callbacks(report)
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Custom")
            or chains[1][:2] != ("TaskChainCompleted", "Custom") or chains[0][2] != chains[1][2]
            or report["evidence"].get("internal_error_lines")):
        raise ValueError("medicine_scan_execution_unverified")
    completed = []
    for kind, value in events:
        if kind not in {"SubTaskStart", "SubTaskCompleted"}:
            continue
        detail = value.get("details", {})
        name = detail.get("task", "").removeprefix(MP)
        if (value.get("first") != [MP + entry] or value.get("taskid") != chains[0][2]
                or value.get("taskchain") != "Custom"
                or name not in {"Open", "Dialog", "ExpiryA", "ExpiryB"}
                or detail.get("action") != ("ClickSelf" if name == "Open" else "DoNothing")):
            raise ValueError("medicine_scan_unexpected_action_or_origin")
        if kind == "SubTaskCompleted":
            completed.append(name)
    expected = (["Open"] if entry == "Open" else []) + ["Dialog", "ExpiryA", "ExpiryB"]
    if completed != expected:
        raise ValueError("medicine_scan_incomplete")
    evidence = report["evidence"]
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(evidence["before_size"])
        data = handle.read(evidence["after_size"] - evidence["before_size"])
    # callbacks already verified the interval; detect a concurrent rewrite as well.
    import hashlib
    if hashlib.sha256(data).hexdigest() != evidence["interval_sha256"]:
        raise ValueError("changed_log_interval")
    pages = {"ExpiryA": [], "ExpiryB": []}
    for line in data.decode("utf-8").splitlines():
        for node in pages:
            marker = "PipelineAnalyzer::analyze | OcrDetect " + MP + node + " "
            if marker in line:
                items = []
                for text, x, y, w, h, score in OCR_ITEM.findall(line.split(marker, 1)[1]):
                    x, y, w, h = map(int, (x, y, w, h))
                    expiry = native_expire_days(text.strip())
                    if (expiry is not None and float(score) >= .90 and float(score) <= 1
                            and 650 <= x < x+w <= 1160 and 330 <= y < y+h <= 378):
                        items.append((round((x+w/2)/20), expiry))
                pages[node].append(items)
    if any(len(p) != 1 for p in pages.values()):
        raise ValueError("medicine_scan_ambiguous_reads")
    first, second = pages["ExpiryA"][0], pages["ExpiryB"][0]
    stable = sorted(set(first) & set(second))
    detected = any(expiry <= days for _, expiry in stable)
    return {"status": "detected" if detected else "unknown", "reminder_required": True,
            "medicine_expire_days": days, "visible_expiry_days": [d for _, d in stable],
            "inventory_complete": False, "quantity": None,
            "reason": "repeated_visible_expiry" if detected else "no_reliable_positive_expiry",
            "message": "检测到范围内临期药；数量和完整库存未核验。" if detected else "无法确认临期药情况；未读到不代表没有。"}


def preflight(runtime) -> None:
    nodes = json.loads((Path(__file__).resolve().parents[1] / "assets/medicine-check/tasks.json").read_text(encoding="utf-8"))
    actual = json.loads((runtime.config / "resource/tasks/tasks.json").read_text(encoding="utf-8-sig"))
    for key, value in nodes.items():
        if actual.get(key) != value:
            raise ValueError("medicine_resource_missing_or_changed: " + key)


def scan(runtime, stage: str, days: int, *, dialog_open: bool = False, cost: int | None = None) -> dict:
    if not dialog_open:
        sanity = read_probe(runtime.run([probe_task(stage)], "medicine-precondition"), stage)
        if sanity >= stage_cost(stage, cost):
            raise ValueError("medicine_scan_requires_below_one_run")
    entry = "Dialog" if dialog_open else "Open"
    report = runtime.run([{"type": "Custom", "params": {"task_names": [MP + entry]}}], "medicine-scan")
    result = read_scan(report, days, entry)
    closed = runtime.run([{"type": "Custom", "params": {"task_names": [MP + "Close"]}}], "medicine-close")
    events = callbacks(closed)
    actions = [v.get("details", {}) for k, v in events if k == "SubTaskCompleted"]
    if (len(actions) != 1 or actions[0].get("task", "").removeprefix(MP) != "Close"
            or actions[0].get("action") != "ClickRect"):
        raise ValueError("medicine_close_unverified")
    read_probe(runtime.run([probe_task(stage)], "medicine-return-probe"), stage)
    result["end_at"] = "prepared"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读临期药窗口检查；清体力统一使用 drain_sanity.py run。")
    parser.add_argument("mode", choices=["check"])
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--cost", type=int, help="未收录关卡的已核实单场理智")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--maa", default="maa")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dialog-open", action="store_true")
    args = parser.parse_args(argv)
    runtime = None
    try:
        args.stage = normalize_stage(args.stage)
        cost = stage_cost(args.stage, args.cost)
        policy = load_policy(args.policy)
        runtime = Runtime(args.maa, args.profile, args.output_dir)
        runtime.configure_stage(args.stage)
        preflight(runtime)
        print("本地证据目录：" + str(runtime.output), flush=True)
        if not args.dialog_open:
            navigate(runtime, args.stage)
        result = scan(runtime, args.stage, policy["medicine_expire_days"], dialog_open=args.dialog_open, cost=cost)
        result.update(observed_at=dt.datetime.now(dt.timezone.utc).isoformat(), stage=args.stage)
        write_json(runtime.output / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "detected" else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result = {"status": "unknown", "reason": str(error), "reminder_required": True}
        if runtime is not None:
            write_json(runtime.output / "failure.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
