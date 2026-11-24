"""Challenge requirements: deterministic checks a challenge switches on in its config.

Built for OneGov-style Must criteria (open source, rules in code not in a prompt, tests, one PR per team),
but every check is generic. Nothing runs unless the challenge has a `requirements:` block:

  challenges:
    inkooptafel:
      requirements:
        team_work: [code, config, doc]  # what counts as the team's own work (default: code and notebooks)
        open_licence: true              # an OSI licence for the code and an open licence (CC BY / CC0) for docs
        tests: true                     # the team added tests (starter-repo tests do not count)
        explicit_rules: true            # flag language-model calls: decisions must not hide in a prompt
        submission_dir: "submissions/"  # team work belongs under this folder
        must_checklist: true            # read the ticked Must boxes (- [x] **M1** ...) from the PR description
        pr_checks: true                 # CI result of the team's pull request

Every result is evidence for the jury, never a verdict: each flag says what to ask.
"""
from __future__ import annotations

import re

from .facts import TEST_RE, notebook_to_text

KNOWN = {"team_work", "open_licence", "tests", "explicit_rules", "submission_dir", "must_checklist", "pr_checks"}
NOT_TEAM = ("template", "scaffold", "boilerplate", "generated")
LICENCE_FILE = re.compile(r"(?i)(^|/)(licen[cs]e|copying|unlicense)([-_.][\w.-]*)?$")
OSI = [("Apache-2.0", r"apache license,?\s+version 2\.0|\bapache-2\.0\b"),
       ("MIT", r"\bmit license\b|permission is hereby granted, free of charge"),
       ("EUPL-1.2", r"european union public licen[cs]e|\beupl\b"),
       ("AGPL-3.0", r"gnu affero general public license"),
       ("LGPL", r"gnu lesser general public license"),
       ("GPL", r"gnu general public license"),
       ("MPL-2.0", r"mozilla public license"),
       ("BSD", r"redistribution and use in source and binary forms"),
       ("ISC", r"\bisc license\b"),
       ("Unlicense", r"this is free and unencumbered software")]
OPEN_CONTENT = [("CC-BY-4.0", r"creative commons attribution 4\.0|\bcc[- ]by[- ]4\.0\b|\bcc by 4\.0\b"),
                ("CC-BY-SA-4.0", r"attribution-sharealike 4\.0|\bcc[- ]by[- ]sa\b"),
                ("CC0-1.0", r"\bcc0\b|public domain dedication"),
                ("ODbL", r"open database license")]
NOT_OPEN = [("CC-BY-NC", r"noncommercial|\bcc[- ]by[- ]nc\b"), ("CC-BY-ND", r"noderivatives|\bcc[- ]by[- ]nd\b"),
            ("proprietary", r"all rights reserved")]
LLM_RE = re.compile(
    r"\bimport\s+(openai|anthropic|ollama|litellm|mistralai|cohere|groq)\b"
    r"|\bfrom\s+(openai|anthropic|ollama|litellm|mistralai|cohere|groq|langchain[\w.]*|llama_index[\w.]*"
    r"|google\.generativeai|google\.genai)\b"
    r"|\bfrom\s+google\s+import\s+genai\b"
    r"|require\(\s*['\"](openai|@anthropic-ai/sdk|ollama|@google/genai|@google/generative-ai)['\"]"
    r"|from\s+['\"](openai|@anthropic-ai/sdk|ai|@ai-sdk/[\w-]+|ollama|@langchain/[\w-]+|@google/genai)['\"]"
    r"|/v1/chat/completions|/v1/messages\b|api\.greenpt\.|\bChat(OpenAI|Anthropic|Ollama|MistralAI)\s*\(",
    re.I)
MUST_BOX = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*\**\s*(M\d{1,2})\b", re.M)
PR_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)", re.I)


