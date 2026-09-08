import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maa-daily/scripts"))
import drain_sanity as drain
sys.path.pop(0)


def event(kind, **values):
    return drain.MARKER + kind + " " + json.dumps({"taskchain": "Fight", "taskid": 1, **values}) + "\n"


class DrainTests(unittest.TestCase):
    def run_loop(self, readings, cost=30, maximum=10, failure=False, **bounds):
        sequence = iter(readings)
        fights, saved = [], []
        def fight(params):
            fights.append(params)
            if failure:
                raise ValueError("failed_fight")
        result = drain.drain(lambda: next(sequence), fight, lambda r: saved.append(copy.deepcopy(r)),
                             cost=cost, maximum=maximum, max_phases=bounds.get("max_phases", 5),
                             max_runs=bounds.get("max_runs", 100))
        return result, fights, saved

    def test_zero_and_below_cost_never_fight(self):
        for sanity in (0, 13, 29):
            result, fights, _ = self.run_loop([sanity])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(fights, [])

    def test_tail_and_bulk_reobserve(self):
        result, fights, saved = self.run_loop([943, 103, 7], cost=12)
        self.assertEqual([(p["series"], p["times"]) for p in fights], [(10, 70), (8, 8)])
        self.assertEqual(result["completed_runs"], 78)
        self.assertEqual(result["remaining_sanity"], 7)
        self.assertEqual(saved[-1]["status"], "completed")
        for params in fights:
            self.assertEqual([params[k] for k in ("medicine", "stone", "medicine_expire_days")], [0, 0, 0])
        result, fights, _ = self.run_loop([205, 25])
        self.assertEqual(len(fights), 1)
        self.assertEqual(fights[0]["series"], 6)

    def test_stop_on_failure_no_progress_and_budgets(self):
        for readings, options, reason in [([205], {"failure": True}, "failed_fight"),
                ([205, 205], {}, "sanity_not_decreasing"),
                ([943], {"cost": 12, "max_runs": 10}, "run_budget_exceeded"),
                ([943, 103], {"cost": 12, "max_phases": 1}, "phase_budget_exceeded")]:
            result, fights, _ = self.run_loop(readings, **options)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["reason"], reason)
            self.assertLessEqual(len(fights), 1)

    def test_navigation_has_no_fight_and_probe_resources_only_observe(self):
        for stage in drain.STAGES:
            tasks = drain.navigation_tasks(stage, "home")
            self.assertEqual([t["type"] for t in tasks], ["Custom", "Custom"])
            self.assertEqual(tasks[-1]["params"]["task_names"], [stage])
            self.assertEqual(drain.navigation_tasks(stage, "prepared"), [])
        with self.assertRaises(ValueError):
            drain.navigation_tasks("Annihilation", "home")
        nodes = json.loads((ROOT / "maa-daily/assets/drain-sanity/tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(set(nodes), {drain.PREFIX + v for v in drain.STAGES.values()})
        for node in nodes.values():
            self.assertEqual(node["action"], "DoNothing")
            self.assertTrue(node["fullMatch"])

    def test_failed_tail_does_not_report_stale_sanity_or_completed_runs(self):
        readings = iter([445, 85])
        calls = []
        def fight(params):
            calls.append(params)
            if len(calls) == 2:
                raise ValueError("runner_failed: 75")
        result = drain.drain(lambda: next(readings), fight, lambda r: None,
                             cost=36, maximum=10, max_phases=3, max_runs=12)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["completed_runs"], 10)
        self.assertIsNone(result["remaining_sanity"])
        self.assertEqual(result["last_known_sanity"], 85)
        self.assertEqual(result["phases"][-1]["status"], "unverified")
        self.assertEqual(len(calls), 2)

    def test_runner_failure_keeps_error_report_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            runtime = object.__new__(drain.Runtime)
            runtime.maa, runtime.profile = "maa", "test"
            runtime.config, runtime.output, runtime.reports = root, root, []
            def execute(command, **kwargs):
                if "--report-file" in command:
                    target = Path(command[command.index("--report-file") + 1])
                    target.write_text(json.dumps({"evidence": {
                        "internal_error_lines": [12], "subtask_error_lines": [15]}}), encoding="utf-8")
                    return drain.subprocess.CompletedProcess(command, 75)
                return drain.subprocess.CompletedProcess(command, 0)
            with patch.object(drain.subprocess, "run", side_effect=execute) as run:
                with self.assertRaisesRegex(ValueError, "runner_failed: 75"):
                    runtime.run([{"type": "Fight", "params": {}}], "fight")
                self.assertEqual(run.call_count, 2)
            record = json.loads((root / "processes.json").read_text(encoding="utf-8"))["processes"][0]
            self.assertEqual(record["runner_exit_code"], 75)
            self.assertEqual(record["internal_error_lines"], [12])
            self.assertEqual(record["subtask_error_lines"], [15])

    def report(self, directory, body):
        path = Path(directory) / "asst.log"
        data = body.encode()
        path.write_bytes(data)
        return {"wrapper_exit_code": 0, "child_exit_code": 0, "evidence": {
            "state": "bounded", "before_size": 0, "after_size": len(data),
            "log_file": str(path), "interval_sha256": hashlib.sha256(data).hexdigest()}}

    def test_fight_requires_matching_stage_and_positive_exact_completion(self):
        body = event("TaskChainStart") + event("SubTaskExtraInfo", what="FightTimes", details={"times_finished": 6})
        body += event("SubTaskExtraInfo", what="StageDrops", details={"stage": {"stageCode": "AP-5"}})
        body += event("TaskChainCompleted")
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body)
            drain.check_fight(report, "AP-5", 6)
            for stage, count in (("CE-6", 6), ("AP-5", 7)):
                with self.assertRaises(ValueError):
                    drain.check_fight(report, stage, count)
            report = self.report(directory, body.replace('"times_finished": 6', '"times_finished": 0'))
            with self.assertRaises(ValueError):
                drain.check_fight(report, "AP-5", 6)
            report = self.report(directory, body + event("SubTaskError"))
            with self.assertRaises(ValueError):
                drain.check_fight(report, "AP-5", 6)

    def test_probe_checks_actual_stage_and_actions(self):
        entry = drain.PREFIX + drain.STAGES["AP-5"]
        body = event("TaskChainStart", taskchain="Custom")
        for node, text in (("VerifyAP5", "AP-5"), ("StagePage", "开始行动"), ("Sanity", "205/205"), ("SanityConfirm", "205/205")):
            body += event("SubTaskCompleted", taskchain="Custom", first=[entry], details={
                "task": node, "action": "DoNothing", "result": {"text": text, "score": 0.999}})
            if node.startswith("Sanity"):
                body += f"OcrDetect MaaDailyCheck@{node} [{{ text: 205/205, rect: [ 1, 2, 3, 4 ], score: 0.999 }}]\n"
        body += event("TaskChainCompleted", taskchain="Custom")
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body)
            self.assertEqual(drain.read_probe(report, "AP-5"), 205)
            for changed in (body.replace('"text": "AP-5"', '"text": "CE-6"'),
                            body.replace('"action": "DoNothing"', '"action": "ClickSelf"')):
                with self.assertRaises(ValueError):
                    drain.read_probe(self.report(directory, changed), "AP-5")

    def test_runtime_dry_run_failure_never_starts_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            runtime = object.__new__(drain.Runtime)
            runtime.maa, runtime.profile = "maa", "test"
            runtime.config, runtime.output, runtime.reports = root, root, []
            with patch.object(drain.subprocess, "run", side_effect=drain.subprocess.CalledProcessError(1, "dry-run")) as run:
                with self.assertRaises(drain.subprocess.CalledProcessError):
                    runtime.run(drain.navigation_tasks("AP-5", "home"), "navigation")
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][-1], "--dry-run")
            task = json.loads(next((root / "tasks").glob("*.json")).read_text(encoding="utf-8"))
            self.assertTrue(all(t["type"] == "Custom" for t in task["tasks"]))


if __name__ == "__main__":
    unittest.main()
