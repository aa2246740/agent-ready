#!/usr/bin/env python3
"""Install all three portable readiness skills without modifying existing ones."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


PROJECT = Path(__file__).resolve().parents[1]
SKILL_NAMES = ("agent-readiness", "readiness-report", "readiness-fix")
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
    "agent_readiness/data/rules.json",
)


def install_skills(destination: Path, *, project: Path = PROJECT) -> list[Path]:
    """Copy the complete skill set, rejecting every pre-existing skill path."""
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
        if not source.is_file() or any(
            part.is_symlink() for part in (source, *source.parents) if part != project and project in part.parents
        ):
            raise ValueError(f"Required source must be a regular, non-symlink file: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    # Claim all three directories first. mkdir's exclusive creation also
    # protects against a skill appearing after the preflight above.
    claimed = []
    try:
        for target in targets:
            target.mkdir()
            claimed.append(target)
    except OSError:
        # These directories are still empty. rmdir cannot remove user files if
        # another process added anything after creation.
        for target in reversed(claimed):
            try:
                target.rmdir()
            except OSError:
                pass
        raise
    for name, target in zip(SKILL_NAMES, targets):
        copies = [(project / "skills" / name / "SKILL.md", target / "SKILL.md")]
        copies.extend((project / relative, target / "_runtime" / relative) for relative in RUNTIME_FILES)
        for source, output in copies:
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive file creation preserves even a file added concurrently
            # inside one of the newly created skill directories.
            with source.open("rb") as source_stream, output.open("xb") as output_stream:
                shutil.copyfileobj(source_stream, output_stream)
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
