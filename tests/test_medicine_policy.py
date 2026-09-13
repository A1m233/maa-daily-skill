import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maa-daily/scripts"))
import medicine_policy as policy


class PolicyTests(unittest.TestCase):
    def test_persistent_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.toml"
            for mode, days in (("off", 1), ("use_and_drain", 2)):
                path.write_text(f'[expiring_medicine]\nmode="{mode}"\nmedicine_expire_days={days}\n', encoding="utf-8")
                result = policy.load_policy(path)
                self.assertEqual(result, {"mode": mode, "medicine_expire_days": days})
                if mode != "use_and_drain":
                    with self.assertRaises(ValueError):
                        policy.recovery_task("AP-5", result, 10, 100)
                else:
                    task = policy.recovery_task("AP-5", result, 1, 2)
                    self.assertEqual((task["params"]["medicine"], task["params"]["stone"]), (0, 0))
                    self.assertEqual(task["params"]["medicine_expire_days"], 2)

    def test_invalid_policy_never_defaults_to_use(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.toml"
            for mode, days in (("unknown", 1), ("off", 0), ("notify", 1), ("use_and_drain", -1)):
                path.write_text(f'[expiring_medicine]\nmode="{mode}"\nmedicine_expire_days={days}\n', encoding="utf-8")
                with self.assertRaises(ValueError):
                    policy.load_policy(path)
            path.write_text('[expiring_medicine]\nmode="notify"\nmedicine_expire_days=1\nstone=1\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                policy.load_policy(path)


if __name__ == "__main__":
    unittest.main()
