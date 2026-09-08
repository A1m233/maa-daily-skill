#!/usr/bin/env python3
"""Bounded, no-recovery supply-stage draining through maa-cli and evidence runner."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from daily_checks import inspect_report, plan

STAGES = {"AP-5": "VerifyAP5", "CE-6": "VerifyCE6", "LS-6": "VerifyLS6"}
PREFIX = "MaaDailyCheck@"
MARKER = "Assistant::append_callback | "


def write_json(path: Path, value: dict) -> None:
    # Atomic local checkpoint; never replace a native task or user configuration.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def callbacks(report: dict) -> list[tuple[str, dict]]:
    evidence = report["evidence"]
    if report.get("wrapper_exit_code") != 0 or report.get("child_exit_code") != 0 or evidence.get("state") != "bounded":
        raise ValueError("process_failed_or_unbounded")
    start, end = evidence["before_size"], evidence["after_size"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end or end-start > 64*1024*1024:
        raise ValueError("invalid_interval")
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(start)
        data = handle.read(end-start)
    if len(data) != end-start or hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        raise ValueError("changed_log_interval")
    events = []
    for line in data.decode("utf-8").splitlines():
        if MARKER in line:
            kind, _, payload = line.split(MARKER, 1)[1].partition(" ")
            value = json.loads(payload)
            if not isinstance(value, dict) or kind in {"TaskChainError", "SubTaskError"}:
                raise ValueError("invalid_or_failed_callback")
            events.append((kind, value))
    return events


def navigation_tasks(stage: str, start_at: str) -> list[dict]:
    if stage not in STAGES or start_at not in {"home", "terminal", "prepared"}:
        raise ValueError("unsupported_navigation")
    names = (["Terminal-Entry"] if start_at == "home" else [])
    if start_at != "prepared":
        names.append(stage)
    return [{"type": "Custom", "params": {"task_names": [name]}} for name in names]


def read_probe(report: dict, stage: str) -> int:
    events = callbacks(report)
    entry = PREFIX + STAGES[stage]
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Custom")
            or chains[1][:2] != ("TaskChainCompleted", "Custom") or chains[0][2] != chains[1][2]):
        raise ValueError("ambiguous_probe_chain")
    verified = False
    for kind, value in events:
        if kind != "SubTaskCompleted":
            continue
        detail = value.get("details", {})
        if value.get("first") != [entry] or value.get("taskid") != chains[0][2] or detail.get("action") != "DoNothing":
            raise ValueError("unexpected_probe_action_or_origin")
        if detail.get("task", "").removeprefix(PREFIX) == STAGES[stage]:
            match = detail.get("result", {})
            verified = match.get("text") == stage and match.get("score", 0) >= 0.98
    reading = inspect_report(report).get("sanity")
    if not verified or reading is None:
        raise ValueError("stage_or_sanity_unverified")
    return reading["current"]


def check_fight(report: dict, stage: str, expected: int) -> None:
    events = callbacks(report)
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Fight")
            or chains[1][:2] != ("TaskChainCompleted", "Fight") or chains[0][2] != chains[1][2]):
        raise ValueError("ambiguous_fight_chain")
    counts, dropped_stages = [], []
    for kind, value in events:
        if kind.startswith("SubTask") and (value.get("taskchain") != "Fight" or value.get("taskid") != chains[0][2]):
            raise ValueError("unexpected_fight_event_origin")
        if kind == "SubTaskExtraInfo":
            detail = value.get("details", {})
            if value.get("what") == "FightTimes":
                count = detail.get("times_finished")
                if type(count) is int:
                    counts.append(count)
            if value.get("what") == "StageDrops":
                dropped_stages.append(detail.get("stage", {}).get("stageCode"))
        if kind in {"SubTaskStart", "SubTaskCompleted"}:
            detail = value.get("details", {})
            node = detail.get("task", "").split("@")[-1]
            if (node in {"MedicineConfirm", "ExpiringMedicineConfirm", "StoneConfirm"}
                    and detail.get("action") not in {None, "DoNothing"}):
                raise ValueError("unexpected_recovery_action")
    if not counts or max(counts) != expected or not dropped_stages or any(s != stage for s in dropped_stages):
        raise ValueError("fight_count_or_stage_unverified")


def drain(observe, fight, save, *, cost: int, maximum: int, max_phases: int, max_runs: int) -> dict:
    plan(0, cost, maximum)
    if max_phases < 1 or max_runs < 1:
        raise ValueError("positive_bounds_required")
    result = {"status": "running", "completed_runs": 0, "phases": [],
              "remaining_sanity": None, "last_known_sanity": None}
    save(result)
    try:
        sanity = observe()
        for _ in range(max_phases):
            result["remaining_sanity"] = sanity
            result["last_known_sanity"] = sanity
            parameters = plan(sanity, cost, maximum)["next_fight"]
            if parameters is None:
                result.update(status="completed", reason="below_one_run")
                save(result)
                return result
            if result["completed_runs"] + parameters["times"] > max_runs:
                raise ValueError("run_budget_exceeded")
            result["phases"].append({"sanity_before": sanity, "params": parameters, "status": "running"})
            # 开战后旧读数不再代表当前理智；失败时也不能复用它继续规划。
            result["remaining_sanity"] = None
            save(result)
            fight(parameters)
            result["completed_runs"] += parameters["times"]
            remaining = observe()
            result["remaining_sanity"] = remaining
            result["last_known_sanity"] = remaining
            result["phases"][-1].update(status="completed", sanity_after=remaining)
            if remaining >= sanity:
                raise ValueError("sanity_not_decreasing")
            sanity = remaining
            save(result)
        if sanity < cost:
            result.update(status="completed", reason="below_one_run")
        else:
            raise ValueError("phase_budget_exceeded")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result.update(status="stopped", reason=str(error))
        if result["phases"] and result["phases"][-1]["status"] == "running":
            result["phases"][-1].update(status="unverified", reason=str(error))
    save(result)
    return result


class Runtime:
    def __init__(self, maa: str, profile: str, output: Path):
        self.maa, self.profile = maa, profile
        query = subprocess.run([maa, "dir", "config", "--batch"], check=True, capture_output=True,
                               text=True, encoding="utf-8", timeout=30)
        self.config = Path(query.stdout.strip().splitlines()[-1])
        assets = Path(__file__).resolve().parents[1] / "assets"
        resource = json.loads((self.config / "resource/tasks/tasks.json").read_text(encoding="utf-8-sig"))
        for file in (assets / "daily-checks/tasks.json", assets / "drain-sanity/tasks.json"):
            for key, value in json.loads(file.read_text(encoding="utf-8")).items():
                if key.startswith(PREFIX + "Verify") or key in {PREFIX + s for s in ("StagePage", "Sanity", "SanityConfirm")}:
                    if resource.get(key) != value:
                        raise ValueError("probe_resource_missing_or_changed: " + key)
        output.mkdir(parents=True, exist_ok=True)
        self.output = Path(tempfile.mkdtemp(prefix="drain-", dir=output))
        self.reports = []

    def run(self, tasks: list[dict], phase: str) -> dict:
        name = "maa-drain-" + uuid.uuid4().hex
        target = self.config / "tasks" / (name + ".json")
        with target.open("x", encoding="utf-8") as handle:
            json.dump({"tasks": tasks}, handle, ensure_ascii=False)
        # Keep exact generated task paths for audit; no automatic deletion or retry.
        record = {"phase": phase, "task_file": str(target), "report_file": str(self.output / (name + ".json"))}
        self.reports.append(record)
        write_json(self.output / "processes.json", {"processes": self.reports})
        command = [self.maa, "run", name, "--profile", self.profile, "--batch", "--user-resource"]
        subprocess.run(command + ["--dry-run"], check=True)
        runner = Path(__file__).with_name("run_with_evidence.py")
        code = subprocess.run([sys.executable, "-B", str(runner), "--report-file", record["report_file"], "--", *command]).returncode
        record["runner_exit_code"] = code
        try:
            report = json.loads(Path(record["report_file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            record["report_error"] = type(error).__name__
            write_json(self.output / "processes.json", {"processes": self.reports})
            raise ValueError("runner_report_unavailable") from error
        record["internal_error_lines"] = report.get("evidence", {}).get("internal_error_lines", [])
        record["subtask_error_lines"] = report.get("evidence", {}).get("subtask_error_lines", [])
        write_json(self.output / "processes.json", {"processes": self.reports})
        if code:
            raise ValueError("runner_failed: " + str(code))
        return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="候选清体力组件：仅 AP-5/CE-6/LS-6，无药、无源石；不切号、不领奖。")
    parser.add_argument("mode", choices=["probe", "run"])
    parser.add_argument("--stage", choices=list(STAGES), required=True)
    parser.add_argument("--start-at", choices=["home", "terminal", "prepared"], required=True)
    parser.add_argument("--maa", default="maa")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cost", type=int)
    parser.add_argument("--maximum", type=int, default=10)
    parser.add_argument("--max-phases", type=int, default=5)
    parser.add_argument("--max-runs", type=int, default=100)
    args = parser.parse_args(argv)
    if args.mode == "run" and (args.cost is None or args.cost <= 0 or not 1 <= args.maximum <= 10
                               or args.max_phases < 1 or args.max_runs < 1):
        parser.error("run requires positive --cost and budgets, maximum in 1..10")
    try:
        runtime = Runtime(args.maa, args.profile, args.output_dir)
        print("本地证据目录：" + str(runtime.output), flush=True)
        nav = navigation_tasks(args.stage, args.start_at)
        if nav:
            report = runtime.run(nav, "navigation")
            callbacks(report)
            if report["evidence"].get("internal_error_lines"):
                raise ValueError("navigation_has_errors")
        def observe():
            report = runtime.run([{"type": "Custom", "params": {"task_names": [PREFIX + STAGES[args.stage]]}}], "probe")
            return read_probe(report, args.stage)
        def fight(parameters):
            report = runtime.run([{"type": "Fight", "params": {"stage": args.stage, **parameters}}], "fight")
            check_fight(report, args.stage, parameters["times"])
        if args.mode == "probe":
            result = {"status": "observed", "sanity": observe(), "stage": args.stage}
        else:
            result = drain(observe, fight, lambda r: write_json(runtime.output / "result.json", r),
                           cost=args.cost, maximum=args.maximum, max_phases=args.max_phases, max_runs=args.max_runs)
        result["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        result["warning_report_files"] = [r["report_file"] for r in runtime.reports
                                          if r.get("internal_error_lines") or r.get("subtask_error_lines")
                                          or r.get("runner_exit_code") or r.get("report_error")]
        write_json(runtime.output / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"completed", "observed"} else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "stopped", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
