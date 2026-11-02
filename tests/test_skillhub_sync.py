import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

import test_skillhub_package as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_skillhub as sync


class FakeGitHub:
    def __init__(self):
        self.rows = [{"id": 1, "tag_name": "v0.1.2", "body": "legacy release", "assets": []}]
        self.states = []
        self.uploads = []

    def releases(self):
        return copy.deepcopy(self.rows)

    def create(self, manifest):
        row = {"id": len(self.rows) + 1, "tag_name": "v" + manifest["version"],
               "body": sync.body(manifest, "prepared"), "assets": []}
        self.rows.append(row)
        return copy.deepcopy(row)

    def update(self, release, manifest, status, **extra):
        self.states.append(status)
        row = next(r for r in self.rows if r["id"] == release["id"])
        row["body"] = sync.body(manifest, status, **extra)

    def upload(self, release, file):
        self.uploads.append(file.name)


class SkillHubSyncTests(unittest.TestCase):
    setUp = fixtures.SkillHubPackageTests.setUp
    git = fixtures.SkillHubPackageTests.git

    def runner(self, cli, archive, *, dry=False, commit=""):
        if not dry:
            self.assertEqual(sync.state(self.github.rows[-1])["status"], "submitting")
        return subprocess.CompletedProcess([], 0, json.dumps(
            {"dryRun": True} if dry else {"skillId": "test-skill", "status": "pending_review"}), "")

    def start(self):
        self.github = FakeGitHub()
        self.cli = Mock(side_effect=self.runner)

    def run_sync(self, name, **kwargs):
        return sync.sync(self.repo, self.root / name, Path("cli.py"), github=self.github,
                         run_cli=self.cli, **kwargs)

    def test_automatic_version_submission_and_same_content_skip(self):
        self.start()
        result = self.run_sync("first")
        self.assertEqual(result["version"], "0.1.3")
        self.assertEqual(result["platform_status"], "pending_review")
        self.assertEqual(self.github.states, ["submitting", "submitted"])
        (self.repo / "README.md").write_text("outside distribution", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "readme")
        self.cli.reset_mock()
        self.assertEqual(self.run_sync("second")["status"], "unchanged")
        self.cli.assert_not_called()
        self.assertEqual(len(self.github.rows), 2)

    def test_new_content_and_revert_each_publish_new_version(self):
        self.start()
        original = (self.skill / "SKILL.md").read_bytes()
        self.run_sync("one")
        for i, content in enumerate((original + b"\nnew\n", original), start=4):
            (self.skill / "SKILL.md").write_bytes(content)
            self.git("add", ".")
            self.git("commit", "-qm", "change")
            self.assertEqual(self.run_sync(str(i))["version"], f"0.1.{i}")

    def test_lost_response_blocks_even_changed_content_and_new_run(self):
        self.start()
        self.cli.side_effect = [self.runner(None, None, dry=True), TimeoutError("lost response")]
        with self.assertRaises(TimeoutError):
            self.run_sync("first")
        self.assertEqual(sync.state(self.github.rows[-1])["status"], "unknown")
        (self.skill / "scripts/helper.py").write_text("changed", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "change")
        self.cli.reset_mock()
        with self.assertRaisesRegex(ValueError, "Uncertain"):
            self.run_sync("second")
        self.cli.assert_not_called()

    def test_cancelled_submission_does_not_retry(self):
        self.start()
        self.cli.side_effect = [self.runner(None, None, dry=True), KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_sync("first")
        self.assertEqual(sync.state(self.github.rows[-1])["status"], "submitting")
        with self.assertRaisesRegex(ValueError, "Uncertain"):
            self.run_sync("second")

    def test_prepared_resume_keeps_version_and_archive(self):
        self.start()
        original_upload = self.github.upload
        self.github.upload = Mock(side_effect=RuntimeError("asset upload failed before SkillHub request"))
        with self.assertRaises(RuntimeError):
            self.run_sync("first")
        self.assertEqual(sync.state(self.github.rows[-1])["status"], "prepared")
        self.github.upload = original_upload
        self.assertEqual(self.run_sync("second")["version"], "0.1.3")
        self.assertEqual(len(self.github.rows), 2)

    def test_dry_validation_failure_creates_no_release(self):
        self.start()
        self.cli.return_value = subprocess.CompletedProcess([], 1, "", "invalid")
        self.cli.side_effect = None
        with self.assertRaisesRegex(ValueError, "dry-run"):
            self.run_sync("bad")
        self.assertEqual(len(self.github.rows), 1)
        self.assertFalse(self.github.states)

    def test_dry_mode_never_uploads(self):
        self.start()
        result = sync.sync(self.repo, self.root / "dry", Path("cli.py"),
                           dry_version="9.8.7", run_cli=self.cli)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["version"], "9.8.7")
        self.assertEqual(self.cli.call_count, 1)
        self.assertTrue(self.cli.call_args.kwargs["dry"])

    def test_nonzero_or_unparseable_publish_response_is_unknown(self):
        for index, result in enumerate((subprocess.CompletedProcess([], 1, '{}', ''),
                                        subprocess.CompletedProcess([], 0, 'not json', ''),
                                        subprocess.CompletedProcess([], 0, '{"success":false}', ''))):
            self.start()
            self.cli.side_effect = [self.runner(None, None, dry=True), result]
            with self.assertRaises(ValueError):
                self.run_sync(str(index))
            self.assertEqual(sync.state(self.github.rows[-1])["status"], "unknown")

    def test_existing_tag_and_api_failure_do_not_create_release(self):
        import urllib.error
        manifest = {"version": "0.1.3", "commit": "abc", "source_digest": "def", "sha256": "ghi"}
        github = sync.GitHub("example/repo", "test-token")
        github.request = Mock(return_value={"ref": "refs/tags/v0.1.3"})
        with self.assertRaisesRegex(ValueError, "tag already exists"):
            github.create(manifest)
        self.assertEqual(github.request.call_count, 1)
        github.request = Mock(side_effect=urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            github.create(manifest)
        caught.exception.close()
        self.assertEqual(github.request.call_count, 1)

    def test_mark_latest_only_after_confirmed_submission(self):
        github = sync.GitHub("example/repo", "test-token")
        github.request = Mock()
        manifest = {"version": "0.1.3", "commit": "abc", "source_digest": "def", "sha256": "ghi"}
        github.update({"id": 1}, manifest, "submitting")
        self.assertNotIn("make_latest", github.request.call_args.args[2])
        github.update({"id": 1}, manifest, "submitted", platform_status="pending_review")
        self.assertEqual(github.request.call_args.args[2]["make_latest"], "true")


if __name__ == "__main__":
    unittest.main()
