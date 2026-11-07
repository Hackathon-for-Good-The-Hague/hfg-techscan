"""Junction platform integration: pull project CSV via tRPC, push scores to Gavel.

The hosted Junction platform (www.junctionorganize.com) uses:
  - Clerk for authentication (__session cookie = short-lived JWT)
  - tRPC API at /api/trpc/
  - Gavel (external) for scoring and peer review

Pull workflow:
  1. User grabs __session cookie from browser DevTools
  2. CLI calls gavelExportProjectsCsv tRPC endpoint
  3. Saves CSV that the scanner can read

Push workflow (via Gavel):
  1. Organizer deploys a Gavel instance and connects it in Junction
  2. After scan, CLI pushes tech scores to Gavel's API
  3. Scores appear in Junction's results

Fallback: user downloads CSV manually from Junction's dashboard (Projects → CSV button).
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import log, truncate

FLAG_ICON = "\u26a0"  # ⚠


class JunctionClient:
    """Client for the Junction organizer platform (tRPC + Clerk auth)."""

    def __init__(self, cfg: dict):
        jcfg = cfg.get("junction") or {}
        self.api_url = (jcfg.get("api_url") or "https://www.junctionorganize.com").rstrip("/")
        self.event_slug = jcfg.get("event_slug") or ""
        self.event_id = jcfg.get("event_id") or ""
        self.push_scores = jcfg.get("push_scores", False)
        self.gavel_url = (jcfg.get("gavel_url") or "").rstrip("/")
        self.gavel_token = os.environ.get("GAVEL_ADMIN_TOKEN") or jcfg.get("gavel_token", "")
        # Clerk session token: either fresh __session cookie or long-lived __client cookie
        self.session_token = os.environ.get("JUNCTION_SESSION") or ""
        self.client_token = os.environ.get("JUNCTION_CLIENT") or ""
        self.clerk_domain = jcfg.get("clerk_domain") or "clerk.hackjunction.app"

    def _refresh_session(self) -> bool:
        """Use the long-lived __client JWT to get a fresh __session token via Clerk's token endpoint."""
        if not self.client_token:
            return False
        # First, get the active session ID from the client endpoint
        session_id = self._get_active_session_id()
        if not session_id:
            return False
        url = f"https://{self.clerk_domain}/v1/client/sessions/{session_id}/tokens?__clerk_api_version=2025-11-10"
        headers = {"Cookie": f"__client={self.client_token}",
                   "Content-Type": "application/x-www-form-urlencoded",
                   "User-Agent": "hfg-techscan"}
        req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
                jwt = data.get("jwt") or ""
                if jwt:
                    self.session_token = jwt
                    return True
        except Exception:
            pass
        return False

    def _get_active_session_id(self) -> str:
        """Get the active session ID from the Clerk client endpoint."""
        url = f"https://{self.clerk_domain}/v1/client?__clerk_api_version=2025-11-10"
        headers = {"Cookie": f"__client={self.client_token}",
                   "User-Agent": "hfg-techscan"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
                resp = data.get("response") or data
                sessions = resp.get("sessions") or []
                for s in sessions:
                    if s.get("status") == "active":
                        return s.get("id", "")
                # Fall back to first session if none marked active
                if sessions:
                    return sessions[0].get("id", "")
        except Exception:
            pass
        return ""

    def _trpc(self, procedures: list, inputs: list, timeout=30):
        """Call one or more tRPC queries in a batch. Returns (results_list, error)."""
        # If no session token, try to get one from the client token first
        if not self.session_token and self.client_token:
            self._refresh_session()
        for attempt in range(2):
            result, err = self._trpc_once(procedures, inputs, timeout)
            if err and "Authentication failed" in err and attempt == 0 and self.client_token:
                log("  Session token expired, refreshing...")
                if self._refresh_session():
                    continue
            return result, err
        return None, "Authentication failed after refresh attempt"

    def _trpc_once(self, procedures, inputs, timeout):
        names = ",".join(procedures)
        batch_input = {str(i): {"json": inp} for i, inp in enumerate(inputs)}
        qs = urllib.parse.urlencode({"batch": "1", "input": json.dumps(batch_input, separators=(",", ":"))})
        url = f"{self.api_url}/api/trpc/{names}?{qs}"
        headers = {"Accept": "*/*", "Content-Type": "application/json",
                   "User-Agent": "hfg-techscan"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
                if isinstance(data, list):
                    return data, ""
                return [data], ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code == 401:
                return None, "Authentication failed. Refresh your __session cookie and set JUNCTION_SESSION."
            return None, f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"Connection error: {e}"

    # ------------------------------------------------------------------ pull

    def problem_pull(self) -> str:
        if not self.event_id and not self.event_slug:
            return "junction.event_id (or event_slug) is not set in the config"
        if not self.session_token and not self.client_token:
            return ("JUNCTION_SESSION or JUNCTION_CLIENT is not set. "
                    "In your browser: DevTools → Application → Cookies → www.junctionorganize.com → "
                    "copy __client value (long-lived) and run: export JUNCTION_CLIENT=eyJ...")
        return ""

    def pull_projects_csv(self) -> tuple:
        """Fetch projects CSV via the tRPC export endpoint. Returns (csv_string, error)."""
        event_id = self.event_id or self.event_slug
        results, err = self._trpc(
            ["gavelExportProjectsCsv"],
            [{"eventId": event_id}],
        )
        if err:
            return "", f"Failed to fetch projects: {err}"
        try:
            csv_data = results[0]["result"]["data"]["json"]
            if not csv_data or not isinstance(csv_data, str):
                return "", "Export returned empty data"
            return csv_data, ""
        except (KeyError, IndexError, TypeError) as e:
            return "", f"Unexpected response format: {e}"

    def pull_all_data(self) -> tuple:
        """Fetch event info + projects CSV in one batch call. Returns (event_data, csv_string, error)."""
        event_id = self.event_id or self.event_slug
        results, err = self._trpc(
            ["getEventById", "gavelExportProjectsCsv"],
            [{"eventId": event_id}, {"eventId": event_id}],
        )
        if err:
            return None, "", err
        try:
            event_data = results[0]["result"]["data"]["json"] if len(results) > 0 else None
            csv_data = results[1]["result"]["data"]["json"] if len(results) > 1 else ""
            return event_data, csv_data or "", ""
        except (KeyError, IndexError, TypeError) as e:
            return None, "", f"Unexpected response format: {e}"

    # ------------------------------------------------------------------ push (via Gavel)

    def problem_push(self) -> str:
        if not self.gavel_url:
            return ("junction.gavel_url is not set. Deploy Gavel from Junction's dashboard, "
                    "then set the URL in the config.")
        if not self.gavel_token:
            return "GAVEL_ADMIN_TOKEN is not set (the admin password from Junction's Gavel Connection panel)"
        return ""

    def push_score(self, project_name: str, score: float, message: str) -> tuple:
        """Push a tech score annotation to the Gavel instance. Returns (ok, error)."""
        # Gavel's API for adding/updating item descriptions with scores
        body = {"description": message[:500]}
        url = f"{self.gavel_url}/api/items/by-name/{urllib.parse.quote(project_name)}"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Authorization": f"Bearer {self.gavel_token}"}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return True, ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return False, f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return False, f"Connection error: {e}"


# ------------------------------------------------------------------ message formatting

def format_score_message(r: dict, cfg: dict) -> str:
    """Build a ≤500-char plain-text summary for the score message field."""
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
        if len(candidate) <= 480:
            parts.append(line)

    # Report pointer
    chal = sub.get("challenge", "")
    pointer = f"Full report: jury-pack-{chal}.html" if chal else "Full report: jury-pack-all.html"
    candidate = "\n".join(parts) + "\n" + pointer
    if len(candidate) <= 500:
        parts.append(pointer)

    msg = "\n".join(parts)
    return msg[:500]


def save_projects_csv(csv_string: str, path) -> int:
    """Save a CSV string from Junction's export to a file the scanner can read. Returns row count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The Junction export may have minimal columns (name, location, description).
    # Write it as-is; the intake module handles column discovery.
    path.write_text(csv_string, encoding="utf-8-sig")
    return csv_string.count("\n")


def write_junction_csv(path, rows: list) -> None:
    """Write pulled Junction data as a CSV the scanner can read."""
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
