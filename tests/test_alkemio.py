"""Offline tests for the Alkemio source, against a local fake of Alkemio's GraphQL API."""
import json
import os
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from techscan.alkemio import AlkemioClient, AlkemioError, check, count_members, first_url, pull, question_role
from techscan.config import ConfigError, load_config
from techscan.intake import read_submissions
from techscan.sources import SourceError, merge_rows, pull_alkemio, resolve_pr_links, source_problems, write_rows

PROMPTS = ["Teamnaam", "Namen van de teamleden", "Wat hebben jullie gebouwd, in een zin?",
           "Welk probleem lost het op, en voor wie?",
           "Welk deel werkt echt, en welk deel is gesimuleerd of nog niet af?", "Link naar de repository",
           "Demo-link, als die er is", "Welke aannames zitten eronder, en waar loopt het stuk?",
           "Wat hebben jullie geleerd dat je niet had verwacht?", "Wil het team hierna doorbouwen?"]


def answers(**values):
    keys = ["name", "members", "one_line", "problem", "works", "repo", "demo", "assumptions", "learned", "continue"]
    return [{"questionID": f"q{i}", "prompt": p, "text": values.get(k, "")} for i, (k, p) in enumerate(zip(keys, PROMPTS))]


RESPONSES = {
    "form-inkoop": [
        {"id": "r1", "createdDate": "2026-12-10T12:00:00.000Z", "createdBy": {"id": "u-ann"}, "answers": answers(
            name="Soevereine Sloths", members="Ann Jansen (BZK)\nBob\nCy", one_line="Eisen op maat per SEAL-niveau.",
            problem="Inkopers krijgen honderd eisen.", works="Selectie werkt, TenderNed-koppeling is gesimuleerd.",
            repo="Onze repo: github.com/sloths/onegov3-inkooptafel (branch dev)", demo="https://sloths.example/demo.",
            assumptions="We nemen aan dat het DACC-register klopt.", learned="secret learned text")},
        {"id": "r2", "createdDate": "2026-12-10T11:00:00.000Z", "createdBy": {"id": "u-bob"}, "answers": answers(
            name="Soevereine  sloths", members="Bob", repo="https://github.com/bob/old-attempt")},
        {"id": "r3", "createdDate": "2026-12-10T13:30:00.000Z", "createdBy": {"id": "u-eve"}, "answers": answers(
            name="Eagles", repo="https://github.com/govtechnl/onegov3-inkooptafel/pull/7")},
    ],
    "form-algo": [
        {"id": "r4", "createdDate": "2026-12-10T10:00:00.000Z", "createdBy": {"id": "u-owl"}, "answers": answers(
            name="Owls", members="Ann, Bob", repo="nog niet")},
    ],
}


def form(form_id):
    return {"id": form_id, "settings": {"state": "OPEN"},
            "questions": [{"id": f"q{i}", "prompt": p, "required": True} for i, p in enumerate(PROMPTS)]}


def subspace(name_id, display, callouts):
    return {"id": name_id, "nameID": name_id,
            "about": {"profile": {"displayName": display, "url": f"https://alkem.io/s/{name_id}"}},
            "collaboration": {"calloutsSet": {"callouts": callouts}}}


def inzending(name_id, form_id):
    return {"id": "c-" + name_id, "nameID": "inzending",
            "framing": {"profile": {"url": f"https://alkem.io/s/{name_id}/collaboration/inzending"},
                        "form": form(form_id)}}


SPACE = {"id": "sp", "nameID": "onegov-hackathon", "about": {"profile": {"displayName": "OneGov", "url": ""}},
         "subspaces": [
             subspace("inkooptafel", "Van beleid naar inkooptafel", [inzending("inkooptafel", "form-inkoop")]),
             subspace("openalgo", "Open Algoritme", [inzending("openalgo", "form-algo")]),
             subspace("last-year", "Editie #2 challenge", [{"id": "x", "nameID": "lever-in", "framing": {"form": None}}]),
         ]}


