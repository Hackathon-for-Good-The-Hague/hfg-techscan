"""HackerEarth adapter — pull hackathon submissions via the organizer API.

HackerEarth API: https://www.hackerearth.com/docs/wiki/organizers/
Auth: set HACKEREARTH_CLIENT_ID and HACKEREARTH_CLIENT_SECRET env vars.

Config:
  hackerearth:
    hackathon_slug: "my-hackathon"
    api_url: "https://api.hackerearth.com"  # (default)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .base import AdapterSubmission, BaseAdapter
from ..util import log


class HackerEarthAdapter(BaseAdapter):
    """Pull submissions from a HackerEarth hackathon."""

    def name(self) -> str:
        return "HackerEarth"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        he_cfg = cfg.get("hackerearth") or {}
        client_id = os.environ.get("HACKEREARTH_CLIENT_ID", "")
        client_secret = os.environ.get("HACKEREARTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError("HACKEREARTH_CLIENT_ID and HACKEREARTH_CLIENT_SECRET must be set")
        slug = he_cfg.get("hackathon_slug", "")
        if not slug:
            raise RuntimeError("hackerearth.hackathon_slug is not set in config")
        base_url = he_cfg.get("api_url", "https://api.hackerearth.com").rstrip("/")

        # Get access token
        token = self._get_token(base_url, client_id, client_secret)
        if not token:
            raise RuntimeError("HackerEarth: failed to obtain access token")

        # Fetch submissions
        subs = []
        offset = 0
        limit = 100
        while True:
            url = (f"{base_url}/v4/partner/hackathons/{slug}/submissions/"
                   f"?limit={limit}&offset={offset}")
            data = self._fetch_json(url, token)
            if not data:
                break
            results = data.get("results") or data.get("submissions") or []
            if not results:
                break
            for item in results:
                sub = self._parse_submission(item)
                if sub:
                    subs.append(sub)
            if not data.get("next"):
                break
            offset += limit

        log(f"HackerEarth: {len(subs)} submission(s) from hackathon '{slug}'")
        return subs

    def _parse_submission(self, item: dict) -> AdapterSubmission | None:
        name = item.get("title") or item.get("name") or ""
        if not name:
            return None
        # HackerEarth stores GitHub links in various fields
        repo = (item.get("github_url") or item.get("source_code_url")
                or item.get("repository_url") or "")
        return AdapterSubmission(
            name=name,
            repo_url=repo,
            description=item.get("description") or item.get("summary") or "",
            punchline=item.get("tagline") or "",
            demo_url=item.get("demo_url") or item.get("deployed_url") or "",
            video_url=item.get("video_url") or item.get("pitch_video") or "",
            challenge=item.get("theme") or item.get("track") or "",
            status=item.get("status") or "",
            team_size=item.get("team_size") or 0,
        )

    def _get_token(self, base_url: str, client_id: str, client_secret: str) -> str:
        """Get an OAuth2 access token."""
        url = f"{base_url}/v4/partner/auth/token/"
        body = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
                return data.get("access_token", "")
        except (urllib.error.URLError, ValueError, OSError) as e:
            log(f"HackerEarth token error: {e}")
            return ""

    def _fetch_json(self, url: str, token: str) -> dict | None:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, ValueError, OSError) as e:
            log(f"HackerEarth API error: {e}")
            return None
