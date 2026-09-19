import copy
import contextlib
import io
import shutil
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maa-daily/scripts"))
spec = importlib.util.spec_from_file_location("reward_check", ROOT / "maa-daily/scripts/reward_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def items(centers):
    return [{"text": "已完成", "rect": [102, center-9, 58, 18], "score": 0.99} for center in centers]


def pages_for(count):
    return {phase: items([origin+89*row for row in visible if row >= 10-count])
            for phase, origin, visible in [
                ("RewardTopA", 153, range(7)), ("RewardTopB", 153, range(7)),
                ("RewardBottomA", -137, range(3, 10)), ("RewardBottomB", -137, range(3, 10))]}


class RewardCheckTests(unittest.TestCase):
    def page_report(self, directory, mutate=lambda p: None):
        from test_drain_sanity import DrainTests, event
        rows = [{"text": "t24/日常任务", "rect": [502, 10, 142, 43], "score": 0.845},
                {"text": "周常任务", "rect": [753, 21, 87, 24], "score": 0.94},
                {"text": "主线任务", "rect": [949, 19, 90, 26], "score": 0.99}]
        pages = {p: copy.deepcopy(rows) for p in check.PAGE_READS}
        mutate(pages)
        body = event("TaskChainStart", taskchain="Custom")
        for phase, items in pages.items():
            ocr = ", ".join("{ text: %s, rect: [ %d, %d, %d, %d ], score: %.6f }" %
                            (i["text"], *i["rect"], i["score"]) for i in items)
            body += f"PipelineAnalyzer::analyze | OcrDetect MaaDailyCheck@{phase} [{ocr}]\n"
            body += event("SubTaskCompleted", taskchain="Custom", first=[check.PREFIX+check.PAGE_READS[0]],
                          details={"task": phase, "action": "DoNothing", "algorithm": "OcrDetect"})
        body += event("TaskChainCompleted", taskchain="Custom")
        report = DrainTests().report(directory, body)
        report.update(started_at="2026-09-18T01:00:20+08:00", ended_at="2026-09-18T01:00:25+08:00")
        return report

    def test_page_recheck_joint_evidence_and_fail_closed(self):
        nav = {"started_at": "2026-09-18T01:00:00+08:00", "ended_at": "2026-09-18T01:00:15+08:00"}
        with tempfile.TemporaryDirectory() as directory:
            report = self.page_report(directory)
            self.assertEqual(check.read_page_recheck(report, nav)["basis"], "repeated_joint_labels")
            for mutation in (
                lambda p: p[check.PAGE_READS[0]][0].update(text="任务完成"),
                lambda p: p[check.PAGE_READS[0]][0].update(score=0.79),
                lambda p: p[check.PAGE_READS[1]].pop(),
                lambda p: p[check.PAGE_READS[1]][0].update(rect=[502, 300, 142, 43]),
                lambda p: p[check.PAGE_READS[1]][1].update(rect=[790, 21, 87, 24]),
                lambda p: p[check.PAGE_READS[1]].append(copy.deepcopy(p[check.PAGE_READS[1]][0])),
                lambda p: p.pop(check.PAGE_READS[1]),
            ):
                with self.assertRaises(ValueError):
                    check.read_page_recheck(self.page_report(directory, mutation), nav)
            report = self.page_report(directory)
            report["ended_at"] = "2026-09-18T04:00:00+08:00"
            with self.assertRaisesRegex(ValueError, "time_boundary"):
                check.read_page_recheck(report, nav)
            report = self.page_report(directory)
            Path(report["evidence"]["log_file"]).write_text("changed")
            with self.assertRaises(ValueError):
                check.read_page_recheck(report, nav)

    def test_low_navigation_requests_recheck_not_immediate_success(self):
        from test_drain_sanity import DrainTests, event
        body = (event("TaskChainStart", taskchain="Custom") +
                event("SubTaskCompleted", taskchain="Custom", details={"task": "RewardNavReady", "action": "DoNothing",
                      "result": {"text": "日常任务", "score": 0.845, "rect": [502, 10, 142, 43]}}) +
                event("TaskChainCompleted", taskchain="Custom"))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(check.read_navigation(DrainTests().report(directory, body))["status"], "recheck_required")

    def test_scan_rechecks_once_and_never_scans_after_failed_recheck(self):
        from subprocess import CompletedProcess
        for passed in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "tasks").mkdir()
                calls = []
                def execute(command, **kwargs):
                    calls.append(command)
                    if "--report-file" in command:
                        Path(command[command.index("--report-file")+1]).write_text('{}')
                    return CompletedProcess(command, 0)
                with patch.object(check, "check_install", return_value=root), \
                        patch.object(check.subprocess, "run", side_effect=execute), \
                        patch.object(check, "read_navigation", return_value={"status": "recheck_required"}), \
                        patch.object(check, "read_page_recheck", side_effect=None if passed else ValueError("page_recheck_features_unverified"),
                                     return_value={"status": "verified"}), \
                        patch.object(check, "evaluate", return_value={"status": "evaluated"}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result, output = check.scan_once("maa", "test", root / "out")
                self.assertEqual(len(calls), 6 if passed else 4)
                self.assertEqual(result["status"], "evaluated" if passed else "unknown")
                self.assertEqual((output / "evidence.json").exists(), passed)
                recheck = json.loads((output / "page-recheck-task.json").read_text())
                self.assertEqual(recheck["tasks"][0]["params"]["task_names"], [check.PREFIX+check.PAGE_READS[0]])
                assets = json.loads((ROOT / "maa-daily/assets/daily-checks/tasks.json").read_text())
                self.assertEqual(assets[check.PREFIX+check.PAGE_READS[0]]["action"], "DoNothing")
                self.assertEqual(assets[check.PREFIX+check.PAGE_READS[1]]["next"], [])

    def test_navigation_requires_task_page_and_no_business_clicks(self):
        from test_drain_sanity import DrainTests, event
        fixture = DrainTests()
        body = (event("TaskChainStart", taskchain="Custom") +
                event("SubTaskCompleted", taskchain="Custom", details={"task": "RewardNavReady", "action": "DoNothing",
                      "result": {"text": "t24/日常任务", "score": 0.91, "rect": [640, 11, 144, 40]}}) +
                event("TaskChainCompleted", taskchain="Custom"))
        with tempfile.TemporaryDirectory() as directory:
            report = fixture.report(directory, body)
            self.assertEqual(check.read_navigation(report)["end_at"], "task_page")
            for bad in (body.replace(json.dumps("t24/日常任务"), json.dumps("周常任务")), body.replace("0.91", "0.5"),
                        body.replace("640, 11", "640, 400"), body + event("SubTaskError"),
                        body + event("SubTaskCompleted", taskchain="Custom", details={"task": "ReceiveAward", "action": "ClickSelf"})):
                with self.assertRaises(ValueError):
                    check.read_navigation(fixture.report(directory, bad))

    def test_high_confidence_path_has_no_extra_recheck_process(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            def execute(command, **kwargs):
                if "--report-file" in command:
                    Path(command[command.index("--report-file")+1]).write_text('{}')
                return CompletedProcess(command, 0)
            with patch.object(check, "check_install", return_value=root), \
                    patch.object(check.subprocess, "run", side_effect=execute) as run, \
                    patch.object(check, "read_navigation", return_value={"status":"verified"}), \
                    patch.object(check, "read_page_recheck") as recheck, \
                    patch.object(check, "evaluate", return_value={"status":"evaluated"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                result, output = check.scan_once("maa", "test", root / "out")
            self.assertEqual(run.call_count, 4)
            recheck.assert_not_called()
            self.assertNotIn("page_recheck_report", result)
            self.assertFalse((output / "page-recheck-task.json").exists())

    def test_failed_navigation_never_scans_or_claims(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            with patch.object(check, "check_install", return_value=root), patch.object(check.subprocess, "run") as run:
                run.side_effect = [CompletedProcess([], 0), CompletedProcess([], 1)]
                with contextlib.redirect_stdout(io.StringIO()):
                    result, output = check.scan_once("maa", "example", root / "output")
                self.assertEqual(result["reason"], "navigation_failed")
                self.assertEqual(run.call_count, 2)
                self.assertFalse((output / "evidence.json").exists())

    def test_measured_five_state_projection(self):
        data = json.loads((ROOT / "tests/fixtures/reward-tier-centers.json").read_text(encoding="utf-8"))
        for sample in data["samples"]:
            with self.subTest(claimed=sample["claimed"]):
                pages = {"RewardTopA": items(sample["top"]), "RewardTopB": items(sample["top"]),
                         "RewardBottomA": items(sample["bottom"]), "RewardBottomB": items(sample["bottom_repeat"])}
                result = check.classify(pages)
                self.assertEqual(sample["claimed"], result["claimed_tiers_min"])
                self.assertEqual(10-sample["claimed"], result["unclaimed_tiers_max"])
                self.assertEqual(10, result["claimed_tiers_max"])

    def test_synthetic_thresholds_and_deduplication(self):
        for count in range(1, 11):
            result = check.classify(pages_for(count))
            self.assertEqual(count, result["claimed_tiers_min"])
            self.assertEqual(count if count == 10 else None, result["claimed_tiers"])
            self.assertEqual(count < 9, result["reminder_required"])
            self.assertEqual("claimed" if count >= 7 else "unknown", result["daily_orundum"])
            self.assertEqual("claimed" if count >= 9 else "unknown", result["daily_annihilation_ticket"])
        self.assertEqual("unknown", check.classify(pages_for(0))["status"])

    def test_fail_closed_on_missing_unstable_shifted_and_low_confidence(self):
        for mutation in ("missing", "unstable", "shift", "confidence", "hole", "overlap", "ambiguous"):
            pages = pages_for(10)
            if mutation == "missing":
                del pages["RewardTopB"]
            elif mutation == "unstable":
                pages["RewardBottomB"].pop()
            elif mutation == "shift":
                for page in pages.values():
                    for item in page:
                        item["rect"][1] += 25
            elif mutation == "confidence":
                pages["RewardBottomB"][-1]["score"] = 0.6
            elif mutation == "hole":
                pages["RewardBottomA"].pop(-2)
                pages["RewardBottomB"].pop(-2)
            elif mutation == "overlap":
                pages["RewardTopA"].pop()
                pages["RewardTopB"].pop()
            else:
                pages["RewardTopA"][0]["text"] = "已完戎"
            with self.subTest(mutation=mutation):
                self.assertEqual(mutation != "hole", check.classify(pages)["reminder_required"])
                self.assertIsNone(check.classify(pages)["claimed_tiers"])

    def test_overlap_resolves_only_with_two_reliable_other_endpoint_reads(self):
        pages = pages_for(9)
        for phase in ("RewardBottomA", "RewardBottomB"):
            pages[phase][2].update(text="!完成", score=0.76)
        result = check.classify(pages)
        self.assertEqual(result["claimed_tiers_min"], 9)
        self.assertEqual(len(result["resolved_ambiguities"]), 2)
        self.assertNotIn(6, result["observed_claimed"]["RewardBottomA"])
        self.assertIn(6, result["visible_claimed"]["RewardBottomA"])
        for mutation in ("both_ends", "missing_support", "shift", "duplicate", "negative", "invalid_score"):
            bad = copy.deepcopy(pages)
            if mutation == "both_ends":
                bad["RewardTopA"][4].update(text="!完成", score=0.76)
            elif mutation == "missing_support":
                bad["RewardTopA"].pop(4)
            elif mutation == "shift":
                bad["RewardBottomA"][2]["rect"][1] += 25
            elif mutation == "negative":
                bad["RewardBottomA"][2]["text"] = "未完成"
            elif mutation == "invalid_score":
                bad["RewardBottomA"][2]["score"] = float("nan")
            else:
                bad["RewardBottomA"].append(copy.deepcopy(bad["RewardBottomA"][2]))
            with self.subTest(mutation=mutation):
                self.assertEqual(check.classify(bad)["status"], "unknown")

    def test_completed_substring_in_reward_region_and_negative_exclusions(self):
        pages = pages_for(10)
        for phase in check.PHASES[2:]:
            pages[phase][1].update(text="]完成", score=0.836506)
            pages[phase][4].update(text="完成", score=0.999839)
        result = check.classify(pages)
        self.assertEqual(result["claimed_tiers"], 10)
        self.assertFalse(result["reminder_required"])
        for text in ("未完成", "尚未完成", "没有完成", "可领取"):
            bad = copy.deepcopy(pages)
            bad["RewardBottomA"][4]["text"] = text
            self.assertEqual(check.classify(bad)["status"], "unknown")
        for phase in check.PHASES[2:]:
            pages[phase][4]["rect"][0] = 800
        self.assertEqual(check.classify(pages)["status"], "unknown")

    def test_uncertainty_preserves_supported_reward_conclusions(self):
        for count, orundum, ticket in [(9, "claimed", "claimed"),
                                       (8, "claimed", "unknown"), (6, "unknown", "unknown")]:
            pages = pages_for(count)
            result = check.classify(pages)
            self.assertEqual(result["claimed_tiers_min"], count)
            self.assertEqual(result["claimed_tiers_max"], 10)
            self.assertEqual(result["daily_orundum"], orundum)
            self.assertEqual(result["daily_annihilation_ticket"], ticket)
            self.assertIsNone(result["claimed_tiers"])
        for text, score in [("已完戎", 0.99), ("完成", 0.6)]:
            pages = pages_for(10)
            for phase in check.PHASES[2:]:
                pages[phase][4].update(text=text, score=score)
            result = check.classify(pages)
            self.assertEqual(result["claimed_tiers_min"], 9)
            self.assertEqual(result["uncertain_positions"], [8])
            self.assertEqual(result["daily_annihilation_ticket"], "claimed")

    def test_blank_reads_never_prove_zero_claimed(self):
        result = check.classify({phase: [] for phase in check.PHASES})
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["claimed_tiers_min"])

    def test_bounded_runtime_protocol(self):
        def event(name, value):
            return "Assistant::append_callback | " + name + " " + json.dumps(value, ensure_ascii=False)
        def completed(name, action="DoNothing", text=""):
            return event("SubTaskCompleted", {"taskchain": "Custom", "taskid": 1,
                "first": [check.ENTRY], "details": {"task": name, "action": action, "result": {"text": text}}})
        lines = [event("TaskChainStart", {"taskchain": "Custom", "taskid": 1}),
                 completed("RewardScan", "ClickSelf", "日常任务")]
        for phase, rows in pages_for(10).items():
            ocr = ", ".join("{ text: 已完成, rect: [ %d, %d, %d, %d ], score: %.6f }" % (*item["rect"], item["score"]) for item in rows)
            lines += [f"PipelineAnalyzer::analyze | OcrDetect MaaDailyCheck@{phase} [{ocr}]", completed(phase)]
        lines += [event("TaskChainCompleted", {"taskchain": "Custom", "taskid": 1})]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "asst.log"
            data = ("\n".join(lines) + "\n").encode()
            log.write_bytes(b"old\n" + data + b"new\n")
            report = {"child_exit_code": 0, "wrapper_exit_code": 0,
                "started_at": "2026-09-07T01:00:00+08:00", "ended_at": "2026-09-07T01:00:25+08:00",
                "evidence": {"state": "bounded", "before_size": 4, "after_size": len(data)+4,
                    "interval_sha256": hashlib.sha256(data).hexdigest(), "log_file": str(log)}}
            result = check.evaluate(report)
            self.assertEqual(10, result["claimed_tiers"])
            self.assertEqual("2026-09-06", result["game_day"])
            bad = copy.deepcopy(report)
            bad["started_at"] = "2026-09-07T03:59:59+08:00"
            bad["ended_at"] = "2026-09-07T04:00:15+08:00"
            self.assertEqual("unknown", check.evaluate(bad)["status"])
            bad = copy.deepcopy(report)
            bad["wrapper_exit_code"] = 75
            self.assertEqual("unknown", check.evaluate(bad)["status"])
            log.write_bytes(b"x"*log.stat().st_size)
            self.assertEqual("unknown", check.evaluate(report)["status"])

    def test_install_tampering_prevents_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            (root / "tasks/maa-daily-reward-scan.toml").write_text('tasks = []', encoding="utf-8")
            with patch.object(check.subprocess, "run") as run:
                run.return_value.stdout = str(root)
                with self.assertRaises(ValueError):
                    check.check_install("maa")
                self.assertEqual(1, run.call_count)  # Only directory discovery, never maa run.

    def test_preflight_installed_missing_and_conflicting_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = ROOT / "maa-daily/assets/daily-checks"
            (root / "tasks").mkdir()
            (root / "resource/tasks").mkdir(parents=True)
            with patch.object(check.subprocess, "run") as run:
                run.return_value.stdout = str(root)
                def invoke():
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                        code = check.main(["--layout", check.LAYOUT, "preflight", "--maa", "chosen-maa"])
                    self.assertEqual(run.call_args.args[0], ["chosen-maa", "dir", "config", "--batch"])
                    return code, json.loads(stdout.getvalue())
                self.assertEqual(invoke()[0], 2)
                shutil.copyfile(assets / (check.TASK + ".toml"), root / "tasks" / (check.TASK + ".toml"))
                shutil.copyfile(assets / "tasks.json", root / "resource/tasks/tasks.json")
                code, result = invoke()
                self.assertEqual(code, 0)
                self.assertEqual(result["status"], "installed")
                self.assertFalse(result["profile_checked"])
                self.assertFalse(result["game_state_checked"])
                (root / "resource/tasks/tasks.json").write_text('{}', encoding="utf-8")
                self.assertEqual(invoke()[0], 2)
                self.assertEqual(run.call_count, 3)  # No MAA run or resource writes by preflight.


if __name__ == "__main__":
    unittest.main()
