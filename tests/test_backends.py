"""Offline tests for the review backends: GreenPT / OpenAI-compatible against a local fake server,
the rubric-aware schema, and separate review caches per backend."""
import json
import os
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from techscan.config import load_config
from techscan.review import AnthropicAPI, OpenAICompatible, cached_review, make_backend, run_review

REVIEW = {"summary": "Werkt.", "architecture": "Python", "claims": [{"claim": "c", "source": "readme", "status": "verified",
          "evidence": [], "note": ""}], "challenge_data": {"used": "yes", "detail": "", "evidence": []},
          "ai_approach": {"type": "none", "detail": "", "evidence": []}, "team_vs_generated": "", "strengths": [],
          "weaknesses": [], "red_flags": [], "responsible_ai": "", "jury_questions": ["a", "b", "c"],
          "scores": {"impact": 4}, "technical_score": 4, "technical_score_rationale": "", "confidence": "high",
          "confidence_note": "", "concept_only": False}


class FakeAPI(BaseHTTPRequestHandler):
    reject_schema = False
    requests = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"data": [{"id": "eu-large"}, {"id": "eu-small"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.requests.append({"auth": self.headers.get("Authorization"), **body})
        if body.get("max_tokens", 0) > 16384:
            return self._send(400, {"error": {"message": "400 payload validation: max_completion_tokens is limited to "
                                                         "16384 for eu-large"}})
        fmt = (body.get("response_format") or {}).get("type")
        if fmt == "json_schema" and FakeAPI.reject_schema:
            return self._send(400, {"error": {"message": "response_format json_schema is not supported"}})
        self._send(200, {"model": body["model"], "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                         "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(REVIEW)}}]})


def cfg_for(backend: str, port: int, model: str = "eu-large") -> dict:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "c.yaml").write_text(textwrap.dedent(f"""\
        event: {{name: T, start: "2026-12-09T09:00:00+01:00", deadline: "2026-12-10T13:00:00+01:00"}}
        challenges: {{a: {{name: A}}}}
        rubric: {{max_score: 5, max_total: 5, criteria: {{impact: {{label: Impact, weight: 1, description: d}}}}}}
        review: {{backend: {backend}, model: "{model}", api_base: "http://127.0.0.1:{port}/v1", timeout_minutes: 1}}
        paths: {{workdir: work, output: out}}
        """), encoding="utf-8")
    return load_config(tmp / "c.yaml")


class OpenAICompatibleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAPI)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        os.environ["GREENPT_API_KEY"] = "test-key"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeAPI.reject_schema, FakeAPI.requests = False, []

    def test_review_with_json_schema(self):
        b = make_backend(cfg_for("greenpt", self.port))
        self.assertIsInstance(b, OpenAICompatible)
        self.assertEqual(b.problem(), "")
        review, meta = b.review("sys", "user", Path("."))
        self.assertEqual(review["summary"], "Werkt.")
        self.assertEqual((meta["backend"], meta["model"], meta["response_format"]), ("greenpt", "eu-large", "json_schema"))
        sent = FakeAPI.requests[0]
        self.assertEqual(sent["auth"], "Bearer test-key")
        self.assertEqual(sent["response_format"]["json_schema"]["schema"]["properties"]["scores"]["required"], ["impact"])

    def test_steps_down_when_json_schema_is_not_supported(self):
        FakeAPI.reject_schema = True
        b = make_backend(cfg_for("greenpt", self.port))
        review, meta = b.review("sys", "user", Path("."))
        self.assertEqual(meta["response_format"], "json_object")
        self.assertEqual([(r.get("response_format") or {}).get("type") for r in FakeAPI.requests],
                         ["json_schema", "json_object"])

    def test_retries_with_the_servers_output_token_limit(self):
        cfg = cfg_for("greenpt", self.port)
        cfg["review"]["max_output_tokens"] = 32000
        b = make_backend(cfg)
        review, _ = b.review("sys", "user", Path("."))
        self.assertEqual(review["summary"], "Werkt.")
        self.assertEqual([r["max_tokens"] for r in FakeAPI.requests], [32000, 16384])

    def test_ping_lists_models_when_the_model_is_wrong(self):
        self.assertEqual(make_backend(cfg_for("greenpt", self.port)).ping(), "")
        msg = make_backend(cfg_for("greenpt", self.port, model="nope")).ping()
        self.assertIn("eu-large", msg)

    def test_problems(self):
        self.assertIn("review.model", make_backend(cfg_for("greenpt", self.port, model="claude-sonnet-4-6")).problem())
        saved = os.environ.pop("GREENPT_API_KEY")
        try:
            self.assertIn("GREENPT_API_KEY", make_backend(cfg_for("greenpt", self.port)).problem())
        finally:
            os.environ["GREENPT_API_KEY"] = saved

    def test_greenpt_and_claude_reviews_are_cached_separately(self):
        g = cfg_for("greenpt", self.port)
        out = run_review(make_backend(g), g, "team", "sys", "user", Path("."), False, {}, {})
        self.assertEqual(out["status"], "ok")
        self.assertTrue((Path(g["paths"]["workdir"]) / "reviews" / "team.greenpt.json").exists())
        self.assertIsNotNone(cached_review(g, "team", "sys", "user", False, {}, {}))
        claude = dict(g, review=dict(g["review"], backend="claude-cli", model="claude-sonnet-4-6"))
        self.assertIsNone(cached_review(claude, "team", "sys", "user", False, {}, {}))


class SchemaTest(unittest.TestCase):
    def test_anthropic_backend_sends_the_rubric_criteria(self):
        cfg = cfg_for("anthropic", 1)
        props = AnthropicAPI(cfg).schema["properties"]
        self.assertEqual(props["scores"]["required"], ["impact"])
        self.assertEqual(props["technical_score"]["maximum"], 5)


if __name__ == "__main__":
    unittest.main()
