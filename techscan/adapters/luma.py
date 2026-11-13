"""Luma (lu.ma) adapter — pull hackathon submissions from a Luma event.

Luma's API allows fetching event guests and their submissions.
Auth: set LUMA_API_KEY env var (get one from lu.ma/settings/api).

Config:
  luma:
    event_id: "evt-..."           # Event ID from your Luma dashboard URL
    api_url: "https://api.lu.ma"  # (default)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .base import AdapterSubmission, BaseAdapter
from ..util import log


class LumaAdapter(BaseAdapter):
    """Pull submissions from a Luma event."""

    def name(self) -> str:
        return "Luma"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        luma_cfg = cfg.get("luma") or {}
        api_key = os.environ.get("LUMA_API_KEY", "")
        if not api_key:
            raise RuntimeError("LUMA_API_KEY is not set")
        event_id = luma_cfg.get("event_id", "")
        if not event_id:
            raise RuntimeError("luma.event_id is not set in config")
        base_url = luma_cfg.get("api_url", "https://api.lu.ma").rstrip("/")

        # Fetch event guests/submissions
        subs = []
        cursor = ""
        while True:
            url = f"{base_url}/public/v1/event/get-guests?event_api_id={event_id}&pagination_limit=100"
            if cursor:
                url += f"&pagination_cursor={cursor}"
            data = self._fetch_json(url, api_key)
            if not data:
                break
            entries = data.get("entries") or []
            if not entries:
                break
            for entry in entries:
                guest = entry.get("guest") or {}
                event_data = entry.get("event") or {}
                sub = self._parse_guest(guest, event_data)
                if sub:
                    subs.append(sub)
            # Pagination
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor", "")
            if not cursor:
                break

        log(f"Luma: {len(subs)} submission(s) from event {event_id}")
        return subs

    def _parse_guest(self, guest: dict, event: dict) -> AdapterSubmission | None:
        name = guest.get("name") or guest.get("user_name") or ""
        if not name:
            return None
        # Luma stores custom form responses in approval_answers or registration_answers
        answers = guest.get("approval_answers") or guest.get("registration_answers") or {}
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except ValueError:
                answers = {}
        repo_url = self._find_field(answers, ["github", "repo", "repository", "source_code", "github_url"])
        demo_url = self._find_field(answers, ["demo", "demo_url", "live_url", "deployed"])
        video_url = self._find_field(answers, ["video", "video_url", "pitch", "youtube"])
        description = self._find_field(answers, ["description", "about", "summary", "project_description"])
        challenge = self._find_field(answers, ["challenge", "track", "category"])

        return AdapterSubmission(
            name=name,
            repo_url=repo_url,
            description=description,
            demo_url=demo_url,
            video_url=video_url,
            challenge=challenge,
            status=guest.get("approval_status") or "",
        )

    def _find_field(self, answers: dict, keywords: list) -> str:
        """Find a field value by trying common field name patterns."""
        if not isinstance(answers, dict):
            return ""
        for key, value in answers.items():
            low = key.lower().replace(" ", "_").replace("-", "_")
            for kw in keywords:
                if kw in low:
                    return str(value).strip() if value else ""
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
