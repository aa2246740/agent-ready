"""Strict local reports; these checks extend the permissive native wire schema."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import RUBRIC_VERSION
from .application_scope import javascript_application_supported
from .catalog import criteria_by_id, load_catalog
from .condition_evidence import condition_errors
from .repository import ensure_external_output, inspect_repository, validate_evidence
from .runner_contract import java_test_execution, javascript_collection, python_collection_file, python_collection_matches_file
from .scoring import score_report, status


class ReportError(ValueError):
    pass


def read_json(path: str | Path) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReportError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ReportError(f"Non-finite JSON number: {value}")

    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    if not isinstance(data, dict):
        raise ReportError("Expected a JSON object")
    return data


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def new_evaluation(repository: dict) -> dict:
    return {
        "schemaVersion": 1,
        "rubricVersion": RUBRIC_VERSION,
        "repository": repository,
        "apps": {".": {"description": "TODO: discover independently deployable applications"}},
        "report": {
            key: {"numerator": "UNASSESSED", "denominator": 1, "rationale": ""}
            for key in criteria_by_id()
        },
        "evidence": {key: [] for key in criteria_by_id()},
        "actionItems": [],
    }


def _runner_context(command: str, cwd: str, root: Path | None, *, check_paths: bool) -> tuple[list[str], Path | None]:
    """Parse one literal in-repository cd and retain the initial evidence cwd.

    The second return value is the runner's directory relative to the recorded
    snapshot root. Current reports reject symlinks in changed directory paths;
    historical readback uses the original root without probing today's files.
    This parses captured records only and never executes or rewrites them.
    """
    rejected = ([], None)
    initial_relative = Path(cwd)
    if initial_relative.is_absolute() or any(part in {"..", ".git"} for part in initial_relative.parts):
        return rejected
    runner_relative = initial_relative
    forbidden = r"2>&1|\||(?:^|[\s;/])(?:head|tail|tee|timeout)(?:\s|$)"
    expansion = r"\$(?:[A-Za-z_0-9{(@*#?$!\-])"
    if re.search(forbidden, command) or re.search(expansion, command) or any(symbol in command for symbol in (";", "`", "\n", "\r", "<", ">")):
        return rejected
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="&")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return rejected
    if words and words[0] == "cd":
        if root is None or not root.is_absolute():
            return rejected
        prefix = 2 if len(words) > 1 and words[1] == "--" else 1
        if len(words) <= prefix + 2 or words[prefix + 1] != "&&":
            return rejected
        destination = words[prefix]
        if not destination or destination.startswith("-") or any(symbol in destination for symbol in ("$", "~", "*", "?", "[", "]", "{", "}", "\\")):
            return rejected
        path = Path(destination)
        if any(part in {"..", ".git"} for part in path.parts):
            return rejected
        # A bare relative name can be redirected by CDPATH, which this evidence
        # schema does not record. Absolute names and ./ prefixes bypass it.
        if not path.is_absolute() and destination != "." and not destination.startswith("./"):
            return rejected
        initial = root / initial_relative
        resolved = path if path.is_absolute() else initial / path
        try:
            runner_relative = resolved.relative_to(root)
        except ValueError:
            return rejected
        if check_paths and resolved != initial:
            if root.is_symlink() or not root.is_dir():
                return rejected
            for relative in (initial_relative, runner_relative):
                current = root
                for part in relative.parts:
                    current = current / part
                    if current.is_symlink() or not current.is_dir():
                        return rejected
        words = words[prefix + 2:]
    if any("&" in word for word in words) or (words and words[0] in {
        "!", "(", "{", "if", "for", "while", "until", "case", "function", "eval", "exec", "source", ".",
    }):
        return rejected
    return words, runner_relative


def _runner_words(command: str, cwd: str, root: Path | None) -> list[str]:
    return _runner_context(command, cwd, root, check_paths=True)[0]


def _runner_retargets(words: list[str], root: Path, runner_cwd: Path) -> bool:
    """Reject known cross-directory selectors after a cd into an application.

    This is deliberately bounded syntax support, not a proof of a test script's
    behavior. Arbitrary wrappers and dynamic test configuration still require
    the evaluator to substantiate their actual application coverage.
    """
    executable = Path(words[0]).name
    executable_path = Path(words[0])
    if ".." in executable_path.parts or (executable_path.is_absolute() and executable_path.is_relative_to(root)
                                        and not executable_path.is_relative_to(root / runner_cwd)):
        return True
    selectors = {"--cwd", "--prefix", "-C", "--directory"}
    if executable in {"npm", "npx", "pnpm", "yarn", "bun"}:
        selectors |= {"--workspace", "--workspaces", "-w", "-ws", "workspace", "workspaces", "--dir", "--filter", "-F"}
    elif executable in {"gradle", "gradlew"}:
        selectors |= {"-p", "--project-dir", "--include-build", "-b", "--build-file", "-c", "--settings-file"}
    elif executable in {"mvn", "mvnw"}:
        selectors |= {"-f", "--file", "-pl", "--projects"}
    elif executable.startswith("python") or executable == "pytest":
        selectors |= {"--rootdir", "--pyargs", "-c", "--config-file"}
    for word in words[1:]:
        option, _, value = word.partition("=")
        compact = {"-C"}
        if executable in {"npm", "npx", "pnpm", "yarn", "bun"}:
            compact |= {"-w", "-F"}
        elif executable in {"mvn", "mvnw"}:
            compact |= {"-f", "-pl"}
        elif executable in {"gradle", "gradlew"}:
            compact |= {"-p", "-b", "-c"}
        elif executable.startswith("python") or executable == "pytest":
            compact |= {"-c"}
        known_maven_control = executable in {"mvn", "mvnw"} and word in {"-ff", "-fae", "-fn"}
        if option in selectors or (not known_maven_control and any(word.startswith(flag) and word != flag for flag in compact)):
            return True
        path = Path(value if "=" in word else word)
        if ".." in path.parts or (path.is_absolute() and not path.is_relative_to(root / runner_cwd)):
            return True
    return False


def _test_evidence(items: list, numerator: int, apps: dict, root: Path | None = None, *, check_paths: bool = True) -> None:
    commands = [item for item in items if item.get("kind") == "command" and item.get("exitCode") == 0]
    if not commands:
        raise ReportError("unit_tests_runnable pass requires a successful command and captured output")
    passing_apps = set()
    for item in commands:
        command = item["command"]
        timeout = item.get("timeoutSeconds")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            continue
        words, runner_cwd = _runner_context(command, item["cwd"], root, check_paths=check_paths)
        if not words:
            continue
        changed_directory = runner_cwd != Path(item["cwd"])
        if Path(words[0]).name == "env":
            words = words[1:]
            if changed_directory and words and words[0].startswith("-"):
                continue
        assignments = []
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            assignments.append(words[0])
            words = words[1:]
        # Environment assignments can redirect package managers, import paths,
        # or wrapper dispatch. Only this known path-neutral write-control value
        # is supported after a directory change; unknown values are unverified.
        if changed_directory and any(value != "PYTHONDONTWRITEBYTECODE=1" for value in assignments):
            continue
        if not words:
            continue
        executable = Path(words[0]).name
        if changed_directory and (executable in {"sh", "bash", "zsh", "fish", "dash", "ksh", "cmd", "powershell", "pwsh"}
                                  or _runner_retargets(words, root, runner_cwd)):
            continue
        output = item["output"]
        if not isinstance(output, str) or not output.strip():
            continue
        if re.search(r"\b(?:collected\s+0\s+items|no tests (?:found|collected)|tests run:\s*0)\b", output, re.I):
            continue
        if executable in {"echo", "printf", "true", "false", "cat", "ls", "find", "rg", "grep"}:
            continue
        if any(word in words for word in ("--help", "--version")):
            continue
        if executable.startswith("python") or executable == "pytest":
            # Native Python contract requires one file and collection only.
            assigned_environment = dict(value.split("=", 1) for value in assignments)
            selected_file = python_collection_file(words, addopts=assigned_environment.get("PYTEST_ADDOPTS", ""))
            if selected_file is None or not python_collection_matches_file(selected_file, output):
                continue
            if not re.search(r"\b(?:collected\s+[1-9]\d*\s+items?|[1-9]\d*\s+tests?\s+collected)\b", output, re.I):
                continue
            if re.search(r"\b\d+\s+(?:passed|failed|xfailed|xpassed)\b|\bRan\s+\d+\s+tests?\b", output):
                continue
        elif executable in {"gradle", "gradlew", "mvn", "mvnw"}:
            if not java_test_execution(words, output):
                continue
        elif executable in {"npm", "npx", "pnpm", "yarn", "bun", "jest", "vitest"}:
            if not javascript_collection(words):
                continue
            if re.search(r"(?m)^\s*(?:PASS|FAIL)(?:\s|$)|\b\d+\s+(?:tests?\s+)?(?:passed|failed)\b", output):
                continue
        elif executable == "go":
            if "test" not in words:
                continue
            if re.search(r"\[no tests? (?:to run|files)\]", output, re.I):
                tested_package = any(
                    re.match(r"^ok\s+\S+\s+(?:\d+(?:\.\d+)?s|\(cached\))", line)
                    and not re.search(r"\[no tests? (?:to run|files)\]", line, re.I)
                    for line in output.splitlines()
                )
                for line in output.splitlines():
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(event, dict) and event.get("Action") == "pass" and event.get("Test"):
                        tested_package = True
                if not tested_package:
                    continue
        elif not any("test" in word.lower() or "spec" in word.lower() for word in words):
            continue
        app = item.get("application")
        candidates = [path for path in apps if runner_cwd == Path(path) or Path(path) in runner_cwd.parents]
        inferred_app = max(candidates, key=lambda path: len(Path(path).parts)) if candidates else None
        if app is not None:
            if (app not in apps or (changed_directory and app != inferred_app)
                    or (inferred_app not in {None, "."} and app != inferred_app)):
                continue
        else:
            if inferred_app is None:
                continue
            app = inferred_app
        if executable.startswith("python") or executable == "pytest":
            selected_path = Path(selected_file)
            if selected_path.is_absolute():
                if root is not None:
                    try:
                        selected_path = selected_path.relative_to(root)
                    except ValueError:
                        continue
            else:
                selected_path = runner_cwd / selected_path
            owners = [path for path in apps if selected_path == Path(path) or Path(path) in selected_path.parents]
            if owners and app != max(owners, key=lambda path: len(Path(path).parts)):
                continue
        if executable in {"npm", "npx", "pnpm", "yarn", "bun", "jest", "vitest"}:
            if not javascript_application_supported(app, words, runner_cwd, apps, root, output, check_paths=check_paths):
                continue
        passing_apps.add(app)
    if len(passing_apps) < numerator:
        raise ReportError("unit_tests_runnable needs a valid bounded runner command for each passing application; collection/execution must match its native language contract")


def validate_evaluation(data: dict, target: str | Path) -> dict:
    """Validate a complete assessment against the current working tree, without writing it."""
    current = inspect_repository(target)
    previous = data.get("repository")
    if not isinstance(previous, dict):
        raise ReportError("Missing repository snapshot; use prepare first")
    for key in ("root", "identity", "head", "fingerprint"):
        if previous.get(key) != current.get(key):
            raise ReportError(f"Repository {key} changed since prepare; re-prepare and re-evaluate changed evidence")
    _validate_structure(data, Path(current["root"]), check_paths=True)
    gaps = condition_errors(data, Path(current["root"]))
    if gaps:
        raise ReportError("Required-condition evidence failed: " + json.dumps(gaps, ensure_ascii=False))
    return current


def _historical_evidence(items: list) -> None:
    """Validate old evidence records without requiring deleted files to exist."""
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in {"file", "command"}:
            raise ReportError("Invalid historical evidence kind")
        path = item.get("path" if item["kind"] == "file" else "cwd")
        if not isinstance(path, str) or not path or Path(path).is_absolute() or any(part in {"..", ".git"} for part in Path(path).parts) or "\\" in path or "\x00" in path:
            raise ReportError("Invalid historical evidence path")
        if item["kind"] == "file":
            if not isinstance(item.get("note"), str) or not item["note"].strip():
                raise ReportError("Historical file evidence requires a note")
            if "line" in item and (type(item["line"]) is not int or item["line"] <= 0):
                raise ReportError("Invalid historical file line")
        else:
            if type(item.get("exitCode")) is not int:
                raise ReportError("Invalid historical command exit code")
            if not isinstance(item.get("command"), str) or not item["command"].strip():
                raise ReportError("Historical command evidence requires command and output")
            if not isinstance(item.get("output"), str):
                raise ReportError("Historical command evidence requires recorded output as a string")


def _validate_structure(data: dict, root: Path, *, check_paths: bool) -> None:
    if type(data.get("schemaVersion")) is not int or data.get("schemaVersion") != 1 or data.get("rubricVersion") != RUBRIC_VERSION:
        raise ReportError(f"Expected schemaVersion=1 and rubricVersion={RUBRIC_VERSION}")
    metadata = data.get("repository")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("root"), str) or not Path(metadata["root"]).is_absolute():
        raise ReportError("Invalid repository snapshot root")
    if not isinstance(metadata.get("identity"), str) or not metadata["identity"]:
        raise ReportError("Invalid repository identity")
    fingerprint = metadata.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ReportError("Invalid repository fingerprint")
    apps = data.get("apps")
    if not isinstance(apps, dict) or not apps:
        raise ReportError("apps must contain at least one application; use '.' for a single application")
    app_roots = set()
    for path, app in apps.items():
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ReportError("Application paths must be relative directories inside the repository")
        resolved = (root / path).resolve() if check_paths else root / path
        if not resolved.is_relative_to(root) or (check_paths and not resolved.is_dir()) or ".git" in Path(path).parts or "\\" in path or "\x00" in path:
            raise ReportError(f"Invalid application directory: {path}")
        if resolved in app_roots:
            raise ReportError(f"Duplicate application directory: {path}")
        app_roots.add(resolved)
        description = app.get("description") if isinstance(app, dict) else None
        if not isinstance(description, str) or not description.strip() or description.startswith("TODO:"):
            raise ReportError(f"Application {path} needs a real description")
    report = data.get("report")
    evidence = data.get("evidence")
    catalog = criteria_by_id()
    for name, value in (("report", report), ("evidence", evidence)):
        if not isinstance(value, dict) or set(value) != set(catalog):
            missing = sorted(set(catalog) - set(value or {})) if isinstance(value, dict) else list(catalog)
            extra = sorted(set(value or {}) - set(catalog)) if isinstance(value, dict) else []
            raise ReportError(f"{name} must cover exactly {len(catalog)} criteria; missing={missing}, extra={extra}")
    for key, criterion in catalog.items():
        value = report[key]
        if not isinstance(value, dict) or set(value) != {"numerator", "denominator", "rationale"}:
            raise ReportError(f"{key}: expected numerator, denominator, rationale")
        numerator, denominator = value["numerator"], value["denominator"]
        expected = len(apps) if criterion["scope"] == "application" else 1
        if type(denominator) is not int or denominator != expected:
            raise ReportError(f"{key}: {criterion['scope']} denominator must equal {expected}")
        if numerator is None:
            if not criterion.get("isSkippable", False):
                raise ReportError(f"{key}: this criterion cannot be skipped")
        elif type(numerator) is not int or not 0 <= numerator <= denominator:
            raise ReportError(f"{key}: numerator must be an integer between 0 and {denominator}")
        rationale = value["rationale"]
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale.encode("utf-16-le")) // 2 > 500:
            raise ReportError(f"{key}: rationale must be nonempty and at most 500 UTF-16 code units")
        items = evidence[key]
        if not isinstance(items, list) or not items:
            raise ReportError(f"{key}: at least one evidence record is required, including failures and skips")
        if check_paths:
            validate_evidence(root, items)
        else:
            _historical_evidence(items)
        if key == "unit_tests_runnable" and numerator is not None and numerator > 0:
            _test_evidence(items, numerator, apps, root, check_paths=check_paths)
    actions = data.get("actionItems", [])
    if not isinstance(actions, list) or len(actions) > 3:
        raise ReportError("actionItems must be a list of at most three concrete recommendations")
    selected = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"criterionId", "action", "reason"}:
            raise ReportError("Each action item needs criterionId, action and reason")
        key = action["criterionId"]
        if not isinstance(key, str) or key not in catalog or key in selected or status(report[key]) != "failed":
            raise ReportError("Action items must select distinct, evaluated failing criteria")
        if any(not isinstance(action[field], str) or not action[field].strip() for field in ("action", "reason")):
            raise ReportError("Action items require a concrete action and reason")
        selected.add(key)


def default_state_dir() -> Path:
    configured = os.environ.get("AGENT_READINESS_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "agent-readiness"


def _repo_state(repository: dict, state_dir: str | Path | None) -> Path:
    base = ensure_external_output(repository["root"], state_dir or default_state_dir())
    key = hashlib.sha256(repository["identity"].encode()).hexdigest()[:24]
    return ensure_external_output(repository["root"], base / key)


def latest_report(repository: dict, state_dir: str | Path | None) -> dict | None:
    directory = _repo_state(repository, state_dir)
    pointer = directory / "latest.json"
    if not pointer.exists():
        return None
    index = read_json(pointer)
    name = index.get("report")
    if not isinstance(name, str) or not re.fullmatch(r"[0-9TZ-]+-[0-9a-f]{12}\.json", name):
        raise ReportError("Invalid latest report pointer")
    report_path = directory / name
    if not report_path.resolve().is_relative_to(directory.resolve()):
        raise ReportError("Report path escapes state directory")
    expected_hash = index.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ReportError("Missing or invalid stored report integrity hash")
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != expected_hash:
        raise ReportError("Stored report integrity check failed; preserve the original and create a new assessment")
    data = read_json(report_path)
    metadata = data.get("repository")
    if not isinstance(metadata, dict) or metadata.get("identity") != repository["identity"] or data.get("rubricVersion") != RUBRIC_VERSION:
        raise ReportError("Stored report belongs to a different repository or rubric")
    _validate_structure(data, Path(data["repository"]["root"]), check_paths=False)
    # Scores are derived, never trusted from editable report JSON.
    data["summary"] = score_report(data["report"])
    return data


def _escape(text: object) -> str:
    return str(text).replace("\n", " ").replace("|", "\\|")


def _comparison(data: dict, previous: dict) -> dict:
    """Freeze the computed delta so viewing a saved report preserves its history."""
    criteria = []
    for key, value in data["report"].items():
        old = previous.get("report", {}).get(key)
        if old is None or (old["numerator"], old["denominator"]) != (value["numerator"], value["denominator"]):
            criteria.append({"criterionId": key, "before": old, "after": value})
    applications = []
    old_apps = previous.get("apps", {})
    for path in sorted(set(data["apps"]) | set(old_apps)):
        old, new = old_apps.get(path), data["apps"].get(path)
        if old != new:
            applications.append({"path": path, "before": old, "after": new})
    return {
        "previousReportId": previous.get("id"),
        "previousCreatedAt": previous.get("createdAt"),
        "previousCommit": previous.get("repository", {}).get("head"),
        "criteria": criteria,
        "applications": applications,
    }


def render_markdown(data: dict, previous: dict | None = None) -> str:
    summary = score_report(data["report"])
    lines = [
        "# Level", "", f"Level {summary['achievedLevel']} · {summary['scorePercent']:.1f}%", "",
        f"Repository: {_escape(data['repository']['identity'])}",
        f"Rubric: {RUBRIC_VERSION} · Commit: {data['repository'].get('head') or 'unborn'}", "",
        "# Applications", "",
    ]
    for path, app in data["apps"].items():
        lines.append(f"- `{_escape(path)}` — {_escape(app['description'])}")
    lines.extend(["", "# Criteria", ""])
    catalog = load_catalog()
    for category in catalog["categories"]:
        lines.extend([f"## {category['name']}", ""])
        for criterion in catalog["criteria"]:
            if criterion["category"] != category["id"]:
                continue
            key = criterion["id"]
            value = data["report"][key]
            fraction = "N/A" if value["numerator"] is None else f"{value['numerator']}/{value['denominator']}"
            lines.append(f"- **{criterion['name']}** (`{key}`): {fraction} — {_escape(value['rationale'])}")
            for item in data["evidence"][key]:
                if item["kind"] == "file":
                    source = f"{item['path']}:{item.get('line', 1)} — {item['note']}"
                else:
                    source = f"{item['command']} [cwd={item['cwd']}, exit={item['exitCode']}] — {item['output']}"
                lines.append(f"  Evidence: {_escape(source)}")
        lines.append("")
    lines.extend(["# Action Items", ""])
    failures = [row for row in catalog["criteria"] if status(data["report"][row["id"]]) == "failed"]
    failures.sort(key=lambda row: (row["level"], row["scope"] != "repository", row["id"]))
    if data.get("actionItems"):
        for action in data["actionItems"]:
            lines.append(f"- `{action['criterionId']}` — {_escape(action['action'])} {_escape(action['reason'])}")
    else:
        for row in failures[:3]:
            lines.append(f"- `{row['id']}` — {row['description']} Re-verify its recorded evidence before changing code.")
    if not failures:
        lines.append("No failing evaluated signals. Review skipped criteria separately; a high score does not establish universal readiness.")
    comparison = _comparison(data, previous) if previous is not None else data.get("comparison")
    if comparison is not None:
        lines.extend(["", "# Changes Since Last Report", ""])
        lines.extend([
            f"Previous report: {_escape(comparison.get('previousReportId') or 'unknown')} · "
            f"Date: {_escape(comparison.get('previousCreatedAt') or 'unknown')} · "
            f"Commit: {_escape(comparison.get('previousCommit') or 'unborn')}", "",
        ])
        for change in comparison["criteria"]:
            key, old, value = change["criterionId"], change["before"], change["after"]
            lines.append(f"- `{key}`: {old['numerator'] if old else '?'} / {old['denominator'] if old else '?'} → {value['numerator']} / {value['denominator']} — {_escape(value['rationale'])}")
        for change in comparison["applications"]:
            old, new = change["before"], change["after"]
            action = "added" if old is None else "removed" if new is None else "updated"
            description = new["description"] if new is not None else old["description"]
            lines.append(f"- Application {action}: `{_escape(change['path'])}` — {_escape(description)}")
        if not comparison["criteria"] and not comparison["applications"]:
            lines.append("No application or score changes.")
    lines.extend(["", "Local report; evidence and scoring are reproducible. Criterion judgments are supplied by the evaluating agent.", ""])
    return "\n".join(lines)


def store_report(data: dict, target: str | Path, state_dir: str | Path | None = None) -> dict:
    repository = validate_evaluation(data, target)
    directory = _repo_state(repository, state_dir)
    previous = latest_report(repository, state_dir)
    stored = dict(data, repository=repository, summary=score_report(data["report"]))
    # Never trust a comparison supplied in an agent's input document.
    stored.pop("comparison", None)
    if previous is not None:
        stored["comparison"] = _comparison(stored, previous)
    stored["createdAt"] = datetime.now(timezone.utc).isoformat()
    stored["id"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    markdown = render_markdown(stored, previous)
    # Check once more immediately before committing local state.
    if inspect_repository(target)["fingerprint"] != repository["fingerprint"]:
        raise ReportError("Repository changed during report validation")
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stored['id']}.json"
    md_path = directory / f"{stored['id']}.md"
    with json_path.open("x", encoding="utf-8") as stream:
        stream.write(json_text(stored))
    with md_path.open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    temporary = directory / f".latest-{uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json_text({"report": json_path.name, "sha256": hashlib.sha256(json_path.read_bytes()).hexdigest()}))
    os.replace(temporary, directory / "latest.json")
    return {"report": str(json_path), "markdown": str(md_path), "summary": stored["summary"]}
