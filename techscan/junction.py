"""Junction platform integration: pull submissions via tRPC API.

The hosted Junction platform (www.junctionorganize.com) uses:
  - Clerk for authentication (__client cookie → auto-refreshed session JWTs)
  - tRPC API at /api/trpc/
  - Bearer token auth (not cookies) for API calls

Pull workflow:
  1. Set JUNCTION_CLIENT env var (long-lived __client cookie from browser DevTools)
  2. CLI auto-refreshes short-lived session tokens via Clerk's token endpoint
  3. Calls gavelExportProjectsCsv tRPC endpoint → saves CSV for the scanner

Fallback: download CSV manually from Junction dashboard (Projects → CSV button).

Score push is not available — Junction's hosted platform has no API for external
score submission. Share the jury pack HTML and scorecard XLSX with judges directly.
"""
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
    """Client for the Junction organizer platform (tRPC + Clerk auth)."""

    def __init__(self, cfg: dict):
        jcfg = cfg.get("junction") or {}
        self.api_url = (jcfg.get("api_url") or "https://www.junctionorganize.com").rstrip("/")
        self.event_slug = jcfg.get("event_slug") or ""
        self.event_id = jcfg.get("event_id") or ""
        self.clerk_domain = jcfg.get("clerk_domain") or "clerk.hackjunction.app"
        # Clerk tokens: __client is long-lived, __session is auto-refreshed from it
        self.client_token = os.environ.get("JUNCTION_CLIENT") or ""
        self.session_token = os.environ.get("JUNCTION_SESSION") or ""

    # ------------------------------------------------------------------ Clerk auth

    def _refresh_session(self) -> bool:
        """Use the long-lived __client JWT to get a fresh __session token via Clerk."""
        if not self.client_token:
            return False
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
                jwt = json.loads(r.read().decode("utf-8", errors="replace")).get("jwt", "")
                if jwt:
                    self.session_token = jwt
                    return True
        except Exception:
            pass
        return False

    def _get_active_session_id(self) -> str:
        """Get the active session ID from the Clerk client endpoint."""
        url = f"https://{self.clerk_domain}/v1/client?__clerk_api_version=2025-11-10"
        headers = {"Cookie": f"__client={self.client_token}", "User-Agent": "hfg-techscan"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
                sessions = (data.get("response") or data).get("sessions") or []
                for s in sessions:
                    if s.get("status") == "active":
                        return s.get("id", "")
                return sessions[0].get("id", "") if sessions else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ tRPC

    def _trpc(self, procedures: list, inputs: list, timeout=30):
        """Call tRPC queries with auto-refresh on 401. Returns (results_list, error)."""
        if not self.session_token and self.client_token:
            self._refresh_session()
        for attempt in range(2):
            result, err = self._trpc_once(procedures, inputs, timeout)
            if err and "Authentication failed" in err and attempt == 0 and self.client_token:
                log("  Session expired, refreshing...")
                if self._refresh_session():
                    continue
            return result, err
        return None, "Authentication failed after refresh"

    def _trpc_once(self, procedures, inputs, timeout):
        names = ",".join(procedures)
        batch_input = {str(i): {"json": inp} for i, inp in enumerate(inputs)}
        qs = urllib.parse.urlencode({"batch": "1", "input": json.dumps(batch_input, separators=(",", ":"))})
        url = f"{self.api_url}/api/trpc/{names}?{qs}"
        headers = {"Accept": "*/*", "Content-Type": "application/json", "User-Agent": "hfg-techscan"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
                return (data if isinstance(data, list) else [data]), ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code == 401:
                return None, "Authentication failed. Set JUNCTION_CLIENT (browser → DevTools → Cookies → __client)."
            return None, f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"Connection error: {e}"

    # ------------------------------------------------------------------ pull

    def problem_pull(self) -> str:
        if not self.event_id and not self.event_slug:
            return "junction.event_id is not set in the config"
        if not self.session_token and not self.client_token:
            return ("JUNCTION_CLIENT is not set. In your browser: DevTools → Application → "
                    "Cookies → .clerk.hackjunction.app → copy __client → export JUNCTION_CLIENT=eyJ...")
        return ""

    def pull_projects_csv(self) -> tuple:
        """Fetch projects CSV via tRPC export. Returns (csv_string, error)."""
        event_id = self.event_id or self.event_slug
        results, err = self._trpc(["gavelExportProjectsCsv"], [{"eventId": event_id}])
        if err:
            return "", f"Failed to fetch projects: {err}"
        try:
            csv_data = results[0]["result"]["data"]["json"]
            if not csv_data or not isinstance(csv_data, str):
                return "", "Export returned empty data"
            return csv_data, ""
        except (KeyError, IndexError, TypeError) as e:
            return "", f"Unexpected response format: {e}"


# ------------------------------------------------------------------ message formatting

def format_score_message(r: dict, cfg: dict) -> str:
    """Build a compact plain-text summary of a team's tech scan results."""
    rv = r.get("review") or {}
    f = r.get("facts") or {}
    tl = f.get("timeline") or {}
    rd = f.get("readme") or {}
    sub = r.get("submission") or {}
    score = rv.get("technical_score")

    parts = []

    label = {1: "very little working code", 2: "mostly UI or mocked",
             3: "narrow but real feature", 4: "working end to end",
             5: "exceptional for the time"}.get(score, "not reviewed")
    parts.append(f"Tech: {score}/5 — {label}" if score else "Tech: not reviewed")

    stats = []
    rq = rd.get("quality")
    if rq is not None:
        stats.append(f"README: {rq}/6")
    lines = f.get("team_code_lines", 0)
    contribs = tl.get("contributors", 0)
    if lines:
        stats.append(f"Code: {lines:,} lines" + (f", {contribs} contrib" if contribs else ""))
    if (f.get("lovable") or {}).get("lovable"):
        stats.append("Lovable: yes")
    if stats:
        parts.append(" | ".join(stats))

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

    cd = rv.get("challenge_data") or {}
    ai = rv.get("ai_approach") or {}
    line4 = []
    if cd.get("used"):
        line4.append(f"Data: {cd['used']}")
    if ai.get("type") and ai["type"] != "none":
        detail = ai.get("detail", "")
        line4.append(f"AI: {truncate(detail, 40) if detail else ai['type'].replace('_', ' ')}")
    if line4:
        parts.append(" | ".join(line4))

    all_flags = r.get("flags") or []
    ai_flags = (rv.get("red_flags") or [])
    flaglist = ([x for x in all_flags if x["severity"] == "high"]
                + [x for x in ai_flags if x.get("severity") == "high"]
                + [x for x in all_flags if x["severity"] == "medium"]
                + [x for x in ai_flags if x.get("severity") == "medium"])
    seen = set()
    for fl in flaglist:
        title = fl.get("title", fl.get("type", ""))
        if title in seen:
            continue
        seen.add(title)
        line = f"{FLAG_ICON} {title}"
        if len("\n".join(parts) + "\n" + line) <= 480:
            parts.append(line)

    chal = sub.get("challenge", "")
    pointer = f"Full report: jury-pack-{chal}.html" if chal else "Full report: jury-pack-all.html"
    if len("\n".join(parts) + "\n" + pointer) <= 500:
        parts.append(pointer)

    return "\n".join(parts)[:500]


def save_projects_csv(csv_string: str, path) -> int:
    """Save a CSV string from Junction's export to a file. Returns row count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(csv_string, encoding="utf-8-sig")
    return csv_string.count("\n")
