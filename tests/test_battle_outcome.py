import tempfile
import unittest
import json
import contextlib
import io
from pathlib import Path
from unittest.mock import Mock

import test_drain_sanity as fixtures
from test_drain_sanity import event
import drain_sanity as drain
from medicine_policy import inspect_recovery
from daily_report import summarize
import battle_outcome


def settlement(stars=3, refund=False, stage="TL-7"):
    return event("SubTaskExtraInfo", what="StageDrops", details={
        "stage": {"stageCode": stage}, "stars": stars,
        "drops": [{"dropType": "SANITY_DROP", "itemId": "AP_GAMEPLAY", "quantity": 189}]
        if refund else [{"dropType": "NORMAL_DROP", "itemId": "example", "quantity": 2}]})


def body(settlements, failed=False, count=9):
    return (event("TaskChainStart")
            + (event("SubTaskCompleted", details={"task": "Fight@FightMissionFailed",
                "action": "ClickSelf", "algorithm": "OcrDetect"}) if failed else "")
            + settlements
            + event("SubTaskExtraInfo", what="FightTimes", details={"times_finished": count, "finished": True})
            + event("TaskChainCompleted"))


class BattleOutcomeTests(unittest.TestCase):
    def report(self, directory, data):
        return fixtures.DrainTests().report(directory, data)

    def test_failed_or_missing_settlements_rejected_in_both_paths(self):
        cases = [(settlement(0, True), False, "battle_failed"),
                 (settlement(3), True, "battle_failed"),
                 (settlement(3, True), False, "battle_failed"),
                 (settlement(0) + settlement(3), False, "battle_failed"),
                 ("", True, "battle_failed"),
                 ("", False, "battle_success_unverified")]
        cases += [(settlement(stars), False, "battle_success_unverified")
                  for stars in (None, True, "3", -1, 1, 2, 4)]
        with tempfile.TemporaryDirectory() as directory:
            for drops, failed, reason in cases:
                report = self.report(directory, body(drops, failed))
                with self.subTest(drops=drops, failed=failed):
                    with self.assertRaisesRegex(drain.FightUnverified, reason):
                        drain.check_fight(report, "TL-7", 9)
                    with self.assertRaisesRegex(ValueError, reason):
                        inspect_recovery(report, "TL-7", 1, 100)

    def test_success_still_requires_target_count_and_no_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body(settlement()))
            drain.check_fight(report, "TL-7", 9)
            self.assertEqual(inspect_recovery(report, "TL-7", 1, 100)["completed_runs"], 9)
            for stage, count in (("TL-8", 9), ("TL-7", 10)):
                with self.assertRaises(ValueError):
                    drain.check_fight(report, stage, count)
            # Merely listing a failure node as a candidate is not observing failure.
            candidate = event("SubTaskStart", details={"task": "PRTS1", "next": ["FightMissionFailed"]})
            report = self.report(directory, body(candidate + settlement()))
            drain.check_fight(report, "TL-7", 9)

    def test_refunded_batch_stops_before_second_batch_or_post_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body(settlement(0, True), failed=True))
            for mode in ("off", "use_and_drain"):
                observe, check = Mock(return_value=203), Mock()
                fight = Mock(side_effect=lambda p: drain.check_fight(report, "TL-7", p["times"]))
                def recover(p):
                    try:
                        return inspect_recovery(report, "TL-7", 1, p["times"])
                    except ValueError as error:
                        raise drain.FightUnverified(str(error), report) from error
                recovery = Mock(side_effect=recover)
                result = drain.run_daily(observe, fight, recovery, check, lambda r: None,
                    policy={"mode": mode, "medicine_expire_days": 1},
                    cost=21, maximum=10, max_phases=5, max_runs=100)
                self.assertEqual(result["reason"], "battle_failed")
                self.assertEqual(result["completed_runs"], 0)
                self.assertEqual(observe.call_count, 1)
                self.assertEqual(fight.call_count, int(mode == "off"))
                self.assertEqual(recovery.call_count, int(mode == "use_and_drain"))
                check.assert_not_called()
                self.assertIsNone(result["remaining_sanity"])
                obs = result["battle_observation"]
                self.assertEqual(obs["observed_completed_runs"], 9)
                self.assertEqual(obs["battle_outcome"]["status"], "failed")
                self.assertFalse(obs["usable_for_planning"])
                if mode == "use_and_drain":
                    self.assertIsNone(result["medicine_used"])
                brief = summarize({"steps": {"drain": result}})
                self.assertIn("drain_battle_failed", {n["code"] for n in brief["notices"]})
                self.assertTrue(any("不代表成功通关数" in f for f in brief["facts"]))

    def test_zero_fight_medicine_exhaustion_remains_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body("", count=0))
            self.assertEqual(inspect_recovery(report, "TL-7", 1, 100)["completed_runs"], 0)
            report = self.report(directory, body("", failed=True, count=0))
            with self.assertRaisesRegex(ValueError, "battle_failed"):
                inspect_recovery(report, "TL-7", 1, 100)

    def test_readonly_replay_cli_preserves_failed_report_and_checks_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, body(settlement(0, True)))
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            original = path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(battle_outcome.main(["--report", str(path)]), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["battle_outcome"]["status"], "failed")
            self.assertFalse(result["usable_for_planning"])
            self.assertEqual(path.read_bytes(), original)
            Path(report["evidence"]["log_file"]).write_text("changed")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(battle_outcome.main(["--report", str(path)]), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
