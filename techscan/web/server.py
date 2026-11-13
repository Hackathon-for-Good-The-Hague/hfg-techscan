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


# ------------------------------------------------------------------ pages

@app.route("/")
def dashboard():
    workdir = Path(_cfg["paths"]["workdir"])
    run_info = read_json(workdir / "results" / "_run.json", {})
    teams = _load_team_summary(workdir)
    return template("dashboard", cfg=_cfg, run_info=run_info, teams=teams)


@app.route("/config")
def config_page():
    config_text = Path(_config_path).read_text(encoding="utf-8") if Path(_config_path).exists() else ""
    return template("config_editor", cfg=_cfg, config_text=config_text, config_path=_config_path)


@app.route("/scan")
def scan_page():
    return template("scan", cfg=_cfg, running=_scan_running.is_set())


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
        # Validate by loading
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

    yield "data: Connected to scan stream\n\n"
    while _scan_running.is_set() or not _scan_log.empty():
        try:
            msg = _scan_log.get(timeout=1)
            yield f"data: {msg}\n\n"
        except queue.Empty:
            yield ": keepalive\n\n"
    yield "data: [DONE]\n\n"


@app.get("/api/teams")
def teams_json():
    workdir = Path(_cfg["paths"]["workdir"])
    return {"teams": _load_team_summary(workdir)}


@app.get("/api/results")
def results_list():
    output_dir = Path(_cfg["paths"]["output"])
    files = []
    if output_dir.exists():
        for f in sorted(output_dir.iterdir()):
            if f.suffix in (".html", ".xlsx"):
                files.append({"name": f.name, "size_kb": round(f.stat().st_size / 1024)})
    return {"files": files}


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
        high_flags = sum(1 for fl in flags if fl.get("severity") == "high")
        teams.append({
            "slug": f.stem,
            "project": sub.get("project", f.stem),
            "challenge": sub.get("challenge", ""),
            "status": data.get("status", ""),
            "score": review.get("technical_score", ""),
            "flags_total": len(flags),
            "flags_high": high_flags,
            "review_status": data.get("review_status", ""),
        })
    return teams


def _run_scan(submissions: str, skip_review: bool):
    _scan_running.set()
    # Drain any old messages
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
