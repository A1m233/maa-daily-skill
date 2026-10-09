"""Offline client-update checks using synthetic callbacks and OCR only."""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maa-daily/scripts"))
import artifacts
import client_check as check
import run_with_evidence as runner
sys.path.pop(0)


UPDATE_TEXT = "当前客户端版本已过时，即将更新客户端版本"
SCREEN_NODE = "MaaDailyCheck@ScreenText"


def event(kind, chain="StartUp", taskid=1, lane="[P1][T1]", **extra):
    value = {"taskchain": chain, "taskid": taskid, "uuid": "synthetic-device", **extra}
    return "[2026-10-01 00:00:00.000][INF]" + lane + " " + runner.CALLBACK_MARKER + kind + " " + json.dumps(value)


def ocr(text=UPDATE_TEXT, score=0.99, lane="[P1][T1]", node=None):
    source = "asst::WordOcr" if node is None else f"PipelineAnalyzer::analyze | OcrDetect {node}"
    return (f"[2026-10-01 00:00:00.000][TRC]{lane} {source} "
            f"[{{ text: {text}, rect: [ 10, 20, 800, 40 ], score: {score} }}]")


def startup(middle=None, terminal="TaskChainError", taskid=1):
    return [event("TaskChainStart", taskid=taskid),
            *(middle if middle is not None else [ocr()]),
            event(terminal, taskid=taskid)]


def screen(middle=None, terminal="TaskChainCompleted"):
    return [event("TaskChainStart", chain="Custom"),
            *(middle if middle is not None else [ocr(node=SCREEN_NODE)]),
            event("SubTaskCompleted", chain="Custom", first=[SCREEN_NODE], details={
                "task": "ScreenText", "algorithm": "OcrDetect", "action": "DoNothing"}),
            event(terminal, chain="Custom")]


def bounded_report(directory, lines, prefix="old unrelated log\n", suffix="later unrelated log\n", child_exit=1):
    log = Path(directory) / "asst.log"
    data = ("\n".join(lines) + "\n").encode("utf-8")
    prior = prefix.encode("utf-8")
    log.write_bytes(prior + data + suffix.encode("utf-8"))
    return {"schema_version": 2, "wrapper_exit_code": child_exit,
            "child_exit_code": child_exit, "runner_error": None,
            "ended_at": "2026-10-01T00:00:03+00:00", "business_result": "not_evaluated",
            "evidence": {"state": "bounded", "before_size": len(prior),
                         "after_size": len(prior) + len(data),
                         "start_line": prior.count(b"\n") + 1,
                         "log_file": str(log), "interval_sha256": hashlib.sha256(data).hexdigest()}}


