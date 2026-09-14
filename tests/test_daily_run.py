import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import daily_run as daily


class DailyTests(unittest.TestCase):
    def test_fixed_order_even_when_priority_completes(self):
        for status in ("completed", "not_scheduled"):
            ops = Mock(day="2026-09-14", account="example")
            for s in daily.STEPS:
                getattr(ops, s).return_value = {"status": "completed"}
            ops.priority.return_value = {"status": status}
            ops.drain.return_value = {"status": "completed", "completed_runs": 0, "remaining_sanity": 0}
            result = daily.execute_flow(ops, lambda r: None)
            self.assertEqual(result["status"], "completed")
            self.assertEqual([c[0] for c in ops.mock_calls if c[0] != "check_day"], list(daily.STEPS))

    def test_failure_does_not_execute_following_steps(self):
        for failed in daily.STEPS:
            ops = Mock(day="2026-09-14", account="example")
            for s in daily.STEPS:
                getattr(ops, s).return_value = {"status": "completed"}
            getattr(ops, failed).return_value = {"status": "unverified", "reason": "test_failure"}
            result = daily.execute_flow(ops, lambda r: None)
            self.assertEqual(result["status"], "incomplete")
            for s in daily.STEPS[daily.STEPS.index(failed)+1:]:
                getattr(ops, s).assert_not_called()
                self.assertIn(s, result["missing_steps"])

    def test_budget_stop_and_unknown_rewards_are_not_success(self):
        ops = Mock(day="2026-09-14", account="example")
        ops.pre.return_value = ops.priority.return_value = {"status": "completed"}
        ops.drain.return_value = {"status": "stopped", "reason": "run_budget_exceeded"}
        result = daily.execute_flow(ops, lambda r: None)
        ops.award.assert_not_called()
        self.assertEqual(result["steps"]["drain"]["reason"], "run_budget_exceeded")
        ops.drain.return_value = {"status": "completed_with_reminder", "reminder_required": True}
        ops.award.return_value = {"status": "completed"}
        ops.checks.return_value = {"status": "completed", "reminder_required": True,
                                   "rewards": {"daily_annihilation_ticket": "unknown"}}
        self.assertEqual(daily.execute_flow(ops, lambda r: None)["status"], "completed_with_reminder")

    def test_cross_day_stops_before_next_phase(self):
        ops = Mock(day="2026-09-14", account="example")
        ops.pre.return_value = {"status": "completed"}
        ops.check_day.side_effect = [None, ValueError("game_day_changed")]
        result = daily.execute_flow(ops, lambda r: None)
        self.assertEqual(result["reason"], "game_day_changed")
        ops.priority.assert_not_called()

    def test_resolved_native_json_without_reimplementing_weekdays(self):
        text = 'DEBUG Adding task [Fight] with params: {"stage":"AP-5","times":0}\nDEBUG Instance destroyed'
        self.assertEqual(daily.parse_resolved(text), [{"type": "Fight", "params": {"stage": "AP-5", "times": 0}}])
        self.assertEqual(daily.parse_resolved("Instance destroyed"), [])
        with self.assertRaises(ValueError):
            daily.parse_resolved("unexpected format\n[Fight] Unstarted")
        self.assertEqual(daily.parse_resolved(text.replace("[Fight]", "[任意名称]"), {"任意名称": "Fight"})[0]["type"], "Fight")
        with self.assertRaises(ValueError):
            daily.parse_resolved(text, {"Other": "Fight"})

    def test_no_old_fights_startup_or_live_selector_in_plan(self):
        selector = [{"type": "Fight", "params": {"stage": "AP-5", "times": 0}}]
        award = [{"type": "Award", "params": {"award": True, "mail": True}}]
        self.assertEqual(daily.validate_plan([], selector, award), "AP-5")
        for task in ({"type": "Fight", "params": {"stage": "AP-5", "times": 999}},
                     {"type": "StartUp", "params": {}}):
            with self.assertRaises(ValueError):
                daily.validate_plan([task], selector, award)
        bad = copy.deepcopy(selector)
        bad[0]["params"]["times"] = 1
        with self.assertRaises(ValueError):
            daily.validate_plan([], bad, award)

    def test_priority_cap_vs_configured_runs(self):
        events = [("SubTaskExtraInfo", {"taskchain": "Fight", "what": "FightTimes", "details": {"times_finished": 6}}),
                  ("SubTaskExtraInfo", {"taskchain": "Fight", "what": "StageDrops", "details": {"annihilation_weekly_process": [1800, 1800]}})]
        with patch.object(daily, "verified_events", return_value=events):
            self.assertEqual(daily.priority_result({}, {"times": 999}, "weekly_cap")["status"], "completed")
            with self.assertRaises(ValueError):
                daily.priority_result({}, {"times": 999}, "configured_runs")
            events[-1][1]["details"]["annihilation_weekly_process"] = [1750, 1800]
            with self.assertRaises(ValueError):
                daily.priority_result({}, {"times": 999}, "weekly_cap")

    def test_pre_diagnostics_surface_as_reminders(self):
        ops = object.__new__(daily.Daily)
        ops.before = [{"type": "Recruit", "params": {}}]
        ops.pre_reports, ops.priority_reports = [], []
        ops.output = Path("local-evidence")
        ops.check_day = Mock()
        ops.native = Mock(return_value={"evidence": {"subtask_error_lines": [12], "internal_error_lines": [30]}})
        result = ops.pre()
        self.assertEqual(result["status"], "completed_with_reminder")
        self.assertEqual(result["tasks"][0]["business_result"], "not_evaluated")
        self.assertEqual(result["tasks"][0]["subtask_error_lines"], [12])


if __name__ == "__main__":
    unittest.main()
