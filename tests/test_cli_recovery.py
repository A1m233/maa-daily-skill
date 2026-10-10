"""离线故障注入；不连接 MAA、模拟器或网络。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import run_with_evidence as runner
from artifacts import ArtifactRun, ENV_NAME

EOF = "Updating hot update files...\nError: Network error\nCaused by:\n io: unexpected end of file\n"
COMPLETE = ('Assistant::append_callback | TaskChainStart {"taskchain":"Custom","taskid":1}\n'
            'Assistant::append_callback | TaskChainCompleted {"taskchain":"Custom","taskid":1}\n')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop(ENV_NAME, None)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = self.root / "asst.log"
        self.log.write_text("prior\n", encoding="utf-8")
        self.owner = ArtifactRun.begin(self.root / "config", None, "daily")
        self.addCleanup(self.owner.finish)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(runner, "_discover_core_log", return_value=self.log))

    def success(self, command, env, timeout):
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(COMPLETE)
        return 0, "completed", False

    def test_transient_eof_retries_identical_command_and_environment_once(self):
        calls = []
        def execute(command, env, timeout):
            calls.append((list(command), dict(env)))
            return (1, EOF, False) if len(calls) == 1 else self.success(command, env, timeout)
        with patch.object(runner, "_execute", side_effect=execute):
            report = runner.run_bounded(["fake-maa", "run", "task"], self.owner)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(report["wrapper_exit_code"], 0)
        self.assertEqual(report["attempts"][0]["child_exit_code"], 1)
        self.assertTrue(report["recovery"]["recovered"])
        self.assertFalse(report["attempts"][0]["failure"]["game_operated"])
        self.assertEqual(len(list(self.owner.path.glob("cli-attempt-*.json"))), 2)

    def test_cli_phase_logging_is_child_only_and_preserves_explicit_choice(self):
        for configured in (None, "warn", "debug"):
            with self.subTest(configured=configured), patch.dict(os.environ):
                os.environ.pop("MAA_LOG", None)
                if configured is not None:
                    os.environ["MAA_LOG"] = configured
                before = dict(os.environ)
                with patch.object(runner, "_execute", side_effect=self.success) as run:
                    runner.run_bounded(["fake-maa", "run", "x"], self.owner)
                self.assertEqual(run.call_args.args[1]["MAA_LOG"], configured or "info")
                self.assertEqual(dict(os.environ), before)

    def test_retry_budget_is_shared_between_dry_run_and_other_child_components(self):
        with patch.object(runner, "_execute", side_effect=[(1, EOF, False), (0, "resolved", False)]) as run:
            text = runner.run_dry(["fake-maa", "run", "x", "--dry-run"], self.owner, self.owner.path / "dry.json")
        self.assertEqual(text, "resolved")
        self.assertEqual(run.call_count, 2)
        child = ArtifactRun.begin(self.root / "config", None, "drain", parent=self.owner)
        with patch.object(runner, "_execute", return_value=(1, EOF, False)) as run:
            report = runner.run_bounded(["fake-maa", "run", "different-name"], child)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report["wrapper_exit_code"], 1)
        self.assertTrue(report["recovery"]["retry_budget_used"])

    def test_repeated_eof_stops_at_two_and_retains_both_failures(self):
        with patch.object(runner, "_execute", return_value=(1, EOF, False)) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                runner.run_dry(["fake-maa", "run", "x", "--dry-run"], self.owner, self.owner.path / "dry.json")
        report = json.loads((self.owner.path / "dry.json").read_text(encoding="utf-8"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual([a["child_exit_code"] for a in report["attempts"]], [1, 1])
        self.assertFalse(report["recovery"]["recovered"])

    def test_core_activity_missing_rotated_and_unrecognized_failures_never_retry(self):
        for mode in ("business", "replace", "missing", "no-marker", "loaded", "truncated", "killed", "timeout"):
            with self.subTest(mode=mode):
                self.log.write_text("prior\n", encoding="utf-8")
                def execute(command, env, timeout):
                    if mode == "business":
                        self.success(command, env, timeout)
                    elif mode == "replace":
                        self.log.write_text("other\n", encoding="utf-8")
                    elif mode == "missing":
                        self.log.unlink()
                    elif mode == "timeout":
                        raise subprocess.TimeoutExpired(command, 1)
                    return (130 if mode == "killed" else 1,
                            "Error: bad config" if mode == "no-marker" else
                            EOF + "Loading MaaCore" if mode == "loaded" else EOF, mode == "truncated")
                with patch.object(runner, "_execute", side_effect=execute) as run:
                    value = runner.run_bounded(["fake-maa", "run", "x"], self.owner)
                self.assertEqual(run.call_count, 1)
                self.assertFalse(value["failure"]["retry_eligible"])
                self.assertNotEqual(value["wrapper_exit_code"], 0)

    def test_dry_parse_error_records_evidence_without_spending_retry_budget(self):
        with patch.object(runner, "_execute", return_value=(1, "invalid TOML", False)) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                runner.run_dry(["fake-maa", "run", "x", "--dry-run"], self.owner, self.owner.path / "bad.json")
        report = json.loads((self.owner.path / "bad.json").read_text(encoding="utf-8"))
        self.assertEqual(run.call_count, 1)
        self.assertIn("invalid TOML", report["cli"]["output"])
        self.assertFalse(report["recovery"]["retry_budget_used"])

    def test_real_child_stream_capture_and_output_limit(self):
        command = [sys.executable, "-c", "import sys; print('x'*300000); sys.exit(1)"]
        code, output, truncated = runner._execute(command, dict(os.environ), 10)
        self.assertEqual(code, 1)
        self.assertTrue(truncated)
        self.assertLessEqual(len(output.encode()), runner.CLI_OUTPUT_LIMIT)

    def test_real_child_eof_then_success_executes_business_only_once(self):
        script = self.root / "fake_cli.py"
        counter, business = self.root / "counter", self.root / "business"
        script.write_text(
            "from pathlib import Path\n"
            f"counter=Path({str(counter)!r})\n"
            "if not counter.exists():\n"
            "    counter.write_text('1')\n"
            f"    print({EOF!r})\n"
            "    raise SystemExit(1)\n"
            "counter.write_text('2')\n"
            f"Path({str(business)!r}).write_text('one execution')\n"
            f"with Path({str(self.log)!r}).open('a', encoding='utf-8') as handle:\n"
            f"    handle.write({COMPLETE!r})\n", encoding="utf-8")
        value = runner.run_bounded([sys.executable, str(script), "run"], self.owner, core_log=self.log)
        self.assertEqual(value["wrapper_exit_code"], 0)
        self.assertTrue(value["recovery"]["recovered"])
        self.assertEqual(counter.read_text(), "2")
        self.assertEqual(business.read_text(), "one execution")
        self.assertEqual(self.log.read_text().count("TaskChainStart"), 1)


if __name__ == "__main__":
    unittest.main()
