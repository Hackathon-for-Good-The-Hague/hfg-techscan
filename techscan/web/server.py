"""Local web dashboard for TechScan.

Start with: techscan web -c config/my-event.yaml
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    from bottle import Bottle, request, response, static_file, template, TEMPLATE_PATH
except ImportError:
    raise SystemExit(
        "The web dashboard requires Bottle. Install it with:\n"
        "  pip install hfg-techscan[web]\n"
        "  # or: pip install bottle"
    )

from ..config import load_config
from ..util import log, read_json

WEB_DIR = Path(__file__).parent
TEMPLATE_PATH.insert(0, str(WEB_DIR / "templates"))

app = Bottle()

# Globals set by start()
_cfg: dict = {}
_config_path: str = ""
_scan_log: queue.Queue = queue.Queue()
_scan_running = threading.Event()


def _challenge_name(key: str) -> str:
    """Resolve challenge config key to human name."""
    ch = _cfg.get("challenges", {}).get(key, {})
    return ch.get("name", key) if ch else key


def _review_label(status: str) -> str:
    return {"ok": "AI review", "skipped": "Checks only", "error": "Error", "": ""}.get(status, status)


def _output_files() -> list:
    """Categorize output files for the quick actions."""
    output_dir = Path(_cfg["paths"]["output"])
    jury_packs, challenge_reports, scorecards, other = [], [], [], []
    if not output_dir.exists():
        return []
    for f in sorted(output_dir.iterdir()):
        if f.suffix == ".xlsx":
            scorecards.append({"name": f.name, "path": f.name, "label": "Scorecard"})
        elif f.name.startswith("jury-pack-all"):
            jury_packs.insert(0, {"name": f.name, "path": f.name, "label": "All Teams"})
        elif f.name.startswith("jury-pack-"):
            key = f.stem.replace("jury-pack-", "")
            jury_packs.append({"name": f.name, "path": f.name, "label": _challenge_name(key)})
        elif f.name.startswith("challenge-report-"):
            key = f.stem.replace("challenge-report-", "")
            challenge_reports.append({"name": f.name, "path": f.name, "label": _challenge_name(key)})
        elif f.suffix == ".html":
            other.append({"name": f.name, "path": f.name, "label": f.stem})
    return jury_packs + scorecards + challenge_reports


# ------------------------------------------------------------------ pages

@app.route("/")
def dashboard():
    workdir = Path(_cfg["paths"]["workdir"])
    run_info = read_json(workdir / "results" / "_run.json", {})
    teams = _load_team_summary(workdir)
    files = _output_files()
    # Summary stats
    stats = {
        "total": len(teams),
        "ready": sum(1 for t in teams if t["status"] == "ready"),
        "reviewed": sum(1 for t in teams if t["review_status"] == "ok"),
        "unreachable": sum(1 for t in teams if t["status"] in ("unreachable", "error", "no_repo")),
        "flags_high": sum(t["flags_high"] for t in teams),
        "flags_medium": sum(t["flags_medium"] for t in teams),
        "flags_low": sum(t["flags_low"] for t in teams),
        "flags_total": sum(t["flags_total"] for t in teams),
    }
    scores = [t["score"] for t in teams if isinstance(t["score"], (int, float)) and t["score"]]
    stats["score_min"] = round(min(scores), 1) if scores else None
    stats["score_max"] = round(max(scores), 1) if scores else None
    stats["score_median"] = round(sorted(scores)[len(scores) // 2], 1) if scores else None
    # Jury packs and scorecards for quick actions
    jury_packs = [f for f in files if f["name"].startswith("jury-pack-")]
    scorecards = [f for f in files if f["name"].endswith(".xlsx")]
    challenge_reports = [f for f in files if f["name"].startswith("challenge-report-")]
    return template("dashboard", cfg=_cfg, run_info=run_info, teams=teams, stats=stats,
                     jury_packs=jury_packs, scorecards=scorecards, challenge_reports=challenge_reports,
                     current="dashboard")


@app.route("/config")
def config_page():
    config_text = Path(_config_path).read_text(encoding="utf-8") if Path(_config_path).exists() else ""
    return template("config_editor", cfg=_cfg, config_text=config_text, config_path=_config_path,
                     current="config")


@app.route("/scan")
def scan_page():
    return template("scan", cfg=_cfg, running=_scan_running.is_set(), config_path=_config_path,
                     current="scan")


@app.route("/results/<filepath:path>")
def serve_result(filepath):
    output_dir = _cfg["paths"]["output"]
    return static_file(filepath, root=output_dir)


# ------------------------------------------------------------------ API

@app.post("/api/config")
def save_config():
    text = request.forms.get("config_text", "")
    try:
        Path(_config_path).write_text(text, encoding="utf-8")
        load_config(_config_path)
        return {"ok": True, "message": "Config saved and validated."}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.post("/api/scan")
def start_scan():
    if _scan_running.is_set():
        return {"ok": False, "message": "A scan is already running."}
    subs = request.forms.get("submissions", "")
    skip_review = request.forms.get("skip_review") == "on"
    threading.Thread(target=_run_scan, args=(subs, skip_review), daemon=True).start()
    return {"ok": True, "message": "Scan started."}


@app.get("/api/scan/stream")
def scan_stream():
    response.content_type = "text/event-stream"
    response.set_header("Cache-Control", "no-cache")
    response.set_header("X-Accel-Buffering", "no")

    def esc(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    yield "data: <div class=\"log-line muted\">Connected to scan stream</div>\n\n"
    while _scan_running.is_set() or not _scan_log.empty():
        try:
            msg = _scan_log.get(timeout=1)
            yield f"data: <div class=\"log-line\">{esc(msg)}</div>\n\n"
        except queue.Empty:
            yield ": keepalive\n\n"
    yield "data: <div class=\"log-line log-done\">Scan complete. <a href=\"/\">View dashboard</a></div>\n\n"


@app.get("/api/team/<slug>")
def team_detail(slug):
    """Return HTML fragment for the team detail slide-over panel."""
    workdir = Path(_cfg["paths"]["workdir"])
    data = read_json(workdir / "results" / f"{slug}.json", {})
    if not data:
        response.content_type = "text/html"
        return "<p>Team not found.</p>"
    response.content_type = "text/html"
    return template("team_detail", data=data, challenge_name=_challenge_name,
                     cfg=_cfg)


# ------------------------------------------------------------------ static

@app.route("/static/<filepath:path>")
def serve_static(filepath):
    return static_file(filepath, root=str(WEB_DIR / "static"))


# ------------------------------------------------------------------ helpers

def _load_team_summary(workdir: Path) -> list:
    results_dir = workdir / "results"
    if not results_dir.exists():
        return []
    teams = []
    for f in sorted(results_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        data = read_json(f, {})
        if not data:
            continue
        sub = data.get("submission", {})
        review = data.get("review") or {}
        flags = data.get("flags", [])
        high = sum(1 for fl in flags if fl.get("severity") == "high")
        medium = sum(1 for fl in flags if fl.get("severity") == "medium")
        low = sum(1 for fl in flags if fl.get("severity") == "low")
        # Top flag titles for hover
        flag_titles = [fl.get("title", "") for fl in flags if fl.get("severity") in ("high", "medium")][:5]
        teams.append({
            "slug": f.stem,
            "project": sub.get("project", f.stem),
            "challenge": sub.get("challenge", ""),
            "challenge_name": _challenge_name(sub.get("challenge", "")),
            "status": data.get("status", ""),
            "score": review.get("technical_score", ""),
            "flags_total": len(flags),
            "flags_high": high,
            "flags_medium": medium,
            "flags_low": low,
            "flag_titles": flag_titles,
            "review_status": data.get("review_status", ""),
            "review_label": _review_label(data.get("review_status", "")),
            "code_lines": (data.get("facts") or {}).get("team_code_lines", 0),
            "languages": list((data.get("facts") or {}).get("languages", {}).keys())[:3],
        })
    return teams


def _run_scan(submissions: str, skip_review: bool):
    _scan_running.set()
    while not _scan_log.empty():
        try:
            _scan_log.get_nowait()
        except queue.Empty:
            break
    cmd = [sys.executable, "-m", "techscan", "scan", "-c", _config_path]
    if submissions:
        cmd += ["-s", submissions]
    if skip_review:
        cmd += ["--skip-review"]
    _scan_log.put(f"Running: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                bufsize=1, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            _scan_log.put(line.rstrip())
        proc.wait()
        _scan_log.put(f"Scan finished with exit code {proc.returncode}")
    except Exception as e:
        _scan_log.put(f"Scan error: {type(e).__name__}: {e}")
    finally:
        _scan_running.clear()


def start(config_path: str, host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True):
    global _cfg, _config_path
    _config_path = str(Path(config_path).resolve())
    _cfg = load_config(_config_path)
    url = f"http://{host}:{port}"
    log(f"TechScan dashboard at {url}")
    log(f"Config: {_config_path}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, quiet=True)