def config_problems(challenges: dict) -> list:
    out = []
    for key, ch in challenges.items():
        req = ch.get("requirements")
        if req is None:
            continue
        if not isinstance(req, dict):
            out.append(f"challenges.{key}.requirements must be a mapping")
            continue
        unknown = set(req) - KNOWN
        if unknown:
            out.append(f"challenges.{key}.requirements: unknown key(s) {', '.join(sorted(unknown))} "
                       f"(use {', '.join(sorted(KNOWN))})")
        tw = req.get("team_work", ["code", "notebook"])
        if not isinstance(tw, list) or set(tw) - {"code", "notebook", "config", "doc", "data"}:
            out.append(f"challenges.{key}.requirements.team_work: list of code, notebook, config, doc, data")
    return out


# ------------------------------------------------------------------ facts

def _team(f) -> bool:
    return f["status"] not in NOT_TEAM


def team_work_lines(inv, template: dict, kinds) -> dict:
    """Lines the team wrote per file kind; edits to starter files count only their new lines."""
    tl = (template or {}).get("lines", {})
    out = {}
    for f in inv.files:
        if not _team(f) or f["kind"] not in kinds or f["binary"]:
            continue
        if f["kind"] == "notebook":
            text, _ = notebook_to_text(inv.read(f["path"])) if f["size"] <= 20_000_000 else ("", {})
            n = sum(1 for ln in text.splitlines() if ln.strip())
        elif f["kind"] == "data":
            n = f["lines"] or 1
        else:
            n = f["lines"]
            if f["status"] == "template-modified" and tl.get(f["path"]):
                try:
                    n = sum(1 for ln in inv.read(f["path"]).splitlines() if ln.strip() and ln.strip() not in tl[f["path"]])
                except OSError:
                    pass
        if n:
            out[f["kind"]] = out.get(f["kind"], 0) + n
    return out


def detect_licences(inv) -> list:
    out = []
    for f in inv.files:
        if not LICENCE_FILE.search(f["path"]) or f["binary"] or f["size"] > 500_000:
            continue
        try:
            text = inv.read(f["path"]).lower()
        except OSError:
            continue
        ids = [n for n, rx in OSI if re.search(rx, text)]
        content = [n for n, rx in OPEN_CONTENT if re.search(rx, text)]
        closed = [n for n, rx in NOT_OPEN if re.search(rx, text)] if not ids else []
        out.append({"file": f["path"], "code": ids[:1], "content": content[:1], "not_open": closed[:1],
                    "from_template": f["status"] == "template"})
    return out


def llm_calls(inv, limit: int = 12) -> list:
    hits = []
    for f in inv.files:
        if not _team(f) or f["kind"] not in ("code", "notebook") or f["size"] > 2_000_000:
            continue
        try:
            text = inv.read(f["path"])
        except OSError:
            continue
        if f["kind"] == "notebook":
            text, _ = notebook_to_text(text)
        for i, line in enumerate(text.splitlines(), 1):
            if LLM_RE.search(line):
                hits.append({"file": f["path"], "line": i, "text": line.strip()[:120]})
                if len(hits) >= limit:
                    return hits
    return hits


def must_checklist(description: str) -> dict:
    boxes = {}
    for mark, code in MUST_BOX.findall(description or ""):
        boxes[code.upper()] = boxes.get(code.upper(), False) or mark.lower() == "x"
    order = sorted(boxes, key=lambda c: int(c[1:]))
    return {"found": bool(boxes), "ticked": [c for c in order if boxes[c]], "unticked": [c for c in order if not boxes[c]]}


def submission_dir(inv, folder: str) -> dict:
    folder = folder.strip("/") + "/"
    new = [f["path"] for f in inv.files if f["status"] == "team"]
    inside = [p for p in new if p.lower().startswith(folder.lower())]
    teams = sorted({p[len(folder):].split("/", 1)[0] for p in inside if "/" in p[len(folder):]})
    return {"folder": folder, "inside": len(inside), "outside": len(new) - len(inside), "team_folders": teams[:5]}


