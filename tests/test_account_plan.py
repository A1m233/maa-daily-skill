import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import account_plan
import daily_run as daily

ASSETS = Path(__file__).resolve().parents[1] / "maa-daily/assets"


class AccountPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "accounts.toml"
        for name, stage, mode, maximum in (("a", "stage-a", "off", 6),
                                            ("b", "stage-b", "use_and_drain", 10)):
            folder = self.root / name
            folder.mkdir()
            (folder / "medicine.toml").write_text(
                f'[expiring_medicine]\nmode = "{mode}"\nmedicine_expire_days = 1\n', encoding="utf-8")
            (folder / "daily.toml").write_text(
                f'version = 1\npre_tasks = ["shared-biz"]\nstage_task = "{stage}"\n'
                'award_task = "shared-award"\nmedicine_policy = "medicine.toml"\n'
                f'priority_goal = "weekly_cap"\nmaximum = {maximum}\nmax_runs = {maximum * 2}\n',
                encoding="utf-8")
        self.data = {"version": 1, "profile": "test-device", "client": "Official",
                     "default_config": "a/daily.toml", "accounts": [
                         {"alias": "A", "account_name": "本机标识甲"},
                         {"alias": "B", "account_name": "本机标识乙", "config": "b/daily.toml"}]}

    def load(self, data=None):
        data = data or self.data
        lines = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in data.items() if k != "accounts"]
        if not data["accounts"]:
            lines.append("accounts = []")
        for entry in data["accounts"]:
            lines.append("[[accounts]]")
            lines.extend(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in entry.items())
        self.path.write_text("\n".join(lines), encoding="utf-8")
        return account_plan.load_plan(self.path)

    def test_default_override_order_and_relative_policy_paths(self):
        plan = self.load()
        self.assertEqual([p["alias"] for p in plan["accounts"]], ["A", "B"])
        self.assertEqual(plan["profile"], "test-device")
        for entry, name in zip(plan["accounts"], ("a", "b")):
            self.assertEqual(Path(entry["config"]), self.root / name / "daily.toml")
            config = daily.load_config(Path(entry["config"]))
            self.assertEqual(Path(config["medicine_policy"]), self.root / name / "medicine.toml")

    def test_shared_default_and_explicit_configs_without_default(self):
        shared = copy.deepcopy(self.data)
        del shared["accounts"][1]["config"]
        plan = self.load(shared)
        self.assertEqual(plan["accounts"][0]["config"], plan["accounts"][1]["config"])
        explicit = copy.deepcopy(self.data)
        del explicit["default_config"]
        explicit["accounts"][0]["config"] = str(self.root / "a/daily.toml")
        self.assertEqual(self.load(explicit), self.load())

    def test_rejects_invalid_mapping_without_partial_plan(self):
        mutations = [
            lambda d: d.update(version=True),
            lambda d: d.update(accounts=[]),
            lambda d: d.update(profile=""),
            lambda d: d.update(unrecognized=True),
            lambda d: d["accounts"][1].update(alias="a"),
            lambda d: d["accounts"][1].update(account_name="本机标识甲"),
            lambda d: d["accounts"][1].update(account_name="标识甲"),
            lambda d: d["accounts"][1].update(account_name=""),
            lambda d: d["accounts"][1].update(account_name="bad\nname"),
            lambda d: d["accounts"][1].update(profile="other-device"),
            lambda d: d["accounts"][1].update(config="missing.toml"),
            lambda d: d["accounts"][1].update(config=""),
            lambda d: d.pop("default_config"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = copy.deepcopy(self.data)
                mutate(data)
                with self.assertRaises(ValueError):
                    self.load(data)

    def test_invalid_recovery_policy_is_not_accepted(self):
        (self.root / "b/medicine.toml").write_text('[expiring_medicine]\nmode="typo"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def write_template(self):
        text = (ASSETS / "accounts.example.toml").read_text(encoding="utf-8")
        text = text.replace('"daily-execution.toml"', '"a/daily.toml"')
        text = text.replace('"daily-execution-b.toml"', '"b/daily.toml"')
        self.path.write_text(text, encoding="utf-8")

    def cli(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = account_plan.main(["--file", str(self.path), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_template_cli_is_readonly_and_redacts_unselected_logins(self):
        self.write_template()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("must not spawn MAA")):
            code, out, err = self.cli()
            self.assertEqual((code, err), (0, ""))
            plan = json.loads(out)
            self.assertFalse(plan["identity_verified"])
            self.assertFalse(plan["tasks_validated"])
            self.assertTrue(all("account_name" not in e for e in plan["accounts"]))
            code, out, err = self.cli("--account", "B")
            self.assertEqual(code, 0)
            self.assertEqual([e["alias"] for e in json.loads(out)["accounts"]], ["B"])
            self.assertIn("本机唯一登录标识乙", out)
            self.assertNotIn("本机唯一登录标识甲", out)
            for alias in ("unknown", "b"):
                code, out, err = self.cli("--account", alias)
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(json.loads(err)["reason"], "account_not_found")
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_selecting_a_still_rejects_broken_b_and_does_not_disclose_login(self):
        self.write_template()
        (self.root / "b/daily.toml").unlink()
        code, out, err = self.cli("--account", "A")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(json.loads(err)["reason"], "account_2: invalid_config")
        self.assertNotIn("本机唯一登录标识", err)
        self.path.write_text('account_name = "private-invalid', encoding="utf-8")
        code, out, err = self.cli()
        self.assertEqual((code, out), (2, ""))
        self.assertNotIn("private-invalid", err)

    def test_two_daily_instances_keep_stage_policy_budget_and_evidence_separate(self):
        entries = self.load()["accounts"]
        tasks = {"shared-biz": [{"type": "Recruit", "params": {}}],
                 "shared-award": [{"type": "Award", "params": {"award": True}}],
                 "stage-a": [{"type": "Fight", "params": {"stage": "LS-6", "times": 0}}],
                 "stage-b": [{"type": "Fight", "params": {"stage": "AP-5", "times": 0}}]}
        instances = []
        with patch.object(daily, "check_install"), patch.object(daily, "medicine_preflight"), \
             patch.object(daily.Daily, "resolve", side_effect=lambda n: copy.deepcopy(tasks[n])):
            for entry in entries:
                output = self.root / ("evidence-" + entry["alias"])
                output.mkdir()
                runtime = Mock(output=output, stage_config=output / "isolated-config")
                runtime.artifacts.environment.return_value = {}
                config = daily.load_config(Path(entry["config"]))
                with patch.object(daily, "Runtime", return_value=runtime):
                    ops = daily.Daily(config, "unused-maa", "test-device", entry["alias"], output)
                instances.append(ops)
                result = output / "sanity/drain-test/result.json"
                result.parent.mkdir(parents=True)
                result.write_text(json.dumps({"status": "completed", "stage": ops.stage,
                                              "remaining_sanity": 0}), encoding="utf-8")
                with patch.object(daily.subprocess, "run", return_value=Mock(returncode=0)) as run:
                    ops.drain()
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("--stage") + 1], ops.stage)
                self.assertEqual(command[command.index("--max-runs") + 1], str(config["max_runs"]))
                self.assertEqual(command[command.index("--policy") + 1], str(ops.policy))
                self.assertEqual(run.call_args.kwargs["env"]["MAA_CONFIG_DIR"], str(runtime.stage_config))
                snapshot = json.loads((output / "plan.json").read_text(encoding="utf-8"))
                self.assertEqual(snapshot["account"], entry["alias"])
            a, b = instances
            self.assertEqual((a.stage, b.stage), ("LS-6", "AP-5"))
            self.assertEqual((a.config["maximum"], b.config["maximum"]), (6, 10))
            self.assertEqual(a.before, b.before)
            self.assertEqual(a.after, b.after)
            self.assertEqual(tomllib.loads(a.policy.read_text(encoding="utf-8"))["expiring_medicine"]["mode"], "off")
            self.assertEqual(tomllib.loads(b.policy.read_text(encoding="utf-8"))["expiring_medicine"]["mode"], "use_and_drain")
            self.assertNotEqual(a.policy, b.policy)


if __name__ == "__main__":
    unittest.main()
