#!/usr/bin/env python3
"""Daily tier scan through maa-cli; no independent image recognition or claiming."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

PREFIX = "MaaDailyCheck@"
ENTRY = PREFIX + "RewardScan"
PHASES = ("RewardTopA", "RewardTopB", "RewardBottomA", "RewardBottomB")
LAYOUT = "cn-daily-ten-v1"
TASK = "maa-daily-reward-scan"
TOTAL = 10
PITCH = 89
TOLERANCE = 12
OCR_ITEM = re.compile(
    r"\{ text: (.*?), rect: \[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\], score: ([\d.]+) \}"
)


def unknown(reason: str) -> dict:
    return {"layout": LAYOUT, "status": "unknown", "reason": reason,
            "claimed_tiers": None, "unclaimed_tiers": None,
            "claimed_tiers_min": None, "claimed_tiers_max": None,
            "unclaimed_tiers_min": None, "unclaimed_tiers_max": None,
            "daily_orundum": "unknown", "daily_annihilation_ticket": "unknown",
            "reminder_required": True, "message": "无法确认每日合成玉及剿灭扫荡券是否已领取。"}


def classify(pages: dict[str, list[dict]]) -> dict:
    """Map normalized row geometry to list positions, not task points or item names.

    Calibrated CN ten-tier layout: unclaimed tiers precede claimed tiers.
    Top shows positions 1..7; bottom positions 4..10. Repeated endpoints and
    the four overlapping positions must agree. Never infer zero from no OCR.
    """
    if set(pages) != set(PHASES):
        return unknown("missing_or_extra_phase")
    sets, uncertain = {}, {}
    for phase, items in pages.items():
        origin = 153 if phase.startswith("RewardTop") else -137
        visible = set(range(7)) if phase.startswith("RewardTop") else set(range(3, 10))
        rows, ambiguous = set(), set()
        for item in items:
            text = item["text"].strip()
            negative = any(token in text for token in ("未完成", "未领取", "未解锁", "可领取", "尚未", "没有完成", "不完成"))
            if not negative and text != "已完成" and "已" not in text and "完成" not in text:
                continue
            x, y, w, h = item["rect"]
            if (not 80 <= x <= 180
                    or not 20 <= w <= 100 or not 10 <= h <= 35):
                return unknown("marker_confidence_or_geometry")
            center = y + h / 2
            row = round((center - origin) / PITCH)
            if row not in visible or abs(center - (origin + row * PITCH)) > TOLERANCE:
                return unknown("layout_mismatch")
            if negative:
                return unknown("conflicting_tier_marker")
            if not 0 <= item["score"] <= 1:
                return unknown("invalid_marker_score")
            if row in rows or row in ambiguous:
                return unknown("duplicate_row_marker")
            if "完成" in text and 0.8 <= item["score"] <= 1:
                rows.add(row)
            else:
                ambiguous.add(row)
        sets[phase] = rows
        uncertain[phase] = ambiguous
    # 只有另一端点的两次可靠观测能修复局部歧义；缺失和矛盾不等于歧义。
    observed = {key: sorted(row+1 for row in value) for key, value in sets.items()}
    resolved = []
    for phase, rows in uncertain.items():
        other = PHASES[2:] if phase.startswith("RewardTop") else PHASES[:2]
        for row in sorted(rows):
            if row in range(3, 7) and all(row in sets[p] and row not in uncertain[p] for p in other):
                resolved.append({"phase": phase, "position": row+1, "supported_by": list(other)})
    for item in resolved:
        sets[item["phase"]].add(item["position"]-1)
    if sets[PHASES[0]] != sets[PHASES[1]] or sets[PHASES[2]] != sets[PHASES[3]]:
        return unknown("endpoint_not_stable")
    top, bottom = sets[PHASES[0]], sets[PHASES[2]]
    overlap = set(range(3, 7))
    if top & overlap != bottom & overlap:
        return unknown("overlap_mismatch")
    claimed = top | bottom
    if not claimed:
        return unknown("no_positive_marker")
    # Positive markers establish a lower bound. Missing or unreadable text is
    # not negative evidence, even if both reads omit the same position.
    lower, upper = len(claimed), TOTAL
    exact = lower == upper
    count = lower if exact else None
    orundum = "claimed" if lower >= 7 else "unknown"
    ticket = "claimed" if lower >= 9 else "unknown"
    missing = []
    if orundum != "claimed":
        missing.append("每日合成玉")
    if ticket != "claimed":
        missing.append("剿灭扫荡券")
    message = (f"每日奖励已领 {count}/10 档，剩余 0 档。" if exact
               else f"每日奖励至少已领 {lower}/10 档，已领范围 {lower}–{upper} 档，未领范围 0–{TOTAL-lower} 档。")
    message += ("无法确认是否已领取：" + "、".join(missing) + "。") if missing else "每日合成玉及剿灭扫荡券对应档位已领取。"
    return {"layout": LAYOUT, "status": "evaluated", "reason": "stable_tier_geometry" if exact else "bounded_positive_markers",
            "count_precision": "exact" if exact else "bounded",
            "claimed_tiers": count, "unclaimed_tiers": 0 if exact else None,
            "claimed_tiers_min": lower, "claimed_tiers_max": upper,
            "unclaimed_tiers_min": 0, "unclaimed_tiers_max": TOTAL-lower,
            "daily_orundum": orundum, "daily_annihilation_ticket": ticket,
            "reminder_required": bool(missing), "message": message,
            "basis": "tier-state inference, not inventory delta",
            "visible_claimed": {key: sorted(row+1 for row in value) for key, value in sets.items()},
            "observed_claimed": observed, "resolved_ambiguities": resolved,
            "uncertain_positions": sorted(row+1 for row in set(range(TOTAL)) - claimed)}


def game_day(instant: dt.datetime) -> str:
    return (instant.astimezone(dt.timezone(dt.timedelta(hours=8))) - dt.timedelta(hours=4)).date().isoformat()


def evaluate(report: dict) -> dict:
    evidence = report.get("evidence", {})
    if (report.get("child_exit_code") != 0 or report.get("wrapper_exit_code") != 0
            or evidence.get("state") != "bounded"
            or evidence.get("internal_error_lines") or evidence.get("callback_parse_error_lines")
            or evidence.get("subtask_error_lines")):
        return unknown("invalid_run_evidence")
    start_time = dt.datetime.fromisoformat(report["started_at"])
    end_time = dt.datetime.fromisoformat(report["ended_at"])
    if (start_time.tzinfo is None or end_time.tzinfo is None
            or not 0 <= (end_time-start_time).total_seconds() <= 180
            or game_day(start_time) != game_day(end_time)):
        return unknown("time_boundary_invalid")
    start, end = evidence["before_size"], evidence["after_size"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        return unknown("invalid_byte_interval")
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(start)
        data = handle.read(end-start)
    if len(data) != end-start or hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        return unknown("log_interval_changed")
    pages, done = {}, []
    chains, selection_ok = [], False
    for line in data.decode("utf-8").splitlines():
        marker = "Assistant::append_callback | "
        if marker in line:
            event, _, payload = line.split(marker, 1)[1].partition(" ")
            value = json.loads(payload)
            if event in {"TaskChainError", "SubTaskError"}:
                return unknown("task_error")
            if event.startswith("TaskChain"):
                chains.append((event, value.get("taskchain"), value.get("taskid")))
            if event == "SubTaskCompleted":
                if value.get("taskchain") != "Custom" or value.get("first") != [ENTRY]:
                    return unknown("unexpected_task_origin")
                detail = value.get("details", {})
                task = detail.get("task", "").removeprefix(PREFIX)
                if task == "RewardScan":
                    selection_ok = (detail.get("action") == "ClickSelf"
                                    and "日常任务" in detail.get("result", {}).get("text", ""))
                if task in PHASES:
                    if detail.get("action") != "DoNothing":
                        return unknown("unexpected_scan_action")
                    done.append(task)
        match = re.search(r"PipelineAnalyzer::analyze \| OcrDetect MaaDailyCheck@(RewardTop[AB]|RewardBottom[AB]) (.*)$", line)
        if match:
            if match[1] in pages:
                return unknown("duplicate_phase_ocr")
            entries = [{"text": item[0], "rect": list(map(int, item[1:5])), "score": float(item[5])}
                       for item in OCR_ITEM.findall(match[2])]
            if not entries:
                return unknown("empty_phase_ocr")
            pages[match[1]] = entries
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Custom")
            or chains[1][:2] != ("TaskChainCompleted", "Custom") or chains[0][2] != chains[1][2]
            or done != list(PHASES) or not selection_ok):
        return unknown("incomplete_scan_chain")
    result = classify(pages)
    result["game_day"] = game_day(end_time)
    result["observed_at"] = report["ended_at"]
    return result


def check_install(executable: str) -> None:
    query = subprocess.run([executable, "dir", "config", "--batch"], capture_output=True,
                           text=True, encoding="utf-8", errors="strict", check=True, timeout=30)
    config = Path(query.stdout.strip().splitlines()[-1])
    assets = Path(__file__).resolve().parents[1] / "assets/daily-checks"
    if any((config / "tasks" / (TASK + suffix)).exists() for suffix in (".json", ".yaml", ".yml")):
        raise ValueError("alternate scan task format exists; refusing ambiguous task lookup")
    task = tomllib.loads((config / "tasks" / (TASK + ".toml")).read_text(encoding="utf-8"))
    expected = tomllib.loads((assets / (TASK + ".toml")).read_text(encoding="utf-8"))
    if task != expected:
        raise ValueError("scan task differs from bundled task; refusing execution")
    resource = json.loads((config / "resource/tasks/tasks.json").read_text(encoding="utf-8-sig"))
    bundled = json.loads((assets / "tasks.json").read_text(encoding="utf-8"))
    for key, value in bundled.items():
        if key.startswith(PREFIX + "Reward") and resource.get(key) != value:
            raise ValueError(f"scan resource differs: {key}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="国服每日奖励档位检查；scan 会导航和滚动，但不会领奖。")
    parser.add_argument("--layout", choices=[LAYOUT], required=True,
                        help="确认当前客户端符合已验证布局；不是任意客户端的默认值")
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("evaluate", help="只读评估已有报告，观察时间不代表当前状态")
    replay.add_argument("--report", type=Path, required=True)
    scan = sub.add_parser("scan", help="先满足账号、可见窗口、设备与授权门禁；起点为任务页")
    scan.add_argument("--maa", default="maa")
    scan.add_argument("--profile", required=True)
    scan.add_argument("--output-dir", type=Path, required=True, help="本地报告目录，每次建立独立子目录")
    preflight = sub.add_parser("preflight", help="只读核对扫描资源部署，不运行游戏；profile 与页面起点仍需另行核验")
    preflight.add_argument("--maa", default="maa")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            check_install(args.maa)
            print(json.dumps({"status": "installed", "layout": LAYOUT,
                              "profile_checked": False, "game_state_checked": False}, ensure_ascii=False))
            return 0
        elif args.command == "scan":
            check_install(args.maa)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            run_dir = Path(tempfile.mkdtemp(prefix="reward-scan-", dir=args.output_dir))
            report_file = run_dir / "evidence.json"
            print("仅切换日常页、滚动并识别，不领取奖励。", flush=True)
            code = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("run_with_evidence.py")),
                                   "--report-file", str(report_file), "--", args.maa,
                                   "run", TASK, "--batch", "--profile", args.profile], check=False).returncode
            result = evaluate(json.loads(report_file.read_text(encoding="utf-8"))) if report_file.exists() else unknown("missing_report")
            if code:
                result = unknown("runner_failed")
            (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"结果目录：{run_dir}", flush=True)
        else:
            result = evaluate(json.loads(args.report.read_text(encoding="utf-8")))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "evaluated" else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        failure = ({"status": "not_ready", "reason": type(error).__name__,
                    "profile_checked": False, "game_state_checked": False}
                   if args.command == "preflight" else unknown(type(error).__name__))
        print(json.dumps(failure, ensure_ascii=False))
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
