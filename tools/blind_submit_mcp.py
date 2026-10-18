#!/usr/bin/env python3
"""Narrow stdio MCP: validate/store one blind evaluation with a task budget.

All filesystem destinations and the target snapshot come from controller config.
The model supplies JSON content only. No shell, filesystem or network tool is
exposed. The existing readiness validator and scorer remain authoritative.
"""
from __future__ import annotations

import argparse
import json
import os
import math
import re
from pathlib import Path
import sys
import time

try:
    import fcntl
except ImportError:  # non-POSIX
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_readiness.catalog import criteria_by_id
from agent_readiness.condition_evidence import condition_errors
from agent_readiness.report import ReportError, json_text, read_json, store_report, validate_evaluation
from agent_readiness.validation import Problems, ValidationError

MAX_BYTES = 512_000
TOP_KEYS = {"schemaVersion", "rubricVersion", "repository", "apps", "report", "evidence", "actionItems"}
TOOL = {
    "name": "submit_evaluation",
    "description": "Submit the COMPLETE 84-criterion evaluation JSON for immediate strict validation and local storage. Returns validation and file-read errors for correction within this SAME session. The controller configures any task budget; remainingSubmissions=null means no submission cap. STOP after accepted=true or stop=true. No arbitrary paths, execution or network access. Does not invent or fill judgments/evidence.",
    "inputSchema": {"type": "object", "properties": {"evaluation_json": {"type": "string", "description": "Complete JSON object with schemaVersion, rubricVersion, unchanged repository, apps, all 84 report entries, all 84 evidence entries, actionItems. No Markdown."}}, "required": ["evaluation_json"], "additionalProperties": False},
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
}


