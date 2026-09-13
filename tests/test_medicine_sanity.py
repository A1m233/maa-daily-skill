import copy
import contextlib
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import medicine_sanity as med
from medicine_policy import inspect_recovery


def event(kind, **data):
    return "Assistant::append_callback | " + kind + " " + json.dumps(data) + "\n"


def report(directory, body):
    data = body.encode()
    path = Path(directory) / "asst.log"
    path.write_bytes(data)
    return {"child_exit_code": 0, "wrapper_exit_code": 0, "evidence": {
        "state": "bounded", "before_size": 0, "after_size": len(data),
        "log_file": str(path), "interval_sha256": hashlib.sha256(data).hexdigest()}}


def scan_body(text="7小时", score=.999, x=850, mutate=None):
    body = event("TaskChainStart", taskchain="Custom", taskid=1)
    for name in ("Dialog", "ExpiryA", "ExpiryB"):
        body += event("SubTaskCompleted", taskchain="Custom", taskid=1, first=[med.MP + "Dialog"],
                      details={"task": name, "action": "DoNothing"})
        if name.startswith("Expiry"):
            actual = mutate(name, text) if mutate else text
            body += (f"PipelineAnalyzer::analyze | OcrDetect {med.MP}{name} "
                     f"[{{ text: {actual}, rect: [ {x}, 346, 90, 24 ], score: {score} }}]\n")
    return body + event("TaskChainCompleted", taskchain="Custom", taskid=1)


