"""Build the review input: the team's own files, numbered, within a size budget."""
from __future__ import annotations

import json
import re

from .facts import ENTRY_RE, notebook_to_text
from .util import truncate

MANIFEST_NAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "pipfile", "environment.yml", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.mjs", "next.config.ts", "app.json", "vercel.json", "netlify.toml",
    "setup.py", "streamlit_app.py", "procfile", "makefile",
}


def _priority(f) -> tuple:
    p = f["path"]
    name = p.rsplit("/", 1)[-1].lower()
    depth = p.count("/")
    if re.match(r"(?i)^readme", name):
        return (0, depth, 0)
    if name in MANIFEST_NAMES or name.startswith("requirements"):
        return (1, depth, 0)
    if f["kind"] == "notebook":
        return (3, depth, -f["size"])
    if f["kind"] == "code":
        entry = 0 if ENTRY_RE.search(p) else 1
        return (2 + entry, depth, f["size"])
    if f["kind"] == "doc":
        return (5, depth, f["size"])
    if f["kind"] == "config":
        return (6, depth, f["size"])
    if f["kind"] == "data":
        return (7, depth, f["size"])
    return (9, depth, f["size"])


def _numbered(text: str, start=1) -> str:
    lines = text.split("\n")
    width = len(str(start + len(lines)))
    return "\n".join(f"{str(i).rjust(width)}| {ln if len(ln) <= 1500 else ln[:1500] + ' [...line truncated]'}"
                     for i, ln in enumerate(lines, start))


def _redact(text: str, secrets) -> str:
    for s in sorted(secrets, key=len, reverse=True):  # whole key blocks before their first line
        if s and s in text:
            text = text.replace(s, "[REDACTED SECRET]")
    return text


def _escape(text: str) -> str:
    # A file must not be able to close our wrapper tags and pose as reviewer instructions.
    return re.sub(r"</?\s*(file|repo_content|file_manifest|submission|repo_facts|challenge_brief|reviewer_instructions)\b",
                  lambda m: m.group(0).replace("<", "&lt;"), text, flags=re.I)


