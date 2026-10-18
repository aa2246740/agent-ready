"""Required-condition evidence for two native rules, not a new scoring rubric.

These checks bind explicit claims to source excerpts. They do not authenticate
remote history or prove the semantics of arbitrary code/configuration. The host
agent must still review provenance, enabled state and the actual causal chain.
Historical reports retain their original validation contract.
"""

from pathlib import Path
import re
from urllib.parse import urlsplit

from .repository import validate_evidence


CONDITIONS = {
    "dependency_update_automation": {
        "dependency_configuration": ("updater",),
        "dependency_pull_request": ("updater", "url", "author", "dependency"),
    },
    "alerting_configured": {
        "alert_rule": ("handler", "notifier"),
        "automatic_trigger": ("kind", "registration", "handler"),
        "alert_delivery": ("notifier",),
    },
}

INSTRUCTIONS = """### Required-condition evidence (local submission extension)

For a positive dependency_update_automation or alerting_configured judgment,
attach support objects to the relevant evidence records. Each support is
{"condition":"<condition below>","quote":"verbatim source excerpt","facts":{...}}.
Use one support per record; repeat a file record for different conditions.
For alerting_configured in a multi-application repository, each support also
needs application: <exact apps key>. Each counted passing application needs its
own connected chain; evidence for one app must not be counted for another.
For a single application this field may be omitted. A shared service can cover
multiple apps only when its actual wiring supports each, cited separately.
File quotes must occur starting on the cited line (line is required here);
command quotes must occur in actual successful captured output. Every fact
except trigger kind must occur literally in that quote. Never invent captures,
PRs, enabled state, quotes, or operational history to satisfy this contract.

dependency_update_automation requires BOTH:
- dependency_configuration: facts {"updater":"name as present in source"}.
- dependency_pull_request: facts {"updater":"same name", "url":"actual PR URL",
  "author":"actual author", "dependency":"actual dependency name"}. Cite an
  observed dependency-update PR created by that updater for THIS repository.
  A config, template, example, planned PR or manually created unrelated PR is
  insufficient. Match configuration to the PR author and actual dependency
  change. A merged PR, auto-merge, deployment and default-branch proof are NOT
  additional native requirements. Existing authentic records are usable;
  missing history/access is not evidence of a PR and does not permit a skip.

alerting_configured requires a CONNECTED automatic notification chain:
- alert_rule: facts {"handler":"check/handler identity", "notifier":"sender identity"}.
- automatic_trigger: facts {"kind":"schedule|event|process|service",
  "registration":"actual enabled registration excerpt", "handler":"same handler"}.
- alert_delivery: facts {"notifier":"same sender"}.
Trace an enabled scheduler/event/subscription/startup registration through the
rule to its configured notification destination. Source wiring or captured
active configuration may establish this; no particular vendor or live incident
is required. A monitor script, commented/planned/disabled trigger, manual
workflow_dispatch alone, manual --once command or one successful test message
does NOT establish the automatic trigger. Do not relabel a manual trigger as an
automatic kind. Facts must describe actual source relationships, not keywords.

The validator checks required conditions, exact quotes and matching identities;
it cannot authenticate external captures or prove arbitrary code is active.
The evaluating agent remains responsible for semantic/provenance review. If a
required fact cannot be substantiated, explain the limitation and judge using
the native rule; never fabricate evidence or silently default the numerator.
Submission errors enumerate both criteria's gaps for correction in the SAME
session. Historical stored reports are read without retroactive revalidation.

Preserve native boundaries elsewhere: a wired custom permanent kill switch
can meet feature_flag_infrastructure; documented flag lifecycle/cleanup is
expressly sufficient for dead_feature_flag_detection when its prerequisite
passes. release_automation checks an automated pipeline, including approval or
manual dispatch followed by automated steps; it does NOT require a historical
successful deployment. Product analytics can be page analytics when actually
wired into the application; do not invent a minimum event count or claim live
ingestion from configuration. Explain provider-equivalence interpretations.
"""


def _quoted_source(item: dict, root: Path, quote: str) -> bool:
    # Reuse the normal repository containment/snapshot checks BEFORE reading.
    validate_evidence(root, item)
    if item["kind"] == "command":
        return item["exitCode"] == 0 and quote in item["output"]
    line = item.get("line")
    if type(line) is not int or line < 1:
        return False
    count = len(quote.splitlines())
    with (root / item["path"]).open(encoding="utf-8") as source:
        excerpt = []
        for number, text in enumerate(source, 1):
            if number >= line:
                excerpt.append(text)
            if number >= line + count - 1:
                break
    return quote in "".join(excerpt)


