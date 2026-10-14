"""用真实本地假子进程验证输出收敛，不调用 MAA、ADB 或网络。"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import run_with_evidence as runner
from artifacts import ArtifactRun, ENV_NAME


class OutputBoundaryTests(unittest.TestCase):
    def test_brief_and_verbose_keep_same_evidence_and_exit_codes(self):
        for terminal, child_exit, expected in (("TaskChainCompleted", 0, 0), ("TaskChainError", 0, 75),
                                               ("TaskChainCompleted", 9, 9)):
            reports = []
            for verbose in (False, True):
                with self.subTest(terminal=terminal, child_exit=child_exit, verbose=verbose), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    log, target = root / "asst.log", root / "report.json"
                    log.write_text("history\n", encoding="utf-8")
                    rows = ('Assistant::append_callback | TaskChainStart {"taskchain":"Custom","taskid":1}\n'
                            + 'Assistant::append_callback | ' + terminal + ' {"taskchain":"Custom","taskid":1}\n')
                    script = ("import sys; from pathlib import Path; "
                              f"p=Path({str(log)!r}); p.write_bytes(p.read_bytes()+{rows.encode()!r}); "
                              "print('diagnostic-'*8000); print('stderr-marker',file=sys.stderr); "
                              f"sys.exit({child_exit})")
                    args = ["--artifact-config", str(root / "config"), "--core-log", str(log),
                            "--report-file", str(target), *(["--verbose"] if verbose else []),
                            "--", sys.executable, "-c", script]
                    with patch.dict(os.environ):
                        os.environ.pop(ENV_NAME, None)
                        os.environ.pop(runner.VERBOSE_ENV, None)
                        with contextlib.redirect_stdout(io.StringIO()) as stdout:
                            code = runner.main(args)
                        self.assertNotIn(runner.VERBOSE_ENV, os.environ)
                    self.assertEqual(code, expected)
                    report = json.loads(target.read_text(encoding="utf-8"))
                    reports.append(report)
                    self.assertIn("stderr-marker", report["cli"]["output"])
                    self.assertFalse(report["cli"]["truncated"])
                    self.assertGreater(len(report["cli"]["output"]), 80000)
                    if verbose:
                        self.assertIn(runner.REPORT_PREFIX, stdout.getvalue())
                        self.assertIn("diagnostic-", stdout.getvalue())
                    else:
                        self.assertLess(len(stdout.getvalue()), 1500)
                        self.assertNotIn("diagnostic-", stdout.getvalue())
                        summary = json.loads(stdout.getvalue().split("MAA_EVIDENCE_SUMMARY=", 1)[1])
                        self.assertEqual(summary["wrapper_exit_code"], expected)
                        self.assertEqual(summary["business_result"], "not_evaluated")
                    self.assertEqual(report["wrapper_exit_code"], expected)
            for key in ("cli", "child_exit_code", "wrapper_exit_code", "failure", "recovery"):
                self.assertEqual(reports[0][key], reports[1][key])
            self.assertEqual(reports[0]["evidence"]["interval_sha256"], reports[1]["evidence"]["interval_sha256"])

    def test_dry_run_is_quiet_but_returns_and_saves_full_resolved_text(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            os.environ.pop(ENV_NAME, None)
            os.environ.pop(runner.VERBOSE_ENV, None)
            root = Path(tmp)
            log = root / "asst.log"
            log.write_text("history\n", encoding="utf-8")
            owner = ArtifactRun.begin(root / "config", None, "test")
            expected = 'Adding task [Fight] with params: {"stage":"AP-5","times":0}\n'
            with patch.object(runner, "_discover_core_log", return_value=log), contextlib.redirect_stdout(io.StringIO()) as out:
                result = runner.run_dry([sys.executable, "-c", f"import sys; sys.stdout.buffer.write({expected.encode()!r})", "--dry-run"],
                                        owner, owner.path / "dry.json")
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(result, expected)
            saved = json.loads((owner.path / "dry.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["cli"]["output"], expected)
            self.assertEqual(saved["wrapper_exit_code"], 0)
            owner.finish("completed")

    def test_detailed_output_scope_restores_environment_on_failure(self):
        with patch.dict(os.environ):
            os.environ[runner.VERBOSE_ENV] = "0"
            with self.assertRaises(ValueError):
                with runner.output_detail(True):
                    self.assertEqual(os.environ[runner.VERBOSE_ENV], "1")
                    with runner.output_detail(False):
                        self.assertEqual(os.environ[runner.VERBOSE_ENV], "1")
                    raise ValueError("test")
            self.assertEqual(os.environ[runner.VERBOSE_ENV], "0")


if __name__ == "__main__":
    unittest.main()
