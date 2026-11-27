"""Offline tests for the 'pull' command and the platform link in the jury pack."""
import csv
import tempfile
import textwrap
import unittest
from pathlib import Path

from techscan.__main__ import main
from techscan.report import platform_link


class PullCommandTest(unittest.TestCase):
    def test_pull_merges_csv_sources_without_network(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "alkemio.csv").write_text("project,repo,challenge\nSloths,https://github.com/sloths/app,a\n",
                                         encoding="utf-8")
        (tmp / "late.csv").write_text("project,repo\nSloths again,https://github.com/Sloths/app\n"
                                      "Owls,https://github.com/owls/app\n", encoding="utf-8")
        (tmp / "cfg.yaml").write_text(textwrap.dedent("""\
            event: {name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}
            challenges: {a: {name: A}}
            sources:
              - {type: csv, path: alkemio.csv, label: Alkemio}
              - {type: csv, path: late.csv, label: Late}
            paths: {workdir: work, output: out}
            """), encoding="utf-8")
        out = tmp / "pulled.csv"
        self.assertEqual(main(["pull", "-c", str(tmp / "cfg.yaml"), "-o", str(out)]), 0)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["project"] for r in rows], ["Sloths", "Owls"])
        self.assertEqual(rows[0]["platform"], "Alkemio + Late")
        self.assertIn("Not found in: Alkemio", rows[1]["notes"])

    def test_unknown_source_type_is_a_config_error(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "cfg.yaml").write_text(textwrap.dedent("""\
            event: {name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}
            challenges: {a: {name: A}}
            sources: [{type: devpost}]
            """), encoding="utf-8")
        self.assertEqual(main(["pull", "-c", str(tmp / "cfg.yaml")]), 2)


class PlatformLinkTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(platform_link({"platform_url": "https://github.com/o/r/pull/3"})[1], "Pull request")
        self.assertEqual(platform_link({"platform_url": "https://alkem.io/x", "source": "Alkemio + GitHub PR"})[1],
                         "Alkemio page")
        self.assertEqual(platform_link({"junction_url": "https://j/x", "source": "junction"})[1], "Junction page")
        self.assertEqual(platform_link({}), ("", ""))


if __name__ == "__main__":
    unittest.main()
