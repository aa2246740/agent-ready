#!/usr/bin/env python3
"""Install all three portable readiness skills without modifying existing ones."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


PROJECT = Path(__file__).resolve().parents[1]
SKILL_NAMES = ("agent-readiness", "readiness-report", "readiness-fix")
# A bundle that contains the excluded skill texts has to carry the scope notices.
LICENSE_FILES = ("LICENSE", "LICENSE-SCOPE.md", "NOTICE.md")
# Deliberately copy only this pinned runtime. A blanket checkout/package copy
# could include caches, local configuration, credentials or development tools.
RUNTIME_FILES = (
    "scripts/readiness.py",
    "agent_readiness/__init__.py",
    "agent_readiness/__main__.py",
    "agent_readiness/catalog.py",
    "agent_readiness/application_scope.py",
    "agent_readiness/condition_evidence.py",
    "agent_readiness/cli.py",
    "agent_readiness/prompts.py",
    "agent_readiness/report.py",
    "agent_readiness/repository.py",
    "agent_readiness/runner_contract.py",
    "agent_readiness/scoring.py",
    "agent_readiness/validation.py",
    "agent_readiness/data/rules.json",
)


def _require_regular(source: Path, project: Path) -> None:
    inside = [part for part in (source, *source.parents) if part != project and project in part.parents]
    if not source.is_file() or any(part.is_symlink() for part in inside):
        raise ValueError(f"Required source must be a regular, non-symlink file: {source}")


def _rollback(created_files: list[Path], created_dirs: list[Path]) -> None:
    for created in reversed(created_files):
        try:
            created.unlink()
        except OSError:
            pass
    for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def _create_directory(path: Path, created_dirs: list[Path]) -> None:
    missing = []
    current = path
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for directory in reversed(missing):
        directory.mkdir()
        created_dirs.append(directory)


def _license_sources(project: Path) -> list[Path]:
    sources = [project / name for name in LICENSE_FILES]
    for source in sources:
        _require_regular(source, project)
    return sources


def install_skills(destination: Path, *, project: Path = PROJECT) -> list[Path]:
    """Copy the complete skill set, rejecting every pre-existing skill path.

    Each directory is a self-contained bundle of excluded skill text, so it also
    receives the required LICENSE, LICENSE-SCOPE.md and NOTICE.md.
    """
    destination = destination.expanduser().resolve()
    targets = [destination / name for name in SKILL_NAMES]
    # Preflight the entire set before creating any directory or copying files.
    # is_symlink also catches broken links, which must never be overwritten.
    for target in targets:
        if target.exists() or target.is_symlink():
            raise ValueError(f"Skill already exists: {target}; choose a different destination")
    sources = [project / "skills" / name / "SKILL.md" for name in SKILL_NAMES]
    sources.extend(project / relative for relative in RUNTIME_FILES)
    for source in sources:
        _require_regular(source, project)
    license_sources = _license_sources(project)
    destination.mkdir(parents=True, exist_ok=True)
    # Claim all three directories first. mkdir's exclusive creation also
    # protects against a skill appearing after the preflight above.
    created_dirs: list[Path] = []
    created_files: list[Path] = []
    try:
        for target in targets:
            target.mkdir()
            created_dirs.append(target)
        for name, target in zip(SKILL_NAMES, targets):
            copies = [(project / "skills" / name / "SKILL.md", target / "SKILL.md")]
            copies.extend((project / relative, target / "_runtime" / relative) for relative in RUNTIME_FILES)
            copies.extend((source, target / source.name) for source in license_sources)
            for source, output in copies:
                _create_directory(output.parent, created_dirs)
                # Exclusive file creation preserves even a file added concurrently
                # inside one of the newly created skill directories.
                with source.open("rb") as source_stream, output.open("xb") as output_stream:
                    created_files.append(output)
                    shutil.copyfileobj(source_stream, output_stream)
    except Exception:
        _rollback(created_files, created_dirs)
        raise
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install three self-contained readiness skills into an agent's skills directory")
    parser.add_argument("--destination", type=Path, required=True, help="Destination skills directory; existing skills are never overwritten")
    args = parser.parse_args(argv)
    try:
        installed = install_skills(args.destination)
    except (OSError, ValueError) as error:
        print(f"install-skills: {error}", file=sys.stderr)
        return 2
    print(f"Installed {len(installed)} skills in {installed[0].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
