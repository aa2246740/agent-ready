"""Bounded command syntax checks for captured test-discovery records.

These checks never execute commands and do not authenticate captured output or
prove arbitrary package scripts behave as described by their evaluator.
"""

from pathlib import Path
import re
import shlex


def python_collection_file(words: list[str], *, addopts: str = "") -> str | None:
    """Return the sole literal pytest file, or reject unsupported scope."""
    args = words[1:]
    if Path(words[0]).name != "pytest":
        while args and args[0] in {"-B", "-I", "-E", "-s"}:
            args = args[1:]
        if args[:2] != ["-m", "pytest"]:
            return None
        args = args[2:]
    try:
        args = shlex.split(addopts) + args
    except ValueError:
        return None
    value_options = {
        "-k", "-m", "-p", "-c", "--config-file", "-o", "--override-ini", "--rootdir",
        "--confcutdir", "--import-mode", "--tb", "--capture", "--color",
        "--code-highlight", "--maxfail", "--durations", "--durations-min",
        "--ignore", "--ignore-glob", "--deselect", "--log-level",
    }
    switches = {"--collect-only", "--co", "--quiet", "--verbose", "-s", "-x",
                "--disable-warnings", "--no-header", "--no-summary", "--noconftest",
                "--strict-config", "--strict-markers", "--continue-on-collection-errors"}
    files, collect, index, positional = [], False, 0, False
    while index < len(args):
        token = args[index]
        index += 1
        if token == "--" and not positional:
            positional = True
            continue
        if not positional and token.startswith("-"):
            key, equal, value = token.partition("=")
            if key in {"--collect-only", "--co"}:
                if equal and value.lower() != "true":
                    return None
                collect = True
            elif key in value_options:
                if not equal:
                    if index == len(args) or args[index].startswith("-"):
                        return None
                    value = args[index]
                    index += 1
                if key in {"-o", "--override-ini"} and value.split("=", 1)[0].strip() == "addopts":
                    return None
            elif any(token.startswith(flag) and token != flag for flag in ("-k", "-m", "-p", "-c", "-o")):
                if token.startswith("-o") and token[2:].split("=", 1)[0].strip() == "addopts":
                    return None
            elif token not in switches and not re.fullmatch(r"-[qv]+", token):
                return None
            continue
        path = token.split("::", 1)[0]
        if (not path.endswith(".py") or any(c in path for c in "*?[]{}")
                or any(part in {"..", ".git"} for part in Path(path).parts)):
            return None
        files.append(path)
    return files[0] if collect and len(files) == 1 else None


def python_collection_matches_file(selected: str, output: str) -> bool:
    """Reject explicit collection paths contradicting the selected file.

    Pytest settings can add paths beyond argv. Module/nodeid output gives a
    bounded cross-check; count-only output cannot prove hidden plugin behavior.
    """
    clean = re.sub(r"\x1b\[[0-9;]*m", "", output)
    paths = re.findall(r"<Module\s+([^>]+\.py)>", clean)
    paths += re.findall(r"(?m)^\s*([^\s<>]+\.py)(?:::|:\s*\d+\s*$)", clean)
    expected = Path(selected).parts
    for path in paths:
        actual = Path(path).parts
        # Pytest may print paths relative to its rootdir instead of the command
        # cwd. Keep compatible suffixes, but do not collapse two different
        # visible parent paths merely because their filenames match.
        if not (expected[-len(actual):] == actual or actual[-len(expected):] == expected):
            return False
    return True


