"""Orchestrates: intake -> snapshot -> templates -> checks -> cross-team -> review -> outputs."""
from __future__ import annotations

import os
import re
import shutil
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import facts as F
from .gitops import (GitHub, branch_from_tree_path, clone_or_update, commit_log, default_branch, git, history_patch,
                     other_branches, snapshot, summarize_activity)
from .intake import match_challenge, read_submissions
from .pack import build_pack
from .review import cached_review, make_backend, run_review, system_prompt
from .similarity import cross_repo, template_line_hashes
from .util import log, read_text, rmtree, truncate, utcnow, write_json

SANITIZE_NAMES = {"claude.md", "claude.local.md", ".mcp.json", "agents.md", ".cursorrules", ".windsurfrules",
                  ".gitignore", ".ignore", ".rgignore"}
SANITIZE_DIRS = {".claude", ".cursor", ".git", ".github", ".vscode", ".idea"}
CONTEXT_ERROR = re.compile(r"(?i)prompt is too long|too many tokens|context (window|length)|maximum context|input length")


def _pick(subs, only: str):
    if not only:
        return subs
    keys = [k.strip().lower() for k in only.split(",") if k.strip()]
    return [s for s in subs if s.slug in keys or s.project.lower() in keys]


def _template_for(cfg, chal_key, metas):
    """Config template wins. Otherwise the most used template, but only one owned by the HFG org:
    the brief inside it is treated as trusted, so a template a team picked elsewhere must not qualify."""
    ch = cfg["challenges"].get(chal_key) or {}
    if ch.get("template"):
        return ch["template"]
    org = (cfg["event"].get("github_org") or "").lower()
    urls = Counter(m.get("template_clone_url") for m in metas
                   if m.get("template_clone_url") and (not org or (m.get("template") or "").lower().startswith(org + "/")))
    return sorted(urls.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if urls else ""


def _load_templates(cfg, results, workdir: Path) -> dict:
    templates = {}
    for key, ch in cfg["challenges"].items():
        metas = [r["github"].get("meta") or {} for r in results.values() if r["submission"]["challenge"] == key]
        url = _template_for(cfg, key, metas)
        info = {"url": url, "hashes": {}, "lines": {}, "shas": frozenset(), "line_hashes": set(),
                "brief": "", "brief_source": ""}
        if url:
            dest = workdir / "templates" / key
            ok, msg = clone_or_update(url, dest)
            br = default_branch(dest) if ok else ""
            snap = snapshot(dest, "2999-01-01T00:00:00+00:00", preferred=br,
                            out_dir=workdir / "snapshots" / f"template-{key}") if br else {}
            if snap.get("ok"):
                inv = F.Inventory(Path(snap["dir"]))
                info["hashes"] = {f["path"]: f["hash"] for f in inv.files}
                for f in inv.files:
                    if f["kind"] in ("code", "notebook", "config", "doc") and not f["binary"] and f["size"] < 1_000_000:
                        try:
                            info["lines"][f["path"]] = {ln.strip() for ln in inv.read(f["path"]).splitlines() if ln.strip()}
                        except OSError:
                            pass
                info["line_hashes"] = template_line_hashes(inv)
                _, out, _ = git(["rev-list", "--all"], cwd=dest)
                info["shas"] = frozenset(x.strip() for x in out.split() if x.strip())
                cands = [f for f in inv.files if f["kind"] == "doc" and re.search(r"(?i)(brief|challenge)", f["path"])]
                cands += [f for f in inv.files if re.match(r"(?i)^readme\.md$", f["path"])]
                if cands:
                    info["brief"] = inv.read(cands[0]["path"])
                    info["brief_source"] = f"template: {cands[0]['path']}"
            else:
                log(f"Template for {key} not available: {msg if not ok else snap.get('error', 'no commits')}")
        if ch.get("brief_path"):
            info["brief"] = read_text(Path(ch["brief_path"]))
            info["brief_source"] = f"config: {Path(ch['brief_path']).name}"
        templates[key] = info
    return templates


def _sanitized_copy(src: Path, dest: Path) -> None:
    """Copy of the snapshot without git data, agent instruction files, tool configs or ignore files (deep mode)."""
    rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if d.lower() not in SANITIZE_DIRS and d not in F.IGNORE_DIRS
                       and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = Path(dirpath) / name
            if name.lower() in SANITIZE_NAMES or os.path.islink(full):
                continue
            target = dest / full.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(full, target)


def _failed(r: dict, status: str, err: str) -> None:
    r["status"] = status
    r["errors"].append(err)


def run_pipeline(cfg: dict, submissions_path, only="", skip_review=False, force_review=False, deep="",
                 workers=None) -> dict:
    """Scans every team in the CSV. --only limits which teams get a fresh AI review; the cross-team
    checks always see everyone, and the other teams keep the review they already have."""
    workdir = Path(cfg["paths"]["workdir"])
    workdir.mkdir(parents=True, exist_ok=True)
    ev, sc, rv = cfg["event"], cfg["scan"], cfg["review"]
    subs = read_submissions(submissions_path, cfg)
    if not subs:
        raise SystemExit("No submissions found in the CSV.")
    review_set = {s.slug for s in _pick(subs, only)}
    if only and not review_set:
        raise SystemExit("--only matched no team (use the project name or the slug).")
    log(f"{len(subs)} submission(s) from {Path(submissions_path).name}"
        + (f"; fresh AI review only for {len(review_set)}" if only else ""))
    gh = GitHub(enabled=sc.get("github_api", True), cache_dir=workdir / "github_cache")
    started = utcnow()

    # 1. clone and freeze ------------------------------------------------------
    locks, locks_guard = {}, threading.Lock()

    def repo_dir(s):
        name = f"{s.owner}__{s.repo}".lower() if s.owner else s.slug
        return workdir / "repos" / re.sub(r"[^a-z0-9_.-]", "-", name.lower())

    def stage1(s):
        dest = repo_dir(s)
        with locks_guard:
            lock = locks.setdefault(str(dest), threading.Lock())
        with lock:  # the same repo submitted twice must not be checked out by two threads at once
            return _stage1(s, dest)

    def _stage1(s, dest):
        res = {"submission": s.to_dict(), "status": "ready", "github": {}, "snapshot": {}, "errors": list(s.notes),
               "repo_dir": str(dest)}
        if not s.clone_url:
            res["status"] = "no_repo"
            return res
        if s.owner and gh.enabled:
            res["github"]["meta"] = gh.repo_meta(s.owner, s.repo)
        ok, msg = clone_or_update(s.clone_url, dest)
        if not ok:
            _failed(res, "unreachable", msg)
            return res
        push_sha = ""
        if s.owner and gh.enabled and not gh.disabled_reason:
            act = gh.activity(s.owner, s.repo)
            if act.get("items"):
                branch = default_branch(dest).replace("origin/", "", 1)
                res["github"]["activity"] = summarize_activity(act["items"], branch, ev["start_dt"], ev["deadline_dt"])
                push_sha = res["github"]["activity"]["last_push_before_deadline_sha"]
        preferred = branch_from_tree_path(dest, s.tree_path) if s.tree_path else ""
        if preferred == default_branch(dest):
            preferred = ""
        if preferred:
            push_sha = ""  # the push log lookup was for the default branch
        snap = snapshot(dest, ev["deadline_dt"].isoformat(), push_sha, preferred,
                        out_dir=workdir / "snapshots" / dest.name)
        res["snapshot"] = snap
        if not snap.get("ok"):
            _failed(res, "empty", snap.get("error", "snapshot failed"))
            return res
        if preferred:
            snap["notes"].append(f"Reviewed branch '{preferred[7:]}' because the submitted link points to it")
        if msg.startswith("stale:"):
            snap["notes"].append("Could not update the repository; " + msg[len("stale: "):])
        res["github"]["unmerged_branches"] = other_branches(dest, snap["branch"])
        return res

    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(stage1, s): s for s in subs}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                results[s.slug] = fut.result()
            except Exception as e:  # noqa: BLE001
                results[s.slug] = {"submission": s.to_dict(), "status": "error", "github": {}, "snapshot": {},
                                   "errors": [f"{type(e).__name__}: {e}"], "repo_dir": str(repo_dir(s))}
            r = results[s.slug]
            log(f"  {s.project}: {r['status']}" + (f" @ {r['snapshot'].get('short')}" if r["snapshot"].get("ok") else "")
                + (f" ({r['errors'][-1]})" if r["status"] != "ready" and r["errors"] else ""))

    # challenge from template name when the export did not say
    for r in results.values():
        sub = r["submission"]
        if not sub["challenge"]:
            tpl = (r["github"].get("meta") or {}).get("template", "")
            sub["challenge"] = match_challenge(tpl, cfg["challenges"]) or match_challenge(sub["repo"], cfg["challenges"])

    # 2. templates and briefs ----------------------------------------------------
    templates = _load_templates(cfg, results, workdir)

    # 3. inventories and cross-team checks ---------------------------------------
    inventories = {}
    for slug, r in results.items():
        if r["status"] != "ready":
            continue
        try:
            snap = r["snapshot"]
            inv = F.Inventory(Path(snap["dir"]), sc.get("extra_ignore_dirs"))
            inv.add_listed(snap.get("large_files"), snap.get("symlinks"))
            inv.submodules = snap.get("submodules") or []
            F.apply_template_status(inv, templates.get(r["submission"]["challenge"], {}).get("hashes", {}))
            F.apply_generator_status(inv)
            inventories[slug] = inv
        except Exception as e:  # noqa: BLE001  (one strange repo must not stop the run)
            _failed(r, "error", f"Reading files failed: {type(e).__name__}: {e}")
    try:
        boilerplate, similar = cross_repo(
            inventories, sc["boilerplate_min_repos"], sc["similarity_min_shared_lines"], sc["similarity_min_ratio"],
            template_lines={slug: templates.get(results[slug]["submission"]["challenge"], {}).get("line_hashes", set())
                            for slug in inventories},
            repo_ids={slug: results[slug]["repo_dir"] for slug in inventories})
    except Exception as e:  # noqa: BLE001
        log(f"Cross-team check failed, continuing without it: {type(e).__name__}: {e}")
        boilerplate, similar = set(), {}
    for inv in inventories.values():
        F.apply_boilerplate_status(inv, boilerplate)
    names = {slug: r["submission"]["project"] for slug, r in results.items()}

    # 4. facts per repo -----------------------------------------------------------
    secrets_raw = {}
    for slug in list(inventories):
        inv, r = inventories[slug], results[slug]
        sub = r["submission"]
        chal = cfg["challenges"].get(sub["challenge"]) or {}
        tmpl = templates.get(sub["challenge"], {})
        try:
            dest = Path(r["repo_dir"])
            commits = commit_log(dest, r["snapshot"]["sha"])
            facts, raw = F.gather(inv, tmpl, commits, history_patch(dest), cfg, chal, sub.get("demo", ""))
            secrets_raw[slug] = raw
            sim = [dict(x, other=names.get(x["other"], x["other"])) for x in similar.get(slug, [])]
            facts["similar_to"] = sim
            r["facts"] = facts
            r["flags"] = F.build_flags(sub, r["snapshot"], r["github"], facts, sim,
                                       org=ev.get("github_org", ""), template=tmpl.get("url", ""))
        except Exception as e:  # noqa: BLE001
            _failed(r, "error", f"Checks failed: {type(e).__name__}: {e}")
            inventories.pop(slug, None)
    for slug, r in results.items():
        if r["status"] in ("no_repo", "unreachable", "empty", "error"):
            r["facts"] = r.get("facts") or {}
            title = {"no_repo": "No repository link in the submission",
                     "unreachable": "Repository could not be cloned",
                     "empty": "Repository is empty or has no usable commit",
                     "error": "Scanner error: review this team by hand"}[r["status"]]
            r["flags"] = [{"code": r["status"], "severity": "high", "title": title,
                           "detail": "; ".join(r["errors"])[:300], "evidence": []}]

    # 5. AI review -----------------------------------------------------------------
    deep_set = {d.strip().lower() for d in (deep or "").split(",") if d.strip()}
    deep_set |= {d.lower() for d in (rv.get("deep_teams") or [])}
    backend = None if skip_review else make_backend(cfg)
    if backend is not None:
        problem = backend.problem()
        if problem:
            log(f"Review step skipped: {problem}")
            backend = None
    sandbox = workdir / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)

    def pack_for(slug, is_deep, budget=None):
        r, inv = results[slug], inventories[slug]
        sub = r["submission"]
        chal = cfg["challenges"].get(sub["challenge"]) or {}
        brief = templates.get(sub["challenge"], {}).get("brief", "")
        user, info = build_pack(inv, r["facts"], sub, chal, brief, r["flags"], secrets_raw.get(slug, set()),
                                int(budget or rv["budget_chars"]), int(rv["max_file_chars"]), include_content=not is_deep)
        file_lines = {f["path"]: f["lines"] for f in inv.files}
        seen = file_lines if is_deep else info["included_lines"]
        return chal, user, info, file_lines, seen

    def stage5(slug):
        r = results[slug]
        sub = r["submission"]
        is_deep = slug in deep_set or sub["project"].lower() in deep_set
        chal, user, info, file_lines, seen = pack_for(slug, is_deep)
        r["pack"] = {k: v for k, v in info.items() if k != "included_lines"}
        cwd = sandbox / slug  # per-worker directory to avoid file collisions
        cwd.mkdir(parents=True, exist_ok=True)
        if is_deep:
            cwd = workdir / "deep" / slug
            _sanitized_copy(Path(r["snapshot"]["dir"]), cwd)
        sys_text = system_prompt(cfg, chal, deep=is_deep)
        key = slug + (".deep" if is_deep else "")
        out = run_review(backend, cfg, key, sys_text, user, cwd, is_deep, file_lines, seen, force=force_review)
        if out["status"] == "error" and not is_deep and CONTEXT_ERROR.search(out.get("error", "")):
            log(f"  {sub['project']}: input too long for the model, retrying with half the code")
            chal, user, info, file_lines, seen = pack_for(slug, False, int(rv["budget_chars"]) // 2)
            r["pack"] = {k: v for k, v in info.items() if k != "included_lines"}
            out = run_review(backend, cfg, key, sys_text, user, cwd, False, file_lines, seen, force=force_review)
        return out

    def keep_previous(slug):
        """No model call: attach the stored review if the input is unchanged."""
        r = results[slug]
        chal, user, info, file_lines, seen = pack_for(slug, False)
        r["pack"] = {k: v for k, v in info.items() if k != "included_lines"}
        old = cached_review(cfg, slug, system_prompt(cfg, chal), user, False, file_lines, seen)
        r["review_status"] = "ok" if old else "skipped"
        if old:
            r["review"], r["review_meta"] = old["review"], old["meta"]

    ready = list(inventories)
    to_review = [slug for slug in ready if slug in review_set] if backend is not None else []
    for slug in ready:
        if slug not in to_review:
            try:
                keep_previous(slug)
            except Exception as e:  # noqa: BLE001
                results[slug]["review_status"] = "skipped"
                results[slug]["errors"].append(f"Could not prepare the review input: {type(e).__name__}: {e}")
    if to_review:
        n = workers or rv["workers"]
        log(f"Reviewing {len(to_review)} repo(s) with {cfg['review']['backend']} ({rv['model']}), {n} at a time")
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = {pool.submit(stage5, slug): slug for slug in to_review}
            for fut in as_completed(futs):
                slug = futs[fut]
                try:
                    out = fut.result()
                except Exception as e:  # noqa: BLE001
                    out = {"status": "error", "error": f"{type(e).__name__}: {e}"}
                results[slug]["review_status"] = out["status"]
                results[slug]["review"] = out.get("review")
                results[slug]["review_meta"] = out.get("meta", {})
                if out.get("error"):
                    results[slug]["errors"].append("Review: " + truncate(out["error"], 300))
                rv_ = out.get("review") or {}
                log(f"  {names[slug]}: review {out['status']}"
                    + (f", technical score {rv_.get('technical_score')}" if rv_ else "")
                    + (" (cached)" if out.get("cached") else ""))

    run_info = {
        "event": ev["name"], "started": started.isoformat(), "finished": utcnow().isoformat(),
        "deadline": ev["deadline_dt"].isoformat(), "event_start": ev["start_dt"].isoformat(),
        "backend": "skipped" if backend is None else rv["backend"], "model": rv["model"],
        "github_api": gh.disabled_reason or ("on" if gh.enabled else "off"),
        "push_log_checked": "{} of {} GitHub repos".format(
            sum(1 for r in results.values() if r["github"].get("activity")),
            sum(1 for r in results.values() if r["submission"].get("owner"))),
        "templates": {k: {"url": v["url"], "brief_source": v["brief_source"], "files": len(v["hashes"])}
                      for k, v in templates.items()},
        "submissions_file": str(submissions_path),
        "slugs": [s.slug for s in subs],
    }
    for slug, r in results.items():
        write_json(workdir / "results" / f"{slug}.json", r)
    write_json(workdir / "results" / "_run.json", run_info)
    return {"run": run_info, "results": results}
