import contextlib
import io
import json
import itertools
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import stage_runtime as stage
import drain_sanity as drain
from daily_checks import stage_cost


class StageTests(unittest.TestCase):
    def setUp(self):
        from fake_cli import dry
        self.enterContext(patch.object(drain, "run_dry", side_effect=dry))

    def bound_config(self, root):
        source, official = root / "source", root / "official"
        for path in (source / "resource/tasks", source / "policies", official / "tasks"):
            path.mkdir(parents=True)
        # v6.22.0 ClickedCorrectStage 的相关规则及顺序；最后两个大小写修正在标题删除之后。
        rules = [["OPERATION", ""], [" ", ""], ["s", "S"], ["c", "C"], ["o", "O"], ["'", ""]]
        (official / "tasks/tasks.json").write_text(json.dumps({"ClickedCorrectStage": {
            "algorithm": "OcrDetect", "ocrReplace": rules}}), encoding="utf-8")
        (source / "policies/resource-templates.json").write_text(json.dumps({
            "version": 1, "roots": [str(official)], "nodes": {"MaaDailyCheck@DailyPage": {}}}), encoding="utf-8")
        return source, official, rules

    def test_bound_probe_normalizes_mixed_title_but_requires_exact_stage(self):
        def normalize(text, rules):
            for pattern, replacement in rules:
                text = re.sub(pattern, replacement, text)
            return text

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, official, upstream = self.bound_config(root)
            original = (official / "tasks/tasks.json").read_bytes()
            self.assertNotEqual(normalize("oPERATIoNLS-6'", upstream), "LS-6")
            for navigation in (False, True):
                snapshot = stage.isolated_config(source, root / str(navigation), "LS-6", navigation=navigation)
                node = json.loads((snapshot / "resource/tasks/tasks.json").read_text())[stage.VERIFY]
                self.assertEqual(node["action"], "DoNothing")
                self.assertTrue(node["fullMatch"])
                self.assertEqual(node["ocrReplace"][1:], upstream)
                for target in ("LS-6", "CE-6", "AP-5", "PR-D-2", "1-7"):
                    self.assertEqual(normalize(target, node["ocrReplace"]), target)
                    for letters in itertools.product(*(tuple({c, c.lower()}) for c in "OPERATION")):
                        raw = "".join(letters) + target + "'"
                        self.assertEqual(normalize(raw, node["ocrReplace"]), target)
                for raw in ("oPERATIONLS-5'", "oPERATIONLS-60'", "xoperationLS-6'",
                            "LS-6operation", "OPERATLS-6", "operationCE-6", "operationLS-6X"):
                    self.assertNotIn(normalize(raw, node["ocrReplace"]), node["text"])
            self.assertEqual((official / "tasks/tasks.json").read_bytes(), original)
            self.assertFalse((source / "resource/tasks/tasks.json").exists())

    def test_bound_probe_preserves_current_layer_and_user_ocr_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, official, _ = self.bound_config(root)
            overlay = root / "overlay"
            (overlay / "tasks").mkdir(parents=True)
            path = source / "policies/resource-templates.json"
            binding = json.loads(path.read_text())
            binding["roots"].append(str(overlay))
            path.write_text(json.dumps(binding), encoding="utf-8")
            resource = overlay / "tasks/tasks.json"
            for prefix in ("OPERATION", "(O|D)?PERATION"):
                rules = [[prefix, ""], ["future-normalization", "LS-6"]]
                resource.write_text(json.dumps({"ClickedCorrectStage": {"ocrReplace": rules}}), encoding="utf-8")
                self.assertEqual(stage.stage_ocr_overrides(source)["ocrReplace"][1:], rules)
            user_rules = [["user-normalization", "LS-6"]]
            (source / "resource/tasks/user.json").write_text(json.dumps({
                "ClickedCorrectStage": {"ocrReplace": user_rules}}), encoding="utf-8")
            self.assertEqual(stage.stage_ocr_overrides(source)["ocrReplace"][1:], user_rules)

    def test_bound_probe_rejects_ambiguous_or_unsupported_rules_before_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, official, _ = self.bound_config(root)
            resource = official / "tasks/tasks.json"
            for definition in ({}, {"algorithm": "MatchTemplate", "ocrReplace": []},
                               {"algorithm": "OcrDetect", "ocrReplace": "bad"},
                               {"algorithm": "OcrDetect", "ocrReplace": [["a", 1]]},
                               {"algorithm": "OcrDetect", "ocrReplace": [], "replaceFull": True},
                               {"baseTask": "Unknown", "algorithm": "OcrDetect", "ocrReplace": []}):
                resource.write_text(json.dumps({"ClickedCorrectStage": definition}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    stage.isolated_config(source, root / "run", "LS-6", navigation=False)
                self.assertFalse((root / "run").exists())
            (official / "tasks/duplicate.json").write_bytes(resource.read_bytes())
            with self.assertRaisesRegex(ValueError, "ambiguous_stage_ocr"):
                stage.stage_ocr_overrides(source)

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
            self.assertEqual(normal_nodes[stage.VERIFY]["baseTask"], "ClickedCorrectStage")
            self.assertTrue(normal_nodes[stage.VERIFY]["fullMatch"])
            self.assertNotIn("ocrReplace", normal_nodes[stage.VERIFY])
            self.assertNotIn("FightBegin", normal_nodes)
            self.assertEqual(normal_nodes["Explicit@StartButton2"]["action"], "ClickSelf")
            for key in (*stage.stop_resource(), "Explicit@StartButton2"):
                self.assertEqual(nav_nodes[key]["action"], "Stop")
                self.assertEqual(nav_nodes[key]["next"], [])
            with self.assertRaises(FileExistsError):
                stage.isolated_config(source, root / "normal", "1-7", navigation=False)

    def test_snapshot_omits_only_explicit_config_backups_and_keeps_navigation_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            backups = (
                "profiles/mumu.toml.bak-20260817-0102",
                "profiles/default.json.bak",
                "profiles/other.yaml.bak-20260928",
                "profiles/other.yml.bak-20260928-010203",
                "resource/tasks/tasks.json.bak-" + "a1" * 16,
                "resource/tasks/nested/custom.json.bak",
            )
            preserved = (
                "profiles/mumu.toml", "profiles/default.json", "profiles/other.yaml", "profiles/other.yml",
                "profiles/mumu.toml.bak-manual", "profiles/mumu.bak.toml",
                "profiles/dir.toml.bak-20260817-0102/active.toml",
                "resource/template/StartButton2.png", "resource/template/image.png.bak",
                "resource/template/tasks.json.bak-" + "a1" * 16,
                "resource/template/cache.bak-20260817-0102/image.png",
                "resource/models/model.onnx", "resource/config.json.bak",
                "resource/tasks/custom.json.bak-manual",
            )
            for name in (*backups, *preserved):
                file = source / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"unchanged fixture")
            active = source / "resource/tasks/nested/custom.bak.json"
            active.write_text(json.dumps({"Custom@StartButton2": {"action": "ClickSelf"},
                                          "CustomTemplate": {"template": "image.png.bak"}}), encoding="utf-8")
            originals = {file.relative_to(source): file.read_bytes() for file in source.rglob("*") if file.is_file()}
            for navigation in (False, True):
                with self.subTest(navigation=navigation):
                    snapshot = stage.isolated_config(source, root / str(navigation), "1-7", navigation=navigation)
                    for name in backups:
                        self.assertFalse((snapshot / name).exists(), name)
                    for name in preserved:
                        self.assertEqual((snapshot / name).read_bytes(), originals[Path(name)], name)
                    custom = json.loads((snapshot / active.relative_to(source)).read_text(encoding="utf-8"))
                    self.assertEqual(custom["CustomTemplate"]["template"], "image.png.bak")
                    self.assertEqual(custom["Custom@StartButton2"]["action"], "Stop" if navigation else "ClickSelf")
                    if navigation:
                        self.assertEqual(custom["Custom@StartButton2"], stage.stop_resource()["StartButton2"])
                    nodes = json.loads((snapshot / "resource/tasks/tasks.json").read_text(encoding="utf-8"))
                    self.assertEqual(nodes[stage.VERIFY]["text"], ["1-7"])
                    for name, guard in stage.stop_resource().items():
                        if navigation:
                            self.assertEqual(nodes[name], guard)
                        else:
                            self.assertNotIn(name, nodes)
            self.assertEqual({file.relative_to(source): file.read_bytes()
                              for file in source.rglob("*") if file.is_file()}, originals)

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