def gather(inv, template: dict, sub: dict, challenge: dict, github: dict) -> dict:
    """Requirement facts for one team, or {} when the challenge sets no requirements."""
    req = (challenge or {}).get("requirements")
    if not req:
        return {}
    kinds = set(req.get("team_work") or ["code", "notebook"])
    out = {"team_work": team_work_lines(inv, template, kinds)}
    if req.get("open_licence"):
        out["licences"] = detect_licences(inv)
    if req.get("tests"):
        out["team_tests"] = sorted(f["path"] for f in inv.files if _team(f) and TEST_RE.search(f["path"]))[:20]
    if req.get("explicit_rules"):
        out["llm_calls"] = llm_calls(inv)
    if req.get("submission_dir"):
        out["submission_dir"] = submission_dir(inv, req["submission_dir"])
    if req.get("must_checklist"):
        out["must_checklist"] = must_checklist(sub.get("description", ""))
    if req.get("pr_checks"):
        out["pr_checks"] = (github or {}).get("pr") or {"summary": "no pull request"}
    return out


# ------------------------------------------------------------------ flags

def apply_flags(flags: list, rf: dict, challenge: dict, snap: dict) -> list:
    """Add requirement flags and correct generic ones that the challenge makes wrong. Returns the new list."""
    if not rf:
        return flags
    req = challenge.get("requirements") or {}
    out = list(flags)

    def add(code, severity, title, detail="", evidence=None):
        out.append({"code": code, "severity": severity, "title": title, "detail": detail, "evidence": evidence or []})

    work = rf.get("team_work") or {}
    if sum(work.values()) and any(f["code"] == "no_team_code" for f in out):
        # e.g. a skills library or rules in YAML: documents and configuration are the deliverable here
        out = [f for f in out if f["code"] != "no_team_code"]
    elif not sum(work.values()):
        for f in out:
            if f["code"] == "no_team_code":
                f["title"] = "No team work beyond the starter repository"
                f["detail"] = f"Nothing new in {', '.join(sorted(req.get('team_work') or ['code', 'notebook']))} files."

    if "licences" in rf:
        lic = rf["licences"]
        code = sorted({i for x in lic for i in x["code"]})
        content = sorted({i for x in lic for i in x["content"]})
        closed = sorted({i for x in lic for i in x["not_open"]})
        if closed:
            add("licence_not_open", "medium", f"Licence is not open: {', '.join(closed)}",
                "The challenge asks for open-source code and openly licensed rules and docs.",
                [{"file": x["file"]} for x in lic if x["not_open"]][:3])
        if not code:
            add("no_open_licence", "medium", "No open-source (OSI) licence found for the code",
                "No LICENSE file with Apache-2.0, MIT, EUPL or another OSI licence. Ask under which licence the team "
                "releases the code.")
        if not content:
            add("no_content_licence", "low", "No open licence found for rules, data or docs (CC BY 4.0 or CC0)",
                "The challenge asks for CC BY 4.0 or CC0 on rules and documentation.")

    if "team_tests" in rf and not rf["team_tests"]:
        add("no_team_tests", "medium", "No tests added by the team",
            "Tests from the starter repository do not count. Ask how the team checked that its rules give the right "
            "outcome.")

    calls = rf.get("llm_calls") or []
    if calls:
        files = sorted({c["file"] for c in calls})
        add("llm_in_code", "medium",
            f"Calls a language model ({len(calls)} place(s) in {len(files)} file(s)): check what it decides",
            "The brief asks for decision rules in code or configuration, not in a prompt. Fine for explaining or "
            "drafting text; not fine if the model makes the classification or advice.",
            [{"file": c["file"], "lines": str(c["line"])} for c in calls[:5]])

    sd = rf.get("submission_dir")
    if sd and not sd["inside"]:
        add("outside_submission_dir", "low", f"Nothing new under {sd['folder']}",
            f"The challenge asks teams to put their work in {sd['folder']}<team-name>/. "
            f"{sd['outside']} new file(s) elsewhere.")

    mc = rf.get("must_checklist")
    if mc is not None:
        if not mc["found"]:
            add("no_must_checklist", "low", "Must checklist from the PR template is missing",
                "The pull request does not use the template's Must boxes.")
        elif mc["unticked"]:
            add("must_unticked", "medium", f"Must criteria not ticked by the team: {', '.join(mc['unticked'])}",
                "Under the challenge rules a submission that misses a Must is not valid. Ask the team why.")

    pr = rf.get("pr_checks") or {}
    summary = pr.get("summary", "")
    after = (f" The checks ran on the PR head ({pr['head_sha'][:7]}), not on the deadline snapshot "
             f"({snap.get('short', '?')}).") if pr.get("head_sha") and snap.get("sha") and \
        not str(snap["sha"]).startswith(pr["head_sha"][:7]) else ""
    if summary == "failed":
        failed = [c["name"] for c in pr.get("checks", []) if c.get("conclusion") in ("failure", "timed_out", "cancelled")]
        add("pr_checks_failed", "medium", f"Pull request checks fail: {', '.join(failed[:4]) or 'see the PR'}",
            "The challenge's CI (lint, schema checks, tests) does not pass." + after)
    elif summary == "awaiting approval":
        add("pr_checks_not_run", "low", "CI did not run on the pull request: it needs a maintainer's approval",
            "Organisers: approve the workflow run in the PR (first-time contributors from forks)." + after)
    elif summary == "none" and req.get("pr_checks"):
        add("pr_checks_none", "info", "No CI checks ran on the pull request")

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda f: order.get(f["severity"], 9))
    return out


