"""离线检查组件的产物归属；所有游戏子进程均替换为本地假执行。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maa-daily/scripts"))
from artifacts import ArtifactRun, ENV_NAME
import reward_check


class RewardArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        (self.config / "tasks").mkdir(parents=True)
        self.formal = self.config / "tasks/formal.toml"
        self.formal.write_text("tasks = []", encoding="utf-8")
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV_NAME, None)
        self.calls = []

    def index(self):
        return json.loads((self.config / "maa-daily-artifacts/index.json").read_text(encoding="utf-8"))

    def fake_run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.assertIn(ENV_NAME, kwargs["env"])
        if "--report-file" in command:
            report = Path(command[command.index("--report-file") + 1])
            report.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def invoke(self, *, parent=None, run=None, recheck=False):
        with patch.object(reward_check, "check_install", return_value=self.config), \
                patch.object(reward_check.subprocess, "run", side_effect=run or self.fake_run), \
                patch.object(reward_check, "read_navigation", return_value={"status": "recheck_required" if recheck else "verified"}), \
                patch.object(reward_check, "read_page_recheck", return_value={"status": "verified"}), \
                patch.object(reward_check, "evaluate", return_value={"status": "evaluated", "reminder_required": False}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return reward_check.scan_once("fake-maa", "profile", parent.path / "rewards" if parent else None, parent=parent)

    def test_standalone_default_cleanup_and_preserved_evidence(self):
        result, output = self.invoke(recheck=True)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(len(list((self.config / "tasks").iterdir())), 1)
        self.assertTrue(self.formal.is_file())
        self.assertTrue((output / "navigation-task.json").is_file())
        self.assertTrue((output / "page-recheck-task.json").is_file())
        self.assertTrue((output / "result.json").is_file())
        record, = self.index()["runs"]
        self.assertEqual(record["state"], "finished")
        self.assertEqual(record["files"], [])
        self.assertEqual(len(self.calls), 6)

    def test_nested_scan_has_one_owner_and_does_not_finish_parent(self):
        parent = ArtifactRun.begin(self.config, None, "daily", prefix="daily-")
        result, output = self.invoke(parent=parent)
        self.assertTrue(output.is_relative_to(parent.path))
        self.assertEqual(result["status"], "evaluated")
        record, = self.index()["runs"]
        self.assertEqual(record["state"], "active")
        self.assertTrue(all(kwargs["env"][ENV_NAME] == str(parent.path) for _, kwargs in self.calls))
        parent.finish("completed")
        self.assertEqual(self.index()["runs"][0]["state"], "finished")

    def test_finished_failed_process_also_releases_temporary_task(self):
        def fail(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)
        result, output = self.invoke(run=fail)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue((output / "result.json").is_file())
        self.assertEqual(list((self.config / "tasks").iterdir()), [self.formal])
        self.assertEqual(self.index()["runs"][0]["state"], "finished")

    def test_changed_task_is_not_deleted(self):
        def altered(command, **kwargs):
            for path in (self.config / "tasks").glob("maa-reward-nav-*.json"):
                path.write_text("user edit", encoding="utf-8")
            return self.fake_run(command, **kwargs)
        result, _ = self.invoke(run=altered)
        self.assertEqual(result["status"], "evaluated")  # cleanup does not rewrite business result
        task, = (self.config / "tasks").glob("maa-reward-nav-*.json")
        self.assertEqual(task.read_text(encoding="utf-8"), "user edit")
        self.assertEqual(len(self.index()["runs"][0]["files"]), 1)

    def test_interrupt_protects_parent_and_temporary_task(self):
        def interrupted(command, **kwargs):
            raise KeyboardInterrupt()
        parent = ArtifactRun.begin(self.config, None, "daily", prefix="daily-")
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(parent=parent, run=interrupted)
        self.assertEqual(self.index()["runs"][0]["state"], "uncertain")
        self.assertEqual(len(list((self.config / "tasks").glob("maa-reward-nav-*.json"))), 1)
        parent.finish("failed")
        self.assertEqual(self.index()["runs"][0]["state"], "uncertain")

    def test_reward_readonly_preflight_with_safe_path_does_not_create_store(self):
        command = [sys.executable, "-X", "utf8", "-P", "-B", str(ROOT / "maa-daily/scripts/reward_check.py"),
                   "--layout", reward_check.LAYOUT, "preflight", "--maa", str(self.root / "missing-maa.exe")]
        value = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(value.returncode, 2)
        self.assertEqual(json.loads(value.stdout)["status"], "not_ready")
        self.assertNotIn("ModuleNotFoundError", value.stderr)
        self.assertFalse((self.config / "maa-daily-artifacts").exists())


if __name__ == "__main__":
    unittest.main()
