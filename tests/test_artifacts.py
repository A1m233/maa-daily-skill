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
import artifacts as a


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "config"
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop(a.ENV_NAME, None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    @property
    def store(self):
        return self.config / "maa-daily-artifacts"

    def run_package(self, size=0, output=None, status="completed", uncertain=False):
        run = a.ArtifactRun.begin(self.config, output, "test", prefix="test-")
        if size:
            (run.path / "payload.bin").write_bytes(b"x" * size)
        run.finish(status=status, uncertain=uncertain)
        return run

    def policy(self, **values):
        data = dict(a.DEFAULT_POLICY)
        data.update(values)
        (self.store / "policy.json").write_text(json.dumps(data), encoding="utf-8")

    def age(self, run, days):
        index_path = self.store / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        record = next(r for r in data["runs"] if r["run_id"] == run.run_id)
        record["finished_at"] -= days * 86400
        (run.path / a.MANIFEST_NAME).write_text(json.dumps(record), encoding="utf-8")
        index_path.write_text(json.dumps(data), encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def link(self, path, target):
        try:
            path.symlink_to(target, target_is_directory=target.is_dir())
        except OSError as exc:
            self.skipTest(f"symlink privilege unavailable: {exc}")

    def test_defaults_and_only_explicitly_registered_artifacts(self):
        self.config.mkdir()
        old = self.config / "legacy-drain-output"
        old.mkdir()
        user = old / "user.txt"
        user.write_text("keep", encoding="utf-8")
        run = self.run_package()
        report = a.manage(self.config)
        self.assertEqual(report["policy"], a.DEFAULT_POLICY)
        self.assertEqual([r["path"] for r in report["runs"]], [str(run.path)])
        self.assertEqual(report["runs"][0]["state"], "finished")
        self.assertEqual(user.read_text(encoding="utf-8"), "keep")
        self.assertTrue(run.path.is_relative_to(self.store / "runs"))

    def test_preview_never_creates_store_and_never_changes_existing_files(self):
        report = a.manage(self.config)
        self.assertTrue(report["budget_ok"])
        self.assertFalse(self.config.exists())
        run = self.run_package()
        self.age(run, 15)
        before = self.snapshot()
        report = a.manage(self.config)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(report["would_delete"], [str(run.path)])
        self.assertEqual(report["deleted"], [])
        self.assertTrue(run.path.exists())

    def test_expired_package_deleted_without_scanning_adjacent_user_files(self):
        custom = self.root / "custom"
        run = self.run_package(output=custom)
        user = custom / "same-prefix-but-user-owned"
        user.mkdir()
        (user / "keep").write_bytes(b"keep")
        self.age(run, 15)
        report = a.manage(self.config, apply=True)
        self.assertEqual(report["deleted"], [str(run.path)])
        self.assertFalse(run.path.exists())
        self.assertEqual((user / "keep").read_bytes(), b"keep")
        self.assertEqual(a.manage(self.config)["runs"], [])

    def test_budget_evicts_oldest_finished_package_across_custom_roots(self):
        one = self.run_package(2048, self.root / "first")
        two = self.run_package(2048, self.root / "second")
        self.policy(max_bytes=4000)
        report = a.manage(self.config, apply=True)
        self.assertEqual(report["deleted"], [str(one.path)])
        self.assertFalse(one.path.exists())
        self.assertTrue(two.path.exists())
        self.assertLessEqual(report["remaining_bytes"], 4000)
        self.assertFalse((self.root / "first" / "maa-daily-artifacts").exists())

    def test_active_and_uncertain_are_protected_and_new_run_is_refused(self):
        active = a.ArtifactRun.begin(self.config, None, "active")
        (active.path / "active.bin").write_bytes(b"x" * 4096)
        self.policy(max_bytes=2048)
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                if uncertain:
                    result = active.finish("interrupted", uncertain=True)
                    self.assertFalse(result["budget_ok"])
                result = a.manage(self.config, apply=True)
                self.assertEqual(result["deleted"], [])
                self.assertTrue(active.path.exists())
                with self.assertRaises(a.ArtifactError):
                    a.ArtifactRun.begin(self.config, None, "refused")
                self.assertEqual(len(a.manage(self.config)["runs"]), 1)

    def test_finished_current_package_is_protected_until_next_cleanup(self):
        run = a.ArtifactRun.begin(self.config, None, "large")
        self.policy(max_bytes=2048)
        (run.path / "large").write_bytes(b"x" * 4096)
        result = run.finish("business_failed")
        self.assertFalse(result["budget_ok"])
        self.assertTrue(result["warnings"])
        self.assertTrue(run.path.exists())
        self.assertEqual(a.manage(self.config)["runs"][0]["state"], "finished")
        newer = a.ArtifactRun.begin(self.config, None, "new")
        self.assertFalse(run.path.exists())
        self.assertTrue(newer.path.exists())

    def test_empty_runs_cannot_grow_without_bound_with_same_day_preflights(self):
        seed = self.run_package()
        self.policy(max_bytes=4096)
        for _ in range(30):
            self.run_package(size=600)
        report = a.manage(self.config)
        self.assertTrue(report["budget_ok"])
        self.assertLessEqual(report["registered_bytes"], 4096)
        self.assertLessEqual(len(report["runs"]), 4)
        self.assertFalse(seed.path.exists())
        self.assertEqual(len(list((self.store / "runs").iterdir())), len(report["runs"]))

    def test_parent_children_and_isolated_environment_share_one_registration(self):
        parent = a.ArtifactRun.begin(self.config, None, "daily")
        child = a.ArtifactRun.begin(self.config, parent.path / "checks", "checks", parent=parent)
        grandchild = a.ArtifactRun.begin(self.config, None, "nested", parent=child)
        self.assertTrue(parent.owner)
        self.assertFalse(child.owner)
        self.assertTrue(grandchild.path.is_relative_to(child.path))
        with patch.dict(os.environ, parent.environment({"MAA_CONFIG_DIR": str(self.root / "isolated")})):
            borrowed = a.ArtifactRun.current()
            nested = a.ArtifactRun.begin(self.root / "isolated", None, "subprocess")
        self.assertEqual(borrowed.path, parent.path)
        self.assertFalse(borrowed.owner)
        self.assertEqual(nested.store, parent.store)
        self.assertTrue(nested.path.is_relative_to(parent.path))
        self.assertFalse((self.root / "isolated").exists())
        self.assertEqual(len(a.manage(self.config)["runs"]), 1)
        self.assertEqual(child.finish("failed")["owner"], False)
        self.assertEqual(a.manage(self.config)["runs"][0]["state"], "active")
        parent.finish()
        self.assertEqual(a.manage(self.config)["runs"][0]["state"], "finished")

    def test_environment_is_copied_without_mutation(self):
        run = a.ArtifactRun.begin(self.config, None, "parent")
        original = {"PATH": "unchanged"}
        environment = run.environment(original)
        self.assertEqual(original, {"PATH": "unchanged"})
        self.assertEqual(environment[a.ENV_NAME], str(run.path))
        self.assertNotIn(a.ENV_NAME, os.environ)
        self.assertIsNone(a.ArtifactRun.current())

    def test_parent_outside_finished_missing_and_unregistered_are_rejected(self):
        parent = a.ArtifactRun.begin(self.config, None, "daily")
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, self.root / "outside", "child", parent=parent)
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, parent.path, "child-without-context")
        parent.finish()
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, None, "finished-parent", parent=parent)
        for context in (str(parent.path), str(self.root / "missing")):
            with patch.dict(os.environ, {a.ENV_NAME: context}), self.assertRaises(a.ArtifactError):
                a.ArtifactRun.current()

    def test_corrupt_manifest_is_protected_and_blocks_new_runs(self):
        run = self.run_package()
        (run.path / a.MANIFEST_NAME).write_text("broken", encoding="utf-8")
        report = a.manage(self.config, apply=True)
        self.assertFalse(report["size_complete"])
        self.assertFalse(report["budget_ok"])
        self.assertEqual(report["deleted"], [])
        self.assertTrue(run.path.exists())
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, None, "blocked")

    def test_manifest_parent_mismatch_is_protected(self):
        run = self.run_package()
        index_path = self.store / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        record = index["runs"][0]
        record["output_parent"] = str(self.root)
        (run.path / a.MANIFEST_NAME).write_text(json.dumps(record), encoding="utf-8")
        index_path.write_text(json.dumps(index), encoding="utf-8")
        result = a.manage(self.config, apply=True)
        self.assertFalse(result["budget_ok"])
        self.assertTrue(run.path.exists())

    def test_invalid_timestamp_or_index_blocks_cleanup_without_crashing(self):
        run = self.run_package()
        index_path = self.store / "index.json"
        original = json.loads(index_path.read_text(encoding="utf-8"))
        original["runs"][0]["finished_at"] = float("nan")
        (run.path / a.MANIFEST_NAME).write_text(json.dumps(original["runs"][0]), encoding="utf-8")
        index_path.write_text(json.dumps(original), encoding="utf-8")
        self.assertFalse(a.manage(self.config, apply=True)["budget_ok"])
        self.assertTrue(run.path.exists())
        original["runs"][0]["run_id"] = []
        index_path.write_text(json.dumps(original), encoding="utf-8")
        self.assertFalse(a.manage(self.config, apply=True)["budget_ok"])
        self.assertTrue(run.path.exists())

    def test_acl_or_statistics_failure_is_not_zero_bytes(self):
        run = self.run_package()
        self.age(run, 15)
        with patch.object(a, "_tree_bytes", side_effect=PermissionError("denied")):
            report = a.manage(self.config, apply=True)
            with self.assertRaises(a.ArtifactError):
                a.ArtifactRun.begin(self.config, None, "blocked")
        self.assertFalse(report["size_complete"])
        self.assertIsNone(report["runs"][0]["bytes"])
        self.assertTrue(run.path.exists())

    def test_existing_lock_is_never_stolen_even_with_old_timestamp(self):
        run = self.run_package()
        lock = self.store / "store.lock"
        lock.write_text('{"pid": 99999999, "token": "someone-else"}', encoding="utf-8")
        os.utime(lock, (1, 1))
        before = lock.read_bytes()
        for apply in (False, True):
            self.assertFalse(a.manage(self.config, apply=apply)["budget_ok"])
        self.assertTrue(run.finish()["warnings"])
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, None, "blocked")
        self.assertEqual(lock.read_bytes(), before)

    def test_registration_write_failure_does_not_create_unregistered_directories(self):
        self.run_package()
        before = set((self.store / "runs").iterdir())
        original = a._write

        def fail_index(path, value):
            if path == self.store / "index.json":
                raise PermissionError("index write denied")
            return original(path, value)

        with patch.object(a, "_write", side_effect=fail_index):
            for _ in range(3):
                with self.assertRaises(a.ArtifactError):
                    a.ArtifactRun.begin(self.config, None, "blocked")
        self.assertEqual(set((self.store / "runs").iterdir()), before)

    def test_interrupted_package_creation_stays_registered_and_blocks_repetition(self):
        self.run_package()
        original = a._write

        def fail_manifest(path, value):
            if path.name == a.MANIFEST_NAME:
                raise PermissionError("manifest write denied")
            return original(path, value)

        with patch.object(a, "_write", side_effect=fail_manifest):
            with self.assertRaises(a.ArtifactError):
                a.ArtifactRun.begin(self.config, None, "interrupted")
        count = len(list((self.store / "runs").iterdir()))
        self.assertFalse(a.manage(self.config)["size_complete"])
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, None, "blocked")
        self.assertEqual(len(list((self.store / "runs").iterdir())), count)

    def test_link_in_tree_protects_package_and_external_target(self):
        run = self.run_package()
        external = self.root / "external"
        external.mkdir()
        (external / "valuable").write_bytes(b"keep")
        self.link(run.path / "shortcut", external)
        self.age(run, 15)
        result = a.manage(self.config, apply=True)
        self.assertFalse(result["budget_ok"])
        self.assertTrue(run.path.exists())
        self.assertEqual((external / "valuable").read_bytes(), b"keep")

    def test_link_ancestor_and_output_are_rejected_before_creation(self):
        external = self.root / "external"
        external.mkdir()
        linked = self.root / "linked"
        self.link(linked, external)
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(linked / "config", None, "blocked")
        self.assertEqual(list(external.iterdir()), [])
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, linked / "output", "blocked")
        self.assertEqual(list(external.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_windows_junction_in_tree_is_protected(self):
        run = self.run_package()
        external = self.root / "junction-target"
        external.mkdir()
        (external / "valuable").write_bytes(b"keep")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(run.path / "junction"), str(external)],
                                capture_output=True)
        if result.returncode:
            self.skipTest("cannot create isolated test junction")
        self.age(run, 15)
        report = a.manage(self.config, apply=True)
        self.assertFalse(report["budget_ok"])
        self.assertTrue(run.path.exists())
        self.assertEqual((external / "valuable").read_bytes(), b"keep")

    def test_cleanup_failure_retains_registry_and_marker_for_retry(self):
        run = self.run_package(16)
        self.age(run, 15)
        original = Path.unlink

        def deny_payload(path, *args, **kwargs):
            if path == run.path / "payload.bin":
                raise PermissionError("denied")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", deny_payload):
            report = a.manage(self.config, apply=True)
        self.assertEqual(report["deleted"], [])
        self.assertTrue(report["warnings"])
        self.assertTrue((run.path / a.MANIFEST_NAME).exists())
        self.assertEqual(len(a.manage(self.config)["runs"]), 1)
        self.assertEqual(a.manage(self.config, apply=True)["deleted"], [str(run.path)])

    def test_external_new_file_counted_and_deleted_without_parent(self):
        run = a.ArtifactRun.begin(self.config, None, "thin")
        report_file = self.root / "custom-report.json"
        report_file.write_bytes(b"x" * 700)
        run.register_file(report_file)
        inside = run.path / "inside.json"
        inside.write_bytes(b"inside")
        run.register_file(inside)
        run.finish()
        report = a.manage(self.config)
        self.assertGreaterEqual(report["registered_bytes"], 700)
        manifest = json.loads((run.path / a.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["files"]), 1)
        self.age(run, 15)
        self.assertEqual(a.manage(self.config, apply=True)["deleted"], [str(run.path)])
        self.assertFalse(report_file.exists())
        self.assertTrue(self.root.exists())

    def test_external_modified_file_and_its_package_are_protected(self):
        run = a.ArtifactRun.begin(self.config, None, "thin")
        outside = self.root / "report.json"
        outside.write_bytes(b"original")
        run.register_file(outside)
        run.finish()
        self.age(run, 15)
        outside.write_bytes(b"user-change")
        report = a.manage(self.config, apply=True)
        self.assertFalse(report["budget_ok"])
        self.assertEqual(outside.read_bytes(), b"user-change")
        self.assertTrue(run.path.exists())
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, None, "blocked")

    def test_missing_external_file_does_not_block_own_package_cleanup(self):
        run = a.ArtifactRun.begin(self.config, None, "thin")
        outside = self.root / "report.json"
        outside.write_bytes(b"original")
        run.register_file(outside)
        run.finish()
        self.age(run, 15)
        outside.unlink()
        self.assertEqual(a.manage(self.config, apply=True)["deleted"], [str(run.path)])

    def test_finish_failure_is_only_a_warning_and_uncertainty_is_not_cleared(self):
        run = a.ArtifactRun.begin(self.config, None, "test")
        run.finish("interrupted", uncertain=True)
        run.finish("completed")
        self.assertEqual(a.manage(self.config)["runs"][0]["state"], "uncertain")
        with patch.object(a, "_load", side_effect=PermissionError("denied")):
            self.assertEqual(run.finish()["warnings"], ["denied"])

    def test_child_uncertainty_propagates_and_blocks_new_children(self):
        parent = a.ArtifactRun.begin(self.config, None, "parent")
        child = a.ArtifactRun.begin(self.config, None, "child", parent=parent)
        self.assertFalse(child.owner)
        child.finish("interrupt", uncertain=True)
        parent.finish("normal_failure")
        self.assertEqual(a.manage(self.config)["runs"][0]["state"], "uncertain")
        with self.assertRaises(a.ArtifactError):
            a.ArtifactRun.begin(self.config, None, "child", parent=parent)
        with patch.dict(os.environ, parent.environment()), self.assertRaises(a.ArtifactError):
            a.ArtifactRun.current()
        self.age(parent, 15)
        self.assertEqual(a.manage(self.config, apply=True)["deleted"], [])

    def test_exact_registered_file_removal_preserves_other_files_and_latest_manifest(self):
        run = a.ArtifactRun.begin(self.config, None, "parent")
        with patch.dict(os.environ, run.environment()):
            borrowed = a.ArtifactRun.current()
        first = self.root / "first-task.json"
        second = self.root / "second-task.json"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        run.register_file(first)
        borrowed.register_file(second)
        self.assertEqual(run.remove_registered_file(first)["deleted"], [str(first)])
        self.assertTrue(second.exists())
        manifest = json.loads((run.path / a.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual([f["path"] for f in manifest["files"]], [str(second)])
        self.assertTrue(run.remove_registered_file(self.root / "unknown")["warnings"])
        second.write_text("modified", encoding="utf-8")
        self.assertTrue(run.remove_registered_file(second)["warnings"])
        self.assertEqual(second.read_text(encoding="utf-8"), "modified")

    def test_uncertain_package_cannot_delete_registered_temporary_file(self):
        run = a.ArtifactRun.begin(self.config, None, "parent")
        temporary = self.root / "task.json"
        temporary.write_bytes(b"task")
        run.register_file(temporary)
        run.finish("interrupted", uncertain=True)
        self.assertTrue(run.remove_registered_file(temporary)["warnings"])
        self.assertTrue(temporary.exists())

    def test_cli_uses_last_nonempty_directory_discovery_line_and_is_readonly(self):
        run = self.run_package()
        self.age(run, 15)
        before = self.snapshot()
        output = "informational preamble\n\n" + str(self.config) + "\n\n"
        with patch.object(a.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")) as command, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(a.main(["inspect", "--maa", "fake-maa"]), 0)
        self.assertEqual(command.call_args.args[0], ["fake-maa", "dir", "config", "--batch"])
        self.assertEqual(command.call_args.kwargs["timeout"], 30)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(json.loads(stdout.getvalue())["applied"])

    def test_cli_inspect_apply_is_rejected_before_discovery(self):
        with patch.object(a.subprocess, "run") as command, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as raised:
            a.main(["inspect", "--apply"])
        self.assertEqual(raised.exception.code, 2)
        command.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_offline_cli_preview_without_store_never_initializes_directories(self):
        for command_name in ("inspect", "cleanup"):
            with self.subTest(command=command_name), patch.object(a.subprocess, "run") as command, \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(a.main([command_name, "--artifact-config", str(self.config)]), 0)
            command.assert_not_called()
            self.assertFalse(json.loads(stdout.getvalue())["applied"])
            self.assertFalse(self.config.exists())
            self.assertFalse((self.store / "policy.json").exists())

    def test_offline_cli_cleanup_apply_removes_only_eligible_registered_package(self):
        finished = self.run_package()
        active = a.ArtifactRun.begin(self.config, None, "active")
        self.age(finished, 15)
        with patch.object(a.subprocess, "run") as command, contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(a.main(["cleanup", "--artifact-config", str(self.config), "--apply"]), 0)
        command.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())["deleted"], [str(finished.path)])
        self.assertTrue(active.path.exists())

    def test_cli_invalid_directory_output_or_discovery_failure_is_rejected(self):
        for output in ("", "\n\n", "relative/path", str(self.config) + "\nerror: unavailable"):
            with self.subTest(output=output), \
                    patch.object(a.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(a.main(["inspect"]), 2)
        with patch.object(a.subprocess, "run", side_effect=subprocess.TimeoutExpired("maa", 30)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(a.main(["inspect"]), 2)
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
