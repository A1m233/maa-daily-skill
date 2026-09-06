import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daily_checks", ROOT / "maa-daily/scripts/daily_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class DailyChecksTests(unittest.TestCase):
    def test_plan_boundaries_and_conservation(self):
        for maximum in (1, 6, 10):
            for cost in (6, 12, 30, 36):
                for sanity in range(1200):
                    result = checks.plan(sanity, cost, maximum)
                    count = result["available_runs"]
                    self.assertEqual(count * cost + result["estimated_remainder"], sanity)
                    fight = result["next_fight"]
                    if count == 0:
                        self.assertIsNone(fight)
                    else:
                        self.assertGreater(fight["series"], 0)
                        self.assertEqual(fight["times"] % fight["series"], 0)
                        self.assertLessEqual(fight["times"] * cost, sanity)
                        self.assertEqual((0, 0, 0), (fight["stone"], fight["medicine"], fight["medicine_expire_days"]))

    def test_exact_bulk_has_no_extra_probe(self):
        result = checks.plan(943, 12, 10)
        self.assertEqual(70, result["next_fight"]["times"])
        self.assertEqual(8, result["estimated_tail"])
        self.assertTrue(result["reobserve_after_fight"])
        self.assertEqual(8, checks.plan(103, 12, 10)["next_fight"]["series"])

    def test_invalid_inputs(self):
        for args in ((-1, 12, 10), (10, 0, 10), (10, 12, 0), (10, 12, 11)):
            with self.assertRaises(ValueError):
                checks.plan(*args)

    def test_prepare_preserves_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "resource/tasks/tasks.json"
            target.parent.mkdir(parents=True)
            original = b'{"UserNode":{"action":"DoNothing"}}'
            target.write_bytes(original)
            checks.prepare(root)
            self.assertIn("UserNode", json.loads(target.read_text(encoding="utf-8")))
            self.assertEqual(original, next(target.parent.glob("*.bak-*")).read_bytes())
            self.assertEqual([], checks.prepare(root))
            self.assertFalse((root / "profiles").exists())

    def test_conflict_prevents_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "tasks/maa-daily-check-sanity.toml"
            task.parent.mkdir()
            task.write_text("user task", encoding="utf-8")
            with self.assertRaises(ValueError):
                checks.prepare(root)
            self.assertFalse((root / "resource").exists())
            self.assertEqual("user task", task.read_text(encoding="utf-8"))

    def test_alternate_task_format_is_not_shadowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks").mkdir()
            (root / "tasks/maa-daily-reward-scan.json").write_text('{}', encoding="utf-8")
            with self.assertRaises(ValueError):
                checks.prepare(root)
            self.assertFalse((root / "resource").exists())

    def test_probe_resources_do_not_start_or_claim(self):
        resources = json.loads((ROOT / "maa-daily/assets/daily-checks/tasks.json").read_text(encoding="utf-8"))
        for name, node in resources.items():
            effective = node
            while effective.get("baseTask") in resources:
                parent = resources[effective["baseTask"]]
                effective = {**parent, **{k: v for k, v in effective.items() if k != "baseTask"}}
            self.assertIn(effective["action"], ("DoNothing", "ClickSelf", "Swipe"))
            if name == "MaaDailyCheck@RewardScan":
                self.assertEqual(["日常任务"], effective["text"])
            elif effective["action"] == "ClickSelf":
                self.assertEqual("DailyTask", node["baseTask"])
            for child in node["next"]:
                self.assertIn(child, resources)
        self.assertEqual([], resources["MaaDailyCheck@StagePage"]["sub"])
        self.assertEqual([], resources["MaaDailyCheck@StagePage"]["exceededNext"])

    def test_bounded_inspection_never_infers_rewards_from_ocr(self):
        def event(kind, payload):
            if kind == "SubTaskCompleted":
                payload["details"]["task"] = payload["details"]["task"].split("@")[-1]
                payload["first"] = ["MaaDailyCheck@StagePage"]
                payload["taskchain"] = "Custom"
            return "Assistant::append_callback | " + kind + " " + json.dumps(payload) + "\n"
        data = ("OcrDetect MaaDailyCheck@Sanity [{ text: 103/205, rect: [ 1, 2, 3, 4 ], score: 0.99 }]\n"
                + "OcrDetect MaaDailyCheck@SanityConfirm [{ text: 103/205, rect: [ 1, 2, 3, 4 ], score: 0.99 }]\n"
                + event("SubTaskCompleted", {"details": {"task": "MaaDailyCheck@StagePage"}})
                + event("SubTaskCompleted", {"details": {"task": "MaaDailyCheck@Sanity"}})
                + event("SubTaskCompleted", {"details": {"task": "MaaDailyCheck@SanityConfirm"}})
                + event("TaskChainCompleted", {"taskchain": "Custom"})).encode()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "asst.log"
            log.write_bytes(b"old\n" + data + b"later\n")
            report = {"wrapper_exit_code": 0, "evidence": {
                "state": "bounded", "log_file": str(log), "before_size": 4,
                "after_size": 4 + len(data), "interval_sha256": hashlib.sha256(data).hexdigest()}}
            result = checks.inspect_report(report)
            self.assertEqual(103, result["sanity"]["current"])
            self.assertEqual("unknown", result["daily_orundum"])
            self.assertTrue(result["reminder_required"])
            report["wrapper_exit_code"] = 75
            self.assertIsNone(checks.inspect_report(report)["sanity"])
            report["wrapper_exit_code"] = 0
            log.write_bytes(b"x" * len(log.read_bytes()))
            self.assertIsNone(checks.inspect_report(report)["sanity"])

    def test_plan_writes_native_task_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "next.json"
            args = ["plan", "--sanity", "103", "--cost", "12", "--maximum", "10",
                    "--stage", "1-7", "--task-file", str(target)]
            self.assertEqual(0, checks.main(args))
            params = json.loads(target.read_text(encoding="utf-8"))["tasks"][0]["params"]
            self.assertEqual(8, params["times"])
            self.assertEqual(2, checks.main(args))
            zero = Path(directory) / "zero.json"
            args[2] = "0"
            args[-1] = str(zero)
            self.assertEqual(0, checks.main(args))
            self.assertFalse(zero.exists())


if __name__ == "__main__":
    unittest.main()
