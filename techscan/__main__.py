"""Command line: python -m techscan <command> ...

  preflight  check git, GitHub access and the review engine before the event
  discover   list team repos in the GitHub org (for runs before Junction submissions exist)
  scan       clone, check, review and write the jury outputs
  report     rebuild the outputs from the last scan without re-running anything
  demo       build a fake event with planted problems, to rehearse the whole flow
  selftest   build the fake event and check that every planted problem is caught (no AI)
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

from .config import ConfigError, load_config
from .util import ensure_utf8_console, log, parse_dt


def cmd_preflight(cfg, args) -> int:
    from .gitops import GitHub, git_available
    from .review import make_backend
    ok = True
    log(f"Python {sys.version.split()[0]}")
    gv = git_available()
    log(("OK   " if gv else "FAIL ") + (gv or "git not found on PATH"))
    ok &= bool(gv)
    ev = cfg["event"]
    log(f"OK   Event window {ev['start_dt'].isoformat()} -> deadline {ev['deadline_dt'].isoformat()}")
    for key, ch in cfg["challenges"].items():
        log(f"     Challenge {key}: {ch['name']} (template: {ch.get('template') or 'auto from GitHub'}, "
            f"brief: {'config file' if ch.get('brief_path') else 'from template'})")
    gh = GitHub(enabled=cfg["scan"].get("github_api", True))
    if gh.enabled:
        data, err = gh.get("https://api.github.com/rate_limit")
        if data:
            core = (data.get("resources") or {}).get("core") or {}
            log(f"OK   GitHub API {'with token' if gh.token else 'WITHOUT token'}: "
                f"{core.get('remaining')} of {core.get('limit')} calls left this hour")
            if not gh.token:
                log("     Tip: set GITHUB_TOKEN so 25+ repos do not hit the 60 calls/hour anonymous limit")
        else:
            log(f"WARN GitHub API not reachable ({err}); push-log checks will be skipped")
    backend = make_backend(cfg)
    problem = backend.problem()
    log(("FAIL " if problem else "OK   ") + f"Review engine {cfg['review']['backend']} / {cfg['review']['model']}"
        + (f": {problem}" if problem else ""))
    ok &= not problem
    if not problem and args.ping:
        log("     Sending a one-line test request...")
        err = backend.ping()
        log(("FAIL " if err else "OK   ") + ("Review engine answered" if not err else err))
        ok &= not err
    log("Preflight " + ("passed" if ok else "found problems"))
    return 0 if ok else 1


def cmd_discover(cfg, args) -> int:
    from .gitops import GitHub
    from .intake import match_challenge, write_submissions_csv
    org = args.org or cfg["event"].get("github_org")
    if not org:
        raise SystemExit("Set event.github_org in the config or pass --org")
    gh = GitHub(cache_dir=Path(cfg["paths"]["workdir"]) / "github_cache")
    since = cfg["event"]["start_dt"] - timedelta(days=args.days_before)
    def repo_name(url):
        u = str(url or "").rstrip("/")
        return (u[:-4] if u.endswith(".git") else u).rsplit("/", 1)[-1].lower()
    template_names = {repo_name(ch.get("template")) for ch in cfg["challenges"].values() if ch.get("template")}
    rows = []
    for repo in gh.org_repos(org):
        if repo.get("is_template") or repo.get("archived") or repo.get("name", "").lower() in template_names:
            continue
        try:
            created = parse_dt(repo.get("created_at", ""))
        except ValueError:
            continue
        if created < since:
            continue
        meta = gh.repo_meta(org, repo["name"])
        tpl = meta.get("template", "")
        rows.append({
            "project": repo["name"],
            "repo": repo["html_url"],
            "challenge": match_challenge(f"{tpl} {repo['name']}", cfg["challenges"]),
            "description": repo.get("description") or "",
            "created": repo.get("created_at", ""),
            "template": tpl,
        })
    rows.sort(key=lambda r: (r["challenge"], r["project"].lower()))
    out = Path(args.output)
    write_submissions_csv(out, rows)
    log(f"{len(rows)} repo(s) created since {since.date()} written to {out}")
    unknown = [r["project"] for r in rows if not r["challenge"]]
    if unknown:
        log(f"Challenge not recognised for: {', '.join(unknown)}. Fill in the 'challenge' column by hand.")
    return 0


def cmd_scan(cfg, args) -> int:
    from .pipeline import run_pipeline
    from .report import write_outputs
    if not Path(args.submissions).exists():
        raise SystemExit(f"Submissions file not found: {args.submissions}")
    if args.backend:
        cfg["review"]["backend"] = args.backend
    if args.model:
        cfg["review"]["model"] = args.model
    run_pipeline(cfg, args.submissions, only=args.only, skip_review=args.skip_review,
                 force_review=args.force_review, deep=args.deep, workers=args.workers)
    files = write_outputs(cfg, Path(cfg["paths"]["workdir"]), Path(cfg["paths"]["output"]))
    log("Done. Open these:")
    for f in files:
        log(f"  {f}")
    return 0


def cmd_report(cfg, args) -> int:
    from .report import write_outputs
    files = write_outputs(cfg, Path(cfg["paths"]["workdir"]), Path(cfg["paths"]["output"]), only=args.only)
    for f in files:
        log(f"  {f}")
    return 0


def main(argv=None) -> int:
    ensure_utf8_console()
    ap = argparse.ArgumentParser(prog="python -m techscan", description="HFG technical submission scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_config(p):
        p.add_argument("-c", "--config", required=True, help="edition config, e.g. config/hfg8.yaml")
        return p

    p = with_config(sub.add_parser("preflight", help="check that everything is ready"))
    p.add_argument("--ping", action="store_true", help="also send one tiny test request to the review engine")

    p = with_config(sub.add_parser("discover", help="list team repos in the GitHub org"))
    p.add_argument("--org", default="")
    p.add_argument("--days-before", type=int, default=3, help="include repos created this many days before kickoff")
    p.add_argument("-o", "--output", default="submissions-github.csv")

    p = with_config(sub.add_parser("scan", help="run the full scan and write the jury outputs"))
    p.add_argument("-s", "--submissions", required=True, help="Junction export CSV or a project,repo,challenge CSV")
    p.add_argument("--only", default="", help="comma-separated team slugs or project names")
    p.add_argument("--skip-review", action="store_true", help="only the non-AI checks")
    p.add_argument("--force-review", action="store_true", help="ignore cached reviews")
    p.add_argument("--deep", default="", help="comma-separated teams to review in agent mode (finalists)")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--backend", choices=["claude-cli", "anthropic", "offline"], default="")
    p.add_argument("--model", default="")

    p = with_config(sub.add_parser("report", help="rebuild outputs from the last scan"))
    p.add_argument("--only", default="", help="write one pack with just these teams (e.g. the finalists)")

    p = sub.add_parser("demo", help="build a fake event with planted problems")
    p.add_argument("--dir", default="demo-event")

    p = sub.add_parser("selftest", help="build the fake event and check every planted problem is caught (no AI)")
    p.add_argument("--dir", default="selftest-event")

    args = ap.parse_args(argv)
    if args.cmd == "demo":
        from .fixtures import build_demo
        build_demo(Path(args.dir))
        return 0
    if args.cmd == "selftest":
        from .fixtures import selftest
        return selftest(Path(args.dir))
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log(f"Config problem: {e}")
        return 2
    return {"preflight": cmd_preflight, "discover": cmd_discover, "scan": cmd_scan, "report": cmd_report}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
