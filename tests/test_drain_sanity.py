import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maa-daily/scripts"))
import drain_sanity as drain
sys.path.pop(0)


def event(kind, **values):
    return drain.MARKER + kind + " " + json.dumps({"taskchain": "Fight", "taskid": 1, **values}) + "\n"


def probe_body(stage="AP-5", score=0.925990, mutate=lambda rows: None):
    rows = [(drain.STAGES[stage], {"text": stage, "score": score, "rect": [924, 82, 85, 27]}),
            ("StagePage", {"text": "开始行动", "score": 0.999989, "rect": [1126, 645, 104, 26]}),
            ("Sanity", {"text": "205/205", "score": 0.999925, "rect": [1141, 25, 118, 30]}),
            ("SanityConfirm", {"text": "205/205", "score": 0.999925, "rect": [1141, 25, 118, 30]})]
    mutate(rows)
    body = event("TaskChainStart", taskchain="Custom")
    for node, match in rows:
        body += event("SubTaskCompleted", taskchain="Custom", first=[drain.PREFIX + drain.STAGES[stage]], details={
            "task": node, "action": "DoNothing", "algorithm": "OcrDetect", "result": match})
        if node.startswith("Sanity"):
            body += (f"OcrDetect MaaDailyCheck@{node} [{{ text: {match['text']}, "
                     f"rect: [ 1141, 25, 118, 30 ], score: {match['score']} }}]\n")
    return body + event("TaskChainCompleted", taskchain="Custom")


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
        for name in drain.STAGES.values():
            node = nodes[drain.PREFIX + name]
            self.assertEqual(node["action"], "DoNothing")
            self.assertTrue(node["fullMatch"])

    def test_auto_navigation_is_closed_bounded_and_only_clicks_navigation(self):
        nodes = json.loads((ROOT / "maa-daily/assets/drain-sanity/tasks.json").read_text(encoding="utf-8"))
        allowed_clicks = {"NavMenuOpen", "NavMenuEntry", "NavConfirm", "NavHomeTerminal"}
        for stage in drain.STAGES:
            entry = drain.navigation_tasks(stage)[0]["params"]["task_names"][0]
            pending, visited = [entry], set()
            while pending:
                name = pending.pop()
                if name in visited:
                    continue
                visited.add(name)
                node = nodes[name]
                self.assertNotIn("baseTask", node)  # No inherited battle/error branches.
                self.assertEqual(node["maxTimes"], 1)
                self.assertLessEqual(set(node) & {"sub", "onErrorNext", "exceededNext"}, set())
                if node["action"] == "ClickSelf":
                    self.assertIn(name.removeprefix(drain.PREFIX), allowed_clicks)
                    self.assertEqual(node["algorithm"], "MatchTemplate")
                else:
                    self.assertEqual(node["action"], "DoNothing")
                pending.extend(node["next"])
            parents = [n for n in visited if drain.PREFIX + "NavConfirm" in nodes[n]["next"]]
            self.assertEqual(parents, [drain.PREFIX + "NavMenuEntry"])

    def test_auto_navigation_routes_simulated_screens_without_home_roundtrip(self):
        nodes = json.loads((ROOT / "maa-daily/assets/drain-sanity/tasks.json").read_text(encoding="utf-8"))
        # Simulates matched nodes, not image recognition; verifies the resource's actual edges.
        for route in (["NavMenuOpen", "NavMenuEntry", "NavConfirm", "NavTerminal"],
                      ["NavMenuEntry", "NavTerminal"], ["NavHomeTerminal", "NavTerminal"],
                      ["NavTerminal"], ["NavReadyVerifyAP5"]):
            name = drain.PREFIX + "NavVerifyAP5"
            for match in route:
                target = drain.PREFIX + match
                self.assertIn(target, nodes[name]["next"])
                name = target
            self.assertEqual(nodes[name]["next"], [])
        entry = nodes[drain.PREFIX + "NavVerifyAP5"]
        self.assertNotIn(drain.PREFIX + "NavConfirm", entry["next"])
        self.assertTrue(all(nodes[n]["algorithm"] != "JustReturn" for n in entry["next"]))

    def test_navigation_requires_observed_endpoint_in_matching_chain(self):
        def body(endpoint, action="DoNothing"):
            return (event("TaskChainStart", taskchain="Custom") +
                    event("SubTaskCompleted", taskchain="Custom", first=[drain.PREFIX + "NavVerifyAP5"],
                          details={"task": endpoint, "action": action}) +
                    event("TaskChainCompleted", taskchain="Custom"))
        with tempfile.TemporaryDirectory() as directory:
            for node, result in (("NavTerminal", "terminal"), ("NavReadyVerifyAP5", "prepared")):
                self.assertEqual(drain.read_navigation(self.report(directory, body(node)), "AP-5"), result)
            for content in (body("NavMenuOpen", "ClickSelf"), body("NavReadyVerifyCE6"),
                            body("NavTerminal", "ClickSelf"), body("NavTerminal").replace('"taskid": 1', '"taskid": 2', 1)):
                with self.assertRaises(ValueError):
                    drain.read_navigation(self.report(directory, content), "AP-5")

    def test_default_cli_auto_never_fights_without_probe_and_routes_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            for endpoint in ("prepared", "terminal"):
                with patch.object(drain, "Runtime") as factory, patch.object(drain, "callbacks"), \
                        patch.object(drain, "read_navigation", return_value=endpoint), \
                        patch.object(drain, "read_probe", side_effect=ValueError("sanity_unverified")), \
                        contextlib.redirect_stdout(io.StringIO()):
                    runtime = factory.return_value
                    runtime.output, runtime.reports = Path(directory), []
                    runtime.run.return_value = {"evidence": {}}
                    self.assertEqual(drain.main(["run", "--stage", "AP-5", "--profile", "test",
                                                "--output-dir", directory]), 2)
                    phases = [c.args[1] for c in runtime.run.call_args_list]
                    self.assertEqual(phases, ["navigation"] + (["stage-navigation"] if endpoint == "terminal" else []) + ["probe"])
                    self.assertTrue(all(t["type"] == "Custom" for c in runtime.run.call_args_list for t in c.args[0]))

    def test_auto_navigation_failure_does_not_probe_or_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(drain, "Runtime") as factory, \
                contextlib.redirect_stdout(io.StringIO()):
            runtime = factory.return_value
            runtime.output = Path(directory)
            runtime.run.side_effect = ValueError("runner_failed: 1")
            self.assertEqual(drain.main(["run", "--stage", "AP-5", "--profile", "test",
                                        "--output-dir", directory]), 2)
            self.assertEqual(runtime.run.call_count, 1)
            self.assertEqual(runtime.run.call_args.args[1], "navigation")

    def test_old_deployment_rejected_before_any_game_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resource/tasks").mkdir(parents=True)
            nodes = json.loads((ROOT / "maa-daily/assets/daily-checks/tasks.json").read_text(encoding="utf-8"))
            nodes.update({k: v for k, v in json.loads((ROOT / "maa-daily/assets/drain-sanity/tasks.json").read_text(encoding="utf-8")).items()
                          if not k.startswith(drain.PREFIX + "Nav")})
            (root / "resource/tasks/tasks.json").write_text(json.dumps(nodes), encoding="utf-8")
            with patch.object(drain.subprocess, "run", return_value=drain.subprocess.CompletedProcess([], 0, str(root))) as run:
                with self.assertRaisesRegex(ValueError, "probe_resource_missing_or_changed"):
                    drain.Runtime("maa", "test", root / "output")
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ["maa", "dir", "config", "--batch"])
            self.assertFalse((root / "output").exists())

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
        body = probe_body()
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body)
            self.assertEqual(drain.read_probe(report, "AP-5"), 205)
            for changed in (body.replace('"text": "AP-5"', '"text": "CE-6"'),
                            body.replace('"action": "DoNothing"', '"action": "ClickSelf"')):
                with self.assertRaises(ValueError):
                    drain.read_probe(self.report(directory, changed), "AP-5")

    def test_joint_probe_rejects_each_missing_or_conflicting_signal(self):
        changes = [lambda r: r[0][1].update(text="AP-4", score=1),
                   lambda r: r[0][1].update(text="AP-", score=1),
                   lambda r: r[0][1].update(score=0.899),
                   lambda r: r[0][1].update(score=float("nan")),
                   lambda r: r[0][1].update(rect=[20, 82, 85, 27]),
                   lambda r: r[1][1].update(text="开始推演"),
                   lambda r: r[1][1].update(score=0.97),
                   lambda r: r[1][1].update(rect=[1126, 500, 104, 26]),
                   lambda r: r[2][1].update(score=0.925990),
                   lambda r: r[3][1].update(text="204/205"),
                   lambda r: r[3][1].update(rect=[10, 25, 118, 30]),
                   lambda r: r.pop(1), lambda r: r.reverse(), lambda r: r.append(r[0])]
        with tempfile.TemporaryDirectory() as directory:
            for index, mutate in enumerate(changes):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    drain.read_probe(self.report(directory, probe_body(mutate=mutate)), "AP-5")
            for stage, score in (("AP-5", 0.925990), ("CE-6", 0.997455), ("LS-6", 0.998928)):
                self.assertEqual(drain.read_probe(self.report(directory, probe_body(stage, score)), stage), 205)

    def test_wrong_cost_never_constructs_runtime_or_navigates(self):
        for stage in ("LS-6", "CE-6"):
            with patch.object(drain, "Runtime") as runtime, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(drain.main(["run", "--stage", stage, "--cost", "30", "--start-at", "home",
                                             "--profile", "test", "--output-dir", "unused"]), 2)
                runtime.assert_not_called()

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
