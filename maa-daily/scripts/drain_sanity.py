#!/usr/bin/env python3
"""Bounded supply-stage draining, optional native fixed-series medicine phase."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from daily_checks import inspect_report, plan, stage_cost
from medicine_policy import load_policy, validate_policy, recovery_task, inspect_recovery
from stage_runtime import VERIFY, normalize_stage, probe_task, isolated_config, STOP_NODES

STAGES = {"AP-5": "VerifyAP5", "CE-6": "VerifyCE6", "LS-6": "VerifyLS6"}  # 只用于旧报告兼容，不限制新入口。
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


def navigation_tasks(stage: str, start_at: str = "auto") -> list[dict]:
    stage = normalize_stage(stage)
    if start_at not in {"auto", "home", "terminal", "prepared"}:
        raise ValueError("unsupported_navigation")
    if start_at == "prepared":
        return []
    # 正数才能启用原生 StageNavigationTask；必须搭配隔离的 Stop 资源。
    return [{"type": "Fight", "params": {"stage": stage, "times": 1, "series": 1,
             "medicine": 0, "medicine_expire_days": 0, "stone": 0}}]


def read_navigation(report: dict, stage: str) -> str:
    normalize_stage(stage)
    events = callbacks(report)
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Fight")
            or chains[1][:2] != ("TaskChainCompleted", "Fight") or chains[0][2] != chains[1][2]):
        raise ValueError("ambiguous_navigation_chain")
    stopped, navigated = False, False
    for kind, value in events:
        detail = value.get("details", {})
        node = detail.get("task", "").split("@")[-1]
        if kind.startswith("SubTask"):
            if value.get("taskchain") != "Fight":
                raise ValueError("unexpected_navigation_origin")
            if (value.get("what") in {"UseMedicine", "StageDrops"}
                    or (value.get("what") == "FightTimes" and detail.get("times_finished", 0) != 0)
                    or node.startswith("PRTS") or node in {"EndOfAction", "BattleOfficiallyBegin"}):
                raise ValueError("unexpected_navigation_battle")
            if node in STOP_NODES and detail.get("action") not in {None, "Stop", "DoNothing"}:
                raise ValueError("unexpected_navigation_resource_action")
        if kind == "SubTaskCompleted" and value.get("subtask") == "StageNavigationTask":
            navigated = True
        if kind == "SubTaskStart" and node == "FightBegin" and detail.get("action") == "Stop":
            stopped = True
    if not stopped or not navigated:
        raise ValueError("navigation_stop_or_stage_unverified")
    return "prepared"  # 下一进程独立 OCR 验证目标；不是最终开战许可。


def valid_ocr(match: dict, text: str, minimum: float, roi: tuple) -> bool:
    if not isinstance(match, dict):
        return False
    rect, score = match.get("rect"), match.get("score")
    if (match.get("text") != text or type(score) not in (int, float)
            or not math.isfinite(score) or not minimum <= score <= 1
            or not isinstance(rect, list) or len(rect) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in rect)):
        return False
    x, y, w, h = rect
    left, top, width, height = roi
    return w > 0 and h > 0 and left <= x and top <= y and x+w <= left+width and y+h <= top+height


def read_probe(report: dict, stage: str) -> int:
    events = callbacks(report)
    stage = normalize_stage(stage)
    # 接受旧版本报告用于回放；新运行始终生成参数化 VerifyStage。
    legacy = PREFIX + STAGES.get(stage, "VerifyStage")
    origins = [v.get("first") for k, v in events if k == "SubTaskCompleted"]
    entry = legacy if origins and all(o == [legacy] for o in origins) else VERIFY
    chains = [(k, v.get("taskchain"), v.get("taskid")) for k, v in events if k.startswith("TaskChain")]
    if (len(chains) != 2 or chains[0][:2] != ("TaskChainStart", "Custom")
            or chains[1][:2] != ("TaskChainCompleted", "Custom") or chains[0][2] != chains[1][2]):
        raise ValueError("ambiguous_probe_chain")
    completed = []
    for kind, value in events:
        if kind not in {"SubTaskStart", "SubTaskCompleted"}:
            continue
        detail = value.get("details", {})
        if (value.get("first") != [entry] or value.get("taskid") != chains[0][2]
                or value.get("taskchain") != "Custom" or detail.get("action") != "DoNothing"
                or detail.get("algorithm") != "OcrDetect"):
            raise ValueError("unexpected_probe_action_or_origin")
        if kind == "SubTaskCompleted":
            completed.append((detail.get("task", "").removeprefix(PREFIX), detail.get("result", {})))
    if [name for name, _ in completed] != [entry.removeprefix(PREFIX), "StagePage", "Sanity", "SanityConfirm"]:
        raise ValueError("incomplete_or_ambiguous_probe")
    stage_match, page_match, first, second = [match for _, match in completed]
    # 0.90 是联合条件下的保守候选下限，不是正确率；不用于理智数字。
    if (not valid_ocr(stage_match, stage, 0.90, (845, 72, 220, 50))
            or not valid_ocr(page_match, "开始行动", 0.98, (1010, 625, 260, 61))):
        raise ValueError("stage_or_page_unverified")
    for match in (first, second):
        if (not re.fullmatch(r"\d+\s*/\s*\d+", match.get("text", ""))
                or not valid_ocr(match, match["text"], 0.98, (1120, 20, 160, 40))):
            raise ValueError("sanity_unverified")
    pairs = [tuple(map(int, re.findall(r"\d+", match["text"]))) for match in (first, second)]
    reading = inspect_report(report).get("sanity")
    if (reading is None or pairs[0] != pairs[1]
            or pairs[0] != (reading["current"], reading["maximum"])):
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
            # fight() 已核验实际正场次；升级/自然回复可能抵消消耗。
            # 不猜上涨原因，重新计算；总场次和阶段预算限制重复执行。
            if remaining >= sanity:
                result["phases"][-1]["sanity_non_decreasing"] = True
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


def run_daily(observe, fight, recover, check, save, *, policy, cost, maximum, max_phases, max_runs):
    """一个清理入口；recover 返回已核验场次/用药，不用库存猜倍率。"""
    policy = validate_policy(policy)
    plan(0, cost, maximum)
    if type(max_runs) is not int or type(max_phases) is not int or min(max_runs, max_phases) < 1:
        raise ValueError("positive_bounds_required")
    result = {"status": "running", "policy": policy, "completed_runs": 0, "medicine_used": 0,
              "medicine_goal": "unknown" if policy["mode"] == "use_and_drain" else "not_requested",
              "remaining_sanity": None, "last_known_sanity": None, "phases": [],
              "medicine_check": {"status": "unknown", "reason": "not_checked", "reminder_required": True},
              "reminder_required": True}
    save(result)
    try:
        sanity = observe()
        result.update(remaining_sanity=sanity, last_known_sanity=sanity)
        plan(sanity, cost, maximum)  # 校验初始读数，即使用药也先读数。
        phases_left = max_phases
        if policy["mode"] == "use_and_drain":
            limit = max_runs // maximum * maximum
            if limit < maximum:
                raise ValueError("budget_below_full_medicine_batch")
            params = {"series": maximum, "times": limit, "medicine": 0,
                      "medicine_expire_days": policy["medicine_expire_days"], "stone": 0}
            result["phases"].append({"kind": "medicine_bulk", "status": "running",
                                     "sanity_before": sanity, "params": params})
            result["remaining_sanity"] = None
            save(result)
            used = recover(params)
            count, bottles = used["completed_runs"], used["medicine_used"]
            if (type(count) is not int or not 0 <= count <= limit or count % maximum
                    or type(bottles) is not int or bottles < 0):
                raise ValueError("invalid_native_bulk_result")
            result.update(completed_runs=count, medicine_used=bottles)
            result["phases"][-1].update(status="completed", **used)
            save(result)  # 后续读数失败也保留已确认用药。
            sanity = observe()
            plan(sanity, cost, maximum)
            result.update(remaining_sanity=sanity, last_known_sanity=sanity)
            result["phases"][-1]["sanity_after"] = sanity
            phases_left -= 1
            # 满额退出不是理智不足；不能静默把用药目标改成无药补尾。
            if count == limit:
                raise ValueError("medicine_run_budget_reached")
            if count == 0 and bottles == 0:
                result["phases"][-1]["no_resource_progress"] = True
            # 即使零战斗但吃过药，也仅重读补尾；不重试固定十连。
        if sanity >= cost:
            if phases_left < 1:
                raise ValueError("phase_budget_exceeded")
            budget = max_runs - result["completed_runs"]
            if budget < 1:
                raise ValueError("run_budget_exceeded")
            base_count = result["completed_runs"]
            base_phases = list(result["phases"])
            def checkpoint(tail):
                result.update(completed_runs=base_count + tail["completed_runs"],
                              remaining_sanity=tail["remaining_sanity"],
                              last_known_sanity=tail["last_known_sanity"],
                              phases=base_phases + tail["phases"])
                save(result)
            # 首次使用刚取得的初始/恢复后读数，之后每场阶段再独立读取。
            # 两次调用之间没有游戏动作，不重复启动同一准备页检查。
            first_read = [sanity]
            def tail_observe():
                return first_read.pop() if first_read else observe()
            tail = drain(tail_observe, fight, checkpoint, cost=cost, maximum=maximum,
                         max_phases=phases_left, max_runs=budget)
            if tail["status"] != "completed":
                raise ValueError(tail["reason"])
        result.update(status="completed", reason="below_one_run")
        save(result)
        try:
            result["medicine_check"] = check()
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            result["medicine_check"] = {"status": "unknown", "reason": str(error), "reminder_required": True}
        result["reminder_required"] = result["medicine_check"].get("reminder_required", True)
        if result["reminder_required"]:
            result["status"] = "completed_with_reminder"
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
        for file in (assets / "daily-checks/tasks.json",):
            for key, value in json.loads(file.read_text(encoding="utf-8")).items():
                if key in {PREFIX + s for s in ("StagePage", "Sanity", "SanityConfirm")}:
                    if resource.get(key) != value:
                        raise ValueError("probe_resource_missing_or_changed: " + key)
        output.mkdir(parents=True, exist_ok=True)
        self.output = Path(tempfile.mkdtemp(prefix="drain-", dir=output))
        self.reports = []

    def configure_stage(self, stage: str) -> None:
        stage = normalize_stage(stage)
        if not re.fullmatch(r"[\w.-]+", self.profile) or self.profile in {".", ".."}:
            raise ValueError("profile_must_be_a_local_name")
        self.stage_config = isolated_config(self.config, self.output / "config-run", stage, navigation=False)
        self.navigation_config = isolated_config(self.config, self.output / "config-navigation", stage, navigation=True)

    def run(self, tasks: list[dict], phase: str, *, navigation: bool = False) -> dict:
        config = getattr(self, "stage_config", self.config)
        if navigation:
            config = self.navigation_config
        env = os.environ.copy()
        env["MAA_CONFIG_DIR"] = str(config)
        if config != self.config:
            query = subprocess.run([self.maa, "dir", "config", "--batch"], env=env, check=True,
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
            if Path(query.stdout.strip().splitlines()[-1]).resolve() != config.resolve():
                raise ValueError("isolated_config_not_honored")
        name = "maa-drain-" + uuid.uuid4().hex
        target = config / "tasks" / (name + ".json")
        with target.open("x", encoding="utf-8") as handle:
            json.dump({"tasks": tasks}, handle, ensure_ascii=False)
        # Keep exact generated task paths for audit; no automatic deletion or retry.
        record = {"phase": phase, "task_file": str(target), "report_file": str(self.output / (name + ".json"))}
        record["config_dir"] = str(config)
        record["navigation_guard"] = navigation
        self.reports.append(record)
        write_json(self.output / "processes.json", {"processes": self.reports})
        command = [self.maa, "run", name, "--profile", self.profile, "--batch", "--user-resource"]
        subprocess.run(command + ["--dry-run"], check=True, env=env)
        runner = Path(__file__).with_name("run_with_evidence.py")
        code = subprocess.run([sys.executable, "-B", str(runner), "--report-file", record["report_file"], "--", *command], env=env).returncode
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


def navigate(runtime, stage: str, start_at: str = "auto") -> None:
    nav = navigation_tasks(stage, start_at)
    if not nav:
        return
    report = runtime.run(nav, "navigation", navigation=True)
    # 原生 Fight 初始化也会产出内部告警；完整保留于 Runtime 报告。
    # 依赖选关完成、Stop 与无资源动作证据，不按 ERR 数量或节点白名单判失败。
    read_navigation(report, stage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="统一清体力：固定倍率、可选临期药、收尾检查；不切号、不领奖。")
    parser.add_argument("mode", choices=["probe", "run"])
    parser.add_argument("--stage", required=True, help="MAA 支持的标准关卡代码；不接受剿灭/难度后缀")
    parser.add_argument("--start-at", choices=["auto", "home", "terminal", "prepared"], default="auto",
                        help="默认 auto：由 MAA 导航并确认起点；旧显式起点仅供诊断兼容")
    parser.add_argument("--maa", default="maa")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cost", type=int, help="默认按关卡表取值；旧调用显式传入时必须一致，不能覆盖")
    parser.add_argument("--maximum", type=int, default=10)
    parser.add_argument("--max-phases", type=int, default=5)
    parser.add_argument("--max-runs", type=int, default=100)
    parser.add_argument("--policy", type=Path, help="两模式临期药策略；省略时不用药，收尾检查范围为原生 1")
    args = parser.parse_args(argv)
    if args.mode == "run" and (not 1 <= args.maximum <= 10
                               or args.max_phases < 1 or args.max_runs < 1):
        parser.error("run requires positive budgets, maximum in 1..10")
    try:
        args.stage = normalize_stage(args.stage)
        cost = stage_cost(args.stage, args.cost)  # 在目录发现、导航或任何游戏操作前拦截错误成本。
        policy = load_policy(args.policy) if args.policy else {"mode": "off", "medicine_expire_days": 1}
        runtime = Runtime(args.maa, args.profile, args.output_dir)
        runtime.configure_stage(args.stage)
        if args.mode == "run":
            from medicine_sanity import preflight, scan
            preflight(runtime)
        print("本地证据目录：" + str(runtime.output), flush=True)
        navigate(runtime, args.stage, args.start_at)
        def observe():
            report = runtime.run([probe_task(args.stage)], "probe")
            return read_probe(report, args.stage)
        def fight(parameters):
            report = runtime.run([{"type": "Fight", "params": {"stage": args.stage, **parameters}}], "fight")
            check_fight(report, args.stage, parameters["times"])
        if args.mode == "probe":
            result = {"status": "observed", "sanity": observe(), "stage": args.stage}
        else:
            def recover(parameters):
                task = recovery_task(args.stage, policy, parameters["series"], parameters["times"])
                report = runtime.run([task], "medicine-bulk")
                return inspect_recovery(report, args.stage, policy["medicine_expire_days"], parameters["times"])
            result = run_daily(observe, fight, recover,
                               lambda: scan(runtime, args.stage, policy["medicine_expire_days"], cost=cost),
                               lambda r: write_json(runtime.output / "result.json", r), policy=policy,
                               cost=cost, maximum=args.maximum, max_phases=args.max_phases, max_runs=args.max_runs)
        result.update(stage=args.stage, cost=cost, probe_policy="cn-standard-joint-v2",
                      navigation_policy="native-isolated-stop-v1" if args.start_at != "prepared" else "prepared-v1",
                      end_at=("prepared" if result["status"] in {"completed", "observed"}
                              else result.get("medicine_check", {}).get("end_at", "unknown")))
        result["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        result["warning_report_files"] = [r["report_file"] for r in runtime.reports
                                          if r.get("internal_error_lines") or r.get("subtask_error_lines")
                                          or r.get("runner_exit_code") or r.get("report_error")]
        write_json(runtime.output / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"completed", "completed_with_reminder", "observed"} else 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "stopped", "reason": str(error), "reminder_required": True}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
