"""Exercise portable skill installation through its public command line."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT / "tools" / "install_skills.py"
SKILLS = ("agent-readiness", "readiness-report", "readiness-fix")
_INSTALL_SPEC = importlib.util.spec_from_file_location("install_skills_under_test", INSTALLER)
INSTALL = importlib.util.module_from_spec(_INSTALL_SPEC)
_INSTALL_SPEC.loader.exec_module(INSTALL)


class SkillInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="readiness-skill-install-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cwd = self.base / "unrelated-working-directory"
        self.cwd.mkdir()
        self.destination = self.base / "agent-config" / "skills"

    def run_installer(self, *, script=INSTALLER):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(script), "--destination", str(self.destination)],
            cwd=self.cwd, capture_output=True, text=True, timeout=30,
        )

    @staticmethod
    def files(path):
        return {str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()}

    def test_installs_three_self_contained_runtimes_usable_outside_checkout(self):
        self.destination.mkdir(parents=True)
        user_file = self.destination / "existing-agent-preference.txt"
        user_file.write_text("keep this preference", encoding="utf-8")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(user_file.read_text(encoding="utf-8"), "keep this preference")
        for name in SKILLS:
            with self.subTest(skill=name):
                skill = self.destination / name
                self.assertEqual((skill / "SKILL.md").read_bytes(), (PROJECT / "skills" / name / "SKILL.md").read_bytes())
                files = self.files(skill)
                self.assertTrue((skill / "_runtime" / "agent_readiness" / "data" / "rules.json").is_file())
                for license_name in ("LICENSE", "LICENSE-SCOPE.md", "NOTICE.md"):
                    self.assertEqual((skill / license_name).read_bytes(), (PROJECT / license_name).read_bytes())
                for relative in files:
                    self.assertNotIn("__pycache__", relative)
                    self.assertNotIn(".venv", relative)
                    self.assertNotIn("tests/", relative)
                    self.assertNotIn(".git/", relative)
                    self.assertNotIn("node_modules", relative)
                    # LICENSE has no suffix; every other installed file stays in the original allowlist.
                    allowed = Path(relative).suffix in {".md", ".py", ".json"} or Path(relative).name == "LICENSE"
                    self.assertTrue(allowed, relative)
                runtime = skill / "_runtime" / "scripts" / "readiness.py"
                # Isolated Python excludes checkout/PYTHONPATH and caller cwd;
                # the installed launcher must locate its own bundled package.
                catalog = subprocess.run(
                    [sys.executable, "-I", "-B", str(runtime), "catalog"],
                    cwd=self.cwd, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(catalog.returncode, 0, catalog.stderr)
                self.assertEqual(len(json.loads(catalog.stdout)["criteria"]), 84)

    def test_repeated_install_exits_two_and_preserves_every_existing_file(self):
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stderr)
        customized = self.destination / "readiness-report" / "SKILL.md"
        customized.write_text("user customization\n", encoding="utf-8")
        before = self.files(self.destination)
        second = self.run_installer()
        self.assertEqual(second.returncode, 2)
        self.assertIn("already exists", second.stderr)
        self.assertNotIn("Traceback", second.stderr)
        self.assertEqual(second.stdout, "")
        self.assertEqual(self.files(self.destination), before)

    def test_preflights_entire_set_before_creating_any_skills(self):
        self.destination.mkdir(parents=True)
        existing = self.destination / "readiness-fix"
        existing.mkdir()
        (existing / "notes.txt").write_text("preserve", encoding="utf-8")
        before = self.files(self.destination)
        result = self.run_installer()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.files(self.destination), before)
        self.assertFalse((self.destination / "agent-readiness").exists())
        self.assertFalse((self.destination / "readiness-report").exists())

    def test_existing_file_and_broken_symlink_are_never_overwritten(self):
        self.destination.mkdir(parents=True)
        conflict = self.destination / "readiness-report"
        conflict.write_text("user file", encoding="utf-8")
        result = self.run_installer()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(conflict.read_text(encoding="utf-8"), "user file")
        self.assertFalse((self.destination / "agent-readiness").exists())
        conflict.unlink()
        conflict.symlink_to(self.base / "missing-target")
        result = self.run_installer()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(conflict.is_symlink())
        self.assertFalse((self.destination / "agent-readiness").exists())

    def test_copy_manifest_excludes_unrelated_source_files(self):
        fixture = self.base / "source-fixture"
        (fixture / "tools").mkdir(parents=True)
        shutil.copyfile(INSTALLER, fixture / "tools" / "install_skills.py")
        for name in INSTALL.LICENSE_FILES:
            shutil.copyfile(PROJECT / name, fixture / name)
        shutil.copytree(PROJECT / "skills", fixture / "skills")
        shutil.copytree(PROJECT / "scripts", fixture / "scripts")
        shutil.copytree(PROJECT / "agent_readiness", fixture / "agent_readiness")
        extras = (
            fixture / "agent_readiness" / "private-settings.json",
            fixture / "agent_readiness" / "private_helper.py",
            fixture / "agent_readiness" / ".env",
            fixture / "skills" / "readiness-report" / "private-notes.md",
        )
        for extra in extras:
            extra.write_text("must not be copied", encoding="utf-8")
        result = self.run_installer(script=fixture / "tools" / "install_skills.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative, content in self.files(self.destination).items():
            self.assertNotIn(b"must not be copied", content, relative)
            self.assertNotIn("__pycache__", relative)

    def test_license_notices_are_copied_into_each_skill_bundle(self):
        destination = self.base / "direct-skills"
        installed = INSTALL.install_skills(destination)
        self.assertEqual([path.name for path in installed], list(INSTALL.SKILL_NAMES))
        for skill in installed:
            for name in INSTALL.LICENSE_FILES:
                with self.subTest(skill=skill.name, name=name):
                    copied = skill / name
                    self.assertTrue(copied.is_file(), copied)
                    self.assertEqual(copied.read_bytes(), (PROJECT / name).read_bytes())

    def test_missing_all_license_notices_rejects_before_install(self):
        from unittest.mock import patch
        destination = self.base / "missing-notices"
        real_is_file = Path.is_file
        def is_file(path):
            return False if path.name in INSTALL.LICENSE_FILES else real_is_file(path)
        with patch.object(Path, "is_file", is_file):
            with self.assertRaisesRegex(ValueError, "Required source"):
                INSTALL.install_skills(destination)
        self.assertFalse(destination.exists())

    def test_failed_copy_rolls_back_and_retry_can_proceed(self):
        fixture = self.base / "fixture"
        for name in INSTALL.SKILL_NAMES:
            source = fixture / "skills" / name
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        for relative in INSTALL.RUNTIME_FILES:
            path = fixture / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic runtime\n", encoding="utf-8")
        for name in INSTALL.LICENSE_FILES:
            (fixture / name).write_text("Synthetic notice\n", encoding="utf-8")
        blocked = fixture / "agent_readiness" / "data" / "rules.json"
        os.chmod(blocked, 0)
        self.addCleanup(lambda: os.chmod(blocked, 0o644))
        destination = self.base / "rolled-back-skills"
        with self.assertRaises(OSError):
            INSTALL.install_skills(destination, project=fixture)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.rglob("*")), [])
        os.chmod(blocked, 0o644)
        installed = INSTALL.install_skills(destination, project=fixture)
        self.assertEqual([path.name for path in installed], list(INSTALL.SKILL_NAMES))
        restored = destination / "agent-readiness" / "_runtime" / "agent_readiness" / "data" / "rules.json"
        self.assertEqual(restored.read_text(encoding="utf-8"), "synthetic runtime\n")


if __name__ == "__main__":
    unittest.main()
