"""Offline tests for submission sources: GitHub PR intake, PR-link resolution and merging.

Run: python -m unittest discover -s tests
"""
import tempfile
import textwrap
import unittest
from pathlib import Path

from techscan.config import load_config
from techscan.intake import PR_NOTE, read_submissions
from techscan.sources import (is_non_team_pr, merge_rows, pr_row, pull_csv, pull_github_prs, resolve_pr_links,
                              team_name_from_body, write_rows)

PR_TEMPLATE_BODY = textwrap.dedent("""\
    ## Summary
    <!-- One paragraph: what does this PR add or change? -->
    A classifier for sovereignty levels.

    ## Type of contribution
    - [x] **Hackathon team submission** (one PR per team, fork, PR into `main`)
    - [ ] Starter-repo improvement (docs, data, tooling, CI)
    - [ ] Bug fix

    ### Team
    - **Team name:** Soevereine Sloths
    - **Members:**
    """)

STARTER_FIX_BODY = PR_TEMPLATE_BODY.replace("[x] **Hackathon", "[ ] **Hackathon").replace(
    "[ ] Starter-repo", "[x] Starter-repo")


def pr(number, owner, branch="main", body=PR_TEMPLATE_BODY, created="2026-12-09T10:00:00Z", user="team", repo=None):
    head_repo = None if owner is None else {"full_name": f"{owner}/{repo or 'onegov3-inkooptafel'}",
                                             "owner": {"login": owner}}
    return {"number": number, "title": f"Submission {number}", "body": body, "state": "open", "draft": False,
            "html_url": f"https://github.com/govtechnl/onegov3-inkooptafel/pull/{number}",
            "created_at": created, "user": {"login": user}, "head": {"ref": branch, "repo": head_repo}}


