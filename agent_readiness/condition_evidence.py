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
            if condition == "automatic_trigger" and facts["kind"] not in {"schedule", "event", "process", "service"}:
                error(condition, "requires an enabled automatic trigger; manual/test-only delivery is insufficient")
                continue
            if condition == "automatic_trigger":
                registration = facts["registration"]
                # Reject explicit contradictions, not guess arbitrary source
                # semantics. A manual trigger may coexist with an automatic
                # one: cite the latter registration separately in that case.
                if re.search(r"\b(?:manual|workflow_dispatch|disabled|planned|TODO)\b|--once\b|\benabled\s*[:=]\s*false\b", registration, re.I):
                    error(condition, "registration describes a manual, test-only, planned or disabled trigger; cite the enabled automatic registration")
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
