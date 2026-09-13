import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import stage_runtime as stage
import drain_sanity as drain
from daily_checks import stage_cost


class StageTests(unittest.TestCase):
    def test_navigation_warnings_preserved_but_do_not_replace_semantic_checks(self):
        from test_drain_sanity import DrainTests, event
        fixture = DrainTests()
        body = (event("TaskChainStart") +
                event("SubTaskCompleted", subtask="StageNavigationTask", details={}) +
                event("SubTaskStart", details={"task": "FightBegin", "action": "Stop"}) +
                event("TaskChainCompleted"))
        with tempfile.TemporaryDirectory() as directory:
            report = fixture.report(directory, body)
            report["evidence"]["internal_error_lines"] = [1, 2]
            runtime = Mock()
            runtime.run.return_value = report
            drain.navigate(runtime, "AP-5")
            self.assertEqual(report["evidence"]["internal_error_lines"], [1, 2])
            for bad in (body.replace('"Stop"', '"DoNothing"'), body + event("SubTaskError"),
                        body + event("SubTaskExtraInfo", what="UseMedicine")):
                report = fixture.report(directory, bad)
                report["evidence"]["internal_error_lines"] = [1, 2]
                runtime.run.return_value = report
                with self.assertRaises(ValueError):
                    drain.navigate(runtime, "AP-5")

    def test_standard_codes_and_unsafe_or_special_names(self):
        for name in ("1-7", "PR-D-2", "AP-4", "TO-5", "SV-EX-8", "JT8-3"):
            self.assertEqual(stage.normalize_stage(name.lower()), name)
        for name in ("Annihilation", "1-7@StartButton2", "H10-1-Hard", "../AP-5", "SSReopen-XX-1", "", "1-7#next"):
            with self.assertRaises(ValueError):
                stage.normalize_stage(name)

    def test_unknown_cost_requires_explicit_verified_value(self):
        self.assertEqual(stage_cost("PR-D-2"), 36)
        with self.assertRaises(ValueError):
            stage_cost("ZZ-9")
        self.assertEqual(stage_cost("ZZ-9", 18), 18)
        with self.assertRaises(ValueError):
            stage_cost("AP-5", 18)

    def test_snapshot_isolation_and_parametric_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "profiles").mkdir(parents=True)
            (source / "resource/tasks").mkdir(parents=True)
            profile = source / "profiles/mumu.toml"
            profile.write_text('[connection]\naddress="example"\n', encoding="utf-8")
            resource = source / "resource/tasks/tasks.json"
            original = {"Unrelated": {"action": "DoNothing"}, "Explicit@StartButton2": {"action": "ClickSelf"}}
            resource.write_text(json.dumps(original), encoding="utf-8")
            normal = stage.isolated_config(source, root / "normal", "PR-D-2", navigation=False)
            nav = stage.isolated_config(source, root / "nav", "PR-D-2", navigation=True)
            normal_nodes = json.loads((normal / "resource/tasks/tasks.json").read_text(encoding="utf-8"))
            nav_nodes = json.loads((nav / "resource/tasks/tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(json.loads(resource.read_text()), original)
            self.assertEqual((normal / "profiles/mumu.toml").read_bytes(), profile.read_bytes())
            self.assertEqual(normal_nodes[stage.VERIFY]["text"], ["PR-D-2"])
            self.assertEqual(normal_nodes[stage.VERIFY]["action"], "DoNothing")
            self.assertNotIn("FightBegin", normal_nodes)
            self.assertEqual(normal_nodes["Explicit@StartButton2"]["action"], "ClickSelf")
            for key in (*stage.stop_resource(), "Explicit@StartButton2"):
                self.assertEqual(nav_nodes[key]["action"], "Stop")
                self.assertEqual(nav_nodes[key]["next"], [])
            with self.assertRaises(FileExistsError):
                stage.isolated_config(source, root / "normal", "1-7", navigation=False)

    def test_native_dryrun_failure_never_starts_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            runtime = object.__new__(drain.Runtime)
            runtime.config, runtime.output, runtime.reports = root, root, []
            runtime.maa, runtime.profile = "maa", "mumu"
            runtime.navigation_config = root
            with patch.object(drain.subprocess, "run", side_effect=drain.subprocess.CalledProcessError(1, "dry")) as run:
                with self.assertRaises(drain.subprocess.CalledProcessError):
                    runtime.run(drain.navigation_tasks("ZZ-9"), "navigation", navigation=True)
                self.assertEqual(run.call_count, 1)
                self.assertIn("--dry-run", run.call_args.args[0])

    def test_parametric_probe_accepts_exact_target_and_rejects_wrong_stage(self):
        from test_drain_sanity import DrainTests, probe_body
        fixture = DrainTests()
        body = probe_body().replace("VerifyAP5", "VerifyStage").replace("AP-5", "PR-D-2")
        with tempfile.TemporaryDirectory() as directory:
            report = fixture.report(directory, body)
            self.assertEqual(drain.read_probe(report, "PR-D-2"), 205)
            with self.assertRaises(ValueError):
                drain.read_probe(report, "1-7")

    def test_guard_env_is_scoped_to_navigation_not_following_battle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = object.__new__(drain.Runtime)
            runtime.config, runtime.output, runtime.reports = root, root, []
            runtime.maa, runtime.profile = "maa", "mumu"
            runtime.navigation_config, runtime.stage_config = root / "nav", root / "run"
            for directory in (runtime.navigation_config, runtime.stage_config):
                (directory / "tasks").mkdir(parents=True)
            scopes = []
            def execute(command, **kwargs):
                config = kwargs["env"]["MAA_CONFIG_DIR"]
                if command[1:3] == ["dir", "config"]:
                    return drain.subprocess.CompletedProcess(command, 0, config)
                if "--report-file" in command:
                    scopes.append(config)
                    target = Path(command[command.index("--report-file") + 1])
                    target.write_text(json.dumps({"evidence": {}}), encoding="utf-8")
                return drain.subprocess.CompletedProcess(command, 0)
            with patch.object(drain.subprocess, "run", side_effect=execute):
                runtime.run(drain.navigation_tasks("1-7"), "navigation", navigation=True)
                runtime.run([{"type": "Fight", "params": {"stage": "1-7"}}], "fight")
            self.assertEqual(scopes, [str(runtime.navigation_config), str(runtime.stage_config)])

    def test_ignored_isolation_environment_stops_before_dryrun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = object.__new__(drain.Runtime)
            runtime.config, runtime.output, runtime.reports = root, root, []
            runtime.maa, runtime.profile = "maa", "mumu"
            runtime.navigation_config = root / "nav"
            with patch.object(drain.subprocess, "run", return_value=drain.subprocess.CompletedProcess([], 0, str(root))) as run:
                with self.assertRaisesRegex(ValueError, "isolated_config_not_honored"):
                    runtime.run(drain.navigation_tasks("1-7"), "navigation", navigation=True)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.kwargs["env"]["MAA_CONFIG_DIR"], str(root / "nav"))

    def test_bad_stage_or_missing_cost_never_constructs_runtime(self):
        for name in ("Annihilation", "ZZ-9"):
            with patch.object(drain, "Runtime") as runtime, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(drain.main(["probe", "--stage", name, "--profile", "test", "--output-dir", "unused"]), 2)
                runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
