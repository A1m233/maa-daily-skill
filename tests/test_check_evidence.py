import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import test_drain_sanity as drain_fixture
import test_medicine_sanity as medicine_fixture
import test_reward_check as reward_fixture
from check_evidence import read_check
from daily_checks import inspect_report
from drain_sanity import read_probe
from medicine_sanity import read_scan, read_session
from reward_check import evaluate, read_navigation, PHASES, ENTRY

event = drain_fixture.event
ERR = "[2026-11-05 01:00:00][ERR][Px1][Tx2] arbitrary diagnostic\n"


def reward_body():
    body = event("TaskChainStart", taskchain="Custom")
    body += event("SubTaskCompleted", taskchain="Custom", first=[ENTRY], details={
        "task": "RewardScan", "action": "ClickSelf", "result": {"text": "日常任务"}})
    for phase, rows in reward_fixture.pages_for(10).items():
        ocr = ", ".join("{ text: 已完成, rect: [ %d, %d, %d, %d ], score: %.6f }" %
                        (*r["rect"], r["score"]) for r in rows)
        body += f"PipelineAnalyzer::analyze | OcrDetect MaaDailyCheck@{phase} [{ocr}]\n"
        body += event("SubTaskCompleted", taskchain="Custom", first=[ENTRY], details={
            "task": phase, "action": "DoNothing"})
    return body + event("TaskChainCompleted", taskchain="Custom")


class CheckEvidenceTests(unittest.TestCase):
    def report(self, directory, body):
        report = drain_fixture.DrainTests().report(directory, body)
        report.update(started_at="2026-11-05T01:00:00+08:00", ended_at="2026-11-05T01:00:20+08:00")
        # Deliberately stale metadata: the reader must use original sealed text.
        report["evidence"].update(internal_error_lines=[999], start_line=10)
        return report

    def test_external_diagnostics_do_not_discard_probe_reward_or_medicine(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.report(directory, ERR + drain_fixture.probe_body() + ERR)
            self.assertEqual(read_probe(p, "AP-5"), 205)
            self.assertEqual(inspect_report(p)["sanity"]["current"], 205)
            _, diagnostic = read_check(p)
            self.assertEqual(diagnostic["before_chain"], [10])
            self.assertEqual(len(diagnostic["after_chain"]), 1)
            self.assertEqual(diagnostic["within_chain"], [])
            p = self.report(directory, ERR + reward_body() + ERR)
            rewards = evaluate(p)
            self.assertEqual(rewards["claimed_tiers"], 10)
            self.assertTrue(rewards["reminder_required"])
            from daily_report import summarize
            brief = summarize({"steps": {"checks": {"rewards": rewards}}})
            self.assertIn("execution_warnings", [n["code"] for n in brief["notices"]])
            self.assertIn("每日合成玉、剿灭扫荡券已领", brief["facts"])
            p = self.report(directory, ERR + medicine_fixture.scan_body() + ERR)
            self.assertEqual(read_scan(p, 1, "Dialog")["status"], "detected")
            p = self.report(directory, ERR + medicine_fixture.MedicineTests().session_body() + ERR)
            result = read_session(p, "AP-5", 1, "ScanDialog")
            self.assertEqual((result["scan_status"], result["end_at"]), ("completed", "prepared"))

    def test_active_errors_are_rejected_even_if_metadata_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            for body, consumer in (
                (drain_fixture.probe_body(), lambda p: read_probe(p, "AP-5")),
                (medicine_fixture.scan_body(), lambda p: read_scan(p, 1, "Dialog")),
            ):
                first, rest = body.split("\n", 1)
                p = self.report(directory, first + "\n" + ERR + rest)
                p["evidence"]["internal_error_lines"] = []
                with self.assertRaisesRegex(ValueError, "active_or_critical"):
                    consumer(p)
            first, rest = reward_body().split("\n", 1)
            self.assertEqual(evaluate(self.report(directory, first + "\n" + ERR + rest))["status"], "unknown")

    def test_execution_errors_missing_identity_and_corrupt_hash_never_pass(self):
        good = drain_fixture.probe_body()
        cases = [good.replace("TaskChainCompleted", "TaskChainError"),
                 good.replace("TaskChainCompleted", "TaskChainStopped"),
                 good + event("SubTaskCompleted", taskchain="Custom"),
                 good + good, good.replace('"taskid": 1', '"taskid": 2', 1),
                 good + event("InternalError"), good + event("InitFailed"),
                 good + "Assistant::append_callback | SubTaskCompleted {bad\n",
                 ERR.replace("[ERR]", "[CRT]") + good]
        with tempfile.TemporaryDirectory() as directory:
            for body in cases:
                with self.subTest(body=body[-80:]), self.assertRaises(ValueError):
                    read_check(self.report(directory, body))
            p = self.report(directory, good)
            p["evidence"]["interval_sha256"] = "0"*64
            with self.assertRaises(ValueError):
                read_check(p)

    def test_partial_medicine_facts_survive_only_errors_after_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            body = medicine_fixture.MedicineTests().session_body(probe=False, terminal="TaskChainError")
            p = self.report(directory, ERR + body)
            p.update(wrapper_exit_code=1, child_exit_code=1)
            result = read_session(p, "AP-5", 1, "ScanDialog")
            self.assertEqual(result["scan_status"], "completed")
            self.assertEqual(result["end_at"], "unknown")
            first, rest = body.split("\n", 1)
            p = self.report(directory, first + "\n" + ERR + rest)
            p.update(wrapper_exit_code=1, child_exit_code=1)
            self.assertEqual(read_session(p, "AP-5", 1, "ScanDialog")["scan_status"], "unknown")