_HASH_SUFFIXES = {
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".cnf", ".properties", ".env", ".sh", ".bash", ".zsh",
    ".py", ".rb", ".pl", ".r", ".nix", ".ps1", ".mk", ".mak", ".cron", ".crontab", ".service", ".timer", ".socket",
}
_HASH_NAMES = ("dockerfile", "containerfile", "makefile", "gnumakefile", "crontab", "procfile", "gemfile", "rakefile", "justfile", ".env")
_C_LIKE_SUFFIXES = {
    ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".kts", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".rs", ".swift", ".scala", ".groovy", ".gradle", ".dart", ".jsonc", ".json5",
}
_MARKUP_SUFFIXES = {".html", ".htm", ".xhtml", ".xml", ".md", ".markdown", ".vue", ".svelte"}
_HASH, _SLASH, _BLOCK, _MARKUP = "#", "//", "/*", "<!--"


def _comment_syntax(path: str | None) -> frozenset[str]:
    """Comment markers of the cited file's type; unknown types and commands have none."""
    if not path:
        return frozenset()
    file = Path(path)
    name, suffix = file.name.lower(), file.suffix.lower()
    if suffix in _C_LIKE_SUFFIXES or name == "jenkinsfile":
        return frozenset({_SLASH, _BLOCK})
    if suffix in {".tf", ".tfvars", ".hcl", ".php"}:
        return frozenset({_HASH, _SLASH, _BLOCK})
    if suffix in _MARKUP_SUFFIXES:
        return frozenset({_MARKUP})
    if suffix in _HASH_SUFFIXES or name.startswith(_HASH_NAMES) or name.endswith(".dockerfile"):
        return frozenset({_HASH})
    return frozenset()


def _comment_end(quote: str, index: int, syntax: frozenset[str]) -> int | None:
    """End offset of a comment starting at ``index``, or None if none starts there."""
    for opener, closer in ((_BLOCK, "*/"), (_MARKUP, "-->")):
        if opener in syntax and quote.startswith(opener, index):
            close = quote.find(closer, index + len(opener))
            return len(quote) if close < 0 else close + len(closer)
    line_comment = (
        (_SLASH in syntax and quote.startswith("//", index) and quote[index - 1:index] != ":")
        or (_HASH in syntax and quote[index] == "#" and (index == 0 or quote[index - 1].isspace()))
    )
    if line_comment:
        stop = quote.find("\n", index)
        return len(quote) if stop < 0 else stop
    return None


def _comment_mask(quote: str, path: str | None = None) -> list[bool]:
    """Mark characters of ``quote`` that are comments in the cited file's syntax.

    Markers inside quoted strings do not start a comment. Strings end at the
    line end; a quote only opens a string when it does not follow a letter or
    digit (so an apostrophe inside a word is text). Block and HTML comments
    span lines and stay open to the end of an excerpt that does not close them.
    """
    syntax = _comment_syntax(path)
    mask = [False] * len(quote)
    if not syntax:
        return mask
    strings = "" if _MARKUP in syntax else "\"'`" if _SLASH in syntax else "\"'"
    index, string = 0, None
    while index < len(quote):
        char = quote[index]
        if char == "\n":
            string = None
        elif string:
            if char == "\\" and string != "'":
                index += 1
            elif char == string:
                string = None
        elif char in strings and (index == 0 or not quote[index - 1].isalnum()):
            string = char
        else:
            stop = _comment_end(quote, index, syntax)
            if stop is not None:
                mask[index:stop] = [True] * (stop - index)
                index = stop
                continue
        index += 1
    return mask


