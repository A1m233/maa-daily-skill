#!/usr/bin/env python3
"""Read-only expiring-medicine recognition; orchestration belongs to drain_sanity."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys

from daily_checks import stage_cost
from drain_sanity import Runtime, callbacks, recorded_callbacks, navigate, read_probe, write_json
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
    return read_expiry(data, days)


def read_expiry(data: bytes, days: int, prefix: str = "") -> dict:
    pages = {prefix + "ExpiryA": [], prefix + "ExpiryB": []}
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
    first, second = pages[prefix + "ExpiryA"][0], pages[prefix + "ExpiryB"][0]
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


def read_session(report: dict, stage: str, days: int, entry: str) -> dict:
    """从原始失败报告保留已验证前缀；绝不改写进程成功状态。"""
    result = {"status": "unknown", "reason": "medicine_scan_unverified", "reminder_required": True,
              "scan_status": "unknown", "cleanup_status": "unknown", "page_status": "unknown",
              "end_at": "unknown", "execution_status": "failed", "inventory_complete": False,
              "quantity": None, "medicine_expire_days": days}
    try:
        events = recorded_callbacks(report)
        chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
        if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Custom")
                or chains[-1][0] not in {"TaskChainCompleted", "TaskChainError", "TaskChainStopped"}
                or chains[-1][1:] != chains[0][1:]):
            raise ValueError("ambiguous_medicine_session")
        expected = (["ScanOpen"] if entry == "ScanOpen" else []) + [
            "ScanDialog", "ScanExpiryA", "ScanExpiryB", "ScanClose",
            "VerifyStage", "StagePage", "Sanity", "SanityConfirm"]
        completed = []
        active = False
        failed = False
        for kind, value in events:
            if kind == "TaskChainStart":
                active = True
            elif kind.startswith("TaskChain"):
                active = False
            if not kind.startswith("SubTask"):
                continue
            if not active or (value.get("taskchain"), value.get("taskid")) != chains[0][1:]:
                raise ValueError("unexpected_medicine_session_origin")
            if kind == "SubTaskError":
                failed = True
            if kind not in {"SubTaskStart", "SubTaskCompleted"}:
                continue
            details = value.get("details", {})
            name = details.get("task", "").split("@")[-1]
            action = "ClickSelf" if name == "ScanOpen" else "ClickRect" if name == "ScanClose" else "DoNothing"
            if (failed or len(completed) >= len(expected) or name != expected[len(completed)]
                    or details.get("action") != action or value.get("first") != [MP + entry]):
                raise ValueError("unexpected_medicine_session_action")
            if kind == "SubTaskCompleted":
                completed.append(name)
        e = report["evidence"]
        with Path(e["log_file"]).open("rb") as handle:
            handle.seek(e["before_size"])
            data = handle.read(e["after_size"] - e["before_size"])
        import hashlib
        if hashlib.sha256(data).hexdigest() != e["interval_sha256"]:
            raise ValueError("changed_log_interval")
        # 只读取已完成双读之前的日志；后续关窗/复核错误不抹掉扫描事实。
        lines = data.decode("utf-8").splitlines(keepends=True)
        end = next((i for i, line in enumerate(lines) if "SubTaskCompleted" in line
                    and re.search(r'"task"\s*:\s*"(?:MaaDailyMedicine@)?ScanExpiryB"', line)), None)
        if "ScanExpiryB" in completed and end is not None:
            from run_with_evidence import LEVEL_PATTERN
            prefix = lines[:end + 1]
            if not any((m := LEVEL_PATTERN.search(line)) and m[1] in {"ERR", "CRT"} for line in prefix):
                try:
                    result.update(read_expiry("".join(prefix).encode("utf-8"), days, "Scan"))
                    result["scan_status"] = "completed"
                except ValueError as error:
                    result["reason"] = str(error)
        if "ScanClose" in completed:
            result["cleanup_status"] = "closed"
        if report.get("child_exit_code") == report.get("wrapper_exit_code") == 0:
            result["execution_status"] = "completed"
            try:
                read_probe(report, stage, medicine_entry=MP + entry)
                result.update(page_status="prepared", end_at="prepared")
            except ValueError as error:
                result["page_reason"] = str(error)
        else:
            result["execution_reason"] = "medicine_session_failed"
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["reason"] = str(error)
    return result


def scan(runtime, stage: str, days: int, *, dialog_open: bool = False, cost: int | None = None) -> dict:
    if not dialog_open:
        sanity = read_probe(runtime.run([probe_task(stage)], "medicine-precondition"), stage)
        if sanity >= stage_cost(stage, cost):
            raise ValueError("medicine_scan_requires_below_one_run")
    entry = "ScanDialog" if dialog_open else "ScanOpen"
    try:
        report = runtime.run([{"type": "Custom", "params": {"task_names": [MP + entry]}}], "medicine-check")
    except ValueError as error:
        report = getattr(error, "report", None)
        if report is None:
            raise
    result = read_session(report, stage, days, entry)
    write_json(runtime.output / "medicine-check.json", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读临期药窗口检查；清体力统一使用 drain_sanity.py run。")
    parser.add_argument("mode", choices=["check"])
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--cost", type=int, help="未收录关卡的已核实单场理智")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--maa", default="maa")
    parser.add_argument("--output-dir", type=Path, help="可选输出根；省略时使用固定受管产物目录")
    parser.add_argument("--dialog-open", action="store_true")
    args = parser.parse_args(argv)
    from artifacts import ArtifactError
    runtime = None
    settled = False
    artifact_status = "failed"
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
        settled = True
        artifact_status = result["status"]
        return 0 if result["status"] == "detected" and result.get("end_at") == "prepared" else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ArtifactError) as error:
        settled = True
        result = {"status": "unknown", "reason": str(error), "reminder_required": True}
        if runtime is not None:
            write_json(runtime.output / "failure.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 2
    finally:
        if runtime is not None:
            cleanup = runtime.artifacts.finish(artifact_status, uncertain=not settled)
            if cleanup.get("warnings"):
                print("产物清理提示：" + json.dumps(cleanup["warnings"], ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
