import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
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
    def test_measured_five_state_projection(self):
        data = json.loads((ROOT / "tests/fixtures/reward-tier-centers.json").read_text(encoding="utf-8"))
        for sample in data["samples"]:
            with self.subTest(claimed=sample["claimed"]):
                pages = {"RewardTopA": items(sample["top"]), "RewardTopB": items(sample["top"]),
                         "RewardBottomA": items(sample["bottom"]), "RewardBottomB": items(sample["bottom_repeat"])}
                result = check.classify(pages)
                self.assertEqual(sample["claimed"], result["claimed_tiers"])
                self.assertEqual(10-sample["claimed"], result["unclaimed_tiers"])

    def test_synthetic_thresholds_and_deduplication(self):
        for count in range(1, 11):
            result = check.classify(pages_for(count))
            self.assertEqual(count, result["claimed_tiers"])
            self.assertEqual(count < 9, result["reminder_required"])
            self.assertEqual("claimed" if count >= 7 else "not_claimed", result["daily_orundum"])
            self.assertEqual("claimed" if count >= 9 else "not_claimed", result["daily_annihilation_ticket"])
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
                pages["RewardBottomB"][0]["score"] = 0.6
            elif mutation == "hole":
                pages["RewardBottomA"].pop(-2)
                pages["RewardBottomB"].pop(-2)
            elif mutation == "overlap":
                pages["RewardTopA"].pop()
                pages["RewardTopB"].pop()
            else:
                pages["RewardTopA"][0]["text"] = "已完戎"
            with self.subTest(mutation=mutation):
                self.assertTrue(check.classify(pages)["reminder_required"])
                self.assertIsNone(check.classify(pages)["claimed_tiers"])

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


if __name__ == "__main__":
    unittest.main()
