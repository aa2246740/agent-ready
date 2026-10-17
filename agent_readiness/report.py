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

from . import RUBRIC_VERSION, __version__
from .application_scope import javascript_application_supported
from .catalog import criteria_by_id, load_catalog
from .condition_evidence import condition_errors
from .repository import _git, canonicalize_origin, ensure_external_output, inspect_repository, validate_evidence
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


_SURROGATE = re.compile(r"[\ud800-\udfff]")


def _reject_surrogates(value: object) -> None:
    if isinstance(value, str):
        if _SURROGATE.search(value):
            raise ReportError("Invalid Unicode surrogate in report")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_surrogates(key)
            _reject_surrogates(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_surrogates(item)


def json_text(value: object) -> str:
    _reject_surrogates(value)
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


_FORBIDDEN_COMMANDS = {"head", "tail", "tee", "timeout"}
_SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "cmd", "powershell", "pwsh"}
_WRAPPERS = {"uv", "poetry", "pdm", "pipenv", "hatch"}
_SAFE_ASSIGNMENT = re.compile(
    r"^(?:PYTHONDONTWRITEBYTECODE=1|PYTEST_ADDOPTS=.*|CI=.*|NO_COLOR=.*|FORCE_COLOR=.*|TZ=.*|LANG=.*|LC_ALL=.*)$"
)
_GO_VALUE_FLAGS = {
    "-bench", "-benchtime", "-blockprofile", "-blockprofilerate", "-count", "-covermode",
    "-coverpkg", "-coverprofile", "-cpu", "-cpuprofile", "-exec", "-fuzz", "-fuzzcachedir",
    "-fuzzminimizetime", "-fuzztime", "-fuzzworker", "-list", "-memprofile", "-memprofilerate",
    "-mutexprofile", "-mutexprofilefraction", "-outputdir", "-parallel", "-run", "-shuffle",
    "-skip", "-timeout", "-trace", "-vet", "-o", "-p",
}
_DRY_RUN_FLAGS = {"--no-run", "--dry-run", "--list-targets", "-n", "-N",
                  "--list", "--list-tests", "--list-suites", "--show-only"}


def _command_position_is_forbidden(words: list[str]) -> bool:
    """Reject head/tail/tee/timeout only when they are the command, not an argument."""
    segments: list[list[str]] = []
    current: list[str] = []
    for word in words:
        if word == "&&":
            segments.append(current)
            current = []
        else:
            current.append(word)
    segments.append(current)
    for segment in segments:
        index = 0
        if segment and Path(segment[0]).name == "env":
            index = 1
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
        while index < len(segment) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[index]):
            index += 1
        if index < len(segment) and Path(segment[index]).name in _FORBIDDEN_COMMANDS:
            return True
    return False


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
    # Pipes and merged stderr are rejected on the raw text. head/tail/tee/timeout
    # are command names, so argument values such as `-k timeout` stay legal.
    expansion = r"\$(?:[A-Za-z_0-9{(@*#?$!\-])"
    if re.search(r"2>&1|\|", command) or re.search(expansion, command) or any(symbol in command for symbol in (";", "`", "\n", "\r", "<", ">")):
        return rejected
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="&")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return rejected
    if _command_position_is_forbidden(words):
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
            # macOS /tmp is a symlink of /private/tmp. A resolved snapshot root
            # and an unresolved absolute cd target are the same directory.
            if not path.is_absolute():
                return rejected
            try:
                runner_relative = path.resolve(strict=False).relative_to(root.resolve(strict=False))
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