class ClientUpdateClassificationTests(unittest.TestCase):
    def test_failed_startup_with_full_high_confidence_notice_requires_update(self):
        for terminal in ("TaskChainError", "TaskChainStopped"):
            with self.subTest(terminal=terminal):
                result = check.classify_client_update(startup(terminal=terminal), start_line=40)
                self.assertEqual(result["status"], "client_update_required")
                self.assertEqual(result["match_count"], 1)
                self.assertTrue(result["reason"])
                self.assertEqual(result["evidence"][0]["line"], 41)
                self.assertEqual(result["evidence"][0]["text"], UPDATE_TEXT)
                self.assertEqual(result["evidence"][0]["score"], 0.99)

    def test_completed_startup_keeps_notice_as_observation(self):
        result = check.classify_client_update(startup(terminal="TaskChainCompleted"))
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["match_count"], 1)

    def test_later_startup_attempt_does_not_inherit_earlier_notice(self):
        for terminal in ("TaskChainError", "TaskChainCompleted"):
            rows = startup() + startup([ocr("正在下载资源")], terminal=terminal, taskid=2)
            with self.subTest(terminal=terminal):
                result = check.classify_client_update(rows)
                self.assertEqual(result["status"], "observed")
                self.assertEqual(result["match_count"], 1)

    def test_latest_attempt_with_notice_is_still_required(self):
        rows = startup([ocr("正在下载资源")], terminal="TaskChainCompleted") + startup(taskid=2)
        self.assertEqual(check.classify_client_update(rows)["status"], "client_update_required")

    def test_internal_startup_retry_does_not_inherit_previous_notice(self):
        for kind in ("SubTaskStart", "SubTaskCompleted"):
            for extra in ({"details": {"task": "StartUpBegin"}},
                          {"details": {"task": "Official@StartUpBegin"},
                           "first": ["Official@StartUpBegin"]}):
                rows = startup([ocr(), event(kind, taskid=0, **extra), ocr("登录")])
                with self.subTest(kind=kind, extra=extra):
                    result = check.classify_client_update(rows)
                    self.assertEqual(result["status"], "observed")
                    self.assertEqual(result["match_count"], 1)
        rows = startup([ocr(), event("SubTaskStart", taskid=0, details={"task": "StartUpBegin"}), ocr()])
        self.assertEqual(check.classify_client_update(rows)["status"], "client_update_required")

    def test_offline_confirm_keeps_prompt_when_first_and_pre_task_name_startup_begin(self):
        # ProcessTask retains its entry in first while executing a later stop node.
        # Only details.task identifies which node is actually executing.
        middle = [event("SubTaskStart", taskid=0, first=["SwitchAccount@StartUpBegin"],
                        details={"task": "StartUpBegin", "action": "DoNothing"})]
        middle += [ocr(score=0.953437) for _ in range(6)]
        middle += [event("SubTaskStart", taskid=0, first=["SwitchAccount@StartUpBegin"],
                         details={"task": "OfflineConfirm", "action": "Stop", "pre_task": "StartUpBegin"})]
        rows = startup(middle)
        result = check.classify_client_update(rows, execution=runner.classify_execution(rows))
        self.assertEqual(result["status"], "client_update_required")
        self.assertEqual(result["match_count"], 6)
        with tempfile.TemporaryDirectory() as directory:
            report = bounded_report(directory, rows)
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with patch.object(subprocess, "run") as process, contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(check.main(["inspect", "--report", str(path)]), 3)
                process.assert_not_called()
            self.assertEqual(json.loads(stdout.getvalue())["match_count"], 6)

    def test_actual_wordocr_coordinate_shape_is_supported(self):
        line = ocr().replace("[ 10, 20, 800, 40 ]", "[ 10 (383), 174 (319), 514, 24 ]")
        line += " by OCR Pipeline , cost 335 ms"
        self.assertEqual(check.classify_client_update(startup([line]))["status"], "client_update_required")

    def test_update_requires_complete_phrase_and_high_confidence(self):
        for text in ("当前客户端版本已过时", "即将更新客户端版本", "资源版本已过时，即将下载资源",
                     "正在下载资源", "正在检查客户端版本", "GameOffline"):
            with self.subTest(text=text):
                result = check.classify_client_update(startup([ocr(text)]))
                self.assertNotEqual(result["status"], "client_update_required")
                self.assertEqual(result["match_count"], 0)
        for score in (0.899999, 0.1, -1, 1.01, "nan", "inf"):
            with self.subTest(score=score):
                self.assertNotEqual(check.classify_client_update(startup([ocr(score=score)]))["status"],
                                    "client_update_required")
        self.assertEqual(check.classify_client_update(startup([ocr(score=0.9)]))["status"],
                         "client_update_required")

    def test_template_names_and_free_text_are_not_ocr_evidence(self):
        for line in ("[INF][P1][T1] PipelineAnalyzer::analyze | MatchTemplate GameOffline [score: 0.99]",
                     "[INF][P1][T1] " + UPDATE_TEXT,
                     event("SubTaskStart", details={"task": "GameOffline"}),
                     event("SubTaskCompleted", details={"task": "GameOffline", "result": {
                         "text": UPDATE_TEXT, "score": 0.99}})):
            with self.subTest(line=line):
                self.assertEqual(check.classify_client_update(startup([line]))["match_count"], 0)

    def test_other_chain_process_thread_and_missing_lane_are_not_startup_evidence(self):
        cases = [startup([ocr(lane="[P2][T1]")]), startup([ocr(lane="[P1][T2]")]),
                 startup([ocr(lane="")]),
                 [event("TaskChainStart", chain="Fight"), ocr(), event("TaskChainError", chain="Fight")],
                 [ocr(), *startup([])], [*startup([]), ocr()]]
        for rows in cases:
            with self.subTest(rows=rows):
                result = check.classify_client_update(rows)
                self.assertNotEqual(result["status"], "client_update_required")
                self.assertEqual(result["match_count"], 0)

    def test_overlap_is_not_guessed_as_startup_ocr(self):
        rows = [event("TaskChainStart"), event("TaskChainStart", chain="Fight", taskid=2),
                ocr(), event("TaskChainError", chain="Fight", taskid=2), event("TaskChainError")]
        result = check.classify_client_update(rows)
        self.assertNotEqual(result["status"], "client_update_required")
        self.assertEqual(result["match_count"], 0)

    def test_custom_chain_accepts_only_explicit_screen_node(self):
        self.assertEqual(check.classify_client_update(screen())["status"], "client_update_required")
        for line in (ocr(), ocr(node="MaaDailyCheck@RewardTopA"), ocr(node="Other@ScreenText"),
                     ocr(node="MaaDailyCheck@ScreenTextSuffix")):
            with self.subTest(line=line):
                result = check.classify_client_update(screen([line]))
                self.assertNotEqual(result["status"], "client_update_required")
                self.assertEqual(result["match_count"], 0)

    def test_no_callbacks_or_incomplete_startup_never_claims_not_detected(self):
        for rows in ([], [ocr()], [event("TaskChainStart")]):
            with self.subTest(rows=rows):
                self.assertEqual(check.classify_client_update(rows)["status"], "unknown")

    def test_explicit_execution_input_agrees_with_internal_classification(self):
        for rows in (startup(), startup(terminal="TaskChainCompleted"), screen()):
            with self.subTest(rows=rows):
                expected = check.classify_client_update(rows, start_line=7)
                self.assertEqual(expected, check.classify_client_update(
                    rows, start_line=7, execution=runner.classify_execution(rows, start_line=7)))


