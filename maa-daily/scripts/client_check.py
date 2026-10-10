#!/usr/bin/env python3
"""客户端更新提示检查：复用 MAA OCR，不点击、不导航、不更新客户端。"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import unicodedata
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKER = "Assistant::append_callback | "
ENTRY = "MaaDailyCheck@ScreenText"
TASK = "maa-daily-check-screen"
LANE = re.compile(r"\[(P[^\]]+)\]\[(T[^\]]+)\]")
OCR = re.compile(r"\{ text: (.*?), rect: \[([^\]]+)\], score: ([0-9]+(?:\.[0-9]+)?) \}")
UPDATE = re.compile(r"(?:当前)?客户端版本(?:已)?(?:过时|过期)(?:即将|请|需要|必须)?更新客户端(?:版本)?")
MESSAGES = {
    "client_update_required": "游戏客户端提示版本过时，需要更新；暂停后续任务，更新后重新核验再继续。",
    "observed": "本轮曾出现客户端更新提示，但不能据此认定当前仍被阻塞；按最终状态核验，不重跑业务。",
    "not_detected": "本次未识别到明确的客户端更新提示；不证明无需更新，也不替代设备、账号或业务核验。",
    "unknown": "客户端更新状态无法确认；保留原执行错误，不猜测为网络故障或自动重试。",
}


def verdict(status: str, reason: str, matches=()) -> dict:
    # 报告不复制整页 OCR；只保留明确更新文案，避免无界增长或泄露账号信息。
    return {"status": status, "reason": reason, "text": MESSAGES[status],
            "reminder_required": status != "not_detected", "stop_batch": status == "client_update_required",
            "match_count": len(matches), "evidence": list(matches[:4]) + list(matches[-4:]) if len(matches) > 8 else list(matches)}


def classify_client_update(lines: list[str], start_line: int = 1, execution: dict | None = None) -> dict:
    """解释有界日志；不改变执行结论，也不从未命中推导当前可登录。"""
    active, records, matches = {}, [], []
    for number, line in enumerate(lines, start_line):
        context = LANE.search(line)
        lane = context.groups() if context else None
        if MARKER in line:
            try:
                event, _, payload = line.split(MARKER, 1)[1].partition(" ")
                value = json.loads(payload)
                chain, taskid = value.get("taskchain"), value.get("taskid")
                key = (chain, taskid)
                if event == "TaskChainStart":
                    record = {"chain": chain, "lane": lane, "terminal": None,
                              "attempt": number, "ocr": False, "screen": False, "matches": []}
                    active[key] = record
                    records.append(record)
                elif event in {"TaskChainCompleted", "TaskChainError", "TaskChainStopped"}:
                    record = active.pop(key, None)
                    if record is not None and record["lane"] == lane:
                        record["terminal"] = event
                elif chain == "StartUp" and event in {"SubTaskStart", "SubTaskCompleted"}:
                    details = value.get("details", {})
                    # first 是整条 ProcessTask 的入口，OfflineConfirm 等后续节点也携带它。
                    # 只用实际执行的 StartUpBegin 划分尝试，不能把停止动作误作新一轮。
                    name = details.get("task", "")
                    if isinstance(name, str) and name.rsplit("@", 1)[-1] == "StartUpBegin":
                        candidates = [r for (c, _), r in active.items() if c == chain and lane is not None and r["lane"] == lane]
                        if len(candidates) == 1:
                            candidates[0]["attempt"] = number
            except (ValueError, TypeError, AttributeError):
                return verdict("unknown", "invalid_callback", matches)
            continue
        candidates = [r for r in active.values() if lane is not None and r["lane"] == lane]
        if len(candidates) != 1:
            continue
        record = candidates[0]
        startup_ocr = record["chain"] == "StartUp" and "asst::WordOcr [" in line
        screen_ocr = record["chain"] == "Custom" and ("| OcrDetect " + ENTRY + " [") in line
        if not (startup_ocr or screen_ocr):
            continue
        record["ocr"] = True
        record["screen"] |= screen_ocr
        for text, rect, score in OCR.findall(line):
            normalized = "".join(c for c in unicodedata.normalize("NFKC", text)
                                 if not c.isspace() and not unicodedata.category(c).startswith("P"))
            if (UPDATE.fullmatch(normalized) and 0.9 <= float(score) <= 1.0
                    and len(rect.split(",")) == 4):
                match = {"line": number, "text": text, "score": float(score)}
                matches.append(match)
                record["matches"].append(match)
    if execution and (execution.get("issues") or execution.get("incomplete_chains")):
        return verdict("unknown", "ambiguous_execution_boundary", matches)
    relevant = [r for r in records if r["chain"] == "StartUp" or r["screen"]]
    if not relevant:
        return verdict("unknown", "no_supported_ocr_scope", matches)
    last = relevant[-1]
    if last["terminal"] not in {"TaskChainCompleted", "TaskChainError", "TaskChainStopped"}:
        return verdict("unknown", "incomplete_observation", matches)
    current = [m for m in last["matches"] if m["line"] >= last["attempt"]]
    # Completed 不证明账号正确，但也不能让旧更新提示推翻已结束的成功启动链。
    if current and not (last["chain"] == "StartUp" and last["terminal"] == "TaskChainCompleted"):
        return verdict("client_update_required", "explicit_client_update_prompt", matches)
    if matches:
        return verdict("observed", "earlier_prompt_not_current_blocker", matches)
    if last["ocr"]:
        return verdict("not_detected", "no_explicit_update_prompt", matches)
    return verdict("unknown", "no_supported_ocr", matches)


def inspect_report(report: dict) -> dict:
    from run_with_evidence import inspect_execution_report
    checked = inspect_execution_report(report)
    result = checked.get("diagnostics", {}).get("client_update", verdict("unknown", checked["reason"]))
    return {**result, "observed_at": report.get("ended_at"),
            "execution": checked.get("execution"),
            "original_wrapper_exit_code": report.get("wrapper_exit_code"),
            "evidence_verified": checked.get("status") == "evaluated"}


def check_install(executable: str) -> Path:
    query = subprocess.run([executable, "dir", "config", "--batch"], capture_output=True,
                           text=True, encoding="utf-8", check=True, timeout=30)
    config = Path(query.stdout.strip().splitlines()[-1])
    if not config.is_absolute():
        raise ValueError("maa_config_path_not_absolute")
    assets = Path(__file__).resolve().parents[1] / "assets/daily-checks"
    if any((config / "tasks" / (TASK + suffix)).exists() for suffix in (".json", ".yaml", ".yml")):
        raise ValueError("ambiguous_screen_task")
    actual = tomllib.loads((config / "tasks" / (TASK + ".toml")).read_text(encoding="utf-8"))
    expected = tomllib.loads((assets / (TASK + ".toml")).read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError("screen_task_differs_from_readonly_contract")
    resource = json.loads((config / "resource/tasks/tasks.json").read_text(encoding="utf-8-sig"))
    bundled = json.loads((assets / "tasks.json").read_text(encoding="utf-8"))
    if resource.get(ENTRY) != bundled[ENTRY]:
        raise ValueError("screen_resource_differs_from_readonly_contract")
    return config


def scan_once(maa: str, profile: str, output: Path | None = None) -> tuple[dict, Path]:
    from artifacts import ArtifactRun
    config = check_install(maa)
    artifacts = ArtifactRun.begin(config, output, "client-check", prefix="client-check-")
    result, settled = verdict("unknown", "scan_not_finished"), False
    report_file = artifacts.path / "evidence.json"
    command = [maa, "run", TASK, "--profile", profile, "--batch", "--user-resource", "--no-auto-reconnect"]
    try:
        live_requested = False
        try:
            # 两次校验间不接受并发配置写入；dry-run 不证明真实 OCR 成功。
            from run_with_evidence import run_dry
            run_dry(command + ["--dry-run"], artifacts, artifacts.path / "dry-run.json")
            check_install(maa)
            runner = Path(__file__).with_name("run_with_evidence.py")
            live_requested = True
            process = subprocess.run([sys.executable, "-B", str(runner), "--report-file", str(report_file),
                                      "--", *command], env=artifacts.environment())
            report = json.loads(report_file.read_text(encoding="utf-8"))
            settled = (report.get("child_exit_code") is None and process.returncode == 74
                       or type(report.get("child_exit_code")) is int and report["child_exit_code"] >= 0
                       and report["child_exit_code"] != 130)
            result = inspect_report(report)
            if process.returncode != 0 and result["status"] != "client_update_required":
                result = {**result, **verdict("unknown", "screen_runner_failed")}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            settled = settled or not live_requested
            result = verdict("unknown", "scan_failed")
            result["error_type"] = type(error).__name__
            print(str(error), file=sys.stderr)
        result["scan_report"] = str(report_file)
        result["check_kind"] = "current_screen_only"
        (artifacts.path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result, artifacts.path
    finally:
        cleanup = artifacts.finish(result["status"], uncertain=not settled)
        if cleanup.get("warnings"):
            print("产物清理提示：" + json.dumps(cleanup["warnings"], ensure_ascii=False), file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="只读复核已有 runner 报告，不执行游戏，观察时间不代表现在")
    inspect.add_argument("--report", type=Path, required=True)
    scan = sub.add_parser("scan", help="设备就绪且无并发操作者后，执行一次无点击 OCR；不启动游戏或切号")
    scan.add_argument("--maa", default="maa")
    scan.add_argument("--profile", required=True)
    scan.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            result, directory = scan_once(args.maa, args.profile, args.output_dir)
            print(f"检查目录：{directory}", file=sys.stderr)
        else:
            result = inspect_report(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        result = verdict("unknown", "check_failed")
        result["error_type"] = type(error).__name__
        print(str(error), file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return {"not_detected": 0, "client_update_required": 3}.get(result["status"], 2)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
