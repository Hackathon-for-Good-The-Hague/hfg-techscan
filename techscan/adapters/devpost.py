"""DevPost adapter — scrapes a DevPost gallery page for submissions.

Usage: set the gallery URL in the config:
  adapters:
    devpost:
      gallery_url: "https://your-hackathon.devpost.com/project-gallery"
"""
from __future__ import annotations

import re
import urllib.request

from .base import AdapterSubmission, BaseAdapter


class DevPostAdapter(BaseAdapter):
    """Pull submissions from a DevPost hackathon gallery."""

    def __init__(self, gallery_url: str = ""):
        self.gallery_url = gallery_url

    def name(self) -> str:
        return "DevPost"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        url = self.gallery_url or (cfg.get("adapters") or {}).get("devpost", {}).get("gallery_url", "")
        if not url:
            raise RuntimeError("DevPost gallery_url not configured")
        subs = []
        page = 1
        while True:
            page_url = f"{url.rstrip('/')}?page={page}"
            html = self._fetch(page_url)
            projects = self._parse_gallery(html)
            if not projects:
                break
            for p in projects:
                detail = self._fetch(p["url"]) if p.get("url") else ""
                repo = self._extract_repo(detail)
                subs.append(AdapterSubmission(
                    name=p.get("name", ""),
                    repo_url=repo,
                    description=p.get("tagline", ""),
                    demo_url=self._extract_link(detail, "demo") or self._extract_link(detail, "try it"),
                    video_url=self._extract_link(detail, "video") or self._extract_link(detail, "youtube"),
                ))
            page += 1
            if page > 20:  # safety limit
                break
        return subs

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "hfg-techscan"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _parse_gallery(self, html: str) -> list[dict]:
        """Extract project cards from a DevPost gallery page."""
        projects = []
        # DevPost gallery uses <a class="block-wrapper-link" ...> for each project
        for m in re.finditer(
            r'<a[^>]*class="[^"]*block-wrapper-link[^"]*"[^>]*href="([^"]+)"[^>]*>.*?'
            r'<h5[^>]*>([^<]+)</h5>.*?(?:<p[^>]*class="[^"]*tagline[^"]*"[^>]*>([^<]*)</p>)?',
            html, re.S
        ):
            projects.append({"url": m.group(1), "name": m.group(2).strip(),
                             "tagline": (m.group(3) or "").strip()})
        return projects

    def _extract_repo(self, html: str) -> str:
        """Find a GitHub repo link in a DevPost project detail page."""
        m = re.search(r'href="(https?://github\.com/[^"]+)"', html)
        if m:
            url = m.group(1).rstrip("/")
            url = re.sub(r"/(tree|blob)/.*$", "", url)
            return re.sub(r"\.git$", "", url)
        return ""

    def _extract_link(self, html: str, keyword: str) -> str:
        """Find a link whose text contains the keyword."""
        for m in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]*)</a>', html, re.I):
            if keyword.lower() in m.group(2).lower():
                return m.group(1)
        return ""