class ClientUpdateReportTests(unittest.TestCase):
    def test_runner_parser_and_inspector_share_client_diagnostic(self):
        rows = startup()
        data = ("\n".join(rows) + "\n").encode("utf-8")
        expected = check.classify_client_update(rows, start_line=2)
        self.assertEqual(runner._parse_callbacks(data, 2)["diagnostics"]["client_update"], expected)
        with tempfile.TemporaryDirectory() as directory:
            result = runner.inspect_execution_report(bounded_report(directory, rows))
            self.assertEqual(result["diagnostics"]["client_update"], expected)
            self.assertEqual(result["reassessed_wrapper_exit_code"], 1)
            self.assertEqual(result["business_result"], "not_evaluated")

    def test_inspect_is_read_only_and_preserves_nonzero_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            report = bounded_report(directory, startup(), child_exit=9)
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            before = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            with patch.object(subprocess, "run") as process, contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = check.main(["inspect", "--report", str(path)])
                process.assert_not_called()
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 3)
            self.assertEqual(result["status"], "client_update_required")
            self.assertEqual(result["observed_at"], report["ended_at"])
            self.assertEqual({p.name: p.read_bytes() for p in Path(directory).iterdir()}, before)

    def test_inspect_ignores_old_and_appended_notices_outside_report_interval(self):
        old_notice = "\n".join(startup()) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            report = bounded_report(directory, startup([ocr("登录")], terminal="TaskChainCompleted"),
                                    prefix=old_notice, suffix=old_notice, child_exit=0)
            result = check.inspect_report(report)
            self.assertEqual(result["status"], "not_detected")
            self.assertEqual(result["match_count"], 0)

    def test_invalid_hash_or_boundary_cannot_reuse_embedded_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            report = bounded_report(directory, startup())
            report["evidence"]["diagnostics"] = {"client_update": {"status": "client_update_required"}}
            mutations = [{"interval_sha256": "invalid"}, {"interval_sha256": None},
                         {"state": "rotated"}, {"before_size": -1}, {"start_line": 0},
                         {"after_size": report["evidence"]["after_size"] + 1000}]
            for changes in mutations:
                bad = copy.deepcopy(report)
                bad["evidence"].update(changes)
                with self.subTest(changes=changes):
                    self.assertEqual(check.inspect_report(bad)["status"], "unknown")
            log = Path(report["evidence"]["log_file"])
            payload = bytearray(log.read_bytes())
            payload[report["evidence"]["before_size"]] = ord("X")
            log.write_bytes(payload)
            self.assertEqual(check.inspect_report(report)["status"], "unknown")

    def test_cli_exit_codes_keep_observed_and_unknown_inconclusive(self):
        cases = [(startup(terminal="TaskChainCompleted"), 0, "observed", 2),
                 (startup([ocr("登录")], terminal="TaskChainCompleted"), 0, "not_detected", 0),
                 ([event("TaskChainStart")], 74, "unknown", 2)]
        for rows, child_exit, status, expected_code in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                report = bounded_report(directory, rows, child_exit=child_exit)
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with patch.object(subprocess, "run") as process, contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(check.main(["inspect", "--report", str(path)]), expected_code)
                    process.assert_not_called()
                self.assertEqual(json.loads(stdout.getvalue())["status"], status)


