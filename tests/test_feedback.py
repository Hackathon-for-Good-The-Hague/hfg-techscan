"""Offline tests for team feedback: drafting from scan results, finding who sent the Alkemio form, and
sending only approved messages, once, within Alkemio's limits."""
import json
import os
import tempfile
import textwrap
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

import yaml

from techscan.alkemio import MAX_MESSAGE
from techscan.config import ConfigError, load_config
from techscan.feedback import draft, draft_message, load_file, recipients_for, save_file, send

try:
    from test_alkemio import FakeAlkemio  # unittest discover -s tests, pytest
except ImportError:
    from tests.test_alkemio import FakeAlkemio  # python -m unittest tests.test_feedback

STRENGTHS = ["Sterk punt een.", "Sterk punt twee.", "Sterk punt drie.", "Sterk punt vier."]
WEAKNESSES = ["Verbeterpunt een.", "Verbeterpunt twee.", "Verbeterpunt drie.", "Verbeterpunt vier."]


def result(project, challenge, owner, pr_url="", status="ok"):
    return {"submission": {"project": project, "challenge": challenge, "owner": owner, "repo": "onegov3-inkooptafel",
                           "repo_url": f"https://github.com/{owner}/onegov3-inkooptafel", "pr_url": pr_url},
            "review_status": status,
            "review": {"summary": "Samenvatting.", "strengths": STRENGTHS, "weaknesses": WEAKNESSES,
                       "red_flags": [{"detail": "RED FLAG TEXT"}], "jury_questions": ["JURY QUESTION?"],
                       "technical_score": 4, "scores": {"impact": 4}} if status == "ok" else {}}


RESULTS = {
    "soevereine-sloths": result("Soevereine Sloths", "inkooptafel", "sloths"),
    "eagles": result("Eagles", "inkooptafel", "eagles", "https://github.com/govtechnl/onegov3-inkooptafel/pull/7"),
    "owls": result("Owls", "open-algoritme", "owls"),
    "pr-only": result("PR Only", "inkooptafel", "pronly"),
    "broken": result("Broken", "inkooptafel", "broken", status="failed"),
}


class FakeFeedback(FakeAlkemio):
    calls, blocked, limit_after = [], set(), None

    def answer(self, q, v, logged_in):
        if not q.lstrip().startswith("mutation"):
            return super().answer(q, v, logged_in)
        FakeFeedback.calls.append({"email": "sendMessageToUsers(" in q, **v})
        if FakeFeedback.limit_after is not None and len(FakeFeedback.calls) > FakeFeedback.limit_after:
            return {"data": None, "errors": [{"message": "Message send limit reached",
                                              "extensions": {"code": "MESSAGE_SEND_LIMIT_REACHED"}}]}
        if "sendDirectMessageToUsers" in q:
            return {"data": {"sendDirectMessageToUsers": [
                {"receiverID": u, "status": "BLOCKED_NO_CONSENT" if u in FakeFeedback.blocked else "SENT"}
                for u in v["ids"]]}}
        if v["ids"][0] in FakeFeedback.blocked:
            return {"data": {"sendMessageToUsers": None},
                    "errors": [{"message": "User is not open to being contacted via email"}]}
        return {"data": {"sendMessageToUsers": True}}


class FeedbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeFeedback)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeFeedback.calls, FakeFeedback.blocked, FakeFeedback.limit_after = [], set(), None
        os.environ["ALKEMIO_SESSION"] = "good"
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "c.yaml").write_text(textwrap.dedent(f"""\
            event: {{name: "OneGov #3", start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T14:00:00+01:00"}}
            challenges:
              inkooptafel: {{name: Inkooptafel, template: "https://github.com/govtechnl/onegov3-inkooptafel.git"}}
              open-algoritme: {{name: Open Algoritme, match: ["algoritme"]}}
            sources:
              - {{type: alkemio, space: onegov-hackathon, api_url: "http://127.0.0.1:{self.port}/graphql"}}
            review: {{language: nl}}
            branding: {{no_em_dashes: true}}
            paths: {{workdir: work, output: out}}
            """), encoding="utf-8")
        self.cfg = load_config(self.tmp / "c.yaml")
        res = Path(self.cfg["paths"]["workdir"]) / "results"
        res.mkdir(parents=True)
        for slug, r in RESULTS.items():
            (res / f"{slug}.json").write_text(json.dumps(r), encoding="utf-8")
        (res / "_run.json").write_text(json.dumps({"slugs": list(RESULTS)}), encoding="utf-8")
        self.path = draft(self.cfg)

    def tearDown(self):
        os.environ.pop("ALKEMIO_SESSION", None)

    def entries(self):
        return {e["slug"]: e for e in load_file(self.path)}

    def approve(self, *slugs, **changes):
        entries = load_file(self.path)
        for e in entries:
            if e["slug"] in slugs:
                e["send"] = True
                e.update(changes)
        save_file(self.path, entries, self.cfg)

    def test_drafts_hold_feedback_only_and_find_the_form_senders(self):
        e = self.entries()
        self.assertEqual(sorted(e), ["eagles", "owls", "pr-only", "soevereine-sloths"])  # failed review left out
        sloths = e["soevereine-sloths"]
        self.assertEqual(sloths["recipient_ids"], ["u-ann", "u-bob"])  # both members who sent the form
        self.assertFalse(sloths["send"])
        msg = sloths["message"]
        self.assertTrue(msg.startswith("Hoi Soevereine Sloths,"))
        for text in STRENGTHS[:3] + WEAKNESSES[:3]:
            self.assertIn(text, msg)
        for text in ("Sterk punt vier", "RED FLAG", "JURY QUESTION", "4/5", "Samenvatting"):
            self.assertNotIn(text, msg)
        self.assertNotIn("Ann Jansen", self.path.read_text(encoding="utf-8"))
        self.assertEqual(e["eagles"]["recipient_ids"], ["u-eve"])  # matched on name and on the PR link
        self.assertEqual(e["owls"]["recipient_ids"], ["u-owl"])
        self.assertEqual(e["pr-only"]["recipients"], 0)
        self.assertIn("message: |", self.path.read_text(encoding="utf-8"))  # readable, editable block

    def test_drafting_again_keeps_edits(self):
        self.approve("owls", message="Eigen tekst van de organisatie.")
        draft(self.cfg)
        owls = self.entries()["owls"]
        self.assertEqual((owls["message"], owls["send"]), ("Eigen tekst van de organisatie.", True))

    def test_sends_only_approved_messages_once(self):
        FakeFeedback.blocked = {"u-owl"}
        self.approve("soevereine-sloths", "owls")
        self.assertEqual(send(self.cfg, confirm=lambda: False), 1)  # not confirmed: nothing goes out
        self.assertEqual(FakeFeedback.calls, [])
        self.assertEqual(send(self.cfg, yes=True), 0)
        self.assertEqual([c["ids"] for c in FakeFeedback.calls], [["u-ann", "u-bob"], ["u-owl"]])
        self.assertTrue(FakeFeedback.calls[0]["message"].startswith("Hoi Soevereine Sloths"))
        e = self.entries()
        self.assertTrue(e["soevereine-sloths"]["sent"])
        self.assertEqual(e["owls"]["delivery"], {"u-owl": "BLOCKED_NO_CONSENT"})
        self.assertNotIn("sent", e["eagles"])
        self.assertEqual(send(self.cfg, yes=True), 0)  # again: nothing new
        self.assertEqual(len(FakeFeedback.calls), 2)

    def test_hourly_limit_stops_and_the_next_run_carries_on(self):
        FakeFeedback.limit_after = 1
        self.approve("soevereine-sloths", "eagles", "owls")
        self.assertEqual(send(self.cfg, yes=True), 1)
        e = self.entries()  # the file runs per challenge, then by team name: Eagles first
        self.assertTrue(e["eagles"].get("sent"))
        self.assertFalse(e["soevereine-sloths"].get("sent"))
        FakeFeedback.limit_after, FakeFeedback.calls = None, []
        self.assertEqual(send(self.cfg, yes=True), 0)
        self.assertEqual([c["ids"] for c in FakeFeedback.calls], [["u-ann", "u-bob"], ["u-owl"]])  # Eagles not again

    def test_email_channel_reports_who_opted_out(self):
        FakeFeedback.blocked = {"u-bob"}
        self.approve("soevereine-sloths")
        self.assertEqual(send(self.cfg, channel="email", yes=True), 0)
        self.assertEqual([(c["email"], c["ids"]) for c in FakeFeedback.calls], [(True, ["u-ann"]), (True, ["u-bob"])])
        self.assertEqual(self.entries()["soevereine-sloths"]["delivery"], {"u-ann": "SENT", "u-bob": "BLOCKED_NO_CONSENT"})

    def test_refuses_messages_it_cannot_send(self):
        self.approve("pr-only")  # nobody to send it to
        self.assertEqual(send(self.cfg, yes=True), 1)
        self.approve("owls", message="x" * (MAX_MESSAGE + 1))
        self.assertEqual(send(self.cfg, yes=True), 1)
        self.assertEqual(FakeFeedback.calls, [])


class MessageTest(unittest.TestCase):
    def cfg(self, extra=""):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "c.yaml").write_text(textwrap.dedent(f"""\
            event: {{name: E, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T14:00:00+01:00"}}
            challenges: {{a: {{name: A, template: "https://github.com/org/starter"}}}}
            {extra}
            """), encoding="utf-8")
        return load_config(tmp / "c.yaml")

    def test_long_reviews_are_cut_to_alkemio_s_limit(self):
        r = result("T", "a", "t")
        r["review"]["strengths"] = ["s" * 900] * 3
        r["review"]["weaknesses"] = ["w — " * 300] * 3
        msg = draft_message(r, self.cfg("branding: {no_em_dashes: true}"))
        self.assertLessEqual(len(msg), MAX_MESSAGE)
        self.assertIn("Reply to this message", msg)  # English: the config has no review.language
        self.assertNotIn("—", msg)

    def test_a_link_to_the_starter_repo_matches_no_one(self):
        cfg = self.cfg()
        lazy = {"submission": {"project": "Lazy", "challenge": "a", "owner": "org", "repo": "starter"}}
        other = [{"challenge": "a", "team": "Someone Else", "repo": "https://github.com/org/starter", "user_id": "u1"}]
        self.assertEqual(recipients_for(lazy, other, {"org/starter"}), [])

    def test_config(self):
        with self.assertRaises(ConfigError):
            self.cfg("feedback: {channel: sms}")
        cfg = self.cfg("feedback: {intro: 'Beste {team}, zie {unknown}'}")
        self.assertEqual(cfg["feedback"]["channel"], "chat")
        self.assertTrue(draft_message(result("Owls", "a", "o"), cfg).startswith("Beste Owls, zie {unknown}"))


if __name__ == "__main__":
    unittest.main()
