"""BuilderBase adapter — pull submissions and push scores via their REST API.

BuilderBase API docs: https://developers.builderbase.com
Base URL: https://api.builderbase.com
Auth: X-Api-Key header with sk_live_... key (set BUILDERBASE_API_KEY env var)

Unlike Junction (which required browser cookie hacking), BuilderBase has a
clean REST API with API key auth. This adapter can both pull submissions
and potentially push scores back into their judging system.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .base import AdapterSubmission, BaseAdapter
from ..util import log


class BuilderBaseClient:
    """Low-level client for the BuilderBase API."""

    def __init__(self, api_key: str = "", base_url: str = "https://api.builderbase.com"):
        self.api_key = api_key or os.environ.get("BUILDERBASE_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body=None, params=None, timeout=30):
        """Make an API request. Returns (data, error)."""
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "hfg-techscan",
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8", errors="replace"))
                if resp.get("success"):
                    return resp.get("data", resp), ""
                return None, resp.get("error", "Unknown error")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", errors="replace")).get("error", f"HTTP {e.code}")
            except (ValueError, AttributeError):
                detail = f"HTTP {e.code}"
            return None, detail
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"Connection error: {e}"

    def get(self, path, params=None, timeout=30):
        return self._request("GET", path, params=params, timeout=timeout)

    def post(self, path, body=None, timeout=30):
        return self._request("POST", path, body=body, timeout=timeout)

    def problem(self) -> str:
        if not self.api_key:
            return "BUILDERBASE_API_KEY is not set"
        return ""

    # ------------------------------------------------------------------ pull

    def get_events(self):
        """List all events the API key has access to."""
        return self.get("/events")

    def get_event(self, event_id):
        """Get a single event by ID."""
        return self.get(f"/events/{event_id}")

    def get_submissions(self, event_id=None, params=None):
        """List submissions, optionally filtered by event."""
        p = dict(params or {})
        if event_id:
            p["eventId"] = event_id
        return self.get("/submissions", params=p)

    def get_teams(self, event_id=None, params=None):
        """List teams, optionally filtered by event."""
        p = dict(params or {})
        if event_id:
            p["eventId"] = event_id
        return self.get("/teams", params=p)

    def get_registrations(self, event_id=None):
        """List registrations for an event."""
        p = {"eventId": event_id} if event_id else {}
        return self.get("/registrations", params=p)

    def get_judging_criteria(self, event_id):
        """Get the judging criteria for an event."""
        return self.get(f"/events/{event_id}/criteria")

    # ------------------------------------------------------------------ push (TBD)

    # Score push endpoints are not yet documented in BuilderBase's v0.1.0 API.
    # When they become available, add methods here for submitting TechScan scores.


class BuilderBaseAdapter(BaseAdapter):
    """Pull submissions from BuilderBase."""

    def name(self) -> str:
        return "BuilderBase"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        bb_cfg = (cfg.get("builderbase") or cfg.get("junction") or {})
        event_id = bb_cfg.get("event_id", "")
        client = BuilderBaseClient()
        problem = client.problem()
        if problem:
            raise RuntimeError(f"BuilderBase: {problem}")

        # Try to get submissions
        data, err = client.get_submissions(event_id)
        if err:
            raise RuntimeError(f"BuilderBase submissions: {err}")

        subs = []
        items = data if isinstance(data, list) else (data.get("submissions") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            # Extract fields — field names may vary; handle common patterns
            name = item.get("name") or item.get("title") or item.get("projectName") or ""
            repo = item.get("repoUrl") or item.get("sourceCode") or item.get("github") or ""
            demo = item.get("demoUrl") or item.get("demo") or ""
            video = item.get("videoUrl") or item.get("video") or ""
            desc = item.get("description") or item.get("about") or ""
            punchline = item.get("punchline") or item.get("tagline") or ""
            challenge = item.get("challenge") or item.get("track") or ""
            status = item.get("status") or ""

            subs.append(AdapterSubmission(
                name=name,
                repo_url=repo,
                description=desc,
                punchline=punchline,
                demo_url=demo,
                video_url=video,
                challenge=challenge,
                status=status,
            ))

        log(f"BuilderBase: pulled {len(subs)} submission(s)" + (f" for event {event_id}" if event_id else ""))
        return subs
