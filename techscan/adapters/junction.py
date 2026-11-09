"""Junction platform adapter."""
from __future__ import annotations
from .base import BaseAdapter, AdapterSubmission
from ..junction import JunctionClient, save_projects_csv

class JunctionAdapter(BaseAdapter):
    def name(self) -> str:
        return "Junction"

    def pull(self, cfg: dict) -> list[AdapterSubmission]:
        client = JunctionClient(cfg)
        problem = client.problem_pull()
        if problem:
            raise RuntimeError(f"Junction: {problem}")
        csv_data, err = client.pull_projects_csv()
        if err:
            raise RuntimeError(f"Junction pull failed: {err}")
        # Parse the CSV into AdapterSubmission objects
        import csv, io
        subs = []
        reader = csv.DictReader(io.StringIO(csv_data))
        for row in reader:
            subs.append(AdapterSubmission(
                name=row.get("name", ""),
                repo_url=row.get("source", ""),
                description=row.get("description", ""),
            ))
        return subs
