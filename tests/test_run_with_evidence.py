from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "maa-daily" / "scripts" / "run_with_evidence.py"
REPORT_PREFIX = "MAA_EVIDENCE_JSON="
sys.path.insert(0, str(RUNNER.parent))
from artifacts import ArtifactRun, ENV_NAME

RUNNER_SPEC = importlib.util.spec_from_file_location("maa_run_with_evidence", RUNNER)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
RUNNER_MODULE = importlib.util.module_from_spec(RUNNER_SPEC)
PREVIOUS_DONT_WRITE_BYTECODE = sys.dont_write_bytecode
try:
    sys.dont_write_bytecode = True
    RUNNER_SPEC.loader.exec_module(RUNNER_MODULE)
finally:
    sys.dont_write_bytecode = PREVIOUS_DONT_WRITE_BYTECODE


class EvidenceRunnerTests(unittest.TestCase):
    def test_discovers_core_log_with_same_maa_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.CompletedProcess(
                args=["maa-custom", "dir", "log", "--batch"],
                returncode=0,
                stdout=f"{temp}\n",
                stderr="",
            )
            with mock.patch.object(
                RUNNER_MODULE.subprocess, "run", return_value=completed
            ) as run:
                result = RUNNER_MODULE._discover_core_log("maa-custom")

        self.assertEqual(Path(temp) / "asst.log", result)
        run.assert_called_once_with(
            ["maa-custom", "dir", "log", "--batch"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def _write_fake_command(self, directory: Path) -> Path:
        path = directory / "fake_maa.py"
        path.write_text(
            textwrap.dedent(
                """
                from pathlib import Path
                import sys

                log_path = Path(sys.argv[1])
                mode = sys.argv[2]
                exit_code = int(sys.argv[3])
                if mode != "silent":
                    open_mode = "w" if mode == "replace-log" else "a"
                    with log_path.open(open_mode, encoding="utf-8") as handle:
                        handle.write(
                            '[2026-08-26 01:00:00.000][INF][P][T] '
                            'Assistant::append_callback | TaskChainStart '
                            '{"taskchain":"Custom","taskid":2}\\n'
                        )
                        if mode == "completed-with-error":
                            handle.write(
                                '[2026-08-26 01:00:00.100][ERR][P][T] '
                                'synthetic internal error\\n'
                            )
                            terminal = "TaskChainCompleted"
                        else:
                            terminal = "TaskChainError"
                        handle.write(
                            '[2026-08-26 01:00:00.200][INF][P][T] '
                            f'Assistant::append_callback | {terminal} '
                            '{"taskchain":"Custom","taskid":2}\\n'
                        )
                        handle.write(
                            '[2026-08-26 01:00:00.300][INF][P][T] '
                            'Assistant::append_callback | SubTaskExtraInfo '
                            '{"what":"SyntheticEvidence","taskchain":"Custom","taskid":2}\\n'
                        )
                print("synthetic maa output")
                raise SystemExit(exit_code)
                """
            ).lstrip(),
            encoding="utf-8",
        )
        return path

    def _run(
        self,
        directory: Path,
        *,
        mode: str,
        child_exit: int,
        extra_argument: str = "",
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        log_path = directory / "asst.log"
        log_path.write_text("pre-existing log line\n", encoding="utf-8")
        report_path = directory / "report.json"
        fake_command = self._write_fake_command(directory)
        command = [
            sys.executable,
            "-P",
            "-B",
            str(RUNNER),
            "--artifact-config",
            str(directory / "config"),
            "--core-log",
            str(log_path),
            "--report-file",
            str(report_path),
            "--",
            sys.executable,
            str(fake_command),
            str(log_path),
            mode,
            str(child_exit),
        ]
        if extra_argument:
            command.append(extra_argument)
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return result, report

    def _main(self, directory: Path, *, extra: list[str] | None = None,
              child_exit: int = 0, interrupt: bool = False) -> tuple[int, dict, str]:
        log = directory / "asst.log"
        log.write_text("", encoding="utf-8")
        def execute(command, **kwargs):
            if command[1:3] == ["dir", "config"]:
                return subprocess.CompletedProcess(command, 0, str(directory / "config") + "\n")
            if interrupt:
                raise KeyboardInterrupt
            log.write_text(
                'Assistant::append_callback | TaskChainStart {"taskchain":"Custom","taskid":1}\n'
                'Assistant::append_callback | TaskChainCompleted {"taskchain":"Custom","taskid":1}\n',
                encoding="utf-8")
            self.assertIn(ENV_NAME, kwargs["env"])
            return subprocess.CompletedProcess(command, child_exit)
        output, errors = io.StringIO(), io.StringIO()
        with (mock.patch.object(RUNNER_MODULE.subprocess, "run", side_effect=execute),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            code = RUNNER_MODULE.main(["--core-log", str(log), *(extra or []), "--", "fake-maa", "run"])
        reports = [line.removeprefix(REPORT_PREFIX) for line in output.getvalue().splitlines()
                   if line.startswith(REPORT_PREFIX)]
        self.last_output = output.getvalue()
        return code, json.loads(reports[-1]), errors.getvalue()

    def _index(self, directory: Path) -> dict:
        return json.loads((directory / "config/maa-daily-artifacts/index.json").read_text(encoding="utf-8"))

    def test_default_report_discovers_config_and_finishes_one_managed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            code, report, errors = self._main(directory)
            runs = self._index(directory)["runs"]
            self.assertEqual(code, 0)
            self.assertEqual(errors, "")
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["kind"], "evidence")
            self.assertEqual(runs[0]["state"], "finished")
            self.assertEqual(runs[0]["files"], [])
            self.assertIn("MAA_EVIDENCE_REPORT=" + str(Path(runs[0]["path"]) / "evidence.json"), self.last_output)
            self.assertEqual(json.loads((Path(runs[0]["path"]) / "evidence.json").read_text(encoding="utf-8")), report)

    def test_new_external_report_is_registered_without_owning_parent_or_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _, report = self._run(directory, mode="completed-with-error", child_exit=0)
            run = self._index(directory)["runs"][0]
            self.assertEqual(run["state"], "finished")
            self.assertEqual([item["path"] for item in run["files"]], [str(directory / "report.json")])
            self.assertEqual(report["schema_version"], 2)
            self.assertNotIn(str(directory / "asst.log"), [item["path"] for item in run["files"]])

    def test_parent_context_is_reused_without_another_registered_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            parent = ArtifactRun.begin(directory / "config", None, "daily")
            try:
                with (mock.patch.dict(os.environ, parent.environment()),
                      mock.patch.object(RUNNER_MODULE, "_discover_artifact_config") as discover,
                      mock.patch.object(ArtifactRun, "begin", side_effect=AssertionError("must reuse parent"))):
                    code, _, _ = self._main(directory)
                discover.assert_not_called()
                self.assertEqual(code, 0)
                self.assertTrue((parent.path / "evidence.json").is_file())
                runs = self._index(directory)["runs"]
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["state"], "active")
            finally:
                parent.finish()

    def test_governance_failure_and_existing_external_file_prevent_command(self) -> None:
        for failure in ("broken-store", "existing-report"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                extra = ["--artifact-config", str(directory / "config")]
                if failure == "broken-store":
                    (directory / "config/maa-daily-artifacts").mkdir(parents=True)
                else:
                    report = directory / "old-report.json"
                    report.write_bytes(b"old evidence")
                    extra += ["--report-file", str(report)]
                code, value, _ = self._main(directory, extra=extra)
                self.assertEqual(code, 74)
                self.assertIsNone(value["child_exit_code"])
                self.assertEqual((directory / "asst.log").read_bytes(), b"")
                if failure == "existing-report":
                    self.assertEqual(report.read_bytes(), b"old evidence")
                    self.assertFalse((directory / "config").exists())

    def test_interrupt_protects_owned_and_parent_run_from_cleanup(self) -> None:
        for use_parent in (False, True):
            with self.subTest(parent=use_parent), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                parent = ArtifactRun.begin(directory / "config", None, "daily") if use_parent else None
                env = parent.environment() if parent else dict(os.environ)
                with mock.patch.dict(os.environ, env):
                    code, report, _ = self._main(directory, interrupt=True)
                self.assertEqual(code, 130)
                self.assertEqual(report["runner_error"], "KeyboardInterrupt")
                if parent:
                    parent.finish("finished")
                self.assertEqual(self._index(directory)["runs"][0]["state"], "uncertain")

    def test_finish_warning_never_changes_business_exit(self) -> None:
        for child_exit in (0, 9):
            with self.subTest(child_exit=child_exit), tempfile.TemporaryDirectory() as temp:
                with mock.patch.object(ArtifactRun, "finish", side_effect=RuntimeError("cleanup unavailable")):
                    code, report, errors = self._main(Path(temp), child_exit=child_exit)
                self.assertEqual(code, child_exit)
                self.assertEqual(report["wrapper_exit_code"], child_exit)
                self.assertIn("cleanup unavailable", errors)

    def test_interrupted_child_exit_and_registration_failure_keep_evidence_protected(self) -> None:
        for child_exit in (130, -2):
            with self.subTest(child_exit=child_exit), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                code, report, _ = self._main(directory, child_exit=child_exit)
                self.assertEqual(code, child_exit)
                self.assertEqual(report["child_exit_code"], child_exit)
                self.assertEqual(self._index(directory)["runs"][0]["state"], "uncertain")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with mock.patch.object(ArtifactRun, "register_file", side_effect=RuntimeError("registration unavailable")):
                code, report, errors = self._main(directory)
            self.assertEqual(code, 0)
            self.assertEqual(report["wrapper_exit_code"], 0)
            self.assertIn("registration unavailable", errors)
            self.assertEqual(self._index(directory)["runs"][0]["state"], "uncertain")

    def test_inspection_has_no_artifact_or_subprocess_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self._run(directory, mode="completed-with-error", child_exit=0)
            before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
            with (mock.patch.object(ArtifactRun, "current", side_effect=AssertionError("read-only")) as current,
                  mock.patch.object(ArtifactRun, "begin", side_effect=AssertionError("read-only")) as begin,
                  mock.patch.object(RUNNER_MODULE.subprocess, "run") as execute,
                  contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                args = ["--inspect-report", str(directory / "report.json")]
                self.assertEqual(RUNNER_MODULE.main(args), 0)
                with self.assertRaises(SystemExit) as error:
                    RUNNER_MODULE.main([*args, "--artifact-config", str(directory / "unused")])
                self.assertEqual(error.exception.code, 2)
                current.assert_not_called()
                begin.assert_not_called()
                execute.assert_not_called()
            after = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_external_report_created_during_command_is_not_overwritten_or_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = directory / "report.json"
            original_emit = RUNNER_MODULE._emit_report
            def race(report, report_file, **kwargs):
                target.write_bytes(b"created by another writer")
                return original_emit(report, report_file, **kwargs)
            with mock.patch.object(RUNNER_MODULE, "_emit_report", side_effect=race):
                code, report, _ = self._main(directory, extra=["--report-file", str(target)])
            self.assertEqual(code, 74)
            self.assertEqual(report["report_file_error"], "FileExistsError")
            self.assertEqual(target.read_bytes(), b"created by another writer")
            self.assertEqual(self._index(directory)["runs"][0]["files"], [])

    def test_bounds_log_and_surfaces_completed_run_with_internal_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, report = self._run(
                Path(temp),
                mode="completed-with-error",
                child_exit=0,
                extra_argument="private-value-must-not-be-persisted",
            )

        self.assertEqual(0, result.returncode)
        self.assertIn(REPORT_PREFIX, result.stdout)
        self.assertEqual(0, report["child_exit_code"])
        self.assertEqual(0, report["wrapper_exit_code"])
        self.assertEqual("not_evaluated", report["business_result"])
        evidence = report["evidence"]
        self.assertEqual("bounded", evidence["state"])
        self.assertEqual(2, evidence["start_line"])
        self.assertEqual([3], evidence["internal_error_lines"])
        self.assertEqual(1, evidence["callback_counts"]["TaskChainCompleted"])
        self.assertEqual(1, evidence["extra_info_types"]["SyntheticEvidence"])
        self.assertNotIn("private-value-must-not-be-persisted", json.dumps(report))

    def test_preserves_nonzero_child_exit_and_reports_taskchain_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, report = self._run(
                Path(temp), mode="taskchain-error", child_exit=9
            )

        self.assertEqual(9, result.returncode)
        self.assertEqual(9, report["child_exit_code"])
        self.assertEqual(9, report["wrapper_exit_code"])
        self.assertEqual(1, report["evidence"]["callback_counts"]["TaskChainError"])

    def test_fails_closed_when_zero_exit_contains_taskchain_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, report = self._run(
                Path(temp), mode="taskchain-error", child_exit=0
            )

        self.assertEqual(75, result.returncode)
        self.assertEqual(0, report["child_exit_code"])
        self.assertEqual(75, report["wrapper_exit_code"])
        self.assertEqual(1, report["evidence"]["callback_counts"]["TaskChainError"])

    def test_fails_closed_when_success_has_no_new_core_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, report = self._run(Path(temp), mode="silent", child_exit=0)

        self.assertEqual(74, result.returncode)
        self.assertEqual(0, report["child_exit_code"])
        self.assertEqual(74, report["wrapper_exit_code"])
        self.assertEqual("unchanged", report["evidence"]["state"])

    def test_fails_closed_when_core_log_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, report = self._run(Path(temp), mode="replace-log", child_exit=0)

        self.assertEqual(74, result.returncode)
        self.assertEqual(0, report["child_exit_code"])
        self.assertEqual(74, report["wrapper_exit_code"])
        self.assertEqual("rotated", report["evidence"]["state"])


if __name__ == "__main__":
    unittest.main()
