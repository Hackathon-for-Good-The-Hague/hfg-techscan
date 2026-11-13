"""Load and validate the edition config (YAML)."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

from .util import parse_dt

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
        "timeout_minutes": 15,
        "claude_path": "",
        "effort": "",
        "deep_teams": [],
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
    },
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
        brief = ch.get("brief") or ""
        if brief:
            bp = (base / brief).resolve()
            if not bp.exists():
                raise ConfigError(f"challenges.{key}.brief file not found: {bp}")
            ch["brief_path"] = str(bp)

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

    rv = cfg["review"]
    if rv["backend"] not in ("claude-cli", "anthropic", "offline"):
        raise ConfigError("review.backend must be claude-cli, anthropic or offline")
    try:
        rv["workers"] = max(1, int(rv["workers"]))
    except (TypeError, ValueError) as e:
        raise ConfigError(f"review.workers must be a number, got {rv['workers']!r}") from e

    cfg["paths"]["workdir"] = str((base / cfg["paths"]["workdir"]).resolve())
    cfg["paths"]["output"] = str((base / cfg["paths"]["output"]).resolve())
    cfg["_config_path"] = str(path)
    return cfg
