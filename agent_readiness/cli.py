"""Local transport for an agent-led readiness assessment and repair loop."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import __version__
from .catalog import criteria_by_id, load_catalog
from .prompts import assessment_prompt, fix_prompt, fix_without_report_prompt
from .report import ReportError, json_text, latest_report, new_evaluation, read_json, render_markdown, store_report
from .repository import ensure_external_output, inspect_repository
from .scoring import score_report


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Agent-led readiness workflows, pinned to Droid 0.240.0. No model API is called by this CLI.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="Print the exact versioned rule catalog")
    catalog.add_argument("--id", help="Print one criterion")
    prepare = commands.add_parser("prepare", help="Create a read-only assessment task and UNASSESSED template outside the target")
    prepare.add_argument("target")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--state-dir", type=Path)
    prepare.add_argument("--instructions", default="")
    report = commands.add_parser("report", help="Validate a completed evaluation and save a scored JSON/Markdown report")
    report.add_argument("target")
    report.add_argument("--input", type=Path, required=True)
    report.add_argument("--state-dir", type=Path)
    show = commands.add_parser("show", help="Read the latest local report")
    show.add_argument("target")
    show.add_argument("--state-dir", type=Path)
    show.add_argument("--json", action="store_true")
    fix = commands.add_parser("fix", help="Render a repair task from the latest report; the host agent performs actual edits")
    fix.add_argument("target")
    fix.add_argument("--state-dir", type=Path)
    selection = fix.add_mutually_exclusive_group()
    selection.add_argument("--signals", help="Comma-separated exact criterion IDs")
    selection.add_argument("--all", action="store_true", dest="all_signals")
    selection.add_argument("--target-level", type=int, choices=range(1, 6))
    fix.add_argument("--instructions", default="")
    score = commands.add_parser("score", help="Native-compatible arithmetic on supplied values; does NOT validate repository readiness")
    score.add_argument("--input", type=Path, required=True)
    return root


def run(args: argparse.Namespace) -> int:
    if args.command == "catalog":
        data = load_catalog()
        if args.id:
            if args.id not in criteria_by_id():
                raise ReportError(f"Unknown criterion: {args.id}")
            data = criteria_by_id()[args.id]
        print(json_text(data), end="")
    elif args.command == "score":
        data = read_json(args.input)
        values = data.get("report", data)
        if not isinstance(values, dict):
            raise ReportError("score input must be a criterion evaluation object")
        for key, value in values.items():
            if key not in criteria_by_id():
                continue  # Native scorer ignores unknown IDs.
            if not isinstance(value, dict):
                raise ReportError(f"{key}: expected an evaluation object")
            for field in ("numerator", "denominator"):
                number = value.get(field)
                if field == "numerator" and number is None:
                    continue
                if type(number) not in (int, float) or not math.isfinite(number):
                    raise ReportError(f"{key}: {field} must be a finite number")
            numerator = value.get("numerator")
            denominator = value.get("denominator")
            if numerator is not None and type(numerator) in (int, float) and type(denominator) in (int, float):
                if denominator <= 0 or numerator < 0 or numerator > denominator:
                    raise ReportError("Invalid criterion score fraction")
        print(json_text(score_report(values)), end="")
    elif args.command == "prepare":
        repository = inspect_repository(args.target)
        output = ensure_external_output(repository["root"], args.output)
        previous = latest_report(repository, args.state_dir)
        names = ["snapshot.json", "evaluation.json", "assessment.md"]
        if previous:
            names.append("previous-report.json")
        if any((output / name).exists() or (output / name).is_symlink() for name in names):
            raise ReportError("Task output already exists; choose a new directory to preserve prior evaluations")
        output.mkdir(parents=True, exist_ok=True)
        files = {
            "snapshot.json": json_text(repository),
            "evaluation.json": json_text(new_evaluation(repository)),
            "assessment.md": assessment_prompt(repository, output, previous, args.instructions),
        }
        if previous:
            files["previous-report.json"] = json_text(previous)
        for name, content in files.items():
            with (output / name).open("x", encoding="utf-8") as stream:
                stream.write(content)
        print(json_text({"task": str(output / "assessment.md"), "evaluation": str(output / "evaluation.json"), "status": "awaiting_agent_evaluation", "criteria": len(criteria_by_id())}), end="")
    elif args.command == "report":
        print(json_text(store_report(read_json(args.input), args.target, args.state_dir)), end="")
    else:
        repository = inspect_repository(args.target)
        previous = latest_report(repository, args.state_dir)
        if previous is None and args.command == "show":
            raise ReportError("No local readiness report for this repository; prepare and complete an assessment, then run report first")
        if args.command == "show":
            print(json_text(previous) if args.json else render_markdown(previous), end="")
        elif args.command == "fix":
            signals = None
            if args.signals is not None:
                signals = [item.strip() for item in args.signals.split(",") if item.strip()]
                if not signals:
                    raise ReportError("--signals must select at least one criterion")
            options = dict(signals=signals, all_signals=args.all_signals, target_level=args.target_level, instructions=args.instructions)
            task = fix_prompt(repository, previous, **options) if previous is not None else fix_without_report_prompt(repository, **options)
            print(task, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
        print(f"readiness: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