# Manual or disabled triggers are recognized by structure only: a workflow
# whose `on:` block has just workflow_dispatch, an explicit --once flag, or an
# `enabled: false` key in the registration's own block. Free words such as
# "manual" or "planned" in names and values say nothing about the trigger.
_ONCE = re.compile(r"(?<![\w-])--once(?![\w-])")
_ENABLED_FALSE = re.compile(r"(?<![\w-])[\"']?enabled[\"']?\s*[:=]\s*[\"']?false\b", re.I)
_DISPATCH = re.compile(r"(?<![\w.-])workflow_dispatch\b")
_AUTOMATIC_EVENT = re.compile(
    r"(?<![\w.-])(?:schedule|cron|push|pull_request|pull_request_target|pull_request_review|pull_request_review_comment"
    r"|release|workflow_run|workflow_call|repository_dispatch|issues|issue_comment|create|delete|deployment"
    r"|deployment_status|discussion|discussion_comment|fork|gollum|label|merge_group|milestone|page_build|public"
    r"|registry_package|status|watch|check_run|check_suite|branch_protection_rule)[\"']?\s*(?:[:,\]}]|$)",
    re.M,
)
_ON_KEY = re.compile(r"\s*[\"']?on[\"']?\s*:")
_SECTION = re.compile(r"\s*\[[^\[\]\n]*\]\s*$")  # INI/TOML table header


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _own_block(lines: list[str], index: int) -> tuple[int, int]:
    """Line range of ``lines[index]`` and everything nested under it."""
    base, header = _indent(lines[index]), _SECTION.match(lines[index]) is not None
    end = index + 1
    while end < len(lines) and not (
            lines[end].strip() and (_SECTION.match(lines[end]) if header else _indent(lines[end]) <= base)):
        end += 1
    return index, end


def _sibling_range(lines: list[str], index: int) -> tuple[int, int, int | None]:
    """Lines sharing ``lines[index]``'s parent, and the parent line if it is in view."""
    base = _indent(lines[index])

    def leaves(text: str) -> bool:
        return bool(text.strip()) and (_SECTION.match(text) is not None or _indent(text) < base)

    start, end = index, index + 1
    while start > 0 and not leaves(lines[start - 1]):
        start -= 1
    while end < len(lines) and not leaves(lines[end]):
        end += 1
    return start, end, (start - 1 if start > 0 else None)


def _event_scope(lines: list[str], index: int) -> list[str]:
    """The `on:` block around a registration, else the registration's sibling lines."""
    start, end, parent = _sibling_range(lines, index)
    siblings, current = lines[start:end], index
    while True:
        if _ON_KEY.match(lines[current]):
            first, last = _own_block(lines, current)
            return lines[first:last]
        if parent is None:
            return siblings
        current = parent
        parent = _sibling_range(lines, current)[2]


def _manual_registration(lines: list[str], first: int, last: int) -> bool:
    start, end = _own_block(lines, first)
    own = "\n".join(lines[start:max(end, last + 1)])
    if _ONCE.search(own) or _ENABLED_FALSE.search(own):
        return True
    base = _indent(lines[first])
    first_sibling, last_sibling, _ = _sibling_range(lines, first)
    for text in lines[first_sibling:last_sibling] if not _SECTION.match(lines[first]) else ():
        if _indent(text) == base and _ENABLED_FALSE.match(re.sub(r"^\s*(?:-\s+)?", "", text)):
            return True
    events = "\n".join(_event_scope(lines, first))
    return _DISPATCH.search(events) is not None and _AUTOMATIC_EVENT.search(events) is None


def _automatic_trigger_error(quote: str, registration: str, path: str | None = None) -> str | None:
    """Reject a registration that is only a comment or is structurally manual/disabled.

    ``path`` is the cited file; without it (command output) nothing is treated
    as a comment. A registration quoted several times is accepted when at least
    one live occurrence is not manual.
    """
    mask = _comment_mask(quote, path)
    positions = []
    start = 0
    while True:
        found = quote.find(registration, start)
        if found < 0:
            break
        positions.append(found)
        start = found + max(len(registration), 1)
    live = [found for found in positions if not all(mask[found:found + len(registration)])]
    if positions and not live:
        return "registration occurs only in a comment; cite an enabled automatic trigger"
    active = "".join(" " if masked and char != "\n" else char for char, masked in zip(quote, mask))
    lines = active.split("\n")
    for found in live:
        first = active.count("\n", 0, found)
        last = active.count("\n", 0, found + len(registration))
        if not _manual_registration(lines, first, last):
            return None
    if live:
        return "registration describes a manual, test-only, planned or disabled trigger; cite the enabled automatic registration"
    return None