class MedicineTests(unittest.TestCase):
    def test_native_day_semantics_not_calendar_days(self):
        for text, expected in (("7小时", 1), ("C7小时", 1), ("59分钟", 1), ("7天", 8), ("1天", 2),
                               ("使用药剂", None), ("", None)):
            self.assertEqual(med.native_expire_days(text), expected)

    def test_positive_expiry_requires_repeated_qualified_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(med.read_scan(report(directory, scan_body()), 1, "Dialog")["status"], "detected")
            for body in (scan_body(score=.7), scan_body(x=1), scan_body(text="7天"),
                         scan_body(mutate=lambda n, t: "7天" if n.endswith("B") else t)):
                value = med.read_scan(report(directory, body), 1, "Dialog")
                self.assertEqual(value["status"], "unknown")
                self.assertTrue(value["reminder_required"])
                self.assertFalse(value["inventory_complete"])

    def test_scan_rejects_extra_actions_and_changed_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            for body in (scan_body().replace('"DoNothing"', '"ClickSelf"'), scan_body() + scan_body()):
                with self.assertRaises(ValueError):
                    med.read_scan(report(directory, body), 1, "Dialog")
            value = report(directory, scan_body())
            Path(value["evidence"]["log_file"]).write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                med.read_scan(value, 1, "Dialog")

    def test_no_recovery_callbacks_do_not_prove_zero(self):
        body = event("TaskChainStart", taskchain="Fight", taskid=1) + event("TaskChainCompleted", taskchain="Fight", taskid=1)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            inspect_recovery(report(directory, body), "AP-5", 1, 1)

    def test_zero_fights_with_valid_expiring_medicine_is_a_distinct_result(self):
        def body(use=1, days=1, expiring=True, count=1):
            values = {"taskchain": "Fight", "taskid": 1}
            return (event("TaskChainStart", **values) +
                    event("SubTaskExtraInfo", **values, what="FightTimes", details={"times_finished": 0}) +
                    event("SubTaskExtraInfo", **values, what="UseMedicine", details={"count": count,
                        "is_expiring": expiring, "medicines": [{"use": use, "inventory": 4, "expire_days": days}]}) +
                    event("TaskChainCompleted", **values))
        with tempfile.TemporaryDirectory() as directory:
            value = inspect_recovery(report(directory, body()), "AP-5", 1, 1)
            self.assertEqual((value["completed_runs"], value["medicine_used"]), (0, 1))
            for options in ({"days": 2}, {"expiring": False}, {"use": 5}, {"count": 2}):
                with self.assertRaises(ValueError):
                    inspect_recovery(report(directory, body(**options)), "AP-5", 1, 1)

    def run_flow(self, readings, use=False, recovery=None, fail=False, max_runs=100, max_phases=5):
        from drain_sanity import run_daily
        sequence = iter(readings)
        calls, saved = [], []
        def fight(params):
            calls.append(("fight", params))
            if fail:
                raise ValueError("fight_failed")
        def recover(params):
            calls.append(("recover", params))
            if isinstance(recovery, Exception):
                raise recovery
            return recovery
        def check():
            calls.append(("check", None))
            return {"status": "detected", "reminder_required": True}
        result = run_daily(lambda: next(sequence), fight, recover, check,
            lambda r: saved.append(copy.deepcopy(r)),
            policy={"mode": "use_and_drain" if use else "off", "medicine_expire_days": 1},
            cost=30, maximum=10, max_runs=max_runs, max_phases=max_phases)
        return result, calls, saved

    def test_no_medicine_nine_runs_never_attempts_ten(self):
        result, calls, _ = self.run_flow([275, 5])
        self.assertEqual([c[0] for c in calls], ["fight", "check"])
        self.assertEqual(calls[0][1]["series"], 9)
        self.assertEqual(calls[0][1]["medicine_expire_days"], 0)
        self.assertEqual(result["status"], "completed_with_reminder")

    def test_zero_fights_but_medicine_then_direct_nine_tail(self):
        result, calls, saved = self.run_flow([5, 275, 5], use=True,
            recovery={"completed_runs": 0, "medicine_used": 3})
        self.assertEqual([c[0] for c in calls], ["recover", "fight", "check"])
        self.assertEqual((calls[0][1]["series"], calls[1][1]["series"]), (10, 9))
        self.assertEqual(calls[1][1]["medicine_expire_days"], 0)
        self.assertEqual((result["completed_runs"], result["medicine_used"]), (9, 3))
        self.assertEqual(result["medicine_goal"], "unknown")
        self.assertTrue(any(s["medicine_used"] == 3 and s["remaining_sanity"] is None for s in saved))

    def test_bulk_partial_then_tail_upgrade_repeats_without_medicine(self):
        result, calls, _ = self.run_flow([35, 275, 125, 5], use=True,
            recovery={"completed_runs": 20, "medicine_used": 6})
        self.assertEqual([c[0] for c in calls], ["recover", "fight", "fight", "check"])
        self.assertEqual([c[1]["series"] for c in calls[:-1]], [10, 9, 4])
        self.assertEqual(result["completed_runs"], 33)

    def test_upgrade_increase_recomputes_and_respects_budget(self):
        result, calls, _ = self.run_flow([35, 135, 15])
        self.assertEqual([c[1]["series"] for c in calls if c[0] == "fight"], [1, 4])
        self.assertEqual(result["remaining_sanity"], 15)
        result, calls, _ = self.run_flow([35, 135], max_phases=1)
        self.assertEqual(result["reason"], "phase_budget_exceeded")
        self.assertNotIn("check", [c[0] for c in calls])

    def test_native_no_progress_is_not_retried_or_proof_of_empty_stock(self):
        result, calls, _ = self.run_flow([5, 5], use=True,
            recovery={"completed_runs": 0, "medicine_used": 0})
        self.assertEqual([c[0] for c in calls], ["recover", "check"])
        self.assertEqual(result["medicine_goal"], "unknown")

    def test_native_cap_or_failure_never_falls_back(self):
        for native, reason, readings in [
            ({"completed_runs": 100, "medicine_used": 1}, "medicine_run_budget_reached", [5, 5]),
            (ValueError("native_failed"), "native_failed", [5]),
            ({"completed_runs": 9, "medicine_used": 1}, "invalid_native_bulk_result", [5]),
        ]:
            result, calls, _ = self.run_flow(readings, use=True, recovery=native)
            self.assertEqual(result["reason"], reason)
            self.assertEqual([c[0] for c in calls], ["recover"])

    def test_insufficient_budget_never_uses_medicine(self):
        result, calls, _ = self.run_flow([5], use=True, max_runs=9)
        self.assertEqual(result["reason"], "budget_below_full_medicine_batch")
        self.assertEqual(calls, [])

    def test_failed_tail_preserves_medicine_but_invalidates_sanity(self):
        result, calls, _ = self.run_flow([5, 275], use=True,
            recovery={"completed_runs": 0, "medicine_used": 3}, fail=True)
        self.assertEqual(result["reason"], "fight_failed")
        self.assertEqual(result["medicine_used"], 3)
        self.assertIsNone(result["remaining_sanity"])
        self.assertEqual([c[0] for c in calls], ["recover", "fight"])

    def test_check_failure_keeps_drain_result_and_unknown_reminder(self):
        from drain_sanity import run_daily
        result = run_daily(lambda: 5, lambda p: None, lambda p: None,
            lambda: (_ for _ in ()).throw(ValueError("scan_failed")), lambda r: None,
            policy={"mode": "off", "medicine_expire_days": 1},
            cost=30, maximum=10, max_phases=5, max_runs=100)
        self.assertEqual(result["status"], "completed_with_reminder")
        self.assertEqual(result["medicine_check"]["status"], "unknown")
        self.assertEqual(result["medicine_check"]["reason"], "scan_failed")

    def test_unified_cli_passes_native_policy_and_zero_medicine_tail(self):
        import drain_sanity as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.toml"
            policy.write_text('[expiring_medicine]\nmode="use_and_drain"\nmedicine_expire_days=1\n', encoding="utf-8")
            with patch.object(module, "Runtime") as factory, patch.object(module, "navigate"), \
                    patch.object(med, "preflight"), patch.object(med, "scan", return_value={
                        "status": "detected", "reminder_required": True, "end_at": "prepared"}), \
                    patch.object(module, "read_probe", side_effect=[5, 275, 5]), \
                    patch.object(module, "inspect_recovery", return_value={"completed_runs": 0, "medicine_used": 3}), \
                    patch.object(module, "check_fight") as checked, contextlib.redirect_stdout(io.StringIO()):
                runtime = factory.return_value
                runtime.output, runtime.reports = root, []
                code = module.main(["run", "--policy", str(policy), "--stage", "AP-5", "--profile", "test",
                                    "--output-dir", directory])
                self.assertEqual(code, 0)
                fights = [c.args[0][0]["params"] for c in runtime.run.call_args_list
                          if c.args[0][0]["type"] == "Fight"]
                self.assertEqual([(p["series"], p["times"], p["medicine_expire_days"]) for p in fights],
                                 [(10, 100, 1), (9, 9, 0)])
                self.assertTrue(all(p["medicine"] == p["stone"] == 0 for p in fights))
                checked.assert_called_once()
                result = json.loads((root / "result.json").read_text(encoding="utf-8"))
                self.assertEqual((result["completed_runs"], result["medicine_used"], result["end_at"]), (9, 3, "prepared"))

    def test_legacy_run_and_policy_rejected_before_runtime(self):
        import drain_sanity as module
        with patch.object(med, "Runtime") as runtime, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                med.main(["run"])
            runtime.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.toml"
            path.write_text('[expiring_medicine]\nmode="notify"\nmedicine_expire_days=1\n', encoding="utf-8")
            with patch.object(module, "Runtime") as runtime, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(["run", "--policy", str(path), "--stage", "AP-5",
                                             "--profile", "test", "--output-dir", directory]), 2)
                runtime.assert_not_called()

    def test_graph_has_no_confirmation_or_battle_and_all_edges_closed(self):
        path = Path(__file__).resolve().parents[1] / "maa-daily/assets/medicine-check/tasks.json"
        nodes = json.loads(path.read_text(encoding="utf-8"))
        for name, spec in nodes.items():
            self.assertNotIn("baseTask", spec)
            self.assertEqual(spec["maxTimes"], 1)
            self.assertTrue(all(n in nodes for n in spec["next"]))
            self.assertNotIn("sub", spec)
            self.assertNotIn("onErrorNext", spec)
            if name.endswith("@Open"):
                self.assertEqual(spec["action"], "ClickSelf")
            elif name.endswith("@Close"):
                self.assertEqual(spec["action"], "ClickRect")
            else:
                self.assertEqual(spec["action"], "DoNothing")


if __name__ == "__main__":
    unittest.main()