def _runner_retargets(words: list[str], root: Path | None, runner_cwd: Path, *,
                      allow_app_selectors: bool = False) -> bool:
    """Reject unverified scope changes at every recorded cwd.

    This is deliberately bounded syntax support, not a proof of a test script's
    behavior. Arbitrary wrappers and dynamic test configuration still require
    the evaluator to substantiate their actual application coverage.
    """
    executable = Path(words[0]).name
    executable_path = Path(words[0])
    if ".." in executable_path.parts or (root is not None and executable_path.is_absolute() and executable_path.is_relative_to(root)
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
    elif executable in _WRAPPERS:
        selectors |= {"--project", "--directory", "-p"}
    allowed = set()
    if allow_app_selectors:
        if executable in {"npm", "npx", "pnpm", "yarn", "bun"}:
            allowed = {"--cwd", "--prefix", "-C", "--directory", "--dir", "--workspace",
                       "--workspaces", "-w", "-ws", "workspace", "workspaces", "--filter", "-F"}
        elif executable in {"mvn", "mvnw"}:
            allowed = {"-f", "--file", "-pl", "--projects"}
        elif executable in {"gradle", "gradlew"}:
            allowed = {"-p", "--project-dir"}
        elif executable.startswith("python") or executable == "pytest":
            allowed = {"-c", "--config-file", "--rootdir", "--confcutdir"}
    selectors -= allowed
    for word in words[1:]:
        if executable in _WRAPPERS and word == "run":
            break
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
        elif executable in _WRAPPERS:
            compact |= {"-p"}
        known_maven_control = executable in {"mvn", "mvnw"} and word in {"-ff", "-fae", "-fn"}
        if option in selectors or (not known_maven_control and any(word.startswith(flag) and word != flag for flag in compact - allowed)):
            return True
        path = Path(value if "=" in word else word)
        if ".." in path.parts or (path.is_absolute() and (root is None or not path.is_relative_to(root / runner_cwd))):
            return True
    return False


def _path_escapes(path: Path, root: Path | None, runner_cwd: Path, *, check_paths: bool = False) -> bool:
    if ".." in path.parts:
        return True
    if root is not None and check_paths:
        base = root.resolve() / runner_cwd
        candidate = path if path.is_absolute() else base / path
        if not candidate.resolve().is_relative_to(base):
            return True
        try:
            relative = candidate.relative_to(base)
        except ValueError:
            return True
        current = base
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return True
    if not path.is_absolute():
        return False
    if root is None:
        return True
    try:
        return not path.is_relative_to(root / runner_cwd)
    except (TypeError, ValueError):
        return True


def _runner_escapes(words: list[str], root: Path | None, runner_cwd: Path, *, check_paths: bool = True) -> bool:
    """Reject directory escapes when the command did not cd first.

    In-repository selectors such as ``-f apps/web/pom.xml`` and ``--prefix``
    stay available; those are how a root command names one application.
    """
    executable_path = Path(words[0])
    if ".." in executable_path.parts:
        return True
    if root is not None and executable_path.is_absolute() and executable_path.is_relative_to(root) and not executable_path.is_relative_to(root / runner_cwd):
        return True
    compact_prefixes = ("-C", "-w", "-F", "-pl", "-f", "-p", "-b", "-c")
    for word in words[1:]:
        if word in {"-ff", "-fae", "-fn"}:
            continue
        option, separator, value = word.partition("=")
        attached = value if separator else None
        if attached is None:
            for flag in compact_prefixes:
                if word.startswith(flag) and word != flag:
                    attached = word[len(flag):]
                    break
        if attached is not None and _path_escapes(Path(attached), root, runner_cwd, check_paths=check_paths):
            return True
        target = Path(value if separator else word)
        if _path_escapes(target, root, runner_cwd, check_paths=check_paths):
            return True
    return False


def _wrapper_retargets(words: list[str]) -> bool:
    flags = {"--project", "--directory", "-C", "-p", "--cwd", "--prefix"}
    for word in words[1:]:
        if word == "run":
            return False
        option = word.partition("=")[0]
        if option in flags or (word.startswith("-p") and word not in {"-p"}) or (word.startswith("-C") and word != "-C"):
            return True
    return False


def _unwrap_wrapper(words: list[str]) -> list[str] | None:
    if Path(words[0]).name not in _WRAPPERS:
        return words
    args = words[1:]
    while args and args[0] != "run":
        if not args[0].startswith("-"):
            return None
        args = args[1:]
    if not args or args[0] != "run":
        return None
    args = args[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    return args or None


def _recognized_other_runner(words: list[str]) -> bool:
    executable = Path(words[0]).name
    if any(word.partition("=")[0] in _DRY_RUN_FLAGS or word.startswith(("--list-", "--help-"))
           or word in {"-h", "--print-labels"} for word in words):
        return False
    if executable in {"cargo", "dotnet", "mix", "zig", "swift", "flutter", "dart", "deno"}:
        return words[1:2] == ["test"]
    if executable == "ctest":
        return True
    if executable == "rspec":
        return True
    if executable == "phpunit":
        return True
    if executable in {"mocha", "ava", "tap"}:
        return True
    if executable == "bundle" and words[1:2] == ["exec"]:
        inner = words[2:]
        if not inner:
            return False
        if Path(inner[0]).name == "rake":
            if any(token in {"-T", "--tasks", "-D", "--describe", "-P", "--prereqs"}
                   for token in inner[1:]):
                return False
            return any(token == "test" or token.startswith("test:") for token in inner[1:])
        return Path(inner[0]).name in {"rspec", "phpunit"} and _recognized_other_runner(inner)
    return False


def _inside_app(candidate: Path, app: Path) -> bool:
    if candidate.suffix == ".xml":
        candidate = candidate.parent
    if candidate in {Path("."), Path("")}:
        return False
    return candidate == app or app in candidate.parents


def _go_packages(words: list[str]) -> list[Path] | None:
    try:
        index = words.index("test") + 1
    except ValueError:
        return None
    packages = []
    while index < len(words):
        token = words[index]
        if token == "--":
            index += 1
            continue
        if token.startswith("-"):
            name, separator, _value = token.partition("=")
            if separator == "" and name in _GO_VALUE_FLAGS:
                index += 2
                continue
            index += 1
            continue
        value = token[2:] if token.startswith("./") else token
        if value.endswith("/..."):
            value = value[:-4]
        elif value == "...":
            value = "."
        elif value.endswith("..."):
            value = value[:-3]
        value = value.strip("/")
        path = Path(value or ".")
        if path.is_absolute() or ".." in path.parts:
            return None
        packages.append(path)
        index += 1
    return packages


def _go_owns(words: list[str], app: Path) -> bool:
    packages = _go_packages(words)
    if not packages:
        return False
    return all(_inside_app(package, app) or package == app for package in packages)


def _flag_values(words: list[str], names: set[str], compact: set[str]) -> list[str]:
    values = []
    index = 0
    while index < len(words):
        word = words[index]
        option, separator, value = word.partition("=")
        if option in names:
            if separator:
                values.append(value)
            elif index + 1 < len(words):
                values.append(words[index + 1])
                index += 1
        elif any(word.startswith(flag) and word != flag for flag in compact) and word not in {"-ff", "-fae", "-fn"}:
            for flag in sorted(compact, key=len, reverse=True):
                if word.startswith(flag) and word != flag:
                    values.append(word[len(flag):])
                    break
        index += 1
    return values


def _maven_owns(words: list[str], app: Path) -> bool:
    values = _flag_values(words, {"-f", "--file", "-pl", "--projects"}, {"-f", "-pl"})
    targets = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                targets.append(Path(part))
    return bool(targets) and all(_inside_app(target, app) for target in targets)


def _gradle_owns(words: list[str], app: Path) -> bool:
    project_dirs = [Path(value) for value in _flag_values(words, {"-p", "--project-dir"}, {"-p"})]
    if project_dirs:
        return all(_inside_app(path, app) for path in project_dirs)
    tasks = []
    for word in words:
        if word.startswith("-"):
            continue
        if word == "test" or word.endswith(":test"):
            body = word[:-len(":test")] if word.endswith(":test") else ""
            body = body.lstrip(":").replace(":", "/")
            tasks.append(Path(body) if body else Path("."))
    named = [path for path in tasks if path != Path(".")]
    return bool(named) and all(_inside_app(path, app) for path in named)


def _root_command_owns_app(executable: str, words: list[str], app: str) -> bool:
    if app == ".":
        return True
    app_path = Path(app)
    if executable == "go":
        return _go_owns(words, app_path)
    if executable in {"mvn", "mvnw"}:
        return _maven_owns(words, app_path)
    if executable in {"gradle", "gradlew"}:
        return _gradle_owns(words, app_path)
    return True


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
            if words and words[0].startswith("-"):
                continue
        assignments = []
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            assignments.append(words[0])
            words = words[1:]
        # Environment assignments can redirect package managers, import paths,
        # or wrapper dispatch. After a cd, only the path-neutral bytecode flag
        # is verified. At the recorded cwd, allow known test flags and reject
        # every other assignment, including import and cache redirection.
        if changed_directory:
            if any(value != "PYTHONDONTWRITEBYTECODE=1" for value in assignments):
                continue
        elif any(not _SAFE_ASSIGNMENT.fullmatch(value) for value in assignments):
            continue
        if not words:
            continue
        if Path(words[0]).name in _WRAPPERS:
            if _wrapper_retargets(words) or _runner_retargets(words, root, runner_cwd):
                continue
            unwrapped = _unwrap_wrapper(words)
            if not unwrapped:
                continue
            words = unwrapped
        executable = Path(words[0]).name
        if executable in _SHELLS:
            continue
        if _runner_retargets(words, root, runner_cwd, allow_app_selectors=not changed_directory):
            continue
        if _runner_escapes(words, root, runner_cwd, check_paths=check_paths):
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
            addopts = assigned_environment.get("PYTEST_ADDOPTS", "")
            try:
                if _runner_retargets([words[0], *shlex.split(addopts)], root, runner_cwd,
                                     allow_app_selectors=not changed_directory):
                    continue
            except ValueError:
                continue
            scope_words = [words[0], *shlex.split(addopts), *words[1:]]
            if _runner_escapes(scope_words, root, runner_cwd, check_paths=check_paths):
                continue
            selected_file = python_collection_file(words, addopts=addopts)
            if selected_file is None or not python_collection_matches_file(selected_file, output):
                continue
            collected = re.search(r"\b(?:collected\s+[1-9]\d*\s+items?|[1-9]\d*\s+tests?\s+collected)\b", output, re.I)
            quiet_count = re.search(r"(?m)^\s*[^\s<>]+\.py:\s*[1-9]\d*\s*$", output)
            if not collected and not quiet_count:
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
            if words[1:2] != ["test"] or any(
                word.partition("=")[0] in {"-c", "-list", "-test.list", "-h", "-n"} for word in words[2:]
            ):
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
        elif not _recognized_other_runner(words):
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
            config_targets = _flag_values(scope_words, {"-c", "--config-file", "--rootdir", "--confcutdir"}, {"-c"})
            wrong_config = False
            for target in config_targets:
                config_path = Path(target)
                if config_path.is_absolute() and root is not None:
                    try:
                        config_path = config_path.relative_to(root)
                    except ValueError:
                        wrong_config = True
                        break
                else:
                    config_path = runner_cwd / config_path
                config_owners = [path for path in apps if config_path == Path(path) or Path(path) in config_path.parents]
                if config_owners and app != max(config_owners, key=lambda path: len(Path(path).parts)):
                    wrong_config = True
                    break
            if wrong_config:
                continue
            if check_paths and root is not None:
                candidate = root / selected_path
                if not candidate.is_file():
                    continue
        if len(apps) > 1 and runner_cwd == Path(".") and app != "." and not _root_command_owns_app(executable, words, app):
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
        if _repository_field_matches(key, previous, current):
            continue
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
    _reject_surrogates(data)
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
    if check_paths:
        all_items = []
        for key, items in evidence.items():
            if not isinstance(items, list) or not items:
                raise ReportError(f"{key}: at least one evidence record is required, including failures and skips")
            all_items.extend(items)
        validate_evidence(root, all_items)
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
            if check_paths and key == "monorepo_tooling" and len(apps) > 1:
                raise ReportError("monorepo_tooling cannot be skipped when multiple applications exist")
        elif type(numerator) is not int or not 0 <= numerator <= denominator:
            raise ReportError(f"{key}: numerator must be an integer between 0 and {denominator}")
        elif check_paths and numerator > 0:
            prerequisite = {
                "interactive_qa_runnable": "interactive_qa_exists",
            }.get(key)
            if prerequisite is not None:
                previous = report.get(prerequisite, {}).get("numerator")
                if type(previous) is not int or previous < numerator:
                    raise ReportError(f"{key} requires {prerequisite} to cover at least as many applications")
            if key == "agents_md_validation":
                previous = report.get("agents_md", {}).get("numerator")
                if type(previous) is not int or previous <= 0:
                    raise ReportError("agents_md_validation requires agents_md to pass")
        rationale = value["rationale"]
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale.encode("utf-16-le")) // 2 > 500:
            raise ReportError(f"{key}: rationale must be nonempty and at most 500 UTF-16 code units")
        items = evidence[key]
        if not isinstance(items, list) or not items:
            raise ReportError(f"{key}: at least one evidence record is required, including failures and skips")
        if not check_paths:
            _historical_evidence(items)
        if check_paths and key == "unit_tests_runnable" and numerator is not None and numerator > 0:
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
    identity = repository["identity"]
    # A relative origin such as ../shared.git is not a network identity. Fold
    # the resolved root into the key so two local checkouts cannot share state.
    material = identity if "://" in identity else identity + "\0" + str(Path(repository["root"]).resolve())
    key = hashlib.sha256(material.encode()).hexdigest()[:24]
    return ensure_external_output(repository["root"], base / key)


def latest_report(repository: dict, state_dir: str | Path | None) -> dict | None:
    directory = _repo_state(repository, state_dir)
    pointer = directory / "latest.json"
    legacy = False
    if not pointer.exists():
        # Before root-scoped local identities, state was keyed by origin alone.
        # Recover only the current checkout's old pointer, with a root check
        # below before exposing any report. Never migrate or overwrite it here.
        identities = [repository["identity"], repository.get("origin")]
        root = Path(repository["root"])
        if root.is_dir():
            remote = _git(root, "config", "--get", "remote.origin.url", optional=True)
            if not remote:
                remotes = _git(root, "remote").decode("utf-8", "surrogateescape").splitlines()
                if remotes:
                    remote = _git(root, "config", "--get", f"remote.{sorted(remotes)[0]}.url", optional=True)
            if remote:
                identities.append(canonicalize_origin(os.fsdecode(remote).strip()))
        base = ensure_external_output(repository["root"], state_dir or default_state_dir())
        for identity in dict.fromkeys(identities):
            if not identity:
                continue
            candidate = base / hashlib.sha256(identity.encode()).hexdigest()[:24]
            if (candidate / "latest.json").exists():
                directory, pointer, legacy = candidate, candidate / "latest.json", True
                break
        else:
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
    if not isinstance(metadata, dict) or data.get("rubricVersion") != RUBRIC_VERSION:
        raise ReportError("Stored report belongs to a different repository or rubric")
    if not legacy and metadata.get("identity") != repository["identity"]:
        raise ReportError("Stored report belongs to a different repository or rubric")
    if legacy or "://" not in repository["identity"] or repository["identity"].startswith("file:"):
        recorded_root = metadata.get("root", "")
        try:
            same_root = Path(recorded_root).resolve() == Path(repository["root"]).resolve()
        except (OSError, RuntimeError, ValueError):
            same_root = False
        if not isinstance(recorded_root, str) or not same_root:
            if legacy:
                return None
            raise ReportError("Stored report belongs to a different repository or rubric")
        if legacy and metadata.get("identity") != identity:
            raise ReportError("Stored report belongs to a different repository or rubric")
    _validate_structure(data, Path(data["repository"]["root"]), check_paths=False)
    # Scores are derived, never trusted from editable report JSON.
    data["summary"] = score_report(data["report"])
    return data


def _escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
        .replace("|", "\\|")
    )


