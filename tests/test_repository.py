import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_readiness.repository import (
    RepositoryError,
    canonicalize_origin,
    ensure_external_output,
    inspect_repository,
    validate_evidence,
)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Readiness Tests")
        (self.root / "README.md").write_text("# Project\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout

    def inspect(self):
        return inspect_repository(self.root)

    def file_evidence(self, path="README.md", **extra):
        return {"kind": "file", "path": path, "note": "Documented setup", **extra}

    def test_root_resolution_identity_and_read_only(self):
        nested = self.root / "nested"
        nested.mkdir()
        before = {name: (self.root / ".git" / name).read_bytes() for name in ("config", "index", "HEAD")}
        result = inspect_repository(nested)
        self.assertEqual(result["root"], str(self.root))
        self.assertEqual(result["identity"], f"local:{self.root}")
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["head"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertFalse(result["dirty"])
        self.assertEqual(result["fingerprint"], self.inspect()["fingerprint"])
        for name, value in before.items():
            self.assertEqual((self.root / ".git" / name).read_bytes(), value)
        self.assertFalse((self.root / ".git" / "index.lock").exists())
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_changes_include_contents_even_when_status_is_unchanged(self):
        (self.root / "README.md").write_bytes(b"first")
        initial = self.inspect()
        self.assertTrue(initial["dirty"])
        (self.root / "README.md").write_bytes(b"other")
        self.assertNotEqual(initial["fingerprint"], self.inspect()["fingerprint"])
        (self.root / "untracked.bin").write_bytes(b"\x00one")
        untracked = self.inspect()["fingerprint"]
        (self.root / "untracked.bin").write_bytes(b"\x00two")
        self.assertNotEqual(untracked, self.inspect()["fingerprint"])

    def test_ignored_files_are_excluded_but_tracked_ignored_files_are_included(self):
        baseline = self.inspect()["fingerprint"]
        ignored = self.root / "ignored"
        ignored.mkdir()
        (ignored / "cache").write_text("cache", encoding="utf-8")
        self.assertEqual(baseline, self.inspect()["fingerprint"])
        self.git("add", "-f", "ignored/cache")
        tracked = self.inspect()["fingerprint"]
        (ignored / "cache").write_text("new cache", encoding="utf-8")
        self.assertNotEqual(tracked, self.inspect()["fingerprint"])

    def test_head_file_mode_and_missing_files_change_fingerprint(self):
        baseline = self.inspect()["fingerprint"]
        self.git("commit", "--allow-empty", "-m", "new head")
        self.assertNotEqual(baseline, self.inspect()["fingerprint"])
        baseline = self.inspect()["fingerprint"]
        (self.root / "README.md").chmod(0o755)
        self.assertNotEqual(baseline, self.inspect()["fingerprint"])
        (self.root / "README.md").unlink()
        self.assertTrue(self.inspect()["dirty"])

    def test_symlinks_are_hashed_without_reading_their_targets(self):
        outside = self.base / "outside"
        outside.write_text("secret", encoding="utf-8")
        (self.root / "linked").symlink_to(outside)
        initial = self.inspect()["fingerprint"]
        outside.write_text("changed secret", encoding="utf-8")
        self.assertEqual(initial, self.inspect()["fingerprint"])
        (self.root / "linked").unlink()
        (self.root / "linked").symlink_to(self.base / "different")
        self.assertNotEqual(initial, self.inspect()["fingerprint"])

    def test_tracked_parent_replaced_by_external_symlink_is_rejected(self):
        directory = self.root / "directory"
        directory.mkdir()
        (directory / "file").write_text("tracked", encoding="utf-8")
        self.git("add", "directory/file")
        self.git("commit", "-m", "directory fixture")
        (directory / "file").unlink()
        directory.rmdir()
        directory.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(RepositoryError):
            self.inspect()

    def test_snapshot_handles_posix_backslash_filename(self):
        if os.name != "posix":
            self.skipTest("POSIX filename semantics")
        baseline = self.inspect()["fingerprint"]
        (self.root / "literal\\filename").write_bytes(b"contents")
        self.assertNotEqual(baseline, self.inspect()["fingerprint"])

    def test_gitlink_contents_are_included(self):
        nested = self.root / "nested"
        nested.mkdir()
        for args in (("init", "-b", "main"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Tests")):
            subprocess.run(["git", "-C", str(nested), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (nested / "file").write_text("original", encoding="utf-8")
        for args in (("add", "."), ("commit", "-m", "nested fixture")):
            subprocess.run(["git", "-C", str(nested), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.git("add", "nested")
        self.git("commit", "-m", "gitlink fixture")
        (nested / "file").write_text("first change", encoding="utf-8")
        fingerprint = self.inspect()["fingerprint"]
        (nested / "file").write_text("second change", encoding="utf-8")
        self.assertNotEqual(fingerprint, self.inspect()["fingerprint"])

    def test_fsmonitor_hook_is_never_executed(self):
        marker = self.base / "hook-executed"
        hook = self.base / "fsmonitor-hook"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))
        self.inspect()
        self.assertFalse(marker.exists())

    def test_secret_remote_values_never_enter_result(self):
        self.git("remote", "add", "origin", "https://user:topsecret@GitHub.com/Owner/Repo.git?token=hidden#private")
        result = self.inspect()
        self.assertEqual(result["origin"], "https://github.com/Owner/Repo")
        self.assertEqual(result["identity"], result["origin"])
        for secret in ("user", "topsecret", "hidden", "private"):
            self.assertNotIn(secret, repr(result))
        self.git("remote", "set-url", "origin", "git@github.com:Owner/Repo.git")
        self.assertEqual(result["identity"], self.inspect()["identity"])
        self.assertEqual(result["fingerprint"], self.inspect()["fingerprint"])

    def test_remote_url_normalization(self):
        expected = "https://github.com/owner/repo"
        for value in (
            "git@github.com:owner/repo.git",
            "ssh://git:secret@github.com:22/owner/repo.git",
            "https://name:password@github.com:443/owner/repo.git?key=secret#anchor",
            "https://github.com/owner/repo.git/",
        ):
            with self.subTest(value=value):
                self.assertEqual(canonicalize_origin(value), expected)

    def test_file_and_command_evidence(self):
        (self.root / "inside-link").symlink_to("README.md")
        validate_evidence(self.root, [self.file_evidence(line=1), self.file_evidence("inside-link")])
        command = {"kind": "command", "command": "touch MUST_NOT_RUN", "cwd": ".", "exitCode": 0, "output": "No failures"}
        validate_evidence(self.root, command)
        self.assertFalse((self.root / "MUST_NOT_RUN").exists())

    def test_ignored_untracked_file_evidence_is_rejected(self):
        ignored = self.root / "ignored"
        ignored.mkdir()
        (ignored / "report.txt").write_text("test output\n", encoding="utf-8")
        with self.assertRaisesRegex(RepositoryError, "tracked or non-ignored"):
            validate_evidence(self.root, self.file_evidence("ignored/report.txt"))
        self.git("add", "-f", "ignored/report.txt")
        validate_evidence(self.root, self.file_evidence("ignored/report.txt", line=1))
        (self.root / "untracked.txt").write_text("regular untracked file", encoding="utf-8")
        validate_evidence(self.root, self.file_evidence("untracked.txt", line=1))

    def test_evidence_symlinks_and_their_targets_must_all_be_snapshotted(self):
        ignored = self.root / "ignored"
        ignored.mkdir()
        (ignored / "report.txt").write_text("output\n", encoding="utf-8")
        (self.root / "report-link").symlink_to("ignored/report.txt")
        (ignored / "readme-link").symlink_to("../README.md")
        (self.root / "indirect-link").symlink_to("ignored/readme-link")
        for path in ("report-link", "ignored/readme-link", "indirect-link"):
            with self.subTest(path=path), self.assertRaises(RepositoryError):
                validate_evidence(self.root, self.file_evidence(path))
        self.git("add", "-f", "ignored/report.txt", "ignored/readme-link")
        for path in ("report-link", "ignored/readme-link", "indirect-link"):
            validate_evidence(self.root, self.file_evidence(path, line=1))

    def test_evidence_line_must_exist_without_requiring_text_decoding(self):
        cases = ((b"", ()), (b"one", (1,)), (b"one\n", (1,)), (b"one\ntwo", (1, 2)), (b"one\n\n", (1, 2)), (b"\xff\r\nsecond", (1, 2)))
        path = self.root / "lines.txt"
        for content, valid_lines in cases:
            path.write_bytes(content)
            for line in valid_lines:
                with self.subTest(content=content, line=line):
                    validate_evidence(self.root, self.file_evidence("lines.txt", line=line))
            with self.subTest(content=content), self.assertRaisesRegex(RepositoryError, "line does not exist"):
                validate_evidence(self.root, self.file_evidence("lines.txt", line=len(valid_lines) + 1))

    def test_evidence_rejects_invalid_paths_and_symlink_escape(self):
        outside = self.base / "secret"
        outside.write_text("secret", encoding="utf-8")
        (self.root / "outside-link").symlink_to(outside)
        (self.root / "outside-directory").symlink_to(self.base, target_is_directory=True)
        (self.root / "metadata-link").symlink_to(".git/config")
        for path in (str(outside), "../secret", "a/../README.md", "outside-link", "outside-directory/secret", ".git/config", "metadata-link", "missing", ".", "C:\\secret", "\\\\server\\secret"):
            with self.subTest(path=path), self.assertRaises(RepositoryError):
                validate_evidence(self.root, self.file_evidence(path))
        for cwd in ("..", "outside-directory", "README.md", str(self.base), "missing"):
            with self.subTest(cwd=cwd), self.assertRaises(RepositoryError):
                validate_evidence(self.root, {"kind": "command", "command": "true", "cwd": cwd, "exitCode": 0, "output": "ok"})

    def test_evidence_required_fields_and_types(self):
        cases = [None, {}, self.file_evidence(note=" "), self.file_evidence(line=0), self.file_evidence(line=True), self.file_evidence(line="1")]
        command = {"kind": "command", "command": "true", "cwd": ".", "exitCode": 0, "output": "ok"}
        cases.extend({**command, key: value} for key, value in (("command", ""), ("output", None), ("exitCode", True), ("exitCode", "0"), ("cwd", None)))
        for evidence in cases:
            with self.subTest(evidence=evidence), self.assertRaises(RepositoryError):
                validate_evidence(self.root, evidence)

    def test_external_output_rejects_repository_and_symlink_paths(self):
        external = self.base / "reports" / "report.json"
        self.assertEqual(ensure_external_output(self.root, external), external)
        (self.base / "alias").symlink_to(self.root, target_is_directory=True)
        for target in (self.root, self.root / "new" / "report.json", self.base / "alias" / "report.json"):
            with self.subTest(target=target), self.assertRaises(RepositoryError):
                ensure_external_output(self.root, target)
        self.assertFalse(external.exists())

    def test_unborn_and_detached_head(self):
        self.git("checkout", "--detach")
        self.assertIsNone(self.inspect()["branch"])
        empty = self.base / "empty"
        empty.mkdir()
        subprocess.run(["git", "-C", str(empty), "init", "-b", "main"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = inspect_repository(empty)
        self.assertIsNone(result["head"])
        self.assertEqual(result["branch"], "main")
        self.assertFalse(result["dirty"])

    def test_not_a_repository(self):
        with self.assertRaises(RepositoryError):
            inspect_repository(self.base)


if __name__ == "__main__":
    unittest.main()
