import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("infrast_check", ROOT / "maa-daily/scripts/infrast_check.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def event(kind, node=None, action="ClickSelf", text="可收获", taskid=3, chain="Infrast"):
    value = {"taskchain": chain, "taskid": taskid, "details": {
        "task": node, "action": action, "result": {"text": text, "score": 0.99}}}
    return MODULE.MARKER + kind + " " + json.dumps(value) + "\n"


class InfrastTests(unittest.TestCase):
    def inspect(self, body, mutate=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asst.log"
            data = body.encode()
            path.write_bytes(b"old unrelated data\n" + data + b"outside interval")
            report = {"wrapper_exit_code": 0, "child_exit_code": 0, "ended_at": "historical",
                      "evidence": {"state": "bounded", "before_size": 19,
                      "after_size": 19 + len(data), "log_file": str(path),
                      "interval_sha256": hashlib.sha256(data).hexdigest()}}
            if mutate:
                mutate(report)
            return MODULE.inspect_report(report)

    def chain(self, middle):
        return event("TaskChainStart") + middle + event("TaskChainCompleted")

    def test_collection_is_action_not_all_done(self):
        middle = "".join(event("SubTaskCompleted", "InfrastReward", text=t)
                         for t in ("可收获", "订单交付", "干员信赖", "员信赖"))
        result = self.inspect(self.chain(middle))
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["observed_at"], "historical")
        self.assertTrue(all(len(v) == 1 for v in result["chains"][0]["collection"].values()))
        self.assertEqual(result["all_work_completed"], "unknown")
        self.assertTrue(result["reminder_required"])

    def test_rotation_does_not_prove_collection(self):
        result = self.inspect(self.chain("".join(event("SubTaskCompleted", n) for n in (
            "InfrastNotification", "InfrastExitReward", "InfrastRotationClick", "InfrastTrimClick"))))
        chain = result["chains"][0]
        self.assertTrue(chain["possible_collection_skip"])
        self.assertEqual(chain["rotation_status"], "action_observed")
        self.assertEqual(chain["collection_status"], "unknown")

    def test_start_donothing_and_other_chain_are_not_clicks(self):
        body = event("SubTaskStart", "InfrastReward") + event("SubTaskCompleted", "InfrastReward", action="DoNothing")
        body += event("SubTaskCompleted", "InfrastReward", chain="Custom")
        self.assertEqual(self.inspect(self.chain(body))["chains"][0]["collection_status"], "unknown")

    def test_chain_ids_are_not_combined(self):
        body = self.chain(event("SubTaskCompleted", "InfrastReward"))
        body += event("TaskChainStart", taskid=4) + event("TaskChainCompleted", taskid=4)
        chains = self.inspect(body)["chains"]
        self.assertEqual(len(chains), 2)
        self.assertEqual(chains[1]["collection_status"], "unknown")

    def test_invalid_hash_and_failed_run(self):
        body = self.chain(event("SubTaskCompleted", "InfrastReward"))
        for mutate in (lambda r: r["evidence"].update(interval_sha256="bad"),
                       lambda r: r.update(child_exit_code=1),
                       lambda r: r["evidence"].update(state="rotated")):
            self.assertEqual(self.inspect(body, mutate)["status"], "unknown")
        self.assertEqual(self.inspect(body + "[ERR] failed\n")["status"], "unknown")

    def test_missing_lifecycle_and_bad_callback(self):
        self.assertEqual(self.inspect(event("TaskChainStart"))["status"], "unknown")
        self.assertEqual(self.inspect("")["status"], "unknown")
        with self.assertRaises(ValueError):
            self.inspect(event("SubTaskCompleted", "InfrastReward"))
        with self.assertRaises(ValueError):
            self.inspect(self.chain("") + MODULE.MARKER + "SubTaskCompleted {}bad\n")


if __name__ == "__main__":
    unittest.main()
