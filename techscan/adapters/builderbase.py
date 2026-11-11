"""BuilderBase adapter — pull submissions and push scores via their REST API.

BuilderBase API: https://developers.builderbase.com
Base URL: https://api.builderbase.com
Auth: X-Api-Key header with sk_live_... key (set BUILDERBASE_API_KEY env var)

Endpoint pattern:
  /organizations/{org-slug}                  → org details
  /organizations/{org-slug}/events           → list events
  /events/{event-uuid}/submissions           → list submissions
  /events/{event-uuid}/teams                 → list teams
  /events/{event-uuid}/registrations         → list registrations (paginated)
  /events/{event-uuid}/criteria              → judging criteria

Note: event-scoped endpoints require the UUID, not the slug.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .base import AdapterSubmission, BaseAdapter
from ..util import log


class BuilderBaseClient:
    """Client for the BuilderBase API."""

    def __init__(self, api_key: str = "", base_url: str = "https://api.builderbase.com"):
        self.api_key = api_key or os.environ.get("BUILDERBASE_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body=None, params=None, timeout=30):
        """Make an API request via curl (bypasses Cloudflare UA blocking on urllib).
        Returns (data, error)."""
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        cmd = ["curl", "-s", "-X", method, url, "-H", f"X-Api-Key: {self.api_key}",
               "-H", "Accept: application/json"]
        if body is not None:
            cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
            if result.returncode != 0:
                return None, f"curl failed: {result.stderr[:200]}"
            resp = json.loads(result.stdout)
            if resp.get("success"):
                return resp.get("data", resp), ""
            return None, resp.get("error", "Unknown error")
        except subprocess.TimeoutExpired:
            return None, "Request timed out"
        except (ValueError, OSError) as e:
            return None, str(e)

    def get(self, path, params=None, timeout=30):
        return self._request("GET", path, params=params, timeout=timeout)

    def post(self, path, body=None, timeout=30):
        return self._request("POST", path, body=body, timeout=timeout)

    def put(self, path, body=None, timeout=30):
        return self._request("PUT", path, body=body, timeout=timeout)

    def problem(self) -> str:
        if not self.api_key:
            return "BUILDERBASE_API_KEY is not set"
        return ""

    # ------------------------------------------------------------------ org + events

    def get_organization(self, org_slug: str):
        return self.get(f"/organizations/{org_slug}")

    def get_events(self, org_slug: str):
        return self.get(f"/organizations/{org_slug}/events")

    def get_event_by_slug(self, org_slug: str, event_slug: str):
        """Find an event by its slug within the org's events."""
        data, err = self.get_events(org_slug)
        if err:
            return None, err
        events = data.get("events") or []
        for ev in events:
            if ev.get("slug") == event_slug:
                return ev, ""
        return None, f"Event '{event_slug}' not found in {len(events)} events"

    # ------------------------------------------------------------------ event-scoped (needs UUID)

    def get_submissions(self, event_id: str):
        return self.get(f"/events/{event_id}/submissions")

    def get_teams(self, event_id: str):
        return self.get(f"/events/{event_id}/teams")

    def get_registrations(self, event_id: str, page: int = 1, limit: int = 50):
        return self.get(f"/events/{event_id}/registrations", params={"page": page, "limit": limit})

    def get_criteria(self, event_id: str):
        return self.get(f"/events/{event_id}/criteria")

    # ------------------------------------------------------------------ resolve event

    def resolve_event_id(self, cfg: dict) -> tuple:
        """Get the event UUID from config. Returns (event_id, error)."""
        bb = cfg.get("builderbase") or {}
        event_id = bb.get("event_id", "")
        if event_id and len(event_id) > 30:  # looks like a UUID already
            return event_id, ""
        org_slug = bb.get("org_slug", "hackathon-for-good")
        event_slug = bb.get("event_slug") or event_id
        if not event_slug:
            return "", "builderbase.event_slug or builderbase.event_id is not set"
        ev, err = self.get_event_by_slug(org_slug, event_slug)
        if err:
            return "", err
        return ev.get("id", ""), ""


class BuilderBaseAdapter(BaseAdapter):
    """Pull submissions from BuilderBase."""

    def name(self) -> str:
        return "BuilderBase"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        client = BuilderBaseClient()
        problem = client.problem()
        if problem:
            raise RuntimeError(f"BuilderBase: {problem}")

        event_id, err = client.resolve_event_id(cfg)
        if err:
            raise RuntimeError(f"BuilderBase: {err}")

        data, err = client.get_submissions(event_id)
        if err:
            raise RuntimeError(f"BuilderBase submissions: {err}")

        subs = []
        items = data.get("submissions") or (data if isinstance(data, list) else [])
        for item in items:
            if not isinstance(item, dict):
                continue
            # Map BuilderBase fields to our standard format
            name = (item.get("name") or item.get("title") or item.get("project_name") or "")
            repo = (item.get("repo_url") or item.get("source_code") or item.get("github_url") or "")
            demo = (item.get("demo_url") or item.get("demo") or "")
            video = (item.get("video_url") or item.get("video") or "")
            desc = (item.get("description") or "")
            punchline = (item.get("punchline") or item.get("tagline") or "")
            challenge = (item.get("challenge") or item.get("track") or "")
            status = (item.get("status") or "")

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

        log(f"BuilderBase: {len(subs)} submission(s) from event {event_id[:8]}...")
        return subs
