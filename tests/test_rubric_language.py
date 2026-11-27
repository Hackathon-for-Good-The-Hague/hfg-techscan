"""Offline tests for the 1-5 rubric, review language, brief files and config checks."""
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from techscan.__main__ import _check_challenge_keywords
from techscan.config import ConfigError, load_config
from techscan.pipeline import _load_templates
from techscan.report import _score_summary
from techscan.review import build_schema, system_prompt, validate

ROOT = Path(__file__).resolve().parent.parent
BASE = """\
event: {name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}
challenges: {a: {name: A}}
paths: {workdir: work, output: out}
"""


def cfg_from(text: str) -> dict:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "cfg.yaml").write_text(textwrap.dedent(text), encoding="utf-8")
    return load_config(tmp / "cfg.yaml")


class RubricTest(unittest.TestCase):
    def test_onegov_config_is_six_criteria_out_of_30_in_dutch(self):
        cfg = load_config(ROOT / "config" / "onegov3.yaml")
        rb = cfg["rubric"]
        self.assertEqual(len(rb["criteria"]), 6)  # not merged with the HFG defaults
        self.assertEqual(sum(c["weight"] for c in rb["criteria"].values()) * rb["max_score"], rb["max_total"])
        p = system_prompt(cfg, cfg["challenges"]["inkooptafel"])
        self.assertIn("integers 1 to 5", p)
        self.assertIn("Dutch (Nederlands)", p)
        self.assertNotIn("integers 1 to 10", p)
        self.assertNotIn("{{", p)
        self.assertEqual(build_schema(cfg)["properties"]["technical_score"]["maximum"], 5)

    def test_default_prompt_stays_english_ten_point(self):
        p = system_prompt(cfg_from(BASE), {"name": "A"})
        self.assertIn("integers 1 to 10", p)
        self.assertIn("plain English, short sentences", p)
        self.assertNotIn("Write every free-text field", p)
        self.assertNotIn("@@TECHNICAL@@", p)

    def test_unsupported_scale_is_rejected(self):
        with self.assertRaises(ConfigError):
            cfg_from(BASE + "rubric: {max_score: 7}\n")

    def test_model_scores_are_clamped_to_the_scale(self):
        out = validate({"technical_score": 9, "scores": {"impact": 8, "technisch": "n/a"}}, {}, {}, max_score=5)
        self.assertEqual(out["technical_score"], 5)
        self.assertEqual(out["scores"]["impact"], 5)
        self.assertEqual(out["scores"]["technisch"], 3)  # unreadable: mid-scale, not the top
        ten = validate({"technical_score": 9, "scores": {"impact": "?"}}, {}, {})
        self.assertEqual((ten["technical_score"], ten["scores"]["impact"]), (9, 5))  # HFG behaviour unchanged

    def test_score_summary_uses_the_configured_scale(self):
        r = {"review": {"technical_score": 4}, "facts": {}, "flags": []}
        self.assertIn("4/5", _score_summary(r, 5))
        self.assertIn("working end-to-end", _score_summary(r, 5))
        self.assertIn("4/10", _score_summary(r, 10))


class KeywordCheckTest(unittest.TestCase):
    def test_overlap_within_one_challenge_is_fine(self):
        cfg = cfg_from(BASE.replace("challenges: {a: {name: A}}",
                                    "challenges: {a: {name: A, match: [inkoop, inkooptafel]}, b: {name: B, match: [tafel]}}"))
        warnings = _check_challenge_keywords(cfg)
        self.assertEqual(len(warnings), 1)  # only the cross-challenge pair inkooptafel / tafel
        self.assertIn("tafel", warnings[0])


class BriefFilesTest(unittest.TestCase):
    def test_brief_files_are_combined_in_order(self):
        tpl = Path(tempfile.mkdtemp()) / "tpl"
        (tpl / "docs").mkdir(parents=True)
        (tpl / "CHALLENGE.md").write_text("The challenge text", encoding="utf-8")
        (tpl / "docs" / "judging-criteria.md").write_text("M1 must work", encoding="utf-8")
        for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "starter"]):
            subprocess.run(["git", *args], cwd=tpl, check=True)
        cfg = cfg_from(BASE.replace("challenges: {a: {name: A}}", textwrap.dedent(f"""\
            challenges:
              a:
                name: A
                template: "{tpl.as_posix()}"
                brief_files: [docs/judging-criteria.md, CHALLENGE.md, docs/missing.md]
            """)))
        info = _load_templates(cfg, {}, Path(cfg["paths"]["workdir"]))["a"]
        self.assertLess(info["brief"].index("M1 must work"), info["brief"].index("The challenge text"))
        self.assertEqual(info["brief_source"], "template: docs/judging-criteria.md, CHALLENGE.md")


if __name__ == "__main__":
    unittest.main()
