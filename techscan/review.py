"""The AI review step: one call per team, structured JSON out, evidence checked against the snapshot."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .util import log, read_json, run, sha256_text, truncate, write_json

PROMPT_VERSION = "2026-11-07.1"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

EVID = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file", "lines"],
    "properties": {"file": {"type": "string"}, "lines": {"type": "string"}, "note": {"type": "string"}},
}
SCORE = {"type": "integer", "minimum": 1, "maximum": 5}
CLAIM_STATUS = ["verified", "partial", "mocked", "missing", "unclear"]
FLAG_TYPES = ["undisclosed_mock", "hardcoded_output", "prebuilt_code", "ai_directed_text", "secret_exposed",
              "copied_code", "broken_or_incomplete", "privacy_or_safety_risk", "claim_mismatch", "other"]
AI_TYPES = ["none", "hosted_model_api", "prompt_pipeline", "rag", "agent", "classical_ml",
            "trained_or_finetuned_model", "other"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "architecture", "claims", "challenge_data", "ai_approach", "team_vs_generated",
                 "strengths", "weaknesses", "red_flags", "responsible_ai", "jury_questions", "scores",
                 "technical_score", "technical_score_rationale", "confidence", "confidence_note", "concept_only"],
    "properties": {
        "summary": {"type": "string", "description": "2-3 sentences: what the software actually does"},
        "architecture": {"type": "string", "description": "Stack and main components in one or two sentences"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["claim", "source", "status", "evidence", "note"],
            "properties": {
                "claim": {"type": "string"},
                "source": {"type": "string", "enum": ["description", "readme", "both"]},
                "status": {"type": "string", "enum": CLAIM_STATUS},
                "evidence": {"type": "array", "items": EVID},
                "note": {"type": "string"},
            }}},
        "challenge_data": {"type": "object", "additionalProperties": False, "required": ["used", "detail", "evidence"],
                           "properties": {"used": {"type": "string", "enum": ["yes", "partly", "no", "unclear"]},
                                          "detail": {"type": "string"}, "evidence": {"type": "array", "items": EVID}}},
        "ai_approach": {"type": "object", "additionalProperties": False, "required": ["type", "detail", "evidence"],
                        "properties": {"type": {"type": "string", "enum": AI_TYPES}, "detail": {"type": "string"},
                                       "evidence": {"type": "array", "items": EVID}}},
        "team_vs_generated": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["type", "severity", "detail", "evidence"],
            "properties": {"type": {"type": "string", "enum": FLAG_TYPES},
                           "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                           "detail": {"type": "string"}, "evidence": {"type": "array", "items": EVID}}}},
        "responsible_ai": {"type": "string"},
        "jury_questions": {"type": "array", "items": {"type": "string"}},
        "scores": {"type": "object", "additionalProperties": False,
                   "required": ["completeness", "depth", "integrity", "quality"],
                   "properties": {"completeness": SCORE, "depth": SCORE, "integrity": SCORE, "quality": SCORE}},
        "technical_score": SCORE,
        "technical_score_rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "confidence_note": {"type": "string"},
        "concept_only": {"type": "boolean"},
    },
}


class ReviewError(Exception):
    pass


def system_prompt(cfg: dict, challenge: dict, deep: bool = False) -> str:
    text = (PROMPTS_DIR / "review_system.md").read_text(encoding="utf-8")
    ev = cfg["event"]
    text = (text.replace("{{EVENT_NAME}}", ev.get("name", "a hackathon"))
                .replace("{{EVENT_CONTEXT}}", ev.get("context", ""))
                .replace("{{BUILD_TIME}}", ev.get("build_time", "a weekend"))
                .replace("{{CHALLENGE_NAME}}", (challenge or {}).get("name", "see the brief"))
                .replace("{{SCHEMA}}", json.dumps(SCHEMA, separators=(",", ":"))))
    if deep:
        text += "\n" + (PROMPTS_DIR / "deep_mode.md").read_text(encoding="utf-8")
    return text


def extract_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


# ------------------------------------------------------------------ validation

def _clamp(v, default=None):
    try:
        return max(1, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _evidence(items, file_lines: dict, seen_files: dict):
    out, bad = [], 0
    for it in (items or [])[:6]:
        if not isinstance(it, dict):
            continue
        path = str(it.get("file", "")).strip().replace("\\", "/")
        while path.startswith("./") or path.startswith("/"):
            path = path[1:] if path.startswith("/") else path[2:]
        lines = str(it.get("lines", "")).strip()
        ok = path in seen_files  # a file the model was never shown cannot be evidence
        if ok and lines:
            nums = [int(x) for x in re.findall(r"\d+", lines)[:2]]
            total = file_lines.get(path) or 0
            if nums and total and max(nums) > total + 2:
                ok = False
        if not ok:
            bad += 1
        out.append({"file": path, "lines": lines, "note": truncate(str(it.get("note", "")), 200),
                    "valid": ok, "seen": path in seen_files})
    return out, bad


def validate(data, file_lines: dict, seen_files: dict) -> dict:
    """Coerce model output into the schema and check every citation against the snapshot."""
    if not isinstance(data, dict):
        raise ReviewError("Model output was not a JSON object")
    try:
        float(data.get("technical_score"))
    except (TypeError, ValueError):
        raise ReviewError("Model output has no numeric technical_score")
    bad_total, ev_total = 0, 0

    def ev(items):
        nonlocal bad_total, ev_total
        out, bad = _evidence(items, file_lines, seen_files)
        bad_total += bad
        ev_total += len(out)
        return out

    r = {
        "summary": truncate(str(data.get("summary", "")), 900),
        "architecture": truncate(str(data.get("architecture", "")), 500),
        "team_vs_generated": truncate(str(data.get("team_vs_generated", "")), 600),
        "responsible_ai": truncate(str(data.get("responsible_ai", "")), 600),
        "technical_score_rationale": truncate(str(data.get("technical_score_rationale", "")), 900),
        "confidence_note": truncate(str(data.get("confidence_note", "")), 400),
        "concept_only": bool(data.get("concept_only", False)),
    }
    r["claims"] = []
    for c in (data.get("claims") or [])[:10]:
        if not isinstance(c, dict):
            continue
        st = str(c.get("status", "unclear")).lower()
        r["claims"].append({
            "claim": truncate(str(c.get("claim", "")), 300),
            "source": str(c.get("source", "description")) if str(c.get("source", "")).lower() in ("description", "readme", "both") else "description",
            "status": st if st in CLAIM_STATUS else "unclear",
            "evidence": ev(c.get("evidence")),
            "note": truncate(str(c.get("note", "")), 400),
        })
    cd = data.get("challenge_data") or {}
    used = str(cd.get("used", "unclear")).lower()
    r["challenge_data"] = {"used": used if used in ("yes", "partly", "no", "unclear") else "unclear",
                           "detail": truncate(str(cd.get("detail", "")), 500), "evidence": ev(cd.get("evidence"))}
    ai = data.get("ai_approach") or {}
    t = str(ai.get("type", "other")).lower()
    r["ai_approach"] = {"type": t if t in AI_TYPES else "other", "detail": truncate(str(ai.get("detail", "")), 500),
                        "evidence": ev(ai.get("evidence"))}
    r["strengths"] = [truncate(str(s), 300) for s in (data.get("strengths") or [])[:6]]
    r["weaknesses"] = [truncate(str(s), 300) for s in (data.get("weaknesses") or [])[:6]]
    r["red_flags"] = []
    for f in (data.get("red_flags") or [])[:10]:
        if not isinstance(f, dict):
            continue
        ft, sev = str(f.get("type", "other")), str(f.get("severity", "low")).lower()
        r["red_flags"].append({"type": ft if ft in FLAG_TYPES else "other",
                               "severity": sev if sev in ("low", "medium", "high") else "low",
                               "detail": truncate(str(f.get("detail", "")), 500), "evidence": ev(f.get("evidence"))})
    r["jury_questions"] = [truncate(str(q), 300) for q in (data.get("jury_questions") or []) if str(q).strip()][:3]
    sc = data.get("scores") or {}
    r["scores"] = {k: _clamp(sc.get(k), default=3) for k in ("completeness", "depth", "integrity", "quality")}
    r["technical_score"] = _clamp(data.get("technical_score"))
    conf = str(data.get("confidence", "medium")).lower()
    r["confidence"] = conf if conf in ("high", "medium", "low") else "medium"
    r["evidence_checked"] = {"total": ev_total, "invalid": bad_total}
    if ev_total and bad_total / ev_total > 0.3:
        r["confidence"] = "low"
        r["confidence_note"] = (r["confidence_note"] + " " if r["confidence_note"] else "") + \
            f"[Scanner] {bad_total} of {ev_total} citations did not match the snapshot; treat details with care."
    claims = r["claims"]
    r["claims_summary"] = {s: sum(1 for c in claims if c["status"] == s) for s in CLAIM_STATUS}
    return r


# ------------------------------------------------------------------ backends

class ClaudeCLI:
    """Uses the Claude Code CLI with the user's own login (subscription or API key).

    Long text never goes on the command line: the system prompt is passed as a file and the
    review input through stdin. If the installed CLI rejects an option, it falls back to a
    compatible mode (instructions on stdin, JSON parsed from the answer).
    """

    def __init__(self, cfg: dict):
        rv = cfg["review"]
        self.exe = rv.get("claude_path") or shutil.which("claude") or ""
        self.model = rv["model"]
        self.effort = rv.get("effort") or ""
        self.timeout = int(rv["timeout_minutes"]) * 60
        self.tmp = Path(cfg["paths"]["workdir"]) / "prompts_tmp"
        self.flags = set()
        if self.exe:
            code, out, err = run([self.exe, "--help"], timeout=60)
            self.flags = set(re.findall(r"(--[a-z][a-z-]+)", out + err))
        self.shim = self.exe.lower().endswith((".cmd", ".bat"))
        self.compat = False

    def problem(self) -> str:
        if not self.exe:
            return "Claude Code CLI not found. Install it or set review.claude_path in the config."
        missing = [f for f in ("--print", "--output-format", "--model") if f not in self.flags]
        if missing:
            return f"Claude Code CLI is missing {', '.join(missing)}. Run 'claude update'."
        return ""

    def _cmd(self, system_text: str, deep: bool, schema=None) -> tuple:
        cmd = [self.exe, "-p", "--output-format", "json", "--model", self.model]
        for f in ("--no-session-persistence", "--strict-mcp-config", "--safe-mode"):
            if f in self.flags:  # --safe-mode: no CLAUDE.md, hooks, plugins or MCP servers, login kept
                cmd.append(f)
        if self.effort and "--effort" in self.flags:
            cmd += ["--effort", self.effort]
        if deep:
            if "--restricted" not in self.flags:
                raise ReviewError("Deep mode needs a Claude Code version with --restricted. Run 'claude update'.")
            cmd += ["--restricted", "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
                    "--permission-mode", "dontAsk"]
        elif "--tools" in self.flags:
            cmd += ["--tools", ""]
        if self.compat:
            return cmd, "<reviewer_instructions>\n" + system_text + "\n</reviewer_instructions>\n\n"
        self.tmp.mkdir(parents=True, exist_ok=True)
        sp = self.tmp / f"system-{sha256_text(system_text)[:12]}.md"
        if not sp.exists():
            sp.write_text(system_text, encoding="utf-8")
        cmd += ["--system-prompt-file", str(sp)]
        if "--json-schema" in self.flags and not self.shim:
            cmd += ["--json-schema", json.dumps(schema or SCHEMA, separators=(",", ":"))]
        return cmd, ""

    def _call(self, system_text: str, user_text: str, cwd, deep: bool, schema=None):
        for _ in range(2):
            cmd, prefix = self._cmd(system_text, deep, schema)
            code, out, err = run(cmd, cwd=cwd, timeout=self.timeout, input_bytes=(prefix + user_text).encode("utf-8"))
            if code != 0 and "unknown option" in (err + out).lower() and not self.compat:
                log("  Claude Code rejected an option; switching to compatible mode: " + truncate(err.strip(), 120))
                self.compat = True
                continue
            return code, out, err
        return code, out, err

    def review(self, system_text: str, user_text: str, cwd: Path, deep=False) -> tuple:
        code, out, err = self._call(system_text, user_text, cwd, deep)
        if code == 124:
            raise ReviewError(f"Claude Code timed out after {self.timeout // 60} min")
        payload = extract_json(out)
        if not isinstance(payload, dict):
            raise ReviewError(f"Claude Code returned no JSON (exit {code}): {truncate((err or out).strip(), 300)}")
        if payload.get("is_error"):
            raise ReviewError(f"Claude Code error: {truncate(str(payload.get('result', '')), 300)}")
        data = payload.get("structured_output")
        if not isinstance(data, dict):
            data = extract_json(str(payload.get("result", "")))
        if not isinstance(data, dict):
            raise ReviewError("Could not find the review JSON in Claude Code's answer")
        meta = {"backend": "claude-cli", "model": self.model, "deep": deep, "compat": self.compat,
                "cost_usd": payload.get("total_cost_usd"), "duration_ms": payload.get("duration_ms"),
                "turns": payload.get("num_turns")}
        return data, meta

    def ping(self) -> str:
        schema = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]}
        self.tmp.mkdir(parents=True, exist_ok=True)
        code, out, err = self._call('Return the JSON object {"ok": "yes"} and nothing else.', "Go.",
                                    self.tmp, False, schema)
        payload = extract_json(out)
        if not isinstance(payload, dict):
            return f"no JSON from claude (exit {code}): {truncate((err or out).strip(), 200)}"
        if payload.get("is_error"):
            return "claude reported an error: " + truncate(str(payload.get("result")), 200)
        if self.compat:
            log("     Note: running in compatible mode (older Claude Code). Works, but 'claude update' is better.")
        return ""


class AnthropicAPI:
    def __init__(self, cfg: dict):
        self.model = cfg["review"]["model"]
        self.timeout = int(cfg["review"]["timeout_minutes"]) * 60
        self.client = None

    def problem(self) -> str:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "The 'anthropic' package is not installed (pip install anthropic)."
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return "ANTHROPIC_API_KEY is not set."
        return ""

    def review(self, system_text: str, user_text: str, cwd: Path, deep=False) -> tuple:
        if deep:
            raise ReviewError("Deep mode is only available with the claude-cli backend")
        import anthropic
        if self.client is None:
            self.client = anthropic.Anthropic(max_retries=4, timeout=self.timeout)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=system_text,
            messages=[{"role": "user", "content": user_text}],
            tools=[{"name": "submit_review", "description": "Submit the structured technical review.",
                    "input_schema": SCHEMA}],
            tool_choice={"type": "tool", "name": "submit_review"},
        )
        data = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if not isinstance(data, dict):
            raise ReviewError("The API response contained no review")
        usage = getattr(resp, "usage", None)
        meta = {"backend": "anthropic", "model": self.model, "deep": False,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None)}
        return data, meta

    def ping(self) -> str:
        return self.problem()


class Offline:
    """Writes the full request to disk; picks up a response JSON if someone has put one there."""

    def __init__(self, cfg: dict):
        self.dir = Path(cfg["paths"]["workdir"])

    def problem(self) -> str:
        return ""

    def review(self, system_text, user_text, cwd, deep=False, slug=""):
        req = self.dir / "requests" / f"{slug}.md"
        req.parent.mkdir(parents=True, exist_ok=True)
        content = "=== SYSTEM ===\n" + system_text + "\n\n=== USER ===\n" + user_text
        if not req.exists() or req.read_text(encoding="utf-8") != content:
            req.write_text(content, encoding="utf-8")  # only touched when the input really changed
        resp_path = self.dir / "responses" / f"{slug}.json"
        resp = read_json(resp_path)
        try:
            stale = not isinstance(resp, dict) or resp_path.stat().st_mtime < req.stat().st_mtime
        except OSError:
            stale = True
        if stale:
            raise ReviewError(f"pending: request written to {req}; put the answer in responses/{slug}.json")
        return resp, {"backend": "offline", "model": "manual", "deep": deep}

    def ping(self) -> str:
        return ""


def make_backend(cfg: dict):
    kind = cfg["review"]["backend"]
    if kind == "claude-cli":
        return ClaudeCLI(cfg)
    if kind == "anthropic":
        return AnthropicAPI(cfg)
    return Offline(cfg)


def cache_key(system_text: str, user_text: str, deep: bool) -> str:
    """Same input -> same cached review, whichever model or backend produced it (meta records which)."""
    return sha256_text("|".join([PROMPT_VERSION, str(deep), system_text, user_text]))


def cached_review(cfg, slug, system_text, user_text, deep, file_lines, seen_files):
    """The stored review for exactly this input, or None."""
    cached = read_json(Path(cfg["paths"]["workdir"]) / "reviews" / f"{slug}.json")
    if isinstance(cached, dict) and cached.get("key") == cache_key(system_text, user_text, deep) and cached.get("raw"):
        try:
            return {"meta": cached.get("meta", {}), "review": validate(cached["raw"], file_lines, seen_files)}
        except ReviewError:
            return None
    return None


def run_review(backend, cfg, slug, system_text, user_text, cwd, deep, file_lines, seen_files, force=False) -> dict:
    workdir = Path(cfg["paths"]["workdir"])
    cache_file = workdir / "reviews" / f"{slug}.json"
    key = cache_key(system_text, user_text, deep)
    cached = read_json(cache_file)
    if not force and isinstance(cached, dict) and cached.get("key") == key and cached.get("raw"):
        try:
            return {"status": "ok", "cached": True, "meta": cached.get("meta", {}),
                    "review": validate(cached["raw"], file_lines, seen_files)}
        except ReviewError:
            pass
    last_err = ""
    for attempt in (1, 2):
        try:
            if isinstance(backend, Offline):
                raw, meta = backend.review(system_text, user_text, cwd, deep, slug=slug)
            else:
                raw, meta = backend.review(system_text, user_text, cwd, deep)
            review = validate(raw, file_lines, seen_files)
            write_json(cache_file, {"key": key, "raw": raw, "meta": meta, "prompt_version": PROMPT_VERSION})
            return {"status": "ok", "cached": False, "meta": meta, "review": review}
        except ReviewError as e:
            last_err = str(e)
            if last_err.startswith("pending:"):
                return {"status": "pending", "error": last_err}
            log(f"  review attempt {attempt} failed for {slug}: {truncate(last_err, 160)}")
        except Exception as e:  # noqa: BLE001  (network, SDK errors)
            last_err = f"{type(e).__name__}: {e}"
            log(f"  review attempt {attempt} failed for {slug}: {truncate(last_err, 160)}")
    return {"status": "error", "error": last_err}
