"""Offline tests for challenge requirements (OneGov Must criteria and similar)."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from techscan import requirements as R
from techscan.config import ConfigError, load_config
from techscan.facts import Inventory
from techscan.gitops import GitHub

APACHE = "Apache License, Version 2.0, January 2004\n"
CC_BY = "Creative Commons Attribution 4.0 International\n"
REQ = {"team_work": ["code", "config", "doc"], "open_licence": True, "tests": True, "explicit_rules": True,
       "submission_dir": "submissions/", "must_checklist": True, "pr_checks": True}
PR_BODY = textwrap.dedent("""\
    - [x] **M1** Working tool
    - [x] **M2** Advised level per pattern
    - [ ] **M3** Output for tenders
    - [x] **M4** Rules explicit in code
    - [ ] **M5** Open source
    """)


def inventory(files: dict, template=()) -> Inventory:
    root = Path(tempfile.mkdtemp())
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    inv = Inventory(root)
    for f in inv.files:
        if f["path"] in template:
            f["status"] = "template"
    return inv


def no_code_flag():
    return [{"code": "no_team_code", "severity": "high", "title": "No code beyond the template", "detail": "",
             "evidence": []}]


class RequirementFactsTest(unittest.TestCase):
    def setUp(self):
        self.inv = inventory({
            "LICENSE": APACHE, "LICENSE-DATA": CC_BY, "README.md": "# starter\n",
            "tooling/tests/test_starter.py": "def test_x():\n    assert True\n",
            "submissions/sloths/rules.yaml": "P03:\n  level: high\n  source: avg\n",
            "submissions/sloths/explain.py": "from openai import OpenAI\nclient = OpenAI()\n",
            "submissions/sloths/NOTES.md": "Our approach\n\nMore text\n",
        }, template={"LICENSE", "LICENSE-DATA", "README.md", "tooling/tests/test_starter.py"})
        self.rf = R.gather(self.inv, {}, {"description": PR_BODY}, {"requirements": REQ},
                           {"pr": {"summary": "failed", "head_sha": "abcdef1234",
                                   "checks": [{"name": "validate", "status": "completed", "conclusion": "failure"}]}})

    def test_facts(self):
        rf = self.rf
        self.assertEqual(set(rf["team_work"]), {"code", "config", "doc"})
        self.assertEqual([(x["code"], x["content"], x["from_template"]) for x in sorted(rf["licences"], key=lambda x: x["file"])],
                         [(["Apache-2.0"], [], True), ([], ["CC-BY-4.0"], True)])
        self.assertEqual(rf["team_tests"], [])  # the starter repo's test does not count
        self.assertEqual([(c["file"], c["line"]) for c in rf["llm_calls"]], [("submissions/sloths/explain.py", 1)])
        self.assertEqual(rf["submission_dir"]["team_folders"], ["sloths"])
        self.assertEqual(rf["must_checklist"], {"found": True, "ticked": ["M1", "M2", "M4"], "unticked": ["M3", "M5"]})

    def test_flags(self):
        flags = R.apply_flags(no_code_flag(), self.rf, {"requirements": REQ}, {"sha": "9999999", "short": "9999999"})
        codes = {f["code"]: f for f in flags}
        self.assertNotIn("no_team_code", codes)  # rules and docs are the deliverable here
        self.assertNotIn("no_open_licence", codes)
        self.assertNotIn("no_content_licence", codes)
        self.assertIn("no_team_tests", codes)
        self.assertIn("llm_in_code", codes)
        self.assertIn("M3, M5", codes["must_unticked"]["title"])
        self.assertIn("validate", codes["pr_checks_failed"]["title"])
        self.assertIn("not on the deadline snapshot", codes["pr_checks_failed"]["detail"])
        self.assertEqual([f["severity"] for f in flags], sorted((f["severity"] for f in flags),
                         key=lambda s: ["high", "medium", "low", "info"].index(s)))

    def test_nothing_new_keeps_and_rewords_the_flag(self):
        inv = inventory({"README.md": "# starter\n"}, template={"README.md"})
        rf = R.gather(inv, {}, {}, {"requirements": {"team_work": ["code", "doc"]}}, {})
        flags = R.apply_flags(no_code_flag(), rf, {"requirements": {"team_work": ["code", "doc"]}}, {})
        self.assertEqual(flags[0]["title"], "No team work beyond the starter repository")

    def test_closed_licence_and_missing_licence(self):
        inv = inventory({"LICENSE": "Copyright 2026 Acme. All rights reserved.\n", "app.py": "print(1)\n"})
        rf = R.gather(inv, {}, {}, {"requirements": {"open_licence": True}}, {})
        codes = {f["code"] for f in R.apply_flags([], rf, {"requirements": {"open_licence": True}}, {})}
        self.assertTrue({"licence_not_open", "no_open_licence", "no_content_licence"} <= codes)

    def test_no_requirements_means_no_change(self):
        self.assertEqual(R.gather(self.inv, {}, {}, {"name": "HFG challenge"}, {}), {})
        self.assertEqual(R.apply_flags(no_code_flag(), {}, {}, {}), no_code_flag())
        self.assertEqual(R.pack_summary({}), {})
        self.assertEqual(R.report_rows({}), [])

    def test_awaiting_approval_is_explained(self):
        rf = {"pr_checks": {"summary": "awaiting approval"}}
        flags = R.apply_flags([], rf, {"requirements": {"pr_checks": True}}, {})
        self.assertIn("approve", flags[0]["detail"])


class PrStatusTest(unittest.TestCase):
    def fake(self, runs, workflow_runs=()):
        gh = GitHub(enabled=True)
        gh.pull = lambda o, r, n: ({"html_url": "u", "state": "open", "head": {"sha": "abc123"}}, "")
        gh.get = lambda url: (({"check_runs": runs}, "") if "check-runs" in url
                              else ({"workflow_runs": list(workflow_runs)}, ""))
        return gh.pr_status("o", "r", 1)["summary"]

    def test_summaries(self):
        ok = {"name": "validate", "status": "completed", "conclusion": "success"}
        self.assertEqual(self.fake([ok]), "passed")
        self.assertEqual(self.fake([ok, dict(ok, conclusion="failure")]), "failed")
        self.assertEqual(self.fake([dict(ok, status="in_progress", conclusion=None)]), "pending")
        self.assertEqual(self.fake([], [{"conclusion": "action_required"}]), "awaiting approval")
        self.assertEqual(self.fake([]), "none")
        self.assertEqual(R.pr_from_url("https://github.com/govtechnl/x/pull/12"), ("govtechnl", "x", 12))


class ConfigTest(unittest.TestCase):
    def test_unknown_requirement_is_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "c.yaml").write_text(textwrap.dedent("""\
            event: {name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}
            challenges: {a: {name: A, requirements: {open_license: true}}}
            """), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(tmp / "c.yaml")

    def test_onegov_config_loads_with_requirements(self):
        cfg = load_config(Path(__file__).resolve().parent.parent / "config" / "onegov3.yaml")
        self.assertTrue(cfg["challenges"]["inkooptafel"]["requirements"]["explicit_rules"])


if __name__ == "__main__":
    unittest.main()
