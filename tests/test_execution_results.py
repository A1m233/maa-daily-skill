import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import run_with_evidence as runner
import infrast_check as infrast
import drain_sanity as drain
import reward_check as rewards
sys.path.pop(0)


def event(kind, chain="Infrast", taskid=3, **extra):
    return "[INF][P1][T1] " + runner.CALLBACK_MARKER + kind + " " + json.dumps({
        "taskchain": chain, "taskid": taskid, **extra})


def scope(nodes=("UnknownProbe",), terminal="TaskChainCompleted", chain="Infrast"):
    return [event("TaskChainStart", chain),
            *[event("SubTaskError", chain, subtask="ProcessTask", first=[node]) for node in nodes],
            event(terminal, chain)]


class ExecutionResultTests(unittest.TestCase):
    def run_report(self, directory, rows, child_exit=0):
        path = Path(directory) / "asst.log"
        path.write_bytes(b"")
        data = ("\n".join(rows) + "\n").encode()
        def execute(*args, **kwargs):
            path.write_bytes(data)
            return subprocess.CompletedProcess([], child_exit)
        output = io.StringIO()
        with patch.object(runner.subprocess, "run", side_effect=execute), contextlib.redirect_stdout(output):
            code = runner.main(["--core-log", str(path), "--", "fake-maa", "run", "test"])
        return code, json.loads(output.getvalue().split(runner.REPORT_PREFIX)[1])

    def test_arbitrary_child_errors_do_not_override_complete_execution(self):
        # Actual five-node shape, and an unseen name: neither requires a whitelist.
        for nodes in [("UnlockClues", "EndOfClueExchange", "InfrastClueSelfFull",
                       "UnlockClues", "EndOfClueExchange"), ("NewUnknownNode",)]:
            with self.subTest(nodes=nodes), tempfile.TemporaryDirectory() as temp:
                code, report = self.run_report(temp, scope(nodes))
                self.assertEqual(code, 0)
                self.assertEqual(report["schema_version"], 2)
                self.assertEqual(report["evidence"]["execution"]["status"], "completed")
                self.assertEqual(len(report["evidence"]["subtask_error_lines"]), len(nodes))
                self.assertEqual(report["business_result"], "not_evaluated")
                result = infrast.inspect_report(report)
                self.assertEqual(result["status"], "evaluated")
                self.assertEqual(result["run_status"], "warnings")
                self.assertEqual(len(result["error_groups"]["infrast"]), len(nodes))
                self.assertEqual(result["all_work_completed"], "unknown")
                self.assertEqual(result["chains"][0]["collection_status"], "unknown")
                self.assertEqual(result["continuation"], "requires_business_preconditions")
                self.assertTrue(result["business_review_required"])
                self.assertTrue(result["reminder_required"])

    def test_execution_failures_and_incomplete_boundaries_still_stop(self):
        good = scope()
        cases = [(scope(terminal="TaskChainError"), 75),
                 (scope(terminal="TaskChainStopped"), 75),
                 (good[:-1], 74), (good[1:], 74), (good + good, 74),
                 ([event("SubTaskError")], 74),
                 (good + [event("SubTaskError")], 74),
                 (good + [event("TaskChainStart", taskid=9)], 74),
                 (good + [runner.CALLBACK_MARKER + "SubTaskError []"], 74),
                 (good + [runner.CALLBACK_MARKER + "SubTaskError malformed"], 74),
                 (good + [event("InternalError")], 75),
                 (good + [event("InitFailed")], 75)]
        for rows, expected in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as temp:
                code, report = self.run_report(temp, rows)
                self.assertEqual(code, expected)
                self.assertNotEqual(report["evidence"]["execution"]["status"], "completed")
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.run_report(temp, good, child_exit=7)[0], 7)

    def test_legacy_reassessment_is_execution_only_and_never_rewrites_report(self):
        with tempfile.TemporaryDirectory() as temp:
            _, report = self.run_report(temp, scope())
            report.update(schema_version=1, wrapper_exit_code=75)
            result = infrast.inspect_report(report)
            self.assertTrue(result["legacy_exit_reclassified"])
            self.assertEqual(result["original_wrapper_exit_code"], 75)
            self.assertEqual(report["wrapper_exit_code"], 75)
            self.assertEqual(result["all_work_completed"], "unknown")
            for change in ({"schema_version": 2}, {"child_exit_code": 1},
                           {"runner_error": "Interrupted"}, {"wrapper_exit_code": 74}):
                with self.subTest(change=change):
                    failed = infrast.inspect_report({**report, **change})
                    self.assertFalse(failed["legacy_exit_reclassified"])
                    self.assertEqual(failed["run_status"], "failed")
            report["evidence"]["interval_sha256"] = "invalid"
            self.assertEqual(infrast.inspect_report(report)["reason"], "log_interval_changed_or_unhashed")

    def test_strict_business_checks_reject_child_error_even_with_runner_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report = self.run_report(temp, scope(("RecognizeDrops",), chain="Fight"))
            self.assertEqual(code, 0)
            with self.assertRaisesRegex(ValueError, "invalid_or_failed_callback"):
                drain.callbacks(report)
            code, report = self.run_report(temp, scope(("ScanFailed",), chain="Custom"))
            self.assertEqual(code, 0)
            self.assertEqual(rewards.evaluate(report)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
