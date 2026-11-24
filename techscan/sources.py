"""Pull submissions from one or more platforms and merge them into one CSV for the scanner.

Configure under `sources:` (a list, read in order; earlier sources win for team name and links):

  - type: github-prs      open pull requests into the challenge repos (fork + one PR per team).
                          Repos default to each challenge's `template`; override with `repos:`.
  - type: csv             any export (e.g. Alkemio) with project/repo/challenge columns. `path:`, `label:`.
  - type: junction        the Junction tRPC export (uses the `junction:` block).

Rows from different sources that point at the same GitHub repo are merged into one team. A pull
request link anywhere in a submission is resolved to the fork and branch the team actually works in.
Without a `sources:` list, a configured `junction:` block still works as before.
"""
from __future__ import annotations

import csv
import re
from datetime import timezone
from pathlib import Path

from .intake import PR_NOTE, normalize_repo, read_submissions
from .util import log

COLUMNS = ["project", "repo", "challenge", "description", "punchline", "demo", "video", "team_size", "status",
           "platform", "platform_url", "notes"]
PR_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)", re.I)
TEAM_NAME = re.compile(r"\bteam\s*name\s*\**\s*:\s*\**\s*([^*:\s].*)", re.I)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
MEMBERS_LINE = re.compile(r"(?im)^[ \t]*[-*]?[ \t]*\**[ \t]*(team[ \t]*)?(members|leden|teamleden)[ \t]*:?\**[ \t]*:?.*$\n?")
TICK = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+)$", re.M)
MAX_DESCRIPTION = 6000


class SourceError(Exception):
    pass


def configured_sources(cfg: dict) -> list:
    """The `sources:` list, or the legacy Junction block when no list is given."""
    srcs = cfg.get("sources") or []
    if srcs:
        return srcs
    j = cfg.get("junction") or {}
    if j.get("event_slug") or j.get("event_id"):
        return [{"type": "junction"}]
    return []


# ------------------------------------------------------------------ GitHub pull requests

def _repo_full_name(url: str) -> str:
    parsed = normalize_repo(url or "")
    return f"{parsed[2]}/{parsed[3]}" if parsed and parsed[2] else ""


def pr_repos(cfg: dict, src: dict) -> dict:
    """{'owner/repo': challenge_key} for the repos whose pull requests are submissions."""
    out = {}
    for item in src.get("repos") or []:
        if isinstance(item, dict):
            name, chal = _repo_full_name(item.get("repo", "")) or item.get("repo", ""), item.get("challenge", "")
        else:
            name, chal = _repo_full_name(item) or str(item), ""
        if name:
            out[name.lower()] = chal
    if not out:
        for key, ch in cfg["challenges"].items():
            name = _repo_full_name(ch.get("template", ""))
            if name:
                out[name.lower()] = key
    return out


def clean_body(body: str) -> str:
    """PR body without template comments or member names (member data never reaches the model)."""
    text = HTML_COMMENT.sub("", body or "")
    text = MEMBERS_LINE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_DESCRIPTION]


def team_name_from_body(body: str) -> str:
    for line in clean_body(body).splitlines():
        m = TEAM_NAME.search(line)
        if m:
            name = m.group(1).strip().strip("*_` ").strip()
            if name and not name.startswith("<") and name.lower() not in ("tbd", "-", "n/a"):
                return name[:80]
    return ""


def _ticks(body: str) -> dict:
    """Checkbox label (lowercase, without markdown bold) -> ticked?"""
    return {label.replace("*", "").strip().lower(): mark.lower() == "x" for mark, label in TICK.findall(body or "")}


def ticked_team_box(body: str) -> bool:
    return any(v for k, v in _ticks(body).items() if k.startswith("hackathon team submission"))


def is_non_team_pr(body: str) -> bool:
    """True when the PR template says this is a starter-repo fix, not a team submission."""
    ticks = _ticks(body)
    other = any(v for k, v in ticks.items() if k.startswith(("starter-repo", "bug fix")))
    return other and not ticked_team_box(body)


