from __future__ import annotations

import importlib.util
import json
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
            str(RUNNER),
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
