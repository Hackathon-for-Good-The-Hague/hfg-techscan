"""Plain CSV file adapter — reads a local CSV with team/repo data."""
from __future__ import annotations
import csv
from pathlib import Path
from .base import BaseAdapter, AdapterSubmission

class CSVAdapter(BaseAdapter):
    def __init__(self, path: str):
        self.path = Path(path)

    def name(self) -> str:
        return f"CSV ({self.path.name})"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        subs = []
        with open(self.path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                subs.append(AdapterSubmission(
                    name=row.get("name", row.get("project", "")),
                    repo_url=row.get("source", row.get("repo", row.get("repository", ""))),
                    description=row.get("description", ""),
                    punchline=row.get("punchline", row.get("tagline", "")),
                    demo_url=row.get("demo", row.get("demo_url", "")),
                    video_url=row.get("video", row.get("video_url", "")),
                    challenge=row.get("challenge", row.get("track", "")),
                    status=row.get("status", ""),
                ))
        return subs
