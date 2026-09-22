#!/usr/bin/env python3
"""单账号日常的固定阶段执行器；不切号、不调度、不自动恢复。"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from daily_checks import stage_cost
from drain_sanity import Runtime, write_json
from infrast_check import inspect_report as inspect_infrast
from medicine_policy import load_policy
from medicine_sanity import preflight as medicine_preflight
from reward_check import check_install, game_day, scan_once
from run_with_evidence import classify_execution
from stage_runtime import normalize_stage
from recruit_check import inspect_report as inspect_recruit
from daily_report import summarize

HERE = Path(__file__).resolve().parent
MARKER = "Assistant::append_callback | "
STEPS = ("pre", "priority", "drain", "award", "checks")


def load_config(path: Path) -> dict:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed = {"version", "pre_tasks", "stage_task", "award_task", "medicine_policy",
               "maximum", "max_runs", "max_phases", "priority_goal"}
    if set(data) - allowed or data.get("version") != 1:
        raise ValueError("unsupported_daily_config")
    if (not isinstance(data.get("pre_tasks"), list) or not data["pre_tasks"]
            or len(data["pre_tasks"]) != len(set(data["pre_tasks"]))):
        raise ValueError("pre_tasks_required_and_unique")
    for value in [*data["pre_tasks"], data.get("stage_task"), data.get("award_task")]:
        if not isinstance(value, str) or not re.fullmatch(r"[\w-]+", value):
            raise ValueError("task_must_be_unambiguous_local_name_without_extension")
    if len(set([*data["pre_tasks"], data["stage_task"], data["award_task"]])) != len(data["pre_tasks"]) + 2:
        raise ValueError("daily_roles_must_be_distinct")
    for key, default in (("maximum", 10), ("max_runs", 100), ("max_phases", 5)):
        value = data.setdefault(key, default)
        if type(value) is not int or value < 1 or (key == "maximum" and value > 10):
            raise ValueError("invalid_daily_budget")
    if data.get("priority_goal") not in {"weekly_cap", "configured_runs"}:
        raise ValueError("explicit_priority_goal_required")
    policy = data.get("medicine_policy")
    if not isinstance(policy, str) or not policy:
        raise ValueError("medicine_policy_required")
    data["medicine_policy"] = str((path.parent / policy).resolve())
    load_policy(Path(data["medicine_policy"]))
    return data


def parse_resolved(text: str, types: dict | None = None) -> list[dict]:
    """窄适配 maa-cli 的 Adding task JSON；不自行实现 variants/星期规则。"""
    decoder = json.JSONDecoder()
    tasks = []
    for match in re.finditer(r"Adding task \[([^\]\r\n]+)\] with params:\s*", text):
        params, _ = decoder.raw_decode(text[match.end():])
        if not isinstance(params, dict):
            raise ValueError("invalid_resolved_params")
        kind = types.get(match[1]) if types is not None else match[1]
        if kind not in {"StartUp", "Recruit", "Mall", "Infrast", "Custom", "Fight", "Award"}:
            raise ValueError("unknown_resolved_task_type")
        tasks.append({"type": kind, "params": params})
    if not tasks and "Unstarted" in text:
        raise ValueError("unrecognized_cli_dry_run_format")
    return tasks


def validate_plan(pre: list[dict], selector: list[dict], award: list[dict]) -> str:
    if len(selector) != 1 or selector[0]["type"] != "Fight":
        raise ValueError("exactly_one_stage_required")
    choice = selector[0]["params"]
    if choice.get("client_type", "Official") != "Official":
        raise ValueError("daily_runner_requires_official_game_day")
    if choice.get("times") != 0 or any(choice.get(k, 0) != 0 for k in ("medicine", "medicine_expire_days", "stone")):
        raise ValueError("stage_policy_must_be_dry_run_only")
    stage = normalize_stage(choice.get("stage", ""))
    stage_cost(stage)  # 首版日常只接受成本表已收录关卡，通用组件仍支持显式成本。
    for task in pre:
        if task["type"] not in {"Recruit", "Mall", "Custom", "Infrast", "Fight"}:
            raise ValueError("unsupported_pre_task")
        p = task["params"]
        if task["type"] == "Fight":
            if (not str(p.get("stage", "")).endswith("@Annihilation")
                    or type(p.get("times")) is not int or p["times"] < 1
                    or p.get("series", 1) != 1
                    or any(p.get(k, 0) != 0 for k in ("medicine", "medicine_expire_days", "stone"))):
                raise ValueError("pre_fight_must_be_explicit_no_recovery_annihilation")
    if len(award) != 1 or award[0]["type"] != "Award" or award[0]["params"].get("award") is not True:
        raise ValueError("exactly_one_final_award_required")
    return stage


def verified_events(report: dict) -> list[tuple[str, dict]]:
    evidence = report["evidence"]
    start, end = evidence["before_size"], evidence["after_size"]
    if (report.get("child_exit_code") != 0 or report.get("wrapper_exit_code") != 0
            or report.get("runner_error") or evidence.get("state") != "bounded"
            or type(start) is not int or type(end) is not int or not 0 < end-start <= 64*1024*1024):
        raise ValueError("invalid_phase_evidence")
    with Path(evidence["log_file"]).open("rb") as handle:
        handle.seek(start)
        data = handle.read(end-start)
    if len(data) != end-start or hashlib.sha256(data).hexdigest() != evidence.get("interval_sha256"):
        raise ValueError("phase_log_changed")
    lines = data.decode("utf-8").splitlines()
    if classify_execution(lines)["status"] != "completed":
        raise ValueError("phase_execution_incomplete")
    return [(event, json.loads(body)) for line in lines if MARKER in line
            for event, _, body in [line.split(MARKER, 1)[1].partition(" ")]]


def priority_result(report: dict, params: dict, goal: str) -> dict:
    events = verified_events(report)
    if any(k == "SubTaskError" for k, _ in events):
        raise ValueError("priority_subtask_error")
    extras = [v for k, v in events if k == "SubTaskExtraInfo" and v.get("taskchain") == "Fight"]
    counts = [v["details"].get("times_finished") for v in extras if v.get("what") == "FightTimes"]
    if not counts or any(type(n) is not int or n < 0 for n in counts) or counts != sorted(counts):
        raise ValueError("priority_count_unverified")
    drops = [v["details"] for v in extras if v.get("what") == "StageDrops"]
    progress = [d["annihilation_weekly_process"] for d in drops if "annihilation_weekly_process" in d]
    capped = bool(progress and len(progress[-1]) == 2 and all(type(n) is int for n in progress[-1])
                  and 0 < progress[-1][0] == progress[-1][1])
    if (goal == "weekly_cap" and not capped) or (goal == "configured_runs" and counts[-1] < params["times"]):
        raise ValueError("priority_goal_unverified")
    return {"status": "completed", "goal": goal, "completed_runs": counts[-1],
            "weekly_progress": progress[-1] if progress else None}


def execute_flow(ops, save) -> dict:
    """固定顺序，不接受任意 DAG、跳步、resume 或旧报告自动复用。"""
    result = {"status": "running", "game_day": ops.day, "account": ops.account,
              "steps": {s: {"status": "pending"} for s in STEPS}, "reminder_required": True}
    save(result)
    current = "pre"
    try:
        for current in STEPS:
            ops.check_day()
            result["steps"][current] = {"status": "running"}
            save(result)
            value = getattr(ops, current)()
            result["steps"][current] = value
            save(result)
            if value.get("status") not in {"completed", "completed_with_reminder", "not_scheduled"}:
                raise ValueError(current + "_not_completed")
            ops.check_day()
        notices = [s for s, v in result["steps"].items() if v.get("reminder_required") or v["status"] == "completed_with_reminder"]
        result.update(status="completed_with_reminder" if notices else "completed",
                      reminder_required=bool(notices), reminder_steps=notices)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        if result["steps"][current]["status"] == "running":
            result["steps"][current] = {"status": "unverified", "reason": str(error)}
            partial = getattr(ops, "pre_observations", None)
            if current == "pre" and isinstance(partial, list):
                result["steps"][current]["tasks"] = partial
        result.update(status="incomplete", reason=str(error), reminder_required=True)
    result["missing_steps"] = [s for s, v in result["steps"].items()
                               if v["status"] not in {"completed", "completed_with_reminder", "not_scheduled"}]
    save(result)
    return result


class Daily:
    def __init__(self, config: dict, maa: str, profile: str, account: str, output: Path):
        self.config, self.maa, self.profile, self.account = config, maa, profile, account
        self.day = game_day(dt.datetime.now(dt.timezone.utc))
        check_install(maa)
        self.runtime = Runtime(maa, profile, output)
        self.output = self.runtime.output
        self.before, self.after, self.pre_reports, self.priority_reports = [], [], [], []
        for name in config["pre_tasks"]:
            self.before.extend(self.resolve(name))
        selector, self.after = self.resolve(config["stage_task"]), self.resolve(config["award_task"])
        self.stage = validate_plan(self.before, selector, self.after)
        self.runtime.configure_stage(self.stage)
        medicine_preflight(self.runtime)
        self.policy = self.output / "medicine-policy.toml"
        self.policy.write_bytes(Path(config["medicine_policy"]).read_bytes())
        write_json(self.output / "plan.json", {"game_day": self.day, "account": account, "config": config,
                    "stage": self.stage, "pre": self.before, "award": self.after,
                    "policy_sha256": hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                    "identity_note": "caller verified; account alias is not a game identity assertion"})
        self.check_day()

    def resolve(self, name):
        # Reject ambiguous alternative files before letting maa choose one.
        files = [self.runtime.config / "tasks" / (name + ext) for ext in (".toml", ".json", ".yaml", ".yml")]
        if sum(p.is_file() for p in files) != 1:
            raise ValueError("missing_or_ambiguous_task: " + name)
        source = next(p for p in files if p.is_file())
        raw_source = source.read_bytes()
        if source.suffix == ".toml":
            native = tomllib.loads(raw_source.decode("utf-8"))
        elif source.suffix == ".json":
            native = json.loads(raw_source)
        else:
            raise ValueError("daily_runner_supports_toml_or_json_sources")
        if set(native) != {"tasks"}:
            raise ValueError("unsupported_native_top_level_options")
        types, unconditional = {}, set()
        for task in native["tasks"]:
            label = task.get("name", task["type"])
            if label in types:
                raise ValueError("ambiguous_native_task_label")
            types[label] = task["type"]
            if not task.get("variants") or any("condition" not in v for v in task["variants"]):
                unconditional.add(label)
        command = [self.maa, "run", name, "--profile", self.profile, "--batch", "--dry-run", "-vv"]
        p = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True, timeout=120)
        raw = p.stdout + "\n" + p.stderr
        (self.output / (name + ".dry-run.txt")).write_text(raw, encoding="utf-8")
        if source.read_bytes() != raw_source:
            raise ValueError("native_config_changed_during_resolution")
        (self.output / ("source-" + source.name)).write_bytes(raw_source)
        tasks = parse_resolved(raw, types)
        labels = re.findall(r"Adding task \[([^\]\r\n]+)\] with params:", raw)
        if not unconditional.issubset(labels) or len(labels) != len(set(labels)):
            raise ValueError("missing_or_duplicate_resolved_task")
        if not tasks and "Instance destroyed" not in raw:
            raise ValueError("no_resolved_task_boundary: " + name)
        return tasks

    def check_day(self):
        if game_day(dt.datetime.now(dt.timezone.utc)) != self.day:
            raise ValueError("game_day_changed")

    def native(self, task, phase):
        report = self.runtime.run([task], phase)
        events = verified_events(report)
        starts = [v for k, v in events if k == "TaskChainStart"]
        if len(starts) != 1 or starts[0].get("taskchain") != task["type"]:
            raise ValueError("unexpected_phase_chain")
        return report

    def pre(self):
        observations = []
        self.pre_observations = observations
        for index, task in enumerate(self.before):
            self.check_day()
            report = self.native(task, "pre-" + str(index))
            self.pre_reports.append(report)
            e = report["evidence"]
            observations.append({"type": task["type"], "execution": "completed", "business_result": "not_evaluated",
                                 "internal_error_lines": e.get("internal_error_lines", []),
                                 "subtask_error_lines": e.get("subtask_error_lines", [])})
            if task["type"] == "Recruit":
                observations[-1]["recruitment"] = inspect_recruit(report)
            # Priority may be embedded in a pre file; validate immediately, not after consuming normal sanity.
            if task["type"] == "Fight":
                self.priority_reports.append(priority_result(report, task["params"], self.config["priority_goal"]))
        warnings = any(v["internal_error_lines"] or v["subtask_error_lines"]
                       or v.get("recruitment", {}).get("reminder_required") for v in observations)
        return {"status": "completed_with_reminder" if warnings else "completed", "task_count": len(self.before),
                "tasks": observations, "reminder_required": warnings, "reports": str(self.output / "processes.json")}

    def priority(self):
        return {"status": "completed" if self.priority_reports else "not_scheduled", "results": self.priority_reports}

    def drain(self):
        target = self.output / "sanity"
        env = os.environ.copy()
        env["MAA_CONFIG_DIR"] = str(self.runtime.stage_config)
        code = subprocess.run([sys.executable, "-X", "utf8", "-B", str(HERE / "drain_sanity.py"), "run",
                "--stage", self.stage, "--policy", str(self.policy), "--maa", self.maa, "--profile", self.profile,
                "--output-dir", str(target), "--maximum", str(self.config["maximum"]),
                "--max-runs", str(self.config["max_runs"]), "--max-phases", str(self.config["max_phases"])], env=env).returncode
        results = list(target.glob("drain-*/result.json"))
        if len(results) != 1:
            raise ValueError("drain_result_missing_or_ambiguous")
        value = json.loads(results[0].read_text(encoding="utf-8"))
        value["result_file"] = str(results[0])
        if code or value.get("status") not in {"completed", "completed_with_reminder"}:
            return {**value, "status": "stopped"}
        sanity = value.get("remaining_sanity")
        if type(sanity) is not int or not 0 <= sanity < stage_cost(self.stage) or value.get("stage") != self.stage:
            raise ValueError("drain_goal_unverified")
        return value

    def award(self):
        report = self.native(self.after[0], "award")
        return {"status": "completed", "execution_only": True, "ended_at": report["ended_at"]}

    def checks(self):
        infrastructure = [inspect_infrast(r) for r in self.pre_reports
                          if any(c.get("taskchain") == "Infrast" for c in r["evidence"].get("task_chains", []))]
        self.check_day()
        rewards, directory = scan_once(self.maa, self.profile, self.output / "rewards")
        value = {"status": "completed" if rewards["status"] == "evaluated" else "unverified",
                 "rewards": rewards, "reward_directory": str(directory), "infrastructure": infrastructure,
                 "reminder_required": rewards["reminder_required"] or any(r["reminder_required"] for r in infrastructure)}
        write_json(self.output / "checks.json", value)
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preflight", "run"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--maa", default="maa")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account", required=True, help="本地报告别名，不执行切号；调用者先核验身份与设备")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-format", choices=["brief", "json"], default="brief",
                        help="run 的结束输出：默认用户简报；json 保留旧版详细结果输出，退出码不变")
    args = parser.parse_args(argv)
    lock = None
    try:
        config = load_config(args.config.resolve())
        # Exclusive cooperative lock per actual MAA config root, including preflight; never break stale locks.
        root = check_install(args.maa)
        lock_path = root / "maa-daily-run.lock"
        lock = lock_path.open("x", encoding="utf-8")
        lock.write(json.dumps({"pid": os.getpid(), "account": args.account})); lock.flush()
        ops = Daily(config, args.maa, args.profile, args.account, args.output_dir)
        print("日常证据目录：" + str(ops.output), flush=True)
        if args.mode == "preflight":
            result = {"status": "prepared", "game_day": ops.day, "stage": ops.stage, "game_operated": False}
        else:
            result = execute_flow(ops, lambda r: write_json(ops.output / "daily-result.json", r))
            brief = summarize(result)
            write_json(ops.output / "brief.json", brief)
            (ops.output / "brief.md").write_text(brief["text"] + "\n", encoding="utf-8")
        if args.mode == "run" and args.output_format == "brief":
            print("\n用户简报：\n" + brief["text"], flush=True)
            print("\n详细结果：" + str(ops.output / "daily-result.json"))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"prepared", "completed", "completed_with_reminder"} else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        failure = json.dumps({"status": "incomplete", "reason": str(error)}, ensure_ascii=False)
        if args.mode == "run" and args.output_format == "brief":
            print(f"\n用户简报：\n{args.account}：日常未完整完成，未能生成完整简报；请根据错误与已有证据核对停止位置，不要直接重跑。")
            print(failure, file=sys.stderr)
        else:
            print(failure)
        return 2
    finally:
        if lock is not None:
            lock.close()
            # Interrupt/unhandled exception may leave a live child. Do not release its cooperative gate.
            if sys.exc_info()[0] is None:
                lock_path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