# ------------------------------------------------------------------ for the review pack and the report

def pack_summary(rf: dict) -> dict:
    """Compact facts for the model (only present when the challenge sets requirements)."""
    if not rf:
        return {}
    out = {"team_work_lines": rf.get("team_work", {})}
    if "licences" in rf:
        out["licences"] = [{k: v for k, v in x.items() if v} for x in rf["licences"]][:6]
    if "team_tests" in rf:
        out["team_test_files"] = len(rf["team_tests"])
    if "llm_calls" in rf:
        out["language_model_calls"] = [f"{c['file']}:{c['line']}" for c in rf["llm_calls"][:8]]
    if "must_checklist" in rf:
        out["must_boxes"] = {k: rf["must_checklist"][k] for k in ("ticked", "unticked")}
    if "pr_checks" in rf:
        out["pr_checks"] = rf["pr_checks"].get("summary", "")
    return out


def report_rows(rf: dict) -> list:
    """[(label, text)] for the facts table."""
    if not rf:
        return []
    rows = []
    work = rf.get("team_work") or {}
    if work:
        rows.append(("Team work", ", ".join(f"{n} lines of {k}" for k, n in sorted(work.items(), key=lambda kv: -kv[1]))))
    if "licences" in rf:
        parts = []
        for x in rf["licences"]:
            ids = x["code"] + x["content"] + x["not_open"]
            if ids:
                parts.append(f"{'/'.join(ids)} ({x['file']}{', from the starter repo' if x['from_template'] else ''})")
        rows.append(("Licences", "; ".join(parts) or "none found"))
    if "team_tests" in rf:
        rows.append(("Tests by the team", f"{len(rf['team_tests'])} file(s)" if rf["team_tests"] else "none"))
    if "llm_calls" in rf:
        calls = rf["llm_calls"]
        rows.append(("Language model calls", f"{len(calls)} place(s) in {len({c['file'] for c in calls})} file(s)"
                     if calls else "none found"))
    if "submission_dir" in rf:
        sd = rf["submission_dir"]
        rows.append(("Submission folder", f"{sd['folder']}{'/'.join(sd['team_folders'][:1]) + '/' if sd['team_folders'] else ''}: "
                     f"{sd['inside']} new file(s); {sd['outside']} elsewhere"))
    if "must_checklist" in rf:
        mc = rf["must_checklist"]
        rows.append(("Must boxes ticked", "not filled in" if not mc["found"] else
                     f"{', '.join(mc['ticked']) or 'none'}" + (f"; not ticked: {', '.join(mc['unticked'])}" if mc["unticked"] else "")))
    if "pr_checks" in rf:
        pr = rf["pr_checks"]
        names = ", ".join(f"{c['name']} {c.get('conclusion') or c.get('status', '')}" for c in pr.get("checks", [])[:5])
        rows.append(("PR checks", pr.get("summary", "") + (f" ({names})" if names else "")))
    return rows


def pr_from_url(url: str):
    m = PR_URL.search(url or "")
    return (m.group(1), m.group(2), int(m.group(3))) if m else None
