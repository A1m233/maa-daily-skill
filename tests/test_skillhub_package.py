import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load("package_skillhub")
fetcher = load("fetch_skillhub_cli")


class SkillHubPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Package Test")
        self.git("config", "user.email", "package@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.skill = self.repo / "maa-daily"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "assets").mkdir()
        (self.repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
        self.source = "---\nname: maa-daily\ndescription: 测试\n---\n\n# Test\n"
        (self.skill / "SKILL.md").write_text(self.source, encoding="utf-8")
        (self.skill / "scripts" / "helper.py").write_bytes(b"print('test')\n")
        (self.skill / "assets" / "tasks.json").write_bytes(b"{}\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args])

    def test_complete_package_is_immutable_and_reproducible(self):
        (self.skill / "account-private.toml").write_text("not tracked", encoding="utf-8")
        (self.skill / "scripts" / "helper.py").write_text("local modification", encoding="utf-8")
        first = package.build(self.repo, "HEAD", "0.1.0", self.root / "one")
        second = package.build(self.repo, "HEAD", "0.1.0", self.root / "two")
        self.assertEqual(first, second)
        with zipfile.ZipFile(self.root / "one" / first["archive"]) as zipped:
            self.assertEqual(set(zipped.namelist()), {"SKILL.md", "LICENSE", "scripts/helper.py", "assets/tasks.json"})
            self.assertEqual(zipped.read("scripts/helper.py"), b"print('test')\n")
            skill_md = zipped.read("SKILL.md").decode()
            self.assertIn("version: 0.1.0\n", skill_md)
            self.assertIn("displayName: MAA 明日方舟日常\n", skill_md)
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"), self.source)

    def test_invalid_version_creates_nothing(self):
        for version in ("latest", "01.2.3", "1.2", "1.2.3\nslug: other", "1.0.0-beta"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                package.build(self.repo, "HEAD", version, self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())

    def test_existing_output_is_not_overwritten(self):
        package.build(self.repo, "HEAD", "0.1.0", self.root / "existing")
        with self.assertRaises(FileExistsError):
            package.build(self.repo, "HEAD", "0.1.0", self.root / "existing")

    def test_rejects_symlink_in_commit(self):
        oid = self.git("rev-parse", "HEAD:LICENSE")
        self.git("update-index", "--add", "--cacheinfo", "120000", oid.decode().strip(), "maa-daily/link")
        self.git("commit", "-qm", "symlink")
        with self.assertRaisesRegex(ValueError, "unsupported Git entry"):
            package.build(self.repo, "HEAD", "0.1.0", self.root / "links")

    def test_cli_hash_mismatch_does_not_write_executable(self):
        target = self.root / "cli.py"
        with patch.object(fetcher.urllib.request, "urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"unexpected upstream version"
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                fetcher.fetch(target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
