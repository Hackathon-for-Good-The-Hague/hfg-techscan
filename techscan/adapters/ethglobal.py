"""ETHGlobal adapter — scrapes the ETHGlobal showcase for hackathon submissions.

ETHGlobal doesn't have a documented public API, but their showcase pages
are structured and scrapeable. This adapter pulls project data from:
  https://ethglobal.com/showcase?events=<event-slug>

Config:
  ethglobal:
    event_slug: "ethglobal-brussels"
"""
from __future__ import annotations

import json
import re
import urllib.request

from .base import AdapterSubmission, BaseAdapter
from ..util import log


class ETHGlobalAdapter(BaseAdapter):
    """Pull submissions from an ETHGlobal showcase page."""

    def name(self) -> str:
        return "ETHGlobal"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        eg_cfg = cfg.get("ethglobal") or {}
        slug = eg_cfg.get("event_slug", "")
        if not slug:
            raise RuntimeError("ethglobal.event_slug is not set in config")

        subs = []
        page_url = f"https://ethglobal.com/showcase?events={slug}"
        html = self._fetch(page_url)
        projects = self._parse_showcase(html)
        for p in projects:
            if p.get("url"):
                detail_html = self._fetch(p["url"])
                repo, demo, video = self._extract_links(detail_html)
                p["repo_url"] = repo
                p["demo_url"] = demo
                p["video_url"] = video
            subs.append(AdapterSubmission(
                name=p.get("name", ""),
                repo_url=p.get("repo_url", ""),
                description=p.get("description", ""),
                demo_url=p.get("demo_url", ""),
                video_url=p.get("video_url", ""),
            ))

        log(f"ETHGlobal: {len(subs)} project(s) from showcase '{slug}'")
        return subs

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "hfg-techscan"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _parse_showcase(self, html: str) -> list[dict]:
        """Extract projects from ETHGlobal showcase listing."""
        projects = []
        # ETHGlobal uses Next.js with __NEXT_DATA__ JSON blob
        m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                page_props = data.get("props", {}).get("pageProps", {})
                for p in page_props.get("projects", []):
                    projects.append({
                        "name": p.get("name", ""),
                        "description": p.get("description", ""),
                        "url": f"https://ethglobal.com/showcase/{p['slug']}" if p.get("slug") else "",
                    })
                return projects
            except (ValueError, KeyError):
                pass
        # Fallback: regex scraping
        for m in re.finditer(
            r'href="(/showcase/[^"]+)"[^>]*>.*?<h3[^>]*>([^<]+)</h3>',
            html, re.S
        ):
            projects.append({
                "name": m.group(2).strip(),
                "url": f"https://ethglobal.com{m.group(1)}",
                "description": "",
            })
        return projects

    def _extract_links(self, html: str) -> tuple[str, str, str]:
        """Extract repo, demo, and video links from a project detail page."""
        repo, demo, video = "", "", ""
        for m in re.finditer(r'href="(https?://[^"]+)"', html):
            url = m.group(1)
            if "github.com" in url and not repo:
                repo = re.sub(r"/(tree|blob)/.*$", "", url.rstrip("/"))
            elif "youtube.com" in url or "youtu.be" in url or "loom.com" in url:
                if not video:
                    video = url
        # Demo links are often in a specific section
        dm = re.search(r'(?:demo|live|deployed)[^"]*href="(https?://[^"]+)"', html, re.I)
        if dm:
            demo = dm.group(1)
        return repo, demo, video