def _format_score_percent(percent: float, level: int) -> str:
    """Keep a value just below a level boundary from rounding up to that boundary."""
    thresholds = {1: 20.0, 2: 40.0, 3: 60.0, 4: 80.0}
    boundary = thresholds.get(level)
    text = f"{percent:.1f}"
    if boundary is None or not (percent < boundary and text == f"{boundary:.1f}"):
        return text
    finer = f"{percent:.2f}"
    if float(finer) < boundary:
        return finer
    return f"{boundary - 0.1:.1f}"


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
    percent = summary["scorePercent"]
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        shown_percent = percent
    else:
        shown_percent = _format_score_percent(float(percent), int(summary["achievedLevel"]))
    lines = [
        "# Level", "", f"Level {summary['achievedLevel']} · {shown_percent}%", "",
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


def _repository_field_matches(key: str, previous: dict, current: dict) -> bool:
    if previous.get(key) == current.get(key):
        return True
    # /tmp and /private/tmp are the same checkout. A stored alias is not a new root.
    if key != "root" or not isinstance(previous.get(key), str) or not isinstance(current.get(key), str):
        return False
    try:
        return Path(previous[key]).resolve() == Path(current[key]).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _same_directory_root(target: str | Path, repository: dict) -> dict:
    """Keep the caller's absolute path when it is only a symlink alias of the snapshot root.

    macOS ``/tmp`` and ``/private/tmp`` are the same directory. Reports should
    retain the path the operator passed so a later read matches that string.
    """
    requested = Path(target).expanduser()
    recorded = repository.get("root")
    if not requested.is_absolute() or not isinstance(recorded, str):
        return repository
    try:
        same = requested.resolve() == Path(recorded).resolve()
    except (OSError, RuntimeError, ValueError):
        return repository
    if not same or str(requested) == recorded:
        return repository
    return dict(repository, root=str(requested))


def store_report(data: dict, target: str | Path, state_dir: str | Path | None = None) -> dict:
    repository = _same_directory_root(target, validate_evaluation(data, target))
    directory = _repo_state(repository, state_dir)
    previous = latest_report(repository, state_dir)
    stored = dict(data, repository=repository, summary=score_report(data["report"]))
    # Never trust a comparison supplied in an agent's input document.
    stored.pop("comparison", None)
    if previous is not None:
        stored["comparison"] = _comparison(stored, previous)
    stored["createdAt"] = datetime.now(timezone.utc).isoformat()
    stored["id"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    stored["validatorVersion"] = __version__
    markdown = render_markdown(stored, previous)
    # Check once more immediately before committing local state.
    if inspect_repository(target)["fingerprint"] != repository["fingerprint"]:
        raise ReportError("Repository changed during report validation")
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    json_path = directory / f"{stored['id']}.json"
    md_path = directory / f"{stored['id']}.md"
    _atomic_write(json_path, json_text(stored))
    _atomic_write(md_path, markdown)
    _atomic_write(directory / "latest.json", json_text({"report": json_path.name, "sha256": hashlib.sha256(json_path.read_bytes()).hexdigest()}))
    return {"report": str(json_path), "markdown": str(md_path), "summary": stored["summary"]}


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    os.chmod(path, 0o600)