def _attr(value: str) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def build_pack(inv, facts: dict, sub: dict, challenge: dict, brief: str, flags: list, raw_secrets,
               budget_chars: int, max_file_chars: int, include_content: bool = True) -> tuple:
    """Returns (user_text, pack_info). With include_content=False only the manifest is listed (deep mode)."""
    candidates = [f for f in inv.files
                  if f["status"] not in ("template", "scaffold", "boilerplate")
                  and f["kind"] in ("code", "notebook", "doc", "config", "data")
                  and not f["binary"] and f["size"] <= 3_000_000]
    readme = (facts.get("readme") or {}).get("path")
    if readme and all(f["path"] != readme for f in candidates):
        candidates += [f for f in inv.files if f["path"] == readme]
    candidates.sort(key=_priority)

    blocks, included, partial, omitted = [], {}, [], []
    used = 0
    if not include_content:
        candidates = []
    for f in candidates:
        try:
            raw = inv.read(f["path"])
        except OSError:
            continue
        if f["kind"] == "notebook":
            raw, _ = notebook_to_text(raw)
        elif f["kind"] == "data":
            raw = "\n".join(raw.splitlines()[:12]) + "\n[... data file truncated to the first lines ...]"
        elif f["kind"] == "config" and f["size"] > 60_000:
            raw = raw[:4000] + "\n[... truncated ...]"
        text = _escape(_redact(raw, raw_secrets)).replace("\r\n", "\n")
        if text.endswith("\n"):
            text = text[:-1]
        total_lines = text.count("\n") + 1
        cap = max_file_chars if f["kind"] != "doc" else min(max_file_chars, 15000)
        if len(text) > cap:
            head = text[: int(cap * 0.75)]
            head_lines = head.count("\n") + 1
            tail_lines = text.split("\n")[-max(20, int(cap * 0.25 / 80)):]
            tail_start = total_lines - len(tail_lines) + 1
            if tail_start <= head_lines + 1:
                body = _numbered(head) + "\n[... rest of file omitted for length ...]"
                shown = f"1-{head_lines} of {total_lines}"
            else:
                body = (_numbered(head) + f"\n[... lines {head_lines + 1}-{tail_start - 1} omitted for length ...]\n"
                        + _numbered("\n".join(tail_lines), tail_start))
                shown = f"1-{head_lines} and {tail_start}-{total_lines} of {total_lines}"
            partial.append(f["path"])
        else:
            body = _numbered(text)
            shown = f"1-{total_lines} of {total_lines}"
        block = f'<file path="{_attr(f["path"])}" kind="{f["kind"]}" status="{f["status"]}" lines="{shown}">\n{body}\n</file>'
        if used + len(block) > budget_chars:
            omitted.append(f["path"])
            continue
        blocks.append(block)
        used += len(block)
        included[f["path"]] = total_lines

    manifest_lines = []
    for f in sorted(inv.files, key=lambda x: x["path"]):
        if not include_content:
            tag = "in working directory" if f["status"] not in ("template", "scaffold", "boilerplate") else "not the team's work"
        elif f["path"] in included:
            tag = "included" if f["path"] not in partial else "included (shortened)"
        elif f["path"] in omitted:
            tag = "NOT INCLUDED (size budget)"
        else:
            tag = "not included"
        manifest_lines.append(f"{f['path']} | {f['kind']} | {f['status']} | {f['lines'] or '-'} lines | {tag}")
    if len(manifest_lines) > 600:
        manifest_lines = manifest_lines[:600] + [f"[... {len(manifest_lines) - 600} more files ...]"]

    compact_facts = {
        "team_code_lines": facts["team_code_lines"],
        "languages": facts["languages"],
        "files_by_status": facts["files_by_status"],
        "built_with_lovable": facts["lovable"]["lovable"],
        "shadcn_ui_scaffold": facts["lovable"]["shadcn"],
        "notebooks": facts["notebooks"],
        "notable_dependencies": facts["dependencies"]["notable"],
        "tests_found": facts["tests"],
        "readme": {k: v for k, v in facts["readme"].items() if k != "mock_disclosures"},
        "mock_disclosures_in_readme": facts["readme"].get("mock_disclosures", []),
        "data_files": facts["data_files"][:8],
        "challenge_data_signals": facts["challenge_data"]["found"][:10],
        "partner_tech": {k: v["level"] for k, v in facts["partners"].items()},
        "commits": facts["timeline"]["commits"],
        "contributors": facts["timeline"]["contributors"],
        "bot_commits": facts["timeline"]["bot_commits"],
        "commits_per_day": facts["timeline"]["per_day"],
        "demo_link": facts["demo"].get("status"),
        "automated_flags": [f"[{x['severity']}] {x['title']}" + (f": {truncate(x['detail'], 220)}" if x.get("detail") else "")
                            for x in flags],
    }

    sub_lines = [
        f"Project name: {sub['project']}",
        f"Challenge: {challenge.get('name', sub.get('challenge_raw') or 'unknown') if challenge else (sub.get('challenge_raw') or 'unknown')}",
        f"Punchline: {sub.get('punchline') or '(none)'}",
        f"Demo link: {sub.get('demo') or '(none)'}",
        f"Video: {'provided' if sub.get('video') else '(none)'}",
        "Description submitted by the team:",
        sub.get("description") or "(No description submitted. Use the README for the team's claims.)",
    ]
    user = "\n".join([
        "<challenge_brief>",
        truncate(brief or "(No brief available. Judge against the project's own goals.)", 14000),
        "</challenge_brief>",
        "",
        "<submission>",
        _escape("\n".join(sub_lines)),
        "</submission>",
        "",
        "<repo_facts>",
        "Automated signals from non-AI checks. They can have innocent explanations; use them as leads.",
        _escape(json.dumps(compact_facts, ensure_ascii=False, indent=1)),
        "</repo_facts>",
        "",
        "<file_manifest>",
        "path | kind | status | lines | in this review",
        _escape("\n".join(manifest_lines)),
        "</file_manifest>",
        "",
        "<repo_content>",
        ("\n\n".join(blocks) if blocks else "(No team-authored files to show.)") if include_content
        else "(Not provided in this run: inspect the repository in your working directory with your tools.)",
        "</repo_content>",
        "",
        "Review this submission now. Return only the JSON object.",
    ])
    info = {"files_included": len(included), "files_shortened": len(partial), "files_omitted": len(omitted),
            "omitted": omitted[:50], "chars": len(user), "included_lines": included}
    return user, info
