"""Read submissions from a Junction project export or a plain team/repo CSV.

Only the fields needed for the review are kept. Team member data in the export
is ignored and never reaches the model.
"""
from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .util import slugify

GITHUB_HTTP = re.compile(r"(?<![\w.-])(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
GITHUB_SSH = re.compile(r"git@github\.com:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$", re.I)
ANY_URL = re.compile(r"(?:https?://|(?<![\w.-])(?:www\.)?github\.com/)[^\s,;\"'<>]+", re.I)
TEXT_COLUMNS = {"name", "project", "project name", "projectname", "team", "team name", "description", "punchline",
                "tagline", "summary", "about", "demo", "video", "projecturl", "challenge", "track", "status"}


@dataclass
class Submission:
    slug: str
    project: str
    challenge: str = ""          # config challenge key, "" when unknown
    challenge_raw: str = ""      # what the export said
    repo_url: str = ""           # browser URL (https://github.com/owner/repo) or local path
    clone_url: str = ""
    owner: str = ""
    repo: str = ""
    demo: str = ""
    video: str = ""
    punchline: str = ""
    description: str = ""
    status: str = ""             # Junction status (draft/final) when known
    junction_url: str = ""
    tree_path: str = ""          # branch (and maybe path) from a /tree/... link
    source: str = ""             # which input format it came from
    team_size: int = 0           # number of team members (from Junction export)
    notes: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def normalize_repo(value: str):
    """Return (repo_url, clone_url, owner, repo) or None."""
    v = (value or "").strip().strip("\"'<>()[]")
    if not v:
        return None
    m = GITHUB_HTTP.search(v)
    if m:
        owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2).rstrip("."), flags=re.I).rstrip(".")
        if repo.lower() in ("", "orgs", "settings") or owner.lower() in ("orgs", "settings", "features", "topics"):
            return None
        return f"https://github.com/{owner}/{repo}", f"https://github.com/{owner}/{repo}.git", owner, repo
    m = GITHUB_SSH.search(v)
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://github.com/{owner}/{repo}", f"https://github.com/{owner}/{repo}.git", owner, repo
    if v.startswith(("https://gitlab.com/", "https://bitbucket.org/", "https://codeberg.org/")):
        url = re.sub(r"(/-)?/(tree|blob)/.*$", "", v).rstrip("/")
        url = re.sub(r"\.git$", "", url)
        return url, url + ".git", "", url.rsplit("/", 1)[-1]
    if v.startswith("file://") or os.path.isdir(v):
        p = v[7:] if v.startswith("file://") else v
        return str(Path(p).resolve()), str(Path(p).resolve()), "", Path(p).name
    return None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _sniff(text: str) -> str:
    """Decide the separator from the header line only (Dutch Excel uses ';')."""
    header = text.split("\n", 1)[0]
    counts = {d: header.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _unescape(s: str) -> str:
    # Junction pre-doubles quotes in text fields before its CSV writer quotes them again.
    return (s or "").replace('""', '"').strip()


def _first(row: dict, *names) -> str:
    for n in names:
        if n in row and (row[n] or "").strip():
            return row[n].strip()
    return ""


def match_challenge(text: str, challenges: dict) -> str:
    t = (text or "").lower()
    if not t:
        return ""
    for key, ch in challenges.items():
        for m in ch.get("match", []):
            if m and m in t:
                return key
    return ""


def read_submissions(path, cfg: dict) -> list:
    raw = Path(path).read_bytes()
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text), delimiter=_sniff(text))
    if not reader.fieldnames:
        raise ValueError(f"{path}: no header row found")

    subs = []
    used = set()
    for raw_row in reader:
        row = {(k or "").strip().lower(): (v if isinstance(v, str) else "") for k, v in raw_row.items()}
        is_junction = "source" in row or "projecturl" in row
        project = _unescape(_first(row, "name", "project", "project name", "projectname", "team name", "team"))
        repo_field = _first(row, "source", "repo", "repository", "github", "repo url", "repository url", "code", "source code", "url")
        parsed = normalize_repo(repo_field)
        guessed_from = ""
        if not parsed:  # a GitHub link in a custom submission field (never from free text like the description)
            for k, v in row.items():
                if k in TEXT_COLUMNS:
                    continue
                for url in ANY_URL.findall(v or ""):
                    parsed = normalize_repo(url)
                    if parsed:
                        guessed_from = k
                        break
                if parsed:
                    break
        if not project and parsed:
            project = parsed[3]
        if not project and not parsed:
            continue

        challenge_bits = [v for k, v in row.items() if (k == "challenge" or k.startswith("challenges") or k == "track") and v]
        challenge_raw = ", ".join(x.strip() for x in challenge_bits if x.strip())
        demo = _first(row, "demo", "demo url", "demo link", "live url")
        video = _first(row, "video", "video url", "pitch video")
        sub = Submission(
            slug="",
            project=project,
            challenge=match_challenge(challenge_raw, cfg["challenges"]),
            challenge_raw=challenge_raw,
            demo=demo if demo.startswith("http") else "",
            video=video if video.startswith("http") else "",
            punchline=_unescape(_first(row, "punchline", "tagline")),
            description=_unescape(_first(row, "description", "summary", "about")),
            status=_first(row, "status"),
            junction_url=_first(row, "projecturl"),
            source="junction" if is_junction else "csv",
            team_size=int(_first(row, "team_size", "teamsize") or 0),
        )
        if parsed:
            sub.repo_url, sub.clone_url, sub.owner, sub.repo = parsed
            if guessed_from:
                sub.notes.append(f"Repository link taken from the '{guessed_from}' field, not the main source field")
            m = re.search(r"github\.com/[^/\s]+/[^/\s]+/tree/([^?#\s]+)", repo_field or "", re.I)
            if m:
                sub.tree_path = m.group(1).strip("/")
        else:
            sub.notes.append("No repository link found in the submission")
        base = slugify(project)
        slug, i = base, 2
        while slug in used:
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        sub.slug = slug
        subs.append(sub)

    seen_repos = {}
    for s in subs:
        if s.repo_url:
            key = s.repo_url.lower()
            if key in seen_repos:
                s.notes.append(f"Same repository as '{seen_repos[key]}'")
            else:
                seen_repos[key] = s.project
    return subs


def write_submissions_csv(path, rows: list) -> None:
    """Write the plain format: project, repo, challenge, description."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["project", "repo", "challenge", "description", "created", "template"])
        for r in rows:
            w.writerow([r.get("project", ""), r.get("repo", ""), r.get("challenge", ""),
                        r.get("description", ""), r.get("created", ""), r.get("template", "")])
