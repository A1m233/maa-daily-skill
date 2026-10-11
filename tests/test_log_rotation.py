"""真实文件 rename + 假子进程；不连接游戏或修改用户日志。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import run_with_evidence as runner
from log_evidence import read_evidence
import daily_run
import drain_sanity
import infrast_check
import recruit_check
import medicine_sanity
import reward_check
from test_drain_sanity import event, probe_body
import test_medicine_sanity as medicine_fixtures
import test_reward_check as reward_fixtures


def recruit_body():
    return (event("TaskChainStart", taskchain="Recruit")
            + event("SubTaskExtraInfo", taskchain="Recruit", what="RecruitTagsDetected", details={"tags": ["近战位"]})
            + event("SubTaskCompleted", taskchain="Recruit", details={"task": "RecruitConfirm", "action": "ClickSelf"})
            + event("TaskChainCompleted", taskchain="Recruit"))


def rotated_report(root, body, cut=None):
    """运行前历史尾部 + 本次开始 + rename + 本次完成，模拟官方行为。"""
    log = root / "asst.log"
    log.write_bytes(b"historical\n" * 300)
    before = runner._snapshot(log)
    raw = body.encode("utf-8")
    cut = raw.index(b"\n") + 1 if cut is None else cut
    with log.open("ab") as handle:
        handle.write(raw[:cut])
    log.replace(root / "asst.bak.log")
    log.write_bytes(raw[cut:])
    evidence = runner._collect_evidence(log, before, runner._snapshot(log))
    return {"schema_version": 2, "child_exit_code": 0, "wrapper_exit_code": 0,
            "started_at": "2026-10-11T22:00:00+08:00", "ended_at": "2026-10-11T22:00:25+08:00",
            "runner_error": None, "evidence": evidence}


class LogRotationTests(unittest.TestCase):
    def test_threshold_sized_history_is_not_copied_or_counted_as_run_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "asst.log"
            with log.open("wb") as handle:
                for _ in range(64):
                    handle.write(b"x" * (1024 * 1024 - 1) + b"\n")
            before = runner._snapshot(log)
            body = recruit_body().encode()
            split = body.index(b"\n") + 1
            with log.open("ab") as handle:
                handle.write(body[:split])
            log.replace(root / "asst.bak.log")
            log.write_bytes(body[split:])
            e = runner._collect_evidence(log, before, runner._snapshot(log))
            self.assertEqual(e["state"], "bounded")
            self.assertEqual(read_evidence(e), body)
            self.assertEqual(sum(s["after_size"] - s["before_size"] for s in e["segments"]), len(body))
            self.assertEqual({p.name for p in root.iterdir()}, {"asst.log", "asst.bak.log"})

    def test_recovers_start_end_split_and_inspects_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = rotated_report(root, recruit_body())
            e = report["evidence"]
            self.assertEqual(e["state"], "bounded")
            self.assertTrue(e["rotation_recovered"])
            self.assertEqual(len(e["segments"]), 2)
            self.assertLess(e["after_size"], e["before_size"])
            self.assertEqual(read_evidence(e), recruit_body().encode())
            before = {p: p.read_bytes() for p in root.iterdir()}
            self.assertEqual(runner.inspect_execution_report(report)["reassessed_wrapper_exit_code"], 0)
            self.assertEqual(recruit_check.inspect_report(report)["chains"][0]["confirmed_actions"], 1)
            self.assertEqual(before, {p: p.read_bytes() for p in root.iterdir()})

    def test_all_rotation_positions_and_log_larger_than_old_file(self):
        for cut in (0, 1, len(recruit_body().encode()), len(recruit_body().encode()) // 2):
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as temp:
                body = recruit_body() + "extra\n" * 1000
                report = rotated_report(Path(temp), body, cut)
                self.assertEqual(read_evidence(report["evidence"]), body.encode())
                self.assertEqual(report["evidence"]["execution"]["status"], "completed")

    def test_missing_copytruncate_modified_and_second_rotation_fail_closed(self):
        for mode in ("missing", "copytruncate", "old-backup", "changed-prefix", "second"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                log, backup = root / "asst.log", root / "asst.bak.log"
                log.write_bytes(b"old history\n" * 200)
                before = runner._snapshot(log)
                start = event("TaskChainStart", taskchain="Recruit").encode()
                end = event("TaskChainCompleted", taskchain="Recruit").encode()
                with log.open("ab") as handle:
                    handle.write(start)
                if mode == "copytruncate":
                    backup.write_bytes(log.read_bytes())
                else:
                    log.replace(backup)
                log.write_bytes(end)
                if mode == "missing":
                    backup.unlink()
                elif mode == "old-backup":
                    # unrelated file, even if it resembles the previous tail
                    replacement = root / "other.log"
                    replacement.write_bytes(backup.read_bytes())
                    replacement.replace(backup)
                elif mode == "changed-prefix":
                    with backup.open("r+b") as handle:
                        handle.write(b"BAD")
                elif mode == "second":
                    log.replace(backup)
                    log.write_bytes(end)
                e = runner._collect_evidence(log, before, runner._snapshot(log))
                self.assertEqual(e["state"], "rotated")
                self.assertNotIn("segments", e)

    def test_rotated_evidence_changes_reordering_and_budget_are_rejected(self):
        for mode in ("hash", "order", "missing", "old-tail", "current", "oversize", "negative"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                report = rotated_report(root, recruit_body())
                e = report["evidence"]
                if mode == "hash":
                    e["interval_sha256"] = "bad"
                elif mode == "order":
                    e["segments"].reverse()
                elif mode == "missing":
                    (root / "asst.bak.log").unlink()
                elif mode == "old-tail":
                    with (root / "asst.bak.log").open("r+b") as handle:
                        handle.seek(e["before_size"])
                        handle.write(b"BAD")
                elif mode == "current":
                    (root / "asst.log").write_bytes(b"BAD")
                elif mode == "oversize":
                    e["segments"][0]["after_size"] = 128 * 1024 * 1024
                else:
                    e["before_size"] = e["segments"][0]["before_size"] = -1
                with self.assertRaises(ValueError):
                    read_evidence(e)
                self.assertEqual(runner.inspect_execution_report(report)["status"], "unknown")

    def test_rotation_during_collection_and_size_limit_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "asst.log"
            log.write_bytes(b"old\n")
            before = runner._snapshot(log)
            with log.open("ab") as handle:
                handle.write(recruit_body().encode())
            log.replace(root / "asst.bak.log")
            log.write_bytes(b"after\n")
            after = runner._snapshot(log)
            with patch.object(runner, "MAX_INTERVAL_BYTES", 1):
                self.assertEqual(runner._collect_evidence(log, before, after)["state"], "rotated")
            original = runner._read_appended
            def changing(path, start, end):
                data = original(path, start, end)
                if path == log:
                    with path.open("ab") as handle:
                        handle.write(b"concurrent\n")
                return data
            with patch.object(runner, "_read_appended", side_effect=changing):
                self.assertEqual(runner._collect_evidence(log, before, after)["state"], "rotated")

    def test_old_single_interval_report_remains_readable_after_later_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "asst.log"
            log.write_bytes(b"old\n")
            before = runner._snapshot(log)
            with log.open("ab") as handle:
                handle.write(recruit_body().encode())
            e = runner._collect_evidence(log, before, runner._snapshot(log))
            self.assertNotIn("segments", e)
            log.replace(root / "asst.bak.log")
            log.write_bytes(b"later\n")
            self.assertEqual(read_evidence(e), recruit_body().encode())
            (root / "asst.bak.log").write_bytes(b"overwritten\n")
            with self.assertRaises(ValueError):
                read_evidence(e)
            e["state"] = "rotated"  # no retrospective guessing from old unsealed reports
            with self.assertRaises(ValueError):
                read_evidence(e)

    def test_native_phase_can_continue_after_recruit_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recruit = rotated_report(root, recruit_body())
            # A second phase appends, it must not invalidate the first segment seals.
            log = root / "asst.log"
            before = runner._snapshot(log)
            middle = event("SubTaskCompleted", taskchain="Infrast", details={
                "task": "InfrastReward", "action": "ClickSelf", "result": {"text": "可收获", "score": .99}})
            body = event("TaskChainStart", taskchain="Infrast") + middle + event("TaskChainCompleted", taskchain="Infrast")
            with log.open("ab") as handle:
                handle.write(body.encode())
            infrast = {**recruit, "evidence": runner._collect_evidence(log, before, runner._snapshot(log))}
            ops = daily_run.Daily.__new__(daily_run.Daily)
            ops.runtime = Mock()
            ops.runtime.run.side_effect = [recruit, infrast]
            ops.check_day = Mock()
            ops.output = root
            ops.before = [{"type": "Recruit", "params": {}}, {"type": "Infrast", "params": {}}]
            ops.pre_reports, ops.priority_reports = [], []
            self.assertIn(ops.pre()["status"], ("completed", "completed_with_reminder"))
            self.assertEqual(ops.runtime.run.call_count, 2)
            self.assertEqual(infrast_check.inspect_report(infrast)["chains"][0]["collection_status"], "action_observed")
            self.assertEqual(recruit_check.inspect_report(recruit)["chains"][0]["confirmed_actions"], 1)

    def test_probe_and_both_medicine_readers_use_rotated_intervals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = rotated_report(root, probe_body())
            self.assertEqual(drain_sanity.read_probe(report, "AP-5"), 205)
            report = rotated_report(root, medicine_fixtures.scan_body())
            self.assertEqual(medicine_sanity.read_scan(report, 1, "Dialog")["status"], "detected")
            report = rotated_report(root, medicine_fixtures.MedicineTests().session_body())
            self.assertEqual(medicine_sanity.read_session(report, "AP-5", 1, "ScanDialog")["end_at"], "prepared")

    def test_reward_scan_and_page_recheck_use_rotated_intervals(self):
        body = event("TaskChainStart", taskchain="Custom")
        def completed(name, action="DoNothing", text=""):
            return event("SubTaskCompleted", taskchain="Custom", first=[reward_check.ENTRY],
                         details={"task": name, "action": action, "result": {"text": text}})
        body += completed("RewardScan", "ClickSelf", "日常任务")
        for phase, rows in reward_fixtures.pages_for(10).items():
            ocr = ", ".join("{ text: 已完成, rect: [ %d, %d, %d, %d ], score: %.6f }" %
                            (*item["rect"], item["score"]) for item in rows)
            body += f"PipelineAnalyzer::analyze | OcrDetect MaaDailyCheck@{phase} [{ocr}]\n" + completed(phase)
        body += event("TaskChainCompleted", taskchain="Custom")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(reward_check.evaluate(rotated_report(root, body))["claimed_tiers"], 10)
            page = reward_fixtures.RewardCheckTests().page_report(temp)
            data = read_evidence(page["evidence"]).decode()
            rotated = rotated_report(root, data)
            rotated.update(started_at=page["started_at"], ended_at=page["ended_at"])
            nav = {"started_at": "2026-09-18T01:00:00+08:00", "ended_at": "2026-09-18T01:00:15+08:00"}
            self.assertEqual(reward_check.read_page_recheck(rotated, nav)["basis"], "repeated_joint_labels")

    def test_real_child_rotation_exit_codes_and_no_business_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "asst.log"
            for terminal, child_exit, expected in (("TaskChainCompleted", 0, 0), ("TaskChainError", 0, 75),
                                                  ("TaskChainCompleted", 9, 9)):
                log.write_bytes(b"previous\n" * 100)
                start = event("TaskChainStart", taskchain="Recruit").encode()
                end = event(terminal, taskchain="Recruit").encode()
                script = ("from pathlib import Path; import sys; "
                          f"p=Path({str(log)!r}); "
                          f"f=p.open('ab'); f.write({start!r}); f.close(); "
                          "p.replace(p.with_name('asst.bak.log')); "
                          f"p.write_bytes({end!r}); sys.exit({child_exit})")
                result = runner._run_command([sys.executable, "-c", script], log, runner._utc_now(),
                                             {"subcommand": "run"}, dict(os.environ))
                self.assertEqual(result["wrapper_exit_code"], expected)
                self.assertEqual(result["child_exit_code"], child_exit)
                self.assertFalse(runner.classify_cli_failure(result)["retry_eligible"])


if __name__ == "__main__":
    unittest.main()
