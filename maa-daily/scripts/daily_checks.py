#!/usr/bin/env python3
"""Reusable, non-executing daily helpers. Live probes remain native maa-cli tasks."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PREFIX = "MaaDailyCheck@"


def plan(sanity: int, cost: int, maximum: int) -> dict:
    if sanity < 0 or cost <= 0 or not 1 <= maximum <= 10:
        raise ValueError("sanity >= 0, cost > 0, 1 <= maximum <= 10 required")
    count = sanity // cost
    batches, tail = divmod(count, maximum)
    # Only issue the next phase: recovery/regen/level-ups invalidate a queued tail.
    next_fight = None
    if count:
        next_fight = {
            "series": maximum if batches else tail,
            "times": batches * maximum if batches else tail,
            "medicine": 0, "medicine_expire_days": 0, "stone": 0,
        }
    return {"available_runs": count, "full_batches": batches,
            "estimated_tail": tail, "estimated_remainder": sanity % cost,
            "next_fight": next_fight, "reobserve_after_fight": bool(count)}


def inspect_report(report: dict) -> dict:
    """Read only the runner's byte interval; never fall back to the whole log."""
    result = {"sanity": None, "ocr": [], "daily_orundum": "unknown",
              "daily_annihilation_ticket": "unknown", "reminder_required": True,
              "reason": "use_reward_check_for_tier_status"}
    evidence = report.get("evidence", {})
    if (report.get("wrapper_exit_code") != 0 or evidence.get("state") != "bounded"
            or evidence.get("callback_parse_error_lines")
            or evidence.get("internal_error_lines")):
        result["reason"] = "invalid_run_evidence"
        return result
    start, end = evidence.get("before_size"), evidence.get("after_size")
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError("invalid log byte interval")
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(start)
        data = handle.read(end - start)
    if len(data) != end - start:
        raise ValueError("log interval no longer available")
    # A stale/rotated log may share its size. Require the runner's interval digest.
    import hashlib
    if hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        result["reason"] = "log_interval_changed_or_unhashed"
        return result
    seen = set()
    completed = 0
    for line in data.decode("utf-8", errors="replace").splitlines():
        marker = "Assistant::append_callback | "
        if marker in line:
            event, _, payload = line.split(marker, 1)[1].partition(" ")
            value = json.loads(payload)
            if event == "SubTaskCompleted":
                task = value.get("details", {}).get("task")
                first = value.get("first", [])
                # MaaCore strips namespace in details.task but retains the
                # original entry in first. Never normalize unrelated chains.
                if (isinstance(task, str) and "@" not in task
                        and value.get("taskchain") == "Custom"
                        and isinstance(first, list) and first
                        and all(isinstance(item, str) and item.startswith(PREFIX) for item in first)):
                    task = PREFIX + task
                seen.add(task)
            if event == "TaskChainCompleted" and value.get("taskchain") == "Custom":
                completed += 1
        match = re.search(r"OcrDetect (MaaDailyCheck@\w+) (.*)$", line)
        if match:
            result["ocr"].append({"node": match[1], "result": match[2]})
    if completed != 1:
        result["reason"] = "probe_not_completed"
        return result
    readings = []
    reading_nodes = set()
    for entry in result["ocr"]:
        if entry["node"] in {PREFIX + "Sanity", PREFIX + "SanityConfirm"} and entry["node"] in seen:
            values = re.findall(r"text: (\d+)\s*/\s*(\d+), rect: \[[^]]+\], score: ([\d.]+)", entry["result"])
            if len(values) == 1:
                current, cap = map(int, values[0][:2])
                if current >= 0 and 0 < cap <= 1000 and float(values[0][2]) >= 0.98:
                    readings.append({"current": current, "maximum": cap})
                    reading_nodes.add(entry["node"])
    if (PREFIX + "StagePage" in seen and len(reading_nodes) == 2
            and all(item == readings[0] for item in readings)):
        result["sanity"] = readings[0]
    # Full-page OCR is evidence for investigation, NOT item/state association.
    # In particular, two unrelated strings '合成玉' and '已领取' prove nothing.
    return result


def prepare(config: Path) -> list[str]:
    """Merge owned resource keys and add tasks; refuse conflicts, never change profile."""
    if not config.is_dir():
        raise ValueError("config directory must already exist")
    assets = Path(__file__).resolve().parents[1] / "assets" / "daily-checks"
    target = config / "resource" / "tasks" / "tasks.json"
    original = target.read_bytes() if target.exists() else None
    existing = json.loads(original.decode("utf-8-sig")) if original else {}
    additions = json.loads((assets / "tasks.json").read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError("user resource must be a JSON object")
    for key, value in additions.items():
        if key in existing and existing[key] != value:
            raise ValueError(f"resource conflict: {key}")
    merged = {**existing, **additions}
    writes = {}
    if existing != merged:
        writes[target] = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode()
    for name in ("maa-daily-check-sanity.toml", "maa-daily-check-rewards.toml",
                 "maa-daily-check-screen.toml", "maa-daily-reward-scan.toml"):
        dest = config / "tasks" / name
        for suffix in (".json", ".yaml", ".yml"):
            if dest.with_suffix(suffix).exists():
                raise ValueError(f"alternate task format conflicts: {dest.with_suffix(suffix).name}")
        content = (assets / name).read_bytes()
        if dest.exists() and dest.read_bytes() != content:
            raise ValueError(f"task conflict: {dest.name}")
        if not dest.exists():
            writes[dest] = content
    if original is not None and target in writes:
        import uuid
        backup = target.with_name(target.name + ".bak-" + uuid.uuid4().hex)
        with backup.open("xb") as handle:
            handle.write(original)
    # Preflight all conflicts before any mutation. No replacement of task files.
    for dest, content in writes.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest == target and original is not None:
            import os
            import tempfile
            with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as handle:
                handle.write(content)
                temporary = handle.name
            os.replace(temporary, dest)
        else:
            with dest.open("xb") as handle:
                handle.write(content)
    return [str(path) for path in writes]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="日常检查与无恢复预算的清体力规划；不会启动 MAA。")
    commands = parser.add_subparsers(dest="command", required=True)
    planner = commands.add_parser("plan")
    planner.add_argument("--sanity", type=int, required=True)
    planner.add_argument("--cost", type=int, required=True)
    planner.add_argument("--maximum", type=int, required=True)
    planner.add_argument("--stage", help="同时生成下一阶段的完整原生 Fight task")
    planner.add_argument("--task-file", type=Path, help="可选新建 JSON task，必须同时指定 --stage；不覆盖")
    reader = commands.add_parser("inspect")
    reader.add_argument("--report", type=Path, required=True)
    installer = commands.add_parser("prepare")
    installer.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = plan(args.sanity, args.cost, args.maximum)
            if args.task_file and (not args.stage or args.task_file.suffix.lower() != ".json"):
                raise ValueError("--task-file requires --stage and a .json suffix")
            if args.stage and value["next_fight"]:
                value["task"] = {"tasks": [{"type": "Fight", "params": {
                    **value["next_fight"], "stage": args.stage}}]}
                if args.task_file:
                    with args.task_file.open("x", encoding="utf-8") as handle:
                        json.dump(value["task"], handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
        elif args.command == "inspect":
            value = inspect_report(json.loads(args.report.read_text(encoding="utf-8")))
        else:
            value = {"written": prepare(args.config_dir), "profile_modified": False}
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