class ClientUpdateScanTests(unittest.TestCase):
    def installed(self, directory):
        config = Path(directory) / "config"
        (config / "tasks").mkdir(parents=True)
        (config / "resource/tasks").mkdir(parents=True)
        (config / "profiles").mkdir(parents=True)
        assets = ROOT / "maa-daily/assets/daily-checks"
        shutil.copyfile(assets / (check.TASK + ".toml"), config / "tasks" / (check.TASK + ".toml"))
        shutil.copyfile(assets / "tasks.json", config / "resource/tasks/tasks.json")
        (config / "profiles/synthetic.toml").write_text("[resource]\nuser_resource = true\n", encoding="utf-8")
        return config

    def test_install_validation_is_read_only_and_rejects_conflicting_deployment(self):
        for mutation in ("none", "task", "resource", "alternate", "missing"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                config = self.installed(directory)
                task = config / "tasks" / (check.TASK + ".toml")
                resource = config / "resource/tasks/tasks.json"
                if mutation == "task":
                    task.write_text('[[tasks]]\ntype = "StartUp"\n', encoding="utf-8")
                elif mutation == "resource":
                    value = json.loads(resource.read_text(encoding="utf-8"))
                    value[SCREEN_NODE]["action"] = "ClickSelf"
                    resource.write_text(json.dumps(value), encoding="utf-8")
                elif mutation == "alternate":
                    task.with_suffix(".json").write_text('{}', encoding="utf-8")
                elif mutation == "missing":
                    task.unlink()
                before = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
                with patch.object(check.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, str(config) + "\n")) as process:
                    if mutation == "none":
                        self.assertEqual(check.check_install("chosen-maa"), config)
                    else:
                        with self.assertRaises((ValueError, OSError)):
                            check.check_install("chosen-maa")
                    self.assertEqual(process.call_count, 1)
                    self.assertEqual(process.call_args.args[0], ["chosen-maa", "dir", "config", "--batch"])
                self.assertEqual(before, {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()})

    def test_scan_runs_exactly_one_screen_probe_and_never_startup(self):
        cases = [(UPDATE_TEXT, 0, "client_update_required"),
                 ("登录", 0, "not_detected"),
                 (UPDATE_TEXT, 9, "client_update_required"),
                 ("登录", 9, "unknown")]
        for text, child_exit, status in cases:
            with self.subTest(status=status, child_exit=child_exit), tempfile.TemporaryDirectory() as directory:
                config = self.installed(directory)
                immutable = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
                calls = []

                def execute(command, **kwargs):
                    calls.append(command)
                    if command[1:3] == ["dir", "config"]:
                        return subprocess.CompletedProcess(command, 0, str(config) + "\n")
                    if "--report-file" in command:
                        report = bounded_report(directory, screen([ocr(text, node=SCREEN_NODE)]), child_exit=child_exit)
                        Path(command[command.index("--report-file") + 1]).write_text(json.dumps(report), encoding="utf-8")
                        return subprocess.CompletedProcess(command, child_exit)
                    self.assertIn("--dry-run", command)
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(check.subprocess, "run", side_effect=execute):
                    result, output = check.scan_once("chosen-maa", "synthetic", Path(directory) / "output")
                self.assertEqual(result["status"], status)
                self.assertEqual(len(calls), 4)
                wrappers = [command for command in calls if "--report-file" in command]
                self.assertEqual(len(wrappers), 1)
                command = wrappers[0][wrappers[0].index("--") + 1:]
                self.assertEqual(command, ["chosen-maa", "run", check.TASK, "--profile", "synthetic",
                                           "--batch", "--user-resource", "--no-auto-reconnect"])
                self.assertFalse(any("startup" in command for command in calls))
                self.assertTrue((output / "evidence.json").is_file())
                self.assertEqual(result["observed_at"], "2026-10-01T00:00:03+00:00")
                for path, before in immutable.items():
                    self.assertEqual((config / path).read_bytes(), before)

    def test_scan_revalidates_deployment_after_dry_run_before_live_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.installed(directory)
            calls = []

            def execute(command, **kwargs):
                calls.append(command)
                if command[1:3] == ["dir", "config"]:
                    return subprocess.CompletedProcess(command, 0, str(config) + "\n")
                self.assertIn("--dry-run", command)
                task = config / "tasks" / (check.TASK + ".toml")
                task.write_text('[[tasks]]\ntype = "Fight"\n', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            with patch.object(check.subprocess, "run", side_effect=execute), contextlib.redirect_stderr(io.StringIO()):
                result, output = check.scan_once("chosen-maa", "synthetic", Path(directory) / "output")
            self.assertEqual(result["status"], "unknown")
            self.assertTrue((output / "result.json").is_file())
            index = json.loads((config / "maa-daily-artifacts/index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["runs"][0]["state"], "finished")
            self.assertEqual(len(calls), 3)
            self.assertFalse(any("--report-file" in command for command in calls))

    def test_dry_run_failure_finishes_artifact_package_without_live_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.installed(directory)
            calls = []

            def execute(command, **kwargs):
                calls.append(command)
                if command[1:3] == ["dir", "config"]:
                    return subprocess.CompletedProcess(command, 0, str(config) + "\n")
                self.assertIn("--dry-run", command)
                self.assertIn("--no-auto-reconnect", command)
                raise subprocess.CalledProcessError(2, command)

            with patch.object(check.subprocess, "run", side_effect=execute), contextlib.redirect_stderr(io.StringIO()):
                result, output = check.scan_once("chosen-maa", "synthetic", Path(directory) / "output")
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["error_type"], "CalledProcessError")
            self.assertTrue((output / "result.json").is_file())
            self.assertFalse((output / "evidence.json").exists())
            index = json.loads((config / "maa-daily-artifacts/index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["runs"][0]["state"], "finished")
            self.assertEqual(len(calls), 2)
            self.assertFalse(any("--report-file" in command for command in calls))

    def test_missing_runner_report_keeps_artifacts_uncertain_without_retry(self):
        for wrapper_exit in (0, 74):
            with self.subTest(wrapper_exit=wrapper_exit), tempfile.TemporaryDirectory() as directory:
                config = self.installed(directory)
                calls = []

                def execute(command, **kwargs):
                    calls.append(command)
                    if command[1:3] == ["dir", "config"]:
                        return subprocess.CompletedProcess(command, 0, str(config) + "\n")
                    if "--report-file" in command:
                        return subprocess.CompletedProcess(command, wrapper_exit)
                    self.assertIn("--dry-run", command)
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(check.subprocess, "run", side_effect=execute), contextlib.redirect_stderr(io.StringIO()):
                    result, output = check.scan_once("chosen-maa", "synthetic", Path(directory) / "output")
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["error_type"], "FileNotFoundError")
                self.assertTrue((output / "result.json").is_file())
                self.assertFalse((output / "evidence.json").exists())
                index = json.loads((config / "maa-daily-artifacts/index.json").read_text(encoding="utf-8"))
                self.assertEqual(index["runs"][0]["state"], "uncertain")
                self.assertEqual(len(calls), 4)
                self.assertEqual(sum("--report-file" in command for command in calls), 1)


if __name__ == "__main__":
    unittest.main()