def condition_errors(data: dict, root: Path) -> list[dict]:
    """Collect all known condition errors, without writing or changing scores."""
    errors = []
    report, evidence = data.get("report", {}), data.get("evidence", {})
    if not isinstance(report, dict) or not isinstance(evidence, dict):
        return errors  # Full schema validator owns shape errors.
    for key, required in CONDITIONS.items():
        value = report.get(key)
        if not isinstance(value, dict) or type(value.get("numerator")) is not int or value["numerator"] <= 0:
            continue

        def error(condition, message):
            errors.append({"criterionId": key, "condition": condition, "error": message})

        proven = {condition: [] for condition in required}
        items = evidence.get(key)
        if not isinstance(items, list):
            items = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or "support" not in item:
                continue
            support = item["support"]
            label = f"evidence[{index}]"
            required_fields = {"condition", "quote", "facts"}
            optional_fields = {"application"} if key == "alerting_configured" else set()
            if not isinstance(support, dict) or not required_fields <= set(support) or set(support) - required_fields - optional_fields:
                error(label, "support requires condition, quote and facts; only alerting may also specify application")
                continue
            application = None
            if key == "alerting_configured":
                apps = data.get("apps", {})
                if not isinstance(apps, dict):
                    apps = {}
                application = support.get("application", next(iter(apps)) if len(apps) == 1 else None)
                if not isinstance(application, str) or application not in apps:
                    error(label, "alert support must identify an existing application; required explicitly when multiple apps exist")
                    continue
            condition, quote, facts = support["condition"], support["quote"], support["facts"]
            if not isinstance(condition, str) or condition not in required:
                error(label, "unknown condition for this criterion")
                continue
            if not isinstance(quote, str) or not quote.strip() or len(quote) > 65536:
                error(condition, "quote must be a nonempty verbatim excerpt of at most 65536 characters")
                continue
            if not isinstance(facts, dict) or set(facts) != set(required[condition]):
                error(condition, "facts requires exactly: " + ", ".join(required[condition]))
                continue
            if any(not isinstance(v, str) or not v.strip() or v.strip().lower() in {"unknown", "todo", "n/a", "..."} for v in facts.values()):
                error(condition, "facts must be concrete nonempty source values")
                continue
            try:
                matches = _quoted_source(item, root, quote)
            except (ValueError, OSError, UnicodeError) as exc:
                error(condition, "cannot verify source quote: " + str(exc))
                continue
            if not matches:
                error(condition, "quote does not match the cited file line or successful command output")
                continue
            if any(v not in quote for k, v in facts.items() if not (condition == "automatic_trigger" and k == "kind")):
                error(condition, "each fact must occur literally in its source quote (except trigger kind)")
                continue
            if condition == "dependency_pull_request":
                try:
                    url = urlsplit(facts["url"])
                except ValueError:
                    error(condition, "requires a valid concrete PR URL")
                    continue
                if url.scheme not in {"https", "http"} or not url.netloc or url.username or url.password or not url.path.strip("/"):
                    error(condition, "requires a concrete PR URL, author and dependency; config alone is insufficient")
                    continue
                if not re.search(r"/(?:pull|pulls|merge_requests|mr)/[1-9]\d*(?:/|$)", url.path, re.I):
                    error(condition, "requires a pull or merge request URL; config alone is insufficient")
                    continue
            if condition == "automatic_trigger" and facts["kind"] not in {"schedule", "event", "process", "service"}:
                error(condition, "requires an enabled automatic trigger; manual/test-only delivery is insufficient")
                continue
            if condition == "automatic_trigger":
                registration = facts["registration"]
                # Reject explicit contradictions, not guess arbitrary source
                # semantics. A manual trigger may coexist with an automatic
                # one: cite the latter registration separately in that case.
                trigger_error = _automatic_trigger_error(quote, registration, item["path"] if item["kind"] == "file" else None)
                if trigger_error:
                    error(condition, trigger_error)
                    continue
            proven[condition].append(dict(facts, _application=application))
        for condition, facts in proven.items():
            if not facts:
                error(condition, "positive judgment lacks verified required-condition evidence")
        if all(proven.values()):
            if key == "dependency_update_automation":
                linked = any(a["updater"] == b["updater"] for a in proven["dependency_configuration"] for b in proven["dependency_pull_request"])
            else:
                passing_apps = {a["_application"]
                                for a in proven["alert_rule"] for b in proven["automatic_trigger"] for c in proven["alert_delivery"]
                                if a["handler"] == b["handler"] and a["notifier"] == c["notifier"]
                                and a["_application"] == b["_application"] == c["_application"]}
                linked = bool(passing_apps)
                if len(passing_apps) < value["numerator"]:
                    error("passing_applications", f"numerator={value['numerator']} requires that many distinct applications with connected alert evidence; found {len(passing_apps)}")
            if not linked:
                error("connection", "condition identities do not connect; cite the same updater or trigger/rule/notifier chain")
    return errors
