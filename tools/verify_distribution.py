#!/usr/bin/env python3
"""Build and inspect distributions offline, entirely outside the checkout.

Uses the already-installed setuptools backend directly: no dependency resolver,
network access, installation into the user's environment, or source-tree build.
The result records both the declared backend requirement and the version used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {"agent_readiness", "tools", "scripts", "skills", "docs", "evidence", "tests"}
SOURCE_FILES = {"AGENTS.md", "README.md", "requirements-dev.txt", "LICENSE", "LICENSE-SCOPE.md", "NOTICE.md", "pyproject.toml", "MANIFEST.in"}


def verify(project: Path, output: Path) -> dict:
    project, output = project.resolve(), output.resolve()
    if output.is_relative_to(project):
        raise ValueError("distribution verification output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             cwd=project, capture_output=True, check=True).stdout
    required = set()
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if relative.parts[0] not in SOURCE_DIRS and relative.as_posix() not in SOURCE_FILES:
            continue
        origin = project / relative
        if not origin.is_file() or origin.is_symlink():
            raise ValueError(f"distribution source must be a regular file: {relative}")
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
        if relative.parts[0] in SOURCE_DIRS or relative.as_posix() in SOURCE_FILES:
            required.add(relative.as_posix())
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    log = output / "build.log"

    def run(argv, cwd):
        with log.open("a", encoding="utf-8") as stream:
            subprocess.run(argv, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=True)

    dist = output / "dist"
    dist.mkdir()
    run([sys.executable, "-B", "-c", "import setuptools.build_meta as b; b.build_sdist(" + repr(str(dist)) + ")"], source)
    archive = next(dist.glob("*.tar.gz"))
    extracted = output / "extracted"
    with tarfile.open(archive) as bundle:
        prefix = bundle.getnames()[0].split("/")[0]
        members = {name.removeprefix(prefix + "/") for name in bundle.getnames()}
        missing = required - members
        if missing:
            raise AssertionError(f"sdist missing source files: {sorted(missing)}")
        bundle.extractall(extracted, filter="data")
    unpacked = extracted / prefix
    for relative in required:
        assert (unpacked / relative).read_bytes() == (project / relative).read_bytes(), relative
    # Building from the sdist also proves that its manifest is sufficient.
    run([sys.executable, "-B", "-c", "import setuptools.build_meta as b; b.build_wheel(" + repr(str(dist)) + ")"], unpacked)
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as bundle:
        names = bundle.namelist()
        unexpected = [name for name in names if not name.startswith("agent_readiness/") and not name.split("/")[0].endswith(".dist-info")]
        assert not unexpected, unexpected
        for relative in required:
            if relative.startswith("agent_readiness/"):
                assert bundle.read(relative) == (project / relative).read_bytes(), relative
        for name in ("LICENSE", "LICENSE-SCOPE.md", "NOTICE.md"):
            matches = [member for member in names if member.endswith(".dist-info/licenses/" + name)]
            assert len(matches) == 1 and bundle.read(matches[0]) == (project / name).read_bytes(), name
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    installed = output / "installed"
    run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile", "--target", str(installed), str(wheel)], output)
    probe = "import sys; sys.path.insert(0, " + repr(str(installed)) + "); from agent_readiness.catalog import load_catalog; assert len(load_catalog()['criteria']) == 84; from agent_readiness.cli import main; raise SystemExit(main(['--version']))"
    run([sys.executable, "-I", "-B", "-c", probe], output)
    assert not any((installed / name).exists() for name in ("docs", "tools", "skills", "evidence", "scripts"))
    result = {
        "output": str(output), "network": "disabled for pip; direct installed backend, no resolver/download step",
        "backend": {"declared": 'setuptools==80.9.0', "used": importlib.metadata.version("setuptools")},
        "sdist": {"path": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "sourceFilesVerified": len(required)},
        "wheel": {"path": str(wheel), "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "entries": len(names), "unexpectedEntries": unexpected},
        "installedProbe": "isolated CLI --version and 84-rule catalog passed; no root-level docs/tools/skills/evidence/scripts",
    }
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New, external directory (default: a fresh temp directory)")
    args = parser.parse_args()
    output = args.output or Path(tempfile.mkdtemp(prefix="agent-ready-v2-distribution-")) / "verification"
    print(json.dumps(verify(PROJECT, output), indent=2))


if __name__ == "__main__":
    main()
