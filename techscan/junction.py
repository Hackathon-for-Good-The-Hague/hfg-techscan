"""Junction platform integration: pull submissions, push technical scores."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import log, truncate

FLAG_ICON = "\u26a0"  # ⚠


class JunctionClient:
    """Minimal client for the JunctionApp REST API."""

    def __init__(self, cfg: dict):
        jcfg = cfg.get("junction") or {}
        self.api_url = (jcfg.get("api_url") or "https://app.hackjunction.com").rstrip("/")
        self.event_slug = jcfg.get("event_slug") or ""
        self.push_scores = jcfg.get("push_scores", False)
        self.token = os.environ.get("JUNCTION_TOKEN") or ""
        self.webhook_key = os.environ.get("JUNCTION_WEBHOOK_KEY") or ""

    def _request(self, method: str, path: str, body=None, auth="token", timeout=30):
        url = f"{self.api_url}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth == "token" and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif auth == "webhook" and self.webhook_key:
            sep = "&" if "?" in url else "?"
            url += f"{sep}webhook_api_key={urllib.parse.quote(self.webhook_key)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace")), ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            return None, f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"Connection error: {e}"

    # ------------------------------------------------------------------ pull

    def problem_pull(self) -> str:
        if not self.event_slug:
            return "junction.event_slug is not set in the config"
        if not self.token:
            return "JUNCTION_TOKEN environment variable is not set (organizer JWT)"
        return ""

    def pull_projects(self) -> tuple:
        """Fetch all projects for the event. Returns (projects_list, error)."""
        data, err = self._request("GET", f"/api/projects/{self.event_slug}/admin")
        if err:
            return [], f"Failed to fetch projects: {err}"
        if not isinstance(data, list):
            return [], f"Unexpected response format (expected list, got {type(data).__name__})"
        return data, ""

    def projects_to_submissions(self, projects: list, cfg: dict) -> list:
        """Convert Junction API projects to the CSV-like rows read_submissions expects."""
        from .intake import match_challenge
        rows = []
        for p in projects:
            if not isinstance(p, dict):
                continue
            challenges = p.get("challenges") or []
            track = p.get("track") or ""
            challenge_raw = ", ".join(challenges) if challenges else track
            team = p.get("team") or {}
            members = team.get("members") or []
            rows.append({
                "project_id": str(p.get("_id", "")),
                "project": p.get("name") or "",
                "punchline": p.get("punchline") or "",
                "description": p.get("description") or "",
                "source": p.get("source") or "",
                "demo": p.get("demo") or "",
                "video": p.get("video") or "",
                "challenge_raw": challenge_raw,
                "challenge": match_challenge(challenge_raw, cfg["challenges"]),
                "status": p.get("status") or "",
                "team_size": len(members) + (1 if team.get("owner") else 0),
                "source_public": p.get("sourcePublic", True),
            })
        return rows

    # ------------------------------------------------------------------ push

    def problem_push(self) -> str:
        if not self.event_slug:
            return "junction.event_slug is not set in the config"
        if not self.webhook_key and not self.token:
            return "Neither JUNCTION_WEBHOOK_KEY nor JUNCTION_TOKEN is set"
        return ""

    def push_score(self, project_id: str, score: int, max_score: int, message: str,
                   challenge: str = "", status: str = "evaluated") -> tuple:
        """Push a tech score for one project. Returns (ok, error)."""
        body = {
            "project": project_id,
            "event": self.event_slug,
            "score": score,
            "maxScore": max_score,
            "message": message[:500],
            "status": status,
        }
        if challenge:
            body["challenge"] = challenge
        auth = "webhook" if self.webhook_key else "token"
        if auth == "webhook":
            data, err = self._request("POST", "/api/project-scores/", body, auth="webhook")
        else:
            data, err = self._request("POST", f"/api/project-scores/event/{self.event_slug}", body, auth="token")
        if err:
            return False, err
        return True, ""


# ------------------------------------------------------------------ message formatting

def format_score_message(r: dict, cfg: dict) -> str:
    """Build a ≤500-char plain-text summary for Junction's score message field."""
    rv = r.get("review") or {}
    f = r.get("facts") or {}
    tl = f.get("timeline") or {}
    rd = f.get("readme") or {}
    sub = r.get("submission") or {}
    score = rv.get("technical_score")

    parts = []

    # Line 1: score label
    label = {1: "very little working code", 2: "mostly UI or mocked",
             3: "narrow but real feature", 4: "working end to end",
             5: "exceptional for the time"}.get(score, "not reviewed")
    parts.append(f"Tech: {score}/5 — {label}" if score else "Tech: not reviewed")

    # Line 2: code stats
    stats = []
    rq = rd.get("quality")
    if rq is not None:
        stats.append(f"README: {rq}/6")
    lines = f.get("team_code_lines", 0)
    contribs = tl.get("contributors", 0)
    if lines:
        stats.append(f"Code: {lines:,} lines" + (f", {contribs} contrib" if contribs else ""))
    lov = (f.get("lovable") or {}).get("lovable")
    if lov:
        stats.append("Lovable: yes")
    if stats:
        parts.append(" | ".join(stats))

    # Line 3: claims
    cs = rv.get("claims_summary") or {}
    n = sum(cs.values())
    if n:
        bits = [f"Claims: {n} checked"]
        if cs.get("verified"):
            bits.append(f"{cs['verified']} verified")
        mocked = cs.get("mocked", 0) + cs.get("missing", 0)
        if mocked:
            bits.append(f"{mocked} mocked/missing")
        parts.append(", ".join(bits))

    # Line 4: data + AI
    cd = rv.get("challenge_data") or {}
    ai = rv.get("ai_approach") or {}
    line4 = []
    if cd.get("used"):
        line4.append(f"Data: {cd['used']}")
    if ai.get("type") and ai["type"] != "none":
        detail = ai.get("detail", "")
        ai_short = truncate(detail, 40) if detail else ai["type"].replace("_", " ")
        line4.append(f"AI: {ai_short}")
    if line4:
        parts.append(" | ".join(line4))

    # Flags (high first, then medium)
    all_flags = r.get("flags") or []
    ai_flags = (rv.get("red_flags") or [])
    high = [x for x in all_flags if x["severity"] == "high"]
    high += [x for x in ai_flags if x.get("severity") == "high"]
    medium = [x for x in all_flags if x["severity"] == "medium"]
    medium += [x for x in ai_flags if x.get("severity") == "medium"]
    # dedupe by title
    seen = set()
    for fl in high + medium:
        title = fl.get("title", fl.get("type", ""))
        if title in seen:
            continue
        seen.add(title)
        line = f"{FLAG_ICON} {title}"
        candidate = "\n".join(parts) + "\n" + line
        if len(candidate) <= 480:  # leave room for report pointer
            parts.append(line)

    # Report pointer
    chal = sub.get("challenge", "")
    pointer = f"Full report: jury-pack-{chal}.html" if chal else "Full report: jury-pack-all.html"
    candidate = "\n".join(parts) + "\n" + pointer
    if len(candidate) <= 500:
        parts.append(pointer)

    msg = "\n".join(parts)
    return msg[:500]


def write_junction_csv(path, rows: list) -> None:
    """Write pulled Junction data as a CSV the scanner can read."""
    import csv
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "punchline", "description", "source", "demo", "video",
                     "challenges.0", "status", "project_id", "team_size"])
        for r in rows:
            w.writerow([r.get("project", ""), r.get("punchline", ""), r.get("description", ""),
                        r.get("source", ""), r.get("demo", ""), r.get("video", ""),
                        r.get("challenge_raw", ""), r.get("status", ""),
                        r.get("project_id", ""), r.get("team_size", "")])