def javascript_collection(words: list[str]) -> bool:
    """Distinguish package dependency listing from a test runner's list mode."""
    executable = Path(words[0]).name
    args = words[1:]
    # Package-manager routing options precede the actual command. The scope
    # validator independently checks where these selectors point.
    value_options = {"--prefix", "--cwd", "--dir", "--directory", "-C",
                     "--workspace", "-w", "--filter", "-F", "--cache", "--registry"}
    while args and args[0].startswith("-"):
        option, equal, _ = args[0].partition("=")
        if option == "--":
            args = args[1:]
            break
        consume = 2 if option in value_options and not equal else 1
        args = args[consume:]
    if executable == "yarn" and args[:1] == ["workspace"]:
        args = args[2:]
    if executable in {"jest", "vitest"}:
        runner = executable
        runner_args = words[1:]
    else:
        if not args:
            return False
        operation, *runner_args = args
        explicit_script = operation in {"run", "run-script"}
        if operation in {"exec", "x", "run", "run-script"}:
            while runner_args and (runner_args[0] == "--" or runner_args[0].startswith("-")):
                runner_args = runner_args[1:]
            if not runner_args:
                return False
            operation, *runner_args = runner_args
        # Package metadata commands can accept an unknown list flag and exit
        # zero (for example `npm root --listTests`). Only test entry points or
        # explicit package scripts qualify; script behavior still needs evidence.
        if not explicit_script and operation not in {"test", "t", "tst", "jest", "vitest"}:
            return False
        runner = operation
    if runner == "vitest" and runner_args[:1] == ["list"]:
        return True
    # A literal `list` alone is never evidence for npm/pnpm/yarn dependency
    # commands, or for an unknown package script.
    return any(arg in {"--listTests", "--list-tests", "--list", "--collect-only"}
               for arg in runner_args)


def java_test_execution(words: list[str], output: str) -> bool:
    """Recognize the native single-class audit contract in captured records."""
    executable = Path(words[0]).name
    if any(word in words for word in ("--dry-run", "-m", "--tasks", "tasks", "help", "compile", "test-compile")):
        return False
    # Maven accepts attached and separate property forms. Count every test
    # selector, including aliases, so a later unbounded value cannot override it.
    properties = []
    for index, word in enumerate(words):
        if word in {"-D", "--define"}:
            properties.append(words[index + 1] if index + 1 < len(words) else "")
        elif word.startswith("-D"):
            properties.append(word[2:])
        elif word.startswith("--define="):
            properties.append(word.split("=", 1)[1])
    # Explicit false is not a skip; unrelated property names are not skip flags.
    for prop in properties:
        key, separator, value = prop.partition("=")
        if key in {"skipTests", "maven.test.skip"} and (not separator or value.lower() != "false"):
            return False
    selectors = []
    if executable.startswith("gradle"):
        if "--rerun-tasks" not in words:
            return False
        test_tasks = [word for word in words if word == "test" or word.endswith(":test")]
        if len(test_tasks) != 1:
            return False
        for index, word in enumerate(words):
            if word == "--tests":
                selectors.append(words[index + 1] if index + 1 < len(words) else "")
            elif word.startswith("--tests="):
                selectors.append(word.split("=", 1)[1])
    else:
        if "test" not in words or "--rerun-tasks" in words:
            return False
        selectors = [prop.partition("=")[2] for prop in properties if prop.partition("=")[0] == "test"]
    # A wildcard, comma-separated list, regex or repeated filter can select the
    # full suite. Native auditing asks for one concrete existing test class.
    if len(selectors) != 1 or not re.fullmatch(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*", selectors[0]):
        return False
    if executable.startswith("gradle"):
        task = ":" + test_tasks[0].lstrip(":")
        task_lines = re.findall(r"(?m)^\s*>?\s*Task\s+" + re.escape(task) + r"(?=\s|$)([^\n]*)", output)
        observed_tasks = re.findall(r"(?m)^\s*>?\s*Task\s+(:\S+)", output)
        if any(name != task and name.lower().endswith("test") for name in observed_tasks):
            return False
        return bool(task_lines) and not any(re.search(r"UP-TO-DATE|FROM-CACHE|SKIPPED|NO-SOURCE|FAILED", line) for line in task_lines)
    running = re.findall(r"(?m)^\s*(?:\[INFO\]\s*)?Running\s+([\w.$]+)\s*$", output)
    selected = selectors[0]
    for name in running:
        outer = name.split("$", 1)[0]
        if not (outer == selected or outer.endswith("." + selected) or selected.endswith("." + outer)):
            return False
    counts = re.findall(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)", output)
    if not counts or any(int(failed) or int(errors) for _, failed, errors, _ in counts):
        return False
    if re.search(r"(?m)^\s*(?:\[(?:INFO|ERROR)\]\s*)?BUILD FAILURE\b", output):
        return False
    return any(int(run) > int(skipped) for run, _, _, skipped in counts)