def pr_row(pr: dict, challenge: str) -> dict:
    """One submission row from a GitHub pull request object."""
    body = pr.get("body") or ""
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    notes = []
    if head_repo.get("full_name") and head.get("ref"):
        repo = f"https://github.com/{head_repo['full_name']}/tree/{head['ref']}"
    else:
        repo = ""
        notes.append(f"Pull request #{pr.get('number')}: the fork behind it was deleted; review the PR by hand")
    title = (pr.get("title") or "").strip()
    project = team_name_from_body(body) or title or (head_repo.get("owner") or {}).get("login", "")
    return {
        "project": project,
        "repo": repo,
        "challenge": challenge,
        "description": clean_body(body),
        "punchline": title if title != project else "",
        "status": "draft PR" if pr.get("draft") else f"PR {pr.get('state', 'open')}",
        "platform": "GitHub PR",
        "platform_url": pr.get("html_url", ""),
        "notes": " | ".join(notes),
    }


def pull_github_prs(cfg: dict, src: dict, gh) -> list:
    state = src.get("state", "open")
    exclude = {a.lower() for a in (src.get("exclude_authors") or [])}
    since = (cfg["event"]["start_dt"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
             if src.get("only_since_start", True) else "")
    rows = []
    repos = pr_repos(cfg, src)
    if not repos:
        raise SourceError("github-prs: no repos. Set challenge templates or list them under 'repos:'")
    for full, chal in repos.items():
        owner, repo = full.split("/", 1)
        prs, err = gh.pulls(owner, repo, state)
        if err and not prs:
            raise SourceError(f"github-prs: could not list pull requests for {full}: {err}")
        kept = 0
        for pr in prs:
            author = ((pr.get("user") or {}).get("login") or "").lower()
            if author in exclude or is_non_team_pr(pr.get("body") or ""):
                continue
            if since and (pr.get("created_at") or "") and pr["created_at"] < since:
                # opened before kickoff: an organiser PR or an early bird; keep only if it ticks the team box
                if not ticked_team_box(pr.get("body") or ""):
                    continue
            rows.append(pr_row(pr, chal))
            kept += 1
        log(f"  github-prs {full}: {kept} team pull request(s)")
    return rows


def resolve_pr_links(rows: list, gh, repo_challenges: dict = None) -> None:
    """A submitted link to a pull request means: scan the fork and branch behind it.
    repo_challenges maps 'owner/repo' of the challenge repos to challenge keys."""
    for r in rows:
        m = PR_URL.search(r.get("repo") or "")
        if not m:
            continue
        owner, repo, num = m.group(1), m.group(2), int(m.group(3))
        pr, err = gh.pull(owner, repo, num)
        if not pr:
            r["notes"] = _join(r.get("notes"), f"Pull request link could not be opened ({err})")
            r["repo"] = ""
            continue
        resolved = pr_row(pr, r.get("challenge", ""))
        r["repo"] = resolved["repo"]
        r["platform_url"] = r.get("platform_url") or resolved["platform_url"]
        r["notes"] = _join(r.get("notes"), resolved["notes"])
        if not r.get("challenge"):
            r["challenge"] = (repo_challenges or {}).get(f"{owner}/{repo}".lower(), "")


# ------------------------------------------------------------------ CSV-like sources

def _rows_from_csv(path: Path, cfg: dict, label: str) -> list:
    rows = []
    for s in read_submissions(path, cfg):
        repo = s.repo_url + (f"/tree/{s.tree_path}" if s.tree_path and s.owner else "")
        # a PR link is kept as-is here; resolve_pr_links turns it into the fork and branch
        rows.append({
            "project": s.project, "repo": repo or s.pr_url, "challenge": s.challenge or s.challenge_raw,
            "description": s.description, "punchline": s.punchline, "demo": s.demo, "video": s.video,
            "team_size": s.team_size or "", "status": s.status, "platform": label,
            "platform_url": s.platform_url,
            "notes": " | ".join(n for n in s.notes if not n.startswith((PR_NOTE, "No repository link"))
                                or not s.pr_url),
        })
    return rows


def pull_csv(cfg: dict, src: dict) -> list:
    path = Path(src.get("path", ""))
    if not path.is_absolute():
        path = Path(cfg["_config_path"]).parent / path
    if not path.exists():
        raise SourceError(f"csv source: file not found: {path}")
    return _rows_from_csv(path, cfg, src.get("label") or path.stem)


def pull_junction(cfg: dict, src: dict) -> list:
    from .junction import JunctionClient, save_projects_csv
    client = JunctionClient(cfg)
    problem = client.problem_pull()
    if problem:
        raise SourceError(f"junction: {problem}")
    csv_data, err = client.pull_projects_csv()
    if err:
        raise SourceError(f"junction: {err}")
    raw = Path(cfg["paths"]["workdir"]) / "junction-submissions.csv"
    save_projects_csv(csv_data, raw)
    return _rows_from_csv(raw, cfg, "Junction")


# ------------------------------------------------------------------ merge

def _join(a, b) -> str:
    return " | ".join(x for x in (a, b) if x)


def _key(row: dict) -> str:
    parsed = normalize_repo(row.get("repo") or "")
    if not parsed:
        return ""
    return (f"{parsed[2]}/{parsed[3]}" if parsed[2] else parsed[0]).lower()


def merge_rows(groups: list, flag_missing: bool = True) -> list:
    """groups: [(platform_label, rows)] in priority order. Same repo = same team."""
    merged, by_key = [], {}
    labels = [g[0] for g in groups if g[1]]
    for label, rows in groups:
        for row in rows:
            row = {c: row.get(c, "") for c in COLUMNS} | {"_seen": {label}}
            k = _key(row)
            if k and k in by_key:
                cur = by_key[k]
                cur["_seen"].add(label)
                if "/tree/" in (row["repo"] or "") and "/tree/" not in (cur["repo"] or ""):
                    cur["repo"] = row["repo"]  # the PR knows the branch, an Alkemio link may not
                for c in ("project", "challenge", "punchline", "demo", "video", "team_size", "status", "platform_url"):
                    cur[c] = cur[c] or row[c]
                if row["description"] and row["description"] not in cur["description"]:
                    cur["description"] = (f"{cur['description']}\n\n[{label}]\n{row['description']}"
                                          if cur["description"] else row["description"])
                cur["notes"] = _join(cur["notes"], row["notes"])
                continue
            merged.append(row)
            if k:
                by_key[k] = row
    for row in merged:
        seen = row.pop("_seen")
        row["platform"] = " + ".join(lb for lb in labels if lb in seen)
        if flag_missing and len(labels) > 1:
            missing = [lb for lb in labels if lb not in seen]
            if missing:
                row["notes"] = _join(row["notes"], f"Not found in: {', '.join(missing)}")
    return merged


def write_rows(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


PULLERS = {"csv": pull_csv, "junction": pull_junction}


def pull_all(cfg: dict, out_path=None) -> Path:
    """Run every configured source, merge, and write one CSV. Returns its path."""
    from .gitops import GitHub
    srcs = configured_sources(cfg)
    if not srcs:
        raise SourceError("No sources configured. Add a 'sources:' list to the config, or pass -s <file>.")
    gh = GitHub(cache_dir=None)
    repo_challenges = pr_repos(cfg, {})
    groups = []
    for src in srcs:
        kind = src.get("type", "")
        log(f"Pulling from {kind}" + (f" ({src['label']})" if src.get("label") else ""))
        if kind == "github-prs":
            rows, label = pull_github_prs(cfg, src, gh), src.get("label") or "GitHub PR"
        elif kind in PULLERS:
            rows = PULLERS[kind](cfg, src)
            label = rows[0]["platform"] if rows else (src.get("label") or kind)
        else:
            raise SourceError(f"Unknown source type '{kind}' (use github-prs, csv or junction)")
        resolve_pr_links(rows, gh, repo_challenges)
        log(f"  {len(rows)} row(s) from {label}")
        groups.append((label, rows))
    rows = merge_rows(groups, flag_missing=bool(cfg.get("sources_flag_missing", True)))
    out = Path(out_path) if out_path else Path(cfg["paths"]["workdir"]) / "submissions-pulled.csv"
    write_rows(out, rows)
    log(f"{len(rows)} team(s) after merging -> {out}")
    return out


def source_problems(cfg: dict) -> list:
    """Readiness per source for preflight: [(label, problem_or_empty)]."""
    out = []
    for src in configured_sources(cfg):
        kind = src.get("type", "")
        if kind == "github-prs":
            repos = pr_repos(cfg, src)
            out.append((f"github-prs ({', '.join(repos) or 'no repos'})",
                        "" if repos else "no repos: set challenge templates or 'repos:'"))
        elif kind == "csv":
            p = Path(src.get("path", ""))
            if not p.is_absolute():
                p = Path(cfg["_config_path"]).parent / p
            out.append((f"csv {src.get('label') or p.name}", "" if p.exists() else f"file not found yet: {p}"))
        elif kind == "junction":
            from .junction import JunctionClient
            out.append(("junction", JunctionClient(cfg).problem_pull()))
        else:
            out.append((kind or "?", f"unknown source type '{kind}'"))
    return out