class FakeGitHub:
    def __init__(self, prs):
        self.prs = prs

    def pulls(self, owner, repo, state="open"):
        return [p for p in self.prs if p["html_url"].startswith(f"https://github.com/{owner}/{repo}/")], ""

    def pull(self, owner, repo, number):
        for p in self.prs:
            if p["html_url"].endswith(f"{owner}/{repo}/pull/{number}"):
                return p, ""
        return None, "HTTP 404"


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "cfg.yaml").write_text(textwrap.dedent("""\
            event: {name: Test, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}
            challenges:
              inkooptafel:
                name: Inkooptafel
                template: https://github.com/govtechnl/onegov3-inkooptafel.git
            sources:
              - {type: csv, path: alkemio.csv, label: Alkemio}
              - {type: github-prs}
            paths: {workdir: work, output: out}
            """), encoding="utf-8")
        self.cfg = load_config(self.tmp / "cfg.yaml")

    def test_team_name_and_fork_branch_from_pr(self):
        row = pr_row(pr(7, "sloths", branch="feature/x"), "inkooptafel")
        self.assertEqual(row["project"], "Soevereine Sloths")
        self.assertEqual(row["repo"], "https://github.com/sloths/onegov3-inkooptafel/tree/feature/x")
        self.assertNotIn("<!--", row["description"])
        body = PR_TEMPLATE_BODY.replace("- **Members:**", "- **Members:** Ada Lovelace, Alan Turing\n- Members: Grace")
        self.assertNotIn("Ada", pr_row(pr(7, "x", body=body), "")["description"])
        self.assertNotIn("Grace", pr_row(pr(7, "x", body=body), "")["description"])
        self.assertEqual(team_name_from_body("- **Team name:**\n"), "")
        self.assertEqual(team_name_from_body("- Team name:\n- Members:\n"), "")  # OneGov #2 PR 3: left empty
        self.assertEqual(team_name_from_body("- Team name: Noctouro\n"), "Noctouro")

    def test_deleted_fork_is_noted_not_dropped(self):
        row = pr_row(pr(8, None), "inkooptafel")
        self.assertEqual(row["repo"], "")
        self.assertIn("deleted", row["notes"])

    def test_starter_repo_prs_and_early_unticked_prs_are_skipped(self):
        self.assertTrue(is_non_team_pr(STARTER_FIX_BODY))
        self.assertFalse(is_non_team_pr(PR_TEMPLATE_BODY))
        gh = FakeGitHub([
            pr(1, "teama"),
            pr(2, "organiser", body=STARTER_FIX_BODY),
            pr(3, "early", body=PR_TEMPLATE_BODY.replace("[x] **Hackathon", "[ ] **Hackathon"),
               created="2026-11-26T18:00:00Z"),
            pr(4, "earlyticked", created="2026-11-26T18:00:00Z"),
            pr(5, "bot", user="dependabot[bot]"),
        ])
        rows = pull_github_prs(self.cfg, {"type": "github-prs", "exclude_authors": ["dependabot[bot]"]}, gh)
        self.assertEqual(sorted(r["repo"].split("/")[3] for r in rows), ["earlyticked", "teama"])
        self.assertTrue(all(r["challenge"] == "inkooptafel" for r in rows))

    def test_pr_link_in_csv_is_not_scanned_as_the_challenge_repo(self):
        (self.tmp / "alkemio.csv").write_text(
            "project,repo\nSloths,https://github.com/govtechnl/onegov3-inkooptafel/pull/7\n", encoding="utf-8")
        sub = read_submissions(self.tmp / "alkemio.csv", self.cfg)[0]
        self.assertEqual(sub.repo_url, "")
        self.assertEqual(sub.pr_url, "https://github.com/govtechnl/onegov3-inkooptafel/pull/7")
        self.assertTrue(any(n.startswith(PR_NOTE) for n in sub.notes))

    def test_alkemio_pr_link_resolves_and_merges_with_the_pr(self):
        (self.tmp / "alkemio.csv").write_text(
            "project,repo,description\n"
            "Soevereine Sloths (Alkemio),https://github.com/govtechnl/onegov3-inkooptafel/pull/7,From Alkemio\n"
            "Only Alkemio,https://github.com/solo/standalone-repo,Own repo\n", encoding="utf-8")
        gh = FakeGitHub([pr(7, "sloths"), pr(9, "prsonly")])
        alk = pull_csv(self.cfg, self.cfg["sources"][0])
        resolve_pr_links(alk, gh, {"govtechnl/onegov3-inkooptafel": "inkooptafel"})
        self.assertEqual(alk[0]["repo"], "https://github.com/sloths/onegov3-inkooptafel/tree/main")
        self.assertEqual(alk[0]["challenge"], "inkooptafel")
        prs = pull_github_prs(self.cfg, self.cfg["sources"][1], gh)
        merged = merge_rows([("Alkemio", alk), ("GitHub PR", prs)])
        self.assertEqual(len(merged), 3)
        sloths = merged[0]
        self.assertEqual(sloths["project"], "Soevereine Sloths (Alkemio)")  # first source wins the name
        self.assertEqual(sloths["platform"], "Alkemio + GitHub PR")
        self.assertIn("From Alkemio", sloths["description"])
        self.assertIn("A classifier", sloths["description"])
        self.assertNotIn("Not found", sloths["notes"])
        by_name = {r["project"]: r for r in merged}
        self.assertIn("Not found in: GitHub PR", by_name["Only Alkemio"]["notes"])
        self.assertIn("Not found in: Alkemio", by_name["Soevereine Sloths"]["notes"])

    def test_plain_fork_link_merges_with_pr_branch(self):
        alk = [{"project": "Sloths", "repo": "https://github.com/Sloths/onegov3-inkooptafel", "platform": "Alkemio"}]
        prs = [pr_row(pr(7, "sloths", branch="dev"), "inkooptafel")]
        merged = merge_rows([("Alkemio", alk), ("GitHub PR", prs)])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["repo"].endswith("/tree/dev"))

    def test_merged_csv_reads_back_for_the_scan(self):
        rows = merge_rows([("GitHub PR", [pr_row(pr(7, "sloths", branch="dev"), "inkooptafel")])])
        write_rows(self.tmp / "merged.csv", rows)
        sub = read_submissions(self.tmp / "merged.csv", self.cfg)[0]
        self.assertEqual((sub.owner, sub.repo, sub.tree_path), ("sloths", "onegov3-inkooptafel", "dev"))
        self.assertEqual(sub.challenge, "inkooptafel")
        self.assertEqual(sub.source, "GitHub PR")
        self.assertTrue(sub.platform_url.endswith("/pull/7"))


if __name__ == "__main__":
    unittest.main()
