import contextlib
import hashlib
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
sys.path.pop(0)


def scope(chain="Mall", node="CreditShop-NoMoney", parent="CreditShoppingTask"):
    def event(kind, subtask=None, **extra):
        value = {"taskchain": chain, "taskid": 3, "subtask": subtask,
                 "class": "asst::" + (subtask or "Task"), **extra}
        return "[INF][P1][T1] " + runner.CALLBACK_MARKER + kind + " " + json.dumps(value)
    return [event("TaskChainStart"), event("SubTaskStart", parent),
            event("SubTaskError", "ProcessTask", first=[node], pre_task="", details={}),
            event("SubTaskCompleted", parent), event("TaskChainCompleted")]


class ConditionalErrorsTests(unittest.TestCase):
    def test_only_proven_scopes_qualify_and_raw_counts_remain(self):
        for chain, node, parent in [("Mall", "CreditShop-NoMoney", "CreditShoppingTask"),
                                    ("Infrast", "UnlockClues", "InfrastReceptionTask"),
                                    ("Infrast", "EndOfClueExchange", "InfrastReceptionTask")]:
            with self.subTest(node=node):
                result = runner._parse_callbacks("\n".join(scope(chain, node, parent)).encode(), 20)
                self.assertEqual(result["subtask_error_lines"], [22])
                self.assertEqual(result["callback_counts"]["SubTaskError"], 1)
                self.assertEqual(result["blocking_subtask_error_lines"], [])
                self.assertEqual(result["conditional_subtask_errors"][0]["node"], node)

    def test_incomplete_wrong_identity_and_unrecognized_errors_block(self):
        good = scope()
        cases = [good[:3], good[:3] + good[4:], good[1:],
                 scope(chain="Fight"), scope(node="RecognizeDrops"),
                 scope(node="Custom@CreditShop-NoMoney"), scope(parent="OtherTask"),
                 good[:2] + [good[2].replace("[T1]", "[T2]")] + good[3:],
                 good[:2] + [good[2].replace('"taskid": 3', '"taskid": 4')] + good[3:],
                 good[:4] + [good[4].replace("TaskChainCompleted", "TaskChainError")],
                 good[:3] + [good[3].replace("SubTaskCompleted", "SubTaskError")] + good[4:],
                 good + [runner.CALLBACK_MARKER + "SubTaskError malformed"],
                 good[:2] + [good[2].replace('"details": {}', '"details": {"action": "ClickSelf"}')] + good[3:],
                 [line.replace('"class": "asst::CreditShoppingTask"', '"class": "asst::OtherTask"') for line in good],
                 good + [good[0].replace('"taskid": 3', '"taskid": 4')],
                 good + good]
        for rows in cases:
            with self.subTest(rows=rows):
                result = runner.classify_conditional_errors(rows)
                self.assertTrue(result["blocking_subtask_error_lines"])
                self.assertFalse(result["conditional_subtask_errors"])

    def test_unknown_error_in_same_chain_invalidates_probe_exemption(self):
        rows = scope()
        rows.insert(3, rows[2].replace("CreditShop-NoMoney", "CreditShop-BuyIt"))
        result = runner.classify_conditional_errors(rows)
        self.assertEqual(result["blocking_subtask_error_lines"], [3, 4])

    def test_runner_exit_and_legacy_inspector_share_policy(self):
        for node, expected in [("UnlockClues", 0), ("UnknownNode", 75)]:
            for child_exit in (0, 7):
                with self.subTest(node=node, child_exit=child_exit), tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "asst.log"
                    data = ("\n".join(scope("Infrast", node, "InfrastReceptionTask")) + "\n").encode()
                    path.write_bytes(b"")
                    def execute(*args, **kwargs):
                        path.write_bytes(data)
                        return subprocess.CompletedProcess([], child_exit)
                    output = io.StringIO()
                    with patch.object(runner.subprocess, "run", side_effect=execute), contextlib.redirect_stdout(output):
                        code = runner.main(["--core-log", str(path), "--", "fake-maa", "run", "test"])
                    self.assertEqual(code, child_exit or expected)
                    report = json.loads(output.getvalue().split(runner.REPORT_PREFIX)[1])
                    self.assertEqual(report["business_result"], "not_evaluated")
                    self.assertEqual(report["evidence"]["interval_sha256"], hashlib.sha256(data).hexdigest())
                    report["wrapper_exit_code"] = 75  # legacy report, unchanged on disk
                    result = infrast.inspect_report(report)
                    allowed = node == "UnlockClues" and child_exit == 0
                    self.assertEqual(result["legacy_exit_reclassified"], allowed)
                    self.assertEqual(result["run_status"], "warnings" if allowed else "failed")
                    self.assertEqual(result["all_work_completed"], "unknown")
                    self.assertEqual(report["wrapper_exit_code"], 75)

    def test_legacy_hash_and_other_failure_cannot_be_overridden(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asst.log"
            data = ("\n".join(scope("Infrast", "UnlockClues", "InfrastReceptionTask")) + "\n").encode()
            path.write_bytes(data)
            report = {"child_exit_code": 0, "wrapper_exit_code": 74, "evidence": {
                "state": "bounded", "before_size": 0, "after_size": len(data),
                "log_file": str(path), "interval_sha256": hashlib.sha256(data).hexdigest()}}
            self.assertEqual(infrast.inspect_report(report)["run_status"], "failed")
            report["evidence"]["interval_sha256"] = "invalid"
            self.assertEqual(infrast.inspect_report(report)["reason"], "log_interval_changed_or_unhashed")


if __name__ == "__main__":
    unittest.main()
