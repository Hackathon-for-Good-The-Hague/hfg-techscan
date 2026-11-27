"""Offline tests for event branding in the jury pack, scorecard and challenge report."""
import base64
import tempfile
import textwrap
import unittest
from pathlib import Path

from openpyxl import load_workbook

from techscan.branding import brand, check, contrast, dedash
from techscan.config import ConfigError, load_config
from techscan.observe import challenge_owner_report
from techscan.report import render_html, write_xlsx

PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
BASE = """\
event: {name: Test Event, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}
challenges: {a: {name: A}}
paths: {workdir: work, output: out}
"""
RUN = {"deadline": "2026-12-10T12:00:00+00:00", "finished": "2026-12-10T12:30:00+00:00"}


def cfg_from(extra: str = "", files: dict = None):
    tmp = Path(tempfile.mkdtemp())
    for name, data in (files or {}).items():
        (tmp / name).write_bytes(data)
    (tmp / "cfg.yaml").write_text(BASE + textwrap.dedent(extra), encoding="utf-8")
    return load_config(tmp / "cfg.yaml"), tmp


class BrandingTest(unittest.TestCase):
    def test_defaults_keep_the_hfg_look(self):
        cfg, _ = cfg_from()
        b = brand(cfg)
        self.assertEqual((b["primary"], b["accent"], b["cover_background"]), ("#1962A1", "#FCCD02", "#1962A1"))
        html = render_html(cfg, RUN, {})
        self.assertIn("--primary:#1962A1", html)
        self.assertNotIn('class="logos"', html)
        self.assertNotIn("@font-face", html)
        self.assertEqual(check(cfg["branding"]), [])

    def test_onegov_branding_is_embedded_and_dashes_removed(self):
        cfg, tmp = cfg_from("""\
            branding:
              primary: "#2C55A1"
              accent: "#E52983"
              cover_background: "#F9DAEA"
              cover_text: "#2C55A1"
              font_family: "Figtree,Arial,sans-serif"
              font_files: {400: font.ttf}
              logos: [logo.png]
              footer: "GovTechNL — OneGov"
              no_em_dashes: true
            """, files={"logo.png": PNG_1PX, "font.ttf": b"\0\1\0\0fake"})
        html = render_html(cfg, RUN, {})
        self.assertIn("--primary:#2C55A1", html)
        self.assertIn('<img src="data:image/png;base64,', html)
        self.assertIn("@font-face{font-family:'Figtree';font-weight:400", html)
        self.assertIn("GovTechNL - OneGov", html)
        self.assertNotIn("—", html)
        self.assertEqual(check(cfg["branding"]), [])

    def test_missing_logo_is_a_warning_not_a_crash(self):
        cfg, _ = cfg_from("branding: {logos: [nope.png]}\n")
        self.assertTrue(any("not found" in w for w in check(cfg["branding"])))
        self.assertNotIn('class="logos"', render_html(cfg, RUN, {}))

    def test_bad_colour_is_a_config_error_and_poor_contrast_a_warning(self):
        with self.assertRaises(ConfigError):
            cfg_from("branding: {primary: blue}\n")
        cfg, _ = cfg_from("branding: {primary: '#2C55A1', accent: '#E52983'}\n")  # pink name on a blue cover
        self.assertTrue(any("contrast" in w and "event name" in w for w in check(cfg["branding"])))

    def test_contrast_and_dedash(self):
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertGreater(contrast("#2C55A1", "#F9DAEA"), 4.5)
        self.assertEqual(dedash("a — b—c"), "a - b-c")

    def test_scorecard_header_and_challenge_report_use_the_brand(self):
        cfg, tmp = cfg_from("branding: {primary: '#2C55A1', cover_accent: '#ffffff', no_em_dashes: true}\n")
        path = write_xlsx(tmp / "s.xlsx", cfg, RUN, {})
        ws = load_workbook(path).worksheets[0]
        self.assertTrue(str(ws.cell(row=1, column=1).fill.fgColor.rgb).endswith("2C55A1"))
        team = {"t": {"submission": {"project": "T", "slug": "t", "challenge": "a"}, "facts": {}, "flags": []}}
        html = challenge_owner_report(team, event_name="E", branding=brand(cfg))
        self.assertIn("background: #2C55A1", html)


if __name__ == "__main__":
    unittest.main()
