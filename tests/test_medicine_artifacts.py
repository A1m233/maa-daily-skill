import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import artifacts as a
import drain_sanity
import medicine_sanity as med


class MedicineArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "config"
        self.arguments = ["check", "--policy", str(self.root / "policy.toml"),
                          "--stage", "AP-5", "--profile", "test", "--maa", "fake-maa", "--dialog-open"]
        self.runtime = None
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ))
        os.environ.pop(a.ENV_NAME, None)
        self.stack.enter_context(patch.object(med, "Runtime", side_effect=self.make_runtime))
        self.stack.enter_context(patch.object(med, "load_policy", return_value={"medicine_expire_days": 1}))
        self.stack.enter_context(patch.object(med, "preflight"))
        self.stack.enter_context(patch.object(med, "navigate", side_effect=AssertionError("unexpected navigation")))
        self.command = self.stack.enter_context(patch.object(
            drain_sanity.subprocess, "run", side_effect=AssertionError("real process forbidden")))
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))

    def make_runtime(self, maa, profile, output):
        runtime = drain_sanity.Runtime.__new__(drain_sanity.Runtime)
        runtime.maa, runtime.profile = maa, profile
        runtime.artifacts = a.ArtifactRun.begin(self.config, output, "medicine-check")
        runtime.output = runtime.artifacts.path
        runtime.config = runtime.output / "config-run"
        (runtime.config / "tasks").mkdir(parents=True)
        runtime.configure_stage = lambda stage: None
        runtime.reports = []
        self.runtime = runtime
        return runtime

    def record(self):
        return json.loads((self.runtime.output / a.MANIFEST_NAME).read_text(encoding="utf-8"))

    def test_optional_output_and_positive_result_finish_registered_package(self):
        with patch.object(med, "scan", return_value={"status": "detected", "reminder_required": True}):
            self.assertEqual(med.main(self.arguments), 0)
        self.assertTrue(self.runtime.output.is_relative_to(self.config / "maa-daily-artifacts/runs"))
        self.assertEqual((self.record()["state"], self.record()["status"]), ("finished", "detected"))
        result = json.loads((self.runtime.output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "detected")
        self.command.assert_not_called()

    def test_unknown_business_result_still_finishes_and_preserves_custom_output_root(self):
        output = self.root / "custom-output"
        with patch.object(med, "scan", return_value={"status": "unknown", "reminder_required": True}):
            self.assertEqual(med.main([*self.arguments, "--output-dir", str(output)]), 2)
        self.assertEqual(self.runtime.output.parent, output)
        self.assertEqual((self.record()["state"], self.record()["status"]), ("finished", "unknown"))

    def test_caught_business_failure_is_finished_and_retains_failure_result(self):
        with patch.object(med, "scan", side_effect=ValueError("medicine_close_unverified")):
            self.assertEqual(med.main(self.arguments), 2)
        self.assertEqual((self.record()["state"], self.record()["status"]), ("finished", "failed"))
        result = json.loads((self.runtime.output / "failure.json").read_text(encoding="utf-8"))
        self.assertEqual(result["reason"], "medicine_close_unverified")

    def test_interruption_or_unhandled_error_protects_package(self):
        for error in (KeyboardInterrupt(), RuntimeError("unexpected")):
            with self.subTest(error=type(error).__name__), patch.object(med, "scan", side_effect=error):
                with self.assertRaises(type(error)):
                    med.main(self.arguments)
                self.assertEqual(self.record()["state"], "uncertain")
                self.assertTrue(self.runtime.output.exists())

    def test_child_interruption_survives_caught_runtime_failure_and_owner_finish(self):
        def execute(command, **kwargs):
            if "--dry-run" in command:
                return subprocess.CompletedProcess(command, 0)
            with patch.dict(os.environ, kwargs["env"]):
                a.ArtifactRun.current().finish("interrupted", uncertain=True)
            report_path = Path(command[command.index("--report-file") + 1])
            report_path.write_text(json.dumps({"child_exit_code": 130, "wrapper_exit_code": 130,
                                              "evidence": {"state": "unavailable"}}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 130)

        self.command.side_effect = execute
        with patch.object(med, "scan", side_effect=lambda runtime, *args, **kwargs:
                          runtime.run([{"type": "Custom", "params": {}}], "medicine-scan")):
            self.assertEqual(med.main(self.arguments), 2)
        self.assertEqual(self.record()["state"], "uncertain")
        result = json.loads((self.runtime.output / "failure.json").read_text(encoding="utf-8"))
        self.assertEqual(result["reason"], "runner_failed: 130")

    def test_cleanup_warning_preserves_success_result_and_exit_code(self):
        finish = a.ArtifactRun.finish

        def warn(run, *args, **kwargs):
            result = finish(run, *args, **kwargs)
            result["warnings"].append("cleanup denied")
            return result

        with patch.object(med, "scan", return_value={"status": "detected", "reminder_required": True}), \
                patch.object(a.ArtifactRun, "finish", warn):
            self.assertEqual(med.main(self.arguments), 0)
        self.assertEqual(self.record()["state"], "finished")
        self.assertIn("cleanup denied", self.stderr.getvalue())
        self.assertNotIn("cleanup denied", self.stdout.getvalue())
        self.assertEqual(json.loads((self.runtime.output / "result.json").read_text(encoding="utf-8"))["status"],
                         "detected")


if __name__ == "__main__":
    unittest.main()
