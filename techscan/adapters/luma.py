"""Luma (lu.ma) adapter — pull hackathon submissions from a Luma event.

Luma has no built-in "submissions" concept. Instead, organizers configure custom
registration questions (Team Name, Repo URL, Demo URL, etc.) and answers come
back in the guest's `registration_answers` array. This adapter maps those answers
to TechScan's submission fields by matching on the question label.

API docs: https://docs.luma.com/reference/getting-started-with-your-api
Auth: set LUMA_API_KEY env var (generate at lu.ma/calendar/manage/api-keys).
Requires Luma Plus subscription on the calendar.

Config:
  luma:
    event_id: "evt-..."                          # Event ID from your Luma event URL
    api_url: "https://public-api.luma.com"       # (default)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .base import AdapterSubmission, BaseAdapter
from ..util import log


class LumaAdapter(BaseAdapter):
    """Pull submissions from a Luma event via registration answers."""

    def name(self) -> str:
        return "Luma"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        luma_cfg = cfg.get("luma") or {}
        api_key = os.environ.get("LUMA_API_KEY", "")
        if not api_key:
            raise RuntimeError("LUMA_API_KEY is not set (generate at lu.ma/calendar/manage/api-keys)")
        event_id = luma_cfg.get("event_id", "")
        if not event_id:
            raise RuntimeError("luma.event_id is not set in config")
        base_url = luma_cfg.get("api_url", "https://public-api.luma.com").rstrip("/")

        # Fetch all guests with their registration answers
        subs = []
        cursor = ""
        while True:
            params = {"event_id": event_id, "pagination_limit": "100"}
            if cursor:
                params["pagination_cursor"] = cursor
            url = f"{base_url}/v1/events/guests/list?{urllib.parse.urlencode(params)}"
            data = self._fetch_json(url, api_key)
            if not data:
                break
            entries = data.get("entries") or []
            if not entries:
                break
            for entry in entries:
                sub = self._parse_guest(entry)
                if sub:
                    subs.append(sub)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor", "")
            if not cursor:
                break

        log(f"Luma: {len(subs)} submission(s) from event {event_id}")
        return subs

    def _parse_guest(self, entry: dict) -> AdapterSubmission | None:
        """Parse a guest entry into an AdapterSubmission."""
        # The entry may contain the guest directly or nested
        guest = entry if "user_name" in entry else entry.get("guest", entry)
        name = guest.get("user_name") or guest.get("name") or ""
        if not name:
            return None

        # Registration answers hold the custom form data
        answers = guest.get("registration_answers") or []
        answer_map = self._answers_to_map(answers)

        repo_url = self._find_field(answer_map, ["github", "repo", "repository", "source_code", "github_url", "code"])
        demo_url = self._find_field(answer_map, ["demo", "demo_url", "live_url", "deployed", "website"])
        video_url = self._find_field(answer_map, ["video", "video_url", "pitch", "youtube", "loom"])
        description = self._find_field(answer_map, ["description", "about", "summary", "project"])
        challenge = self._find_field(answer_map, ["challenge", "track", "category", "theme"])
        team_name = self._find_field(answer_map, ["team_name", "team", "project_name", "project_title"])

        return AdapterSubmission(
            name=team_name or name,
            repo_url=repo_url,
            description=description,
            demo_url=demo_url,
            video_url=video_url,
            challenge=challenge,
            status=guest.get("approval_status") or "",
        )

    def _answers_to_map(self, answers) -> dict:
        """Convert registration_answers array to a {label: value} dict."""
        if isinstance(answers, dict):
            return answers
        if not isinstance(answers, list):
            return {}
        result = {}
        for ans in answers:
            if not isinstance(ans, dict):
                continue
            label = ans.get("label", "")
            value = ans.get("value", "")
            if label and value:
                result[label] = value
        return result

    def _find_field(self, answer_map: dict, keywords: list) -> str:
        """Find a field value by matching question labels against keywords."""
        for label, value in answer_map.items():
            low = label.lower().replace(" ", "_").replace("-", "_")
            for kw in keywords:
                if kw in low:
                    val = str(value).strip() if value else ""
                    if val:
                        return val
        return ""

    def _fetch_json(self, url: str, api_key: str) -> dict | None:
        req = urllib.request.Request(url, headers={
            "x-luma-api-key": api_key,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, ValueError, OSError) as e:
            log(f"Luma API error: {e}")
            return None
