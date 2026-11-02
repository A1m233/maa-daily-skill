"""Recovery is established by scoped navigation evidence, not error names."""
import tempfile
import unittest

import test_drain_sanity as fixtures
from test_drain_sanity import event, drain, probe_body
from medicine_policy import inspect_recovery


class NavigationRecoveryTests(unittest.TestCase):
    def report(self, directory, middle, suffix):
        return fixtures.DrainTests().report(directory, event("TaskChainStart") + middle + suffix +
                                   event("TaskChainCompleted"))

    def navigation(self):
        return (event("SubTaskStart", subtask="StageNavigationTask") +
                event("SubTaskError", subtask="ProcessTask", first=["UnspecifiedNavigationStep"]) +
                event("SubTaskCompleted", subtask="StageNavigationTask"))

    def battle(self):
        return (event("SubTaskExtraInfo", what="FightTimes", details={"times_finished": 2}) +
                event("SubTaskExtraInfo", what="StageDrops", details={"stage": {"stageCode": "AP-5"}}))

    def test_navigation_still_requires_stop_and_independent_probe(self):
        stop = event("SubTaskStart", details={"task": "FightBegin", "action": "Stop"})
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, self.navigation(), stop)
            self.assertEqual(drain.read_navigation(report, "AP-5"), "prepared")
            with self.assertRaises(ValueError):
                drain.read_probe(report, "AP-5")
            with self.assertRaises(ValueError):
                drain.read_navigation(self.report(directory, self.navigation(), ""), "AP-5")
            with self.assertRaises(ValueError):
                drain.callbacks(report)  # Read-only OCR callers stay strict.
            bad_probe = probe_body().replace(event("TaskChainCompleted", taskchain="Custom"),
                event("SubTaskError", taskchain="Custom") + event("TaskChainCompleted", taskchain="Custom"))
            with self.assertRaises(ValueError):
                drain.read_probe(fixtures.DrainTests().report(directory, bad_probe), "AP-5")

    def test_fight_and_medicine_keep_business_evidence_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(directory, self.navigation(), self.battle())
            drain.check_fight(report, "AP-5", 2)
            self.assertEqual(inspect_recovery(report, "AP-5", 1, 100)["completed_runs"], 2)
            for stage, count in (("LS-6", 2), ("AP-5", 3)):
                with self.assertRaises(ValueError):
                    drain.check_fight(report, stage, count)
            for suffix in ("", self.battle() + event("SubTaskError"),
                           self.battle() + event("SubTaskStart", details={"task": "StoneConfirm", "action": "ClickSelf"}),
                           self.battle() + event("SubTaskExtraInfo", what="UseMedicine",
                               details={"is_expiring": False, "count": 1, "medicines": []})):
                with self.assertRaises(ValueError):
                    inspect_recovery(self.report(directory, self.navigation(), suffix), "AP-5", 1, 100)

    def test_recovery_requires_matching_scope_before_any_resource_action(self):
        start = event("SubTaskStart", subtask="StageNavigationTask")
        done = event("SubTaskCompleted", subtask="StageNavigationTask")
        error = event("SubTaskError")
        cases = [error + done, error + start + done, start + done + error,
                 start + event("SubTaskError", taskid=2) + done,
                 start + error + done + done, start + error + event("TaskChainError") + done]
        for action in (event("SubTaskExtraInfo", what="UseMedicine"), self.battle(),
                       event("SubTaskStart", details={"task": "StartButton2", "action": "ClickSelf"})):
            cases.append(start + error + action + done)
        with tempfile.TemporaryDirectory() as directory:
            for middle in cases:
                with self.subTest(middle=middle), self.assertRaises(ValueError):
                    drain.check_fight(self.report(directory, middle, self.battle()), "AP-5", 2)


if __name__ == "__main__":
    unittest.main()