class FakeAlkemio(BaseHTTPRequestHandler):
    admin = True
    cookies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cookie = self.headers.get("Cookie") or ""
        FakeAlkemio.cookies.append(cookie)
        logged_in = cookie == "alkemio_session=good"
        out = json.dumps(self.answer(body["query"], body["variables"], logged_in)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def answer(self, q, v, logged_in):
        if "lookupByName" in q:
            data = {"me": {"user": {"id": "u", "profile": {"displayName": "Organiser"}} if logged_in else None},
                    "lookupByName": {"space": SPACE if v["space"] == "onegov-hackathon" else None}}
        else:
            all_ = RESPONSES.get(v["form"], [])
            start = int(v["after"] or 0)
            page = all_[start:start + 2]  # pages of two, to exercise paging
            more = start + 2 < len(all_)
            can = logged_in and FakeAlkemio.admin
            data = {"lookup": {"calloutFormResponses": {"canReadAll": can, "all": {
                "total": len(all_) if can else 0,
                "pageInfo": {"hasNextPage": can and more, "endCursor": str(start + 2) if more else None},
                "responses": page if can else []}}}}
        return {"data": data}


class AlkemioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAlkemio)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        port = cls.server.server_address[1]
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "c.yaml").write_text(textwrap.dedent(f"""\
            event: {{name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T14:00:00+01:00"}}
            challenges:
              inkooptafel: {{name: Inkooptafel, template: "https://github.com/govtechnl/onegov3-inkooptafel.git"}}
              open-algoritme: {{name: Open Algoritme, match: ["algoritme"]}}
              synthpop-py: {{name: synthpop.py}}
            sources:
              - {{type: alkemio, space: onegov-hackathon, api_url: "http://127.0.0.1:{port}/graphql"}}
            paths: {{workdir: work, output: out}}
            """), encoding="utf-8")
        cls.cfg = load_config(cls.tmp / "c.yaml")
        cls.src = cls.cfg["sources"][0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAlkemio.admin, FakeAlkemio.cookies = True, []
        os.environ["ALKEMIO_SESSION"] = "alkemio_session=good; other=1"  # pasted with name and a second cookie

    def tearDown(self):
        os.environ.pop("ALKEMIO_SESSION", None)

    def test_pull_maps_the_form_to_submission_rows(self):
        rows = pull(self.cfg, self.src)
        self.assertEqual(FakeAlkemio.cookies[0], "alkemio_session=good")
        by = {r["project"]: r for r in rows}
        self.assertEqual(sorted(by), ["Eagles", "Owls", "Soevereine Sloths"])

        sloths = by["Soevereine Sloths"]
        self.assertEqual(sloths["challenge"], "inkooptafel")  # subspace nameID is the challenge key
        self.assertEqual(sloths["repo"], "https://github.com/sloths/onegov3-inkooptafel")  # URL found in the text
        self.assertEqual(sloths["demo"], "https://sloths.example/demo")
        self.assertEqual(sloths["team_size"], "3")
        self.assertEqual(sloths["punchline"], "Eisen op maat per SEAL-niveau.")
        self.assertIn("gesimuleerd of nog niet af?\nSelectie werkt", sloths["description"])
        self.assertIn("DACC-register", sloths["description"])
        self.assertNotIn("Ann", json.dumps(rows))  # member names are counted, never kept
        self.assertNotIn("u-ann", json.dumps(rows))  # nor who sent the form
        self.assertNotIn("secret learned", sloths["description"])
        self.assertIn("2 Alkemio form responses", sloths["notes"])  # two members sent the form: newest wins
        self.assertTrue(sloths["platform_url"].endswith("/inkooptafel/collaboration/inzending"))
        self.assertEqual(sloths["platform"], "Alkemio")

        eagles = by["Eagles"]  # page two of the inkooptafel form
        self.assertTrue(eagles["repo"].endswith("/pull/7"))
        self.assertIn("after the deadline (Thu 10 Dec 14:30)", eagles["notes"])

        owls = by["Owls"]
        self.assertEqual(owls["challenge"], "open-algoritme")  # matched on the subspace's display name
        self.assertEqual(owls["team_size"], "2")
        self.assertEqual(owls["repo"], "")
        self.assertIn("No repository link", owls["notes"])

    def test_rows_go_through_pr_resolution_merge_and_intake(self):
        class GH:
            def pull(self, owner, repo, number):
                return {"html_url": f"https://github.com/{owner}/{repo}/pull/{number}", "number": number,
                        "title": "t", "body": "", "state": "open",
                        "head": {"ref": "main", "repo": {"full_name": "eagles/onegov3-inkooptafel",
                                                         "owner": {"login": "eagles"}}}}, ""
        rows = pull(self.cfg, self.src)
        resolve_pr_links(rows, GH())
        write_rows(self.tmp / "pulled.csv", merge_rows([("Alkemio", rows)]))
        subs = {s.project: s for s in read_submissions(self.tmp / "pulled.csv", self.cfg)}
        self.assertEqual((subs["Eagles"].owner, subs["Eagles"].tree_path), ("eagles", "main"))
        self.assertTrue(subs["Eagles"].pr_url.endswith("/pull/7"))
        self.assertEqual(subs["Soevereine Sloths"].team_size, 3)
        self.assertEqual(subs["Owls"].challenge, "open-algoritme")

    def test_not_an_admin(self):
        FakeAlkemio.admin = False
        with self.assertRaises(AlkemioError) as e:
            pull(self.cfg, self.src)
        self.assertIn("admin", str(e.exception))
        with self.assertRaises(SourceError):
            pull_alkemio(self.cfg, self.src)

    def test_cookie_missing_or_rejected(self):
        os.environ["ALKEMIO_SESSION"] = "expired"
        with self.assertRaises(AlkemioError) as e:
            pull(self.cfg, self.src)
        self.assertIn("not accepted", str(e.exception))
        os.environ.pop("ALKEMIO_SESSION")
        self.assertIn("ALKEMIO_SESSION", AlkemioClient(self.src).problem())
        self.assertIn("ALKEMIO_SESSION", source_problems(self.cfg)[0][1])

    def test_preflight_ping(self):
        summary, problem = check(self.cfg, self.src)
        self.assertIn("logged in as Organiser; 2 form(s), 4 response(s)", summary)
        self.assertEqual(problem, "no submission form for: synthpop-py")
        label, problem = source_problems(self.cfg, ping=True)[0]
        self.assertIn("2 form(s)", label)


class MappingTest(unittest.TestCase):
    def test_question_roles(self):
        roles = [question_role(p) for p in PROMPTS]
        self.assertEqual(roles, ["project", "members", "punchline", "description", "description", "repo", "demo",
                                 "description", "", ""])
        self.assertEqual(question_role("Waar staat de code?", {"repo": "waar staat de code"}), "repo")

    def test_urls_and_member_counts(self):
        self.assertEqual(first_url("zie https://x.nl/a, en github.com/o/r.", prefer_github=True), "https://github.com/o/r")
        self.assertEqual(first_url("geen link"), "")
        self.assertEqual(count_members("- Ann\n- Bob\n\n"), 2)
        self.assertEqual(count_members("Ann; Bob; Cy"), 3)

    def test_config_validation(self):
        tmp = Path(tempfile.mkdtemp())
        for bad in ("{type: alkemio}", "{type: alkemio, space: s, questions: {team: Teamnaam}}"):
            (tmp / "c.yaml").write_text(textwrap.dedent(f"""\
                event: {{name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}}
                challenges: {{a: {{name: A}}}}
                sources: [{bad}]
                """), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(tmp / "c.yaml")


if __name__ == "__main__":
    unittest.main()
