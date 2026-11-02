import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import resource_templates as binding
import daily_checks


class ResourceTemplatesTests(unittest.TestCase):
    def fixture(self, directory):
        config, official = Path(directory) / "config", Path(directory) / "official"
        config.mkdir()
        daily_checks.prepare(config)
        target = config / "resource/tasks/tasks.json"
        nodes = binding.read(target)
        nodes.update(binding.read(binding.ASSETS / binding.SOURCES[1]))
        nodes["UserNode"] = {"action": "DoNothing"}
        binding.atomic(target, nodes)
        (official / "tasks").mkdir(parents=True)
        (official / "template").mkdir()
        definitions = {}
        for i, name in enumerate(set(binding.BINDINGS.values())):
            filename = f"template-{i}.png"
            definitions[name] = {"template": filename, "action": "ClickSelf"}
            (official / "template" / filename).write_bytes(b"fixture")
        binding.atomic(official / "tasks/tasks.json", definitions)
        return config, official, target

    def test_upgrade_binds_templates_preserves_business_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            config, official, target = self.fixture(d)
            before = binding.read(target)
            self.assertTrue(binding.bind(config, [official])["changed"])
            after = binding.read(target)
            for name in before:
                actual, expected = copy.deepcopy(after[name]), copy.deepcopy(before[name])
                if name in binding.BINDINGS:
                    actual.pop("template", None)
                    expected.pop("template", None)
                self.assertEqual(expected, actual)
            self.assertEqual("current", binding.check(config)["status"])
            self.assertFalse(binding.bind(config, [official])["changed"])
            self.assertEqual([], daily_checks.prepare(config))

    def test_upstream_change_is_detected_then_rebound_including_template_arrays(self):
        with tempfile.TemporaryDirectory() as d:
            config, official, target = self.fixture(d)
            binding.bind(config, [official])
            definitions = binding.read(official / "tasks/tasks.json")
            definitions["Award"]["template"] = ["v1.png", "v2.png"]
            for filename in ("v1.png", "v2.png"):
                (official / "template" / filename).write_bytes(b"fixture")
            binding.atomic(official / "tasks/tasks.json", definitions)
            with self.assertRaisesRegex(ValueError, "stale_or_changed"):
                binding.check(config)
            binding.bind(config, [official])
            self.assertEqual(["v1.png", "v2.png"], binding.read(target)["MaaDailyCheck@RewardNavHome"]["template"])
            self.assertEqual("current", binding.check(config)["status"])

    def test_user_conflict_and_missing_template_never_partially_write(self):
        for cause in ("user", "missing", "algorithm", "duplicate", "base_override", "inherited_algorithm"):
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as d:
                config, official, target = self.fixture(d)
                if cause == "user":
                    nodes = binding.read(target)
                    nodes["MaaDailyCheck@RewardNavHome"]["action"] = "ClickSelf"
                    binding.atomic(target, nodes)
                elif cause == "missing":
                    next((official / "template").iterdir()).unlink()
                elif cause == "algorithm":
                    defs = binding.read(official / "tasks/tasks.json")
                    defs["Award"]["algorithm"] = "OcrDetect"
                    binding.atomic(official / "tasks/tasks.json", defs)
                elif cause == "duplicate":
                    binding.atomic(official / "tasks/duplicate.json", {"Award": {"template": "x.png"}})
                elif cause == "base_override":
                    binding.atomic(config / "resource/tasks/overrides.json", {"Award": {"roi": [0, 0, 1280, 720]}})
                else:
                    defs = binding.read(official / "tasks/tasks.json")
                    defs["Award"]["baseTask"] = "Changed"
                    defs["Changed"] = {"algorithm": "OcrDetect"}
                    binding.atomic(official / "tasks/tasks.json", defs)
                before = target.read_bytes()
                with self.assertRaises(ValueError):
                    binding.bind(config, [official])
                self.assertEqual(before, target.read_bytes())
                self.assertFalse((config / binding.MANIFEST).exists())
                self.assertFalse(list(target.parent.glob("*.bak-*")))

    def test_layer_order_and_default_filename_without_installing_credit(self):
        with tempfile.TemporaryDirectory() as d:
            config, official, target = self.fixture(d)
            nodes = {k:v for k,v in binding.read(target).items() if not k.startswith("MaaDailyCredit@")}
            binding.atomic(target, nodes)
            overlay = Path(d) / "overlay"
            (overlay / "tasks").mkdir(parents=True)
            (overlay / "template").mkdir()
            binding.atomic(overlay / "tasks/tasks.json", {"Award": {"template": "new.png"}})
            (overlay / "template/new.png").write_bytes(b"fixture")
            defs = binding.read(official / "tasks/tasks.json")
            defs["CrisisPopup"].pop("template")
            (official / "template/CrisisPopup.png").write_bytes(b"fixture")
            binding.atomic(official / "tasks/tasks.json", defs)
            binding.bind(config, [official, overlay])
            after = binding.read(target)
            self.assertEqual("new.png", after["MaaDailyCheck@RewardNavHome"]["template"])
            self.assertFalse(any(k.startswith("MaaDailyCredit@") for k in after))

    def test_new_skill_policy_is_not_migrated_by_template_rebinding(self):
        with tempfile.TemporaryDirectory() as d:
            config, official, target = self.fixture(d)
            binding.bind(config, [official])
            before = target.read_bytes()
            updated = binding.baseline()
            updated["MaaDailyCheck@RewardNavHome"]["action"] = "ClickSelf"
            with patch.object(binding, "baseline", return_value=updated):
                with self.assertRaisesRegex(ValueError, "resource_policy_conflict"):
                    binding.bind(config, [official])
            self.assertEqual(before, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