def _exclusive_lock(handle):
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return
    try:
        import msvcrt
    except ImportError as error:
        raise OSError("No supported submission lock is available") from error
    handle.seek(0)
    if not handle.read(1):
        handle.write("0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _private_dir(path, *, exist_ok=True):
    path.mkdir(parents=True, exist_ok=exist_ok)
    path.chmod(0o700)


def save(path, data):
    serialized = json_text(data)
    path = Path(path)
    _private_dir(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(serialized)
    path.chmod(0o600)


def _request_record(payload):
    """Persist arguments only when the serialized record stays within MAX_BYTES."""
    try:
        raw = json_text(payload)
    except (TypeError, ValueError):
        return payload
    if len(raw.encode("utf-8")) > MAX_BYTES:
        return {"reason": "payload_too_large", "bytes": len(raw.encode("utf-8"))}
    return payload


def diagnostics(data):
    ids = set(criteria_by_id())
    result = {}
    if not isinstance(data, dict):
        return result
    for field in ("report", "evidence"):
        obj = data.get(field)
        result[field] = {"count": len(obj) if isinstance(obj, dict) else None,
                         "missing": sorted(ids - set(obj)) if isinstance(obj, dict) else sorted(ids),
                         "extra": sorted(set(obj) - ids) if isinstance(obj, dict) else []}
    evidence = data.get("evidence")
    if isinstance(evidence, dict):
        result["emptyEvidence"] = [k for k, v in evidence.items() if not isinstance(v, list) or not v]
    report = data.get("report")
    if isinstance(report, dict):
        invalid = []
        for key, row in report.items():
            rationale = row.get("rationale") if isinstance(row, dict) else None
            try:
                size = len(rationale.encode("utf-16-le")) // 2 if isinstance(rationale, str) else None
            except UnicodeEncodeError:
                size = None
            if not isinstance(rationale, str) or not rationale.strip() or size is None or size > 500:
                invalid.append({"criterionId": key, "utf16CodeUnits": size, "maximum": 500})
        result["invalidRationales"] = invalid
    return result


def _section_end(value, later_paths, start):
    """End of this section: the next requested path header, not an internal '---'."""
    end = len(value)
    for path in later_paths:
        for marker in (f"\n---\n{path}:\n", f"\n---\n{path}: Error"):
            found = value.find(marker, start)
            if found != -1 and found < end:
                end = found
    return end


def _multiple_sections(value, paths):
    """Split a read_multiple_files result without trusting markers inside file text.

    Sections are joined by '\\n---\\n' in request order. A header counts only at the
    start of the value or immediately after that separator. An earlier file's body
    may contain '\\n---\\n' (YAML frontmatter) or another path followed by ':\\n'.
    """
    if not isinstance(value, str):
        return []
    usable = [path for path in paths if isinstance(path, str)]
    # A file body can reproduce the complete separator plus a later header.
    # Text framing cannot distinguish those from real headers; ambiguous results
    # must be read individually rather than credited as successful reads.
    if len(usable) != len(set(usable)):
        return []
    framed = "\n---\n" + value
    for path in usable:
        header = re.compile(r"\n---\n" + re.escape(path) + r":(?:\n| Error)")
        if len(header.findall(framed)) > 1:
            return []
    found = []
    cursor = 0
    for index, path in enumerate(usable):
        success = f"{path}:\n"
        error = f"{path}: Error"
        if value.startswith(success, cursor):
            start = cursor + len(success)
            end = _section_end(value, usable[index + 1:], start)
            body = value[start:end]
            if body.strip() and not body.startswith("Error:"):
                found.append((path, body))
            cursor = end + len("\n---\n") if value.startswith("\n---\n", end) else end
        elif value.startswith(error, cursor):
            end = _section_end(value, usable[index + 1:], cursor)
            cursor = end + len("\n---\n") if value.startswith("\n---\n", end) else end
        else:
            break
    return found


class Gate:
    def __init__(self, config):
        self.config = config
        self.output = Path(config["output"]).resolve()
        self.target = Path(config["target"]).resolve()
        self.state = Path(config["state_dir"]).resolve()
        # Reject before mkdir so a path inside the target never creates a directory there.
        inside = self.output == self.target or self.output.is_relative_to(self.target)
        inside = inside or self.state == self.target or self.state.is_relative_to(self.target)
        if inside:
            raise ValueError("Submission artifacts and state must be outside the target")
        _private_dir(self.output)
        self.template = read_json(config["template"])
        if self.template["repository"]["root"] != str(self.target):
            raise ValueError("Controller target/template mismatch")

    def read_contents(self):
        """Evidence provenance from the current session only, never prior logs."""
        calls, contents = {}, {}
        trace = Path(self.config["trace"])
        if not trace.exists():
            return contents
        for line in trace.read_text().splitlines():
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue  # Current last line may still be being streamed.
            if not isinstance(row, dict) or not isinstance(row.get("id"), (str, int)):
                continue
            if row.get("type") == "tool_call":
                calls[row.get("id")] = row
            elif row.get("type") == "tool_result" and not row.get("isError"):
                call = calls.get(row.get("id"), {})
                if not isinstance(call, dict):
                    continue
                tool = call.get("toolName")
                if tool not in {"blind___read_text_file", "blind___read_multiple_files"}:
                    continue
                args = call.get("parameters", {})
                if not isinstance(args, dict):
                    continue
                body = row.get("value", "")
                if not body and isinstance(row.get("content"), list):
                    body = "\n".join(part["text"] for part in row["content"]
                                     if isinstance(part, dict) and isinstance(part.get("text"), str))
                if not isinstance(body, str):
                    continue
                if tool == "blind___read_multiple_files":
                    raw_paths = args.get("paths", [])
                    if not isinstance(raw_paths, list):
                        continue
                    for path, section in _multiple_sections(body, raw_paths):
                        if path.startswith("/input/"):
                            contents.setdefault(path[len("/input/"):], []).append(section)
                    continue
                path = args.get("path")
                if isinstance(path, str) and path.startswith("/input/") and body.strip():
                    contents.setdefault(path[len("/input/"):], []).append(body)
        return contents

    def read_paths(self):
        return set(self.read_contents())

    def missing_quotes(self, data):
        """A partial file read must not stand in for unseen condition evidence."""
        evidence = data.get("evidence") if isinstance(data, dict) else None
        if not isinstance(evidence, dict):
            return []
        contents = self.read_contents()
        missing = []
        for key, records in evidence.items():
            for item in records if isinstance(records, list) else []:
                if not isinstance(item, dict) or item.get("kind") != "file":
                    continue
                support = item.get("support")
                if not isinstance(support, dict) or not isinstance(support.get("quote"), str):
                    continue
                path = item.get("path")
                if isinstance(path, str) and not any(support["quote"] in text for text in contents.get(path, [])):
                    missing.append({"criterionId": key, "path": path, "condition": support.get("condition")})
        return missing

    def budget(self):
        """Limits are task configuration, not readiness scoring rules."""
        configured = read_json(self.config["budget"])
        if not isinstance(configured, dict):
            raise ValueError("Task budget must be a JSON object")
        deadline = configured.get("deadlineEpoch")
        maximum = configured.get("maxSubmissions", 3)  # Legacy task configs.
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError("deadlineEpoch must be a finite number or null")
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ValueError("maxSubmissions must be a positive integer or null")
        return deadline, maximum

    def missing_reads(self, data):
        """Return all missing file reads, so correction does not stop at one."""
        evidence = data.get("evidence") if isinstance(data, dict) else None
        if not isinstance(evidence, dict):
            return []
        paths = self.read_paths()
        return [{"criterionId": key, "path": item["path"]}
                for key, records in evidence.items() if isinstance(records, list)
                for item in records if isinstance(item, dict) and item.get("kind") == "file"
                and isinstance(item.get("path"), str) and item["path"] not in paths]

    def submit(self, arguments):
        # Lock and persistent attempt reservation prevent parallel calls/restarts
        # from resetting or exceeding the limit. Never count only successes.
        lock_path = self.output / ".lock"
        with lock_path.open("a+") as lock:
            lock_path.chmod(0o600)
            _exclusive_lock(lock)
            done = self.output / "accepted.json"
            numbers = [int(match.group(1)) for path in self.output.iterdir()
                       if path.is_dir() and (match := re.fullmatch(r"attempt-(\d+)", path.name))]
            used = len(numbers)
            if done.exists():
                return {"accepted": True, "stop": True, "reason": "already_accepted", "attemptsUsed": used}
            deadline, maximum = self.budget()
            expired = lambda: deadline is not None and time.time() >= deadline
            remaining = lambda: None if maximum is None else max(0, maximum-used)
            if expired() or (maximum is not None and used >= maximum):
                return {"accepted": False, "stop": True, "reason": "deadline_exceeded" if expired() else "submission_budget_exhausted", "attemptsUsed": used, "remainingSubmissions": remaining()}
            number = max(numbers, default=0) + 1
            used += 1
            attempt = self.output / f"attempt-{number:02d}"
            _private_dir(attempt, exist_ok=False)
            data = None
            try:
                # Size the payload before any unbounded request.json write.
                save(attempt / "request.json", _request_record(arguments))
                if not isinstance(arguments, dict) or set(arguments) != {"evaluation_json"}:
                    raise ValueError("Only evaluation_json is accepted; paths and commands are not arguments")
                text = arguments["evaluation_json"]
                if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
                    raise ValueError("evaluation_json must be a UTF-8 JSON string of at most 512000 bytes")
                evaluation = attempt / "evaluation.json"
                evaluation.write_text(text, encoding="utf-8")
                evaluation.chmod(0o600)
                data = read_json(evaluation)  # Existing duplicate/nonfinite checks.
                problems = Problems()
                if set(data) != TOP_KEYS:
                    problems.add("Expected exactly these top-level keys: " + ", ".join(sorted(TOP_KEYS)) + "; use the prepared evaluation fields")
                if data.get("repository") != self.template["repository"]:
                    problems.add("repository must equal the prepared snapshot exactly; do not change its paths or identity")
                if isinstance(data.get("apps"), dict):
                    for path, app in data["apps"].items():
                        if not isinstance(app, dict) or set(app) != {"description"}:
                            problems.add(f"apps[{path!r}]: Each app accepts only description; remove extra fields")
                if isinstance(data.get("evidence"), dict):
                    for key, records in data["evidence"].items():
                        if not isinstance(records, list):
                            continue  # Full validator supplies the shape error.
                        for index, record in enumerate(records):
                            if not isinstance(record, dict) or record.get("kind") != "file":
                                problems.add(f"{key} evidence[{index}].kind: this read-only session permits only file evidence; command evidence is unavailable; cite an observed file")
                            elif set(record) - {"kind", "path", "note", "line", "support"}:
                                problems.add(f"{key} evidence[{index}]: file evidence accepts only kind, path, note, optional line and support; remove extra fields")
                # The same collected list as CLI validation, plus this
                # file-only session's provenance/transport constraints.
                try:
                    validate_evaluation(data, self.target)
                except ValidationError as error:
                    problems.extend(error)
                contents = self.read_contents()
                evidence = data.get("evidence")
                for key, records in evidence.items() if isinstance(evidence, dict) else []:
                    for index, record in enumerate(records if isinstance(records, list) else []):
                        if not isinstance(record, dict) or record.get("kind") != "file":
                            continue
                        path = record.get("path")
                        if not isinstance(path, str):
                            continue
                        if path not in contents:
                            problems.add(f"{key} evidence[{index}].path: File evidence not read successfully in this session: {path}; read the actual file before citing it")
                        support = record.get("support")
                        if isinstance(support, dict) and isinstance(support.get("quote"), str) and not any(support["quote"] in text for text in contents.get(path, [])):
                            problems.add(f"{key} evidence[{index}].support.quote: Required-condition quotes not read in this session: {path}; read the cited lines before quoting them")
                problems.raise_if_any(ReportError)
                if expired():
                    raise ValueError("deadline_exceeded_before_storage")
                receipt = store_report(data, self.target, self.state)
                response = {"accepted": True, "stop": True, "attempt": number, "remainingSubmissions": remaining(),
                            "criteria": 84, "evidenceCriteria": 84, "validation": "existing_strict_validator_and_current_session_file_reads_passed", **receipt}
                save(attempt / "response.json", response)
                save(done, response)
                return response
            except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
                detail = diagnostics(data)
                detail["problems"] = error.problems if isinstance(error, ReportError) else [str(error)]
                detail["problemCount"] = error.problem_count if isinstance(error, ReportError) else 1
                detail["omittedProblemCount"] = error.omitted_count if isinstance(error, ReportError) else 0
                detail["unreadFileEvidence"] = self.missing_reads(data)
                detail["unreadConditionQuotes"] = self.missing_quotes(data)
                detail["conditionEvidence"] = condition_errors(data, self.target) if isinstance(data, dict) else []
                response = {"accepted": False, "stop": (maximum is not None and used >= maximum) or expired(),
                            "attempt": number, "remainingSubmissions": remaining(),
                            "error": str(error), "diagnostics": detail,
                            "instruction": "Correct the complete JSON using actual evidence and resubmit within this same session only if stop=false. No defaults or fabricated judgments."}
                save(attempt / "response.json", response)
                return response


def serve(config):
    gate = Gate(config)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid MCP request"}}), flush=True)
                continue
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "blind-submission-gate", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": [TOOL]}
            elif method == "ping":
                result = {}
            elif method == "tools/call":
                params = request.get("params")
                if not isinstance(params, dict):
                    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": "Invalid params"}}), flush=True)
                    continue
                if params.get("name") != TOOL["name"]:
                    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method/tool not available"}}), flush=True)
                    continue
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    arguments = {}
                try:
                    answer = gate.submit(arguments)
                except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
                    # A controller/configuration failure must reach the caller
                    # with its request id, rather than silently closing stdio.
                    answer = {"accepted": False, "stop": True,
                              "reason": "controller_configuration_error",
                              "error": str(error)}
                visible = {k: v for k, v in answer.items() if k not in {"summary"}}
                result = {"content": [{"type": "text", "text": json.dumps(visible, ensure_ascii=False)}], "isError": not answer["accepted"]}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method/tool not available"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except (ValueError, TypeError, KeyError) as error:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid MCP request"}}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    # No credentials are required by this deterministic local broker.
    for name in list(os.environ):
        if name.startswith("READINESS_MODEL_") or name.startswith("FACTORY_"):
            os.environ.pop(name)
    serve(json.loads(args.config.read_text()))
