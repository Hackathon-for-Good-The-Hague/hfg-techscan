"""Load and validate the edition config (YAML)."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

from .util import parse_dt

BACKENDS = ("claude-cli", "anthropic", "greenpt", "openai-compatible", "offline")

DEFAULTS = {
    "event": {
        "name": "Hackathon for Good",
        "short_name": "HFG",
        "context": "A purpose-driven weekend hackathon in The Hague.",
        "build_time": "about 40 hours of building time",
        "start": None,
        "deadline": None,
        "github_org": "",
    },
    "challenges": {},
    "partners": {},
    "review": {
        "backend": "claude-cli",  # claude-cli | anthropic | offline
        "model": "claude-sonnet-4-6",
        "workers": 3,
        "budget_chars": 400000,
        "max_file_chars": 40000,
        "brief_chars": 14000,  # how much of the challenge brief the model sees
        "timeout_minutes": 15,
        "claude_path": "",
        "effort": "",
        "deep_teams": [],
        "language": "en",
        # greenpt / openai-compatible only: API root, the env var holding the key, and json output mode
        "api_base": "",          # greenpt default: https://api.greenpt.ai/v1
        "api_key_env": "",       # greenpt default: GREENPT_API_KEY; openai-compatible: OPENAI_API_KEY
        "response_format": "",   # json_schema (default) | json_object | text; steps down automatically
        "max_output_tokens": 0,  # 0 = 16000; reasoning models need more because hidden thinking counts  # language of the AI-written text in the jury pack: en, nl, de, fr
    },
    "rubric": {
        "show_scores_to_jury": False,  # when False, jury packs show evidence/claims/flags but not AI scores
        "criteria": {
            "concept": {"label": "Concept", "weight": 4, "description": "Is the concept a real solution for the challenge, and more than just an idea?"},
            "prototype": {"label": "Prototype", "weight": 2, "description": "Did the team build a prototype, and does it work?"},
            "technology": {"label": "Technology feasibility", "weight": 1.5, "description": "Future-proof technology, including ethics, security and the legal side of AI?"},
            "potential": {"label": "Product potential", "weight": 1.5, "description": "How innovative and clear is the product or service, and its opportunity?"},
            "business": {"label": "Business feasibility", "weight": 0.5, "description": "How feasible and clear is the financial and business model?"},
            "wow": {"label": "Wow factor", "weight": 0.5, "description": "Quality of the pitch and the team's ability to sell the idea."},
        },
        "max_score": 10,
        "max_total": 100,
    },
    "scan": {
        "pre_event_grace_hours": 2,
        "big_drop_lines": 1500,
        "big_drop_share": 0.4,
        "similarity_min_shared_lines": 25,
        "similarity_min_ratio": 0.2,
        "boilerplate_min_repos": 3,
        "extra_ignore_dirs": [],
        "check_demo_urls": True,
        "github_api": True,
        "extra_secret_patterns": [],
        "custom_checks_dir": "",
    },
    # colours, fonts and logos for the jury pack (see techscan/branding.py for every key and its default)
    "branding": {},
    # where submissions come from, in priority order (see techscan/sources.py); empty = pass -s <csv>
    "sources": [],
    "junction": {
        "event_slug": "",
        "event_id": "",
        "api_url": "https://www.junctionorganize.com",
    },
    "builderbase": {
        "org_slug": "hackathon-for-good",
        "event_slug": "",
        "event_id": "",
        "api_url": "https://api.builderbase.com",
    },
    "paths": {"workdir": "./work", "output": "./out"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "partners":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(Exception):
    pass


def load_config(path) -> dict:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, data)
    if "partners" in data:
        cfg["partners"] = data["partners"] or {}
    if (data.get("rubric") or {}).get("criteria"):
        cfg["rubric"]["criteria"] = data["rubric"]["criteria"]  # an event's own criteria replace the defaults

    ev = cfg["event"]
    for key in ("start", "deadline"):
        if not ev.get(key):
            raise ConfigError(f"event.{key} is required in {path.name}")
        try:
            ev[key + "_dt"] = parse_dt(ev[key])
        except ValueError as e:
            raise ConfigError(f"event.{key}: {e}") from e
    if ev["deadline_dt"] <= ev["start_dt"]:
        raise ConfigError("event.deadline must be after event.start")

    if not cfg["challenges"]:
        raise ConfigError("Add at least one challenge under 'challenges:'")
    base = path.parent
    for key, ch in cfg["challenges"].items():
        ch.setdefault("name", key)
        ch["match"] = [m.lower() for m in (ch.get("match") or [key])]
        ch["data_hints"] = [h.lower() for h in (ch.get("data_hints") or [])]
        ch.setdefault("template", "")
        if not isinstance(ch.get("brief_files") or [], list):
            raise ConfigError(f"challenges.{key}.brief_files must be a list of paths in the template repo")
        brief = ch.get("brief") or ""
        if brief:
            bp = (base / brief).resolve()
            if not bp.exists():
                raise ConfigError(f"challenges.{key}.brief file not found: {bp}")
            ch["brief_path"] = str(bp)

    from .requirements import config_problems
    problems = config_problems(cfg["challenges"])
    if problems:
        raise ConfigError(problems[0])

    for key, partner in cfg["partners"].items():
        for pat in partner.get("patterns", []):
            try:
                re.compile(pat)
            except re.error as e:
                raise ConfigError(f"partners.{key}: invalid regex pattern {pat!r}: {e}") from e

    for entry in cfg["scan"].get("extra_secret_patterns") or []:
        if not isinstance(entry, dict) or "pattern" not in entry:
            raise ConfigError("Each extra_secret_patterns entry needs at least a 'pattern' key")
        try:
            re.compile(entry["pattern"])
        except re.error as e:
            raise ConfigError(f"extra_secret_patterns: invalid regex {entry['pattern']!r}: {e}") from e
        entry.setdefault("name", "Custom secret")

    if not isinstance(cfg.get("sources"), list):
        raise ConfigError("sources must be a list, e.g. [{type: github-prs}, {type: csv, path: alkemio.csv}]")
    for i, src in enumerate(cfg["sources"]):
        if not isinstance(src, dict) or src.get("type") not in ("github-prs", "csv", "junction"):
            raise ConfigError(f"sources[{i}]: type must be github-prs, csv or junction")
        if src["type"] == "csv" and not src.get("path"):
            raise ConfigError(f"sources[{i}]: a csv source needs a 'path'")

    rb = cfg["rubric"]
    if rb.get("max_score") not in (5, 10):
        raise ConfigError("rubric.max_score must be 5 or 10 (the scales the review prompt has anchors for)")

    rv = cfg["review"]
    if rv["backend"] not in BACKENDS:
        raise ConfigError(f"review.backend must be one of {', '.join(BACKENDS)}")
    try:
        rv["workers"] = max(1, int(rv["workers"]))
    except (TypeError, ValueError) as e:
        raise ConfigError(f"review.workers must be a number, got {rv['workers']!r}") from e

    from .branding import check as check_branding, resolve_paths
    if not isinstance(cfg.get("branding"), dict):
        raise ConfigError("branding must be a mapping, e.g. {primary: '#2C55A1', accent: '#E52983'}")
    resolve_paths(cfg["branding"], base)
    bad = [m for m in check_branding(cfg["branding"]) if m.startswith("branding.")]
    if bad:
        raise ConfigError(bad[0])

    cfg["paths"]["workdir"] = str((base / cfg["paths"]["workdir"]).resolve())
    cfg["paths"]["output"] = str((base / cfg["paths"]["output"]).resolve())
    cfg["_config_path"] = str(path)
    return cfg
