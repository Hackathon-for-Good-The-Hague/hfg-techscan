"""Git and GitHub operations: clone, freeze at the deadline, commit history, push log."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import log, parse_dt, run

GIT_ENV_EXTRA = {
    "GIT_TERMINAL_PROMPT": "0",   # never hang on a password prompt
    "GCM_INTERACTIVE": "never",   # Git Credential Manager (Windows): fail instead of popping a login
    "GIT_LFS_SKIP_SMUDGE": "1",   # do not download large LFS files
}


def git(args, cwd=None, timeout=600):
    env = os.environ.copy()
    env.update(GIT_ENV_EXTRA)
    cmd = ["git", "-c", "core.longpaths=true", "-c", "core.autocrlf=false", "-c", "core.quotepath=off",
           "-c", "core.symlinks=false", *args]
    return run(cmd, cwd=cwd, timeout=timeout, env=env)


def git_available() -> str:
    code, out, err = run(["git", "--version"], timeout=30)
    return out.strip() if code == 0 else ""


def classify_clone_error(err: str) -> str:
    e = err.lower()
    if "not found" in e or "authentication failed" in e or "could not read username" in e or "403" in e:
        return "Repository not reachable: private, deleted or wrong URL"
    if "timed out" in e:
        return "Clone timed out"
    return "Clone failed: " + err.strip().splitlines()[-1][:200] if err.strip() else "Clone failed"


def clone_or_update(clone_url: str, dest: Path, timeout=900):
    """Full clone without checkout (history is needed for the timeline; files come at snapshot time).

    Returns (ok, message). message starts with 'stale:' when the update failed but an older copy is used.
    """
    import time as _time
    dest = Path(dest)
    last = ""
    for attempt in range(3):
        if attempt:
            _time.sleep(3 * attempt)
        if (dest / ".git").exists():
            git(["remote", "set-url", "origin", clone_url], cwd=dest)
            code, out, err = git(["fetch", "--prune", "--quiet", "--force", "origin",
                                  "+refs/heads/*:refs/remotes/origin/*"], cwd=dest, timeout=timeout)
            if code == 0:
                git(["remote", "set-head", "origin", "--auto"], cwd=dest, timeout=120)
                return True, "updated"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            code, out, err = git(["clone", "--quiet", "--no-tags", "--no-checkout", clone_url, str(dest)],
                                 timeout=timeout)
            if code == 0:
                return True, "cloned"
            if (dest / ".git").exists() and not default_branch(dest):
                from .util import rmtree
                rmtree(dest)  # half-finished clone: start clean next attempt
        last = classify_clone_error(err)
        if last.startswith("Repository not reachable"):
            break  # retrying will not help
    if (dest / ".git").exists() and default_branch(dest):
        return True, f"stale: could not update ({last}); using the copy downloaded earlier"
    return False, last


def default_branch(dest: Path) -> str:
    code, out, _ = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=dest)
    if code == 0 and out.strip():
        return out.strip()
    for cand in ("origin/main", "origin/master"):
        c, _, _ = git(["rev-parse", "--verify", "--quiet", cand], cwd=dest)
        if c == 0:
            return cand
    code, out, _ = git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], cwd=dest)
    for line in out.splitlines():
        if line.strip() and not line.strip().endswith("/HEAD"):
            return line.strip()
    return ""


def commit_exists(dest: Path, sha: str) -> bool:
    if not sha or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return False
    code, _, _ = git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=dest)
    return code == 0


def branch_from_tree_path(dest: Path, tree_path: str) -> str:
    """'main/src/app' from a /tree/ URL -> 'origin/main' if that branch exists (longest match wins)."""
    parts = [p for p in (tree_path or "").split("/") if p]
    for n in range(len(parts), 0, -1):
        cand = "origin/" + "/".join(parts[:n])
        code, _, _ = git(["rev-parse", "--verify", "--quiet", cand], cwd=dest)
        if code == 0:
            return cand
    return ""


WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
WIN_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def bad_on_windows(component: str) -> bool:
    if not component or WIN_BAD_CHARS.search(component) or component[-1] in ". ":
        return True
    return component.split(".", 1)[0].lower() in WIN_RESERVED


def extract_tree(repo: Path, sha: str, out_dir: Path, max_blob: int = 5_000_000) -> dict:
    """Write the files of one commit into out_dir straight from git's object store.

    No checkout, so: no index (Windows-invalid names cannot break it), no hooks, no filters, no
    symlinks followed. Files Windows cannot name, symlinks and submodules are listed, not written.
    """
    import subprocess
    import threading
    from .util import rmtree
    rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    code, out, err = git(["ls-tree", "-r", "-z", "-l", "--full-tree", sha], cwd=repo, timeout=300)
    if code != 0:
        return {"ok": False, "error": "Could not list files: " + err.strip()[:200]}
    blobs, skipped, large, submodules, symlinks = [], [], [], [], []
    for rec in out.split("\x00"):
        if not rec or "\t" not in rec:
            continue
        meta, path = rec.split("\t", 1)
        parts = meta.split()
        if len(parts) < 4:
            continue
        mode, typ, obj, size = parts[:4]
        if typ == "commit":
            submodules.append(path)
            continue
        if typ != "blob":
            continue
        if mode == "120000":
            symlinks.append(path)
            continue
        comps = path.split("/")
        if any(bad_on_windows(c) or c in (".", "..") or c.lower() == ".git" for c in comps):
            skipped.append(path)
            continue
        n = int(size) if size.isdigit() else 0
        if n > max_blob:
            large.append({"path": path, "size": n})
            continue
        blobs.append((obj, path))

    env = os.environ.copy()
    env.update(GIT_ENV_EXTRA)
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=str(repo), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)

    def feed():
        try:
            for obj, _ in blobs:
                proc.stdin.write((obj + "\n").encode("ascii"))
            proc.stdin.close()
        except OSError:
            pass
    threading.Thread(target=feed, daemon=True).start()
    root = os.path.abspath(out_dir)
    written = 0
    for obj, path in blobs:
        header = proc.stdout.readline()
        if not header:
            skipped.append(path)
            break  # process died
        parts = header.split()
        if len(parts) >= 3 and parts[1] == b"blob":
            data = proc.stdout.read(int(parts[2]))
            proc.stdout.read(1)  # newline after each object
        else:
            # "<sha> missing\n" or unexpected type: no body to consume
            skipped.append(path)
            continue
        target = os.path.abspath(os.path.join(root, *path.split("/")))
        if os.path.commonpath([root, target]) != root:
            skipped.append(path)
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
            written += 1
        except OSError:  # e.g. path too long on Windows
            skipped.append(path)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    return {"ok": True, "written": written, "skipped": skipped, "large": large, "submodules": submodules,
            "symlinks": symlinks}


def snapshot(dest: Path, deadline_iso: str, push_sha: str = "", preferred: str = "", out_dir: Path = None) -> dict:
    """Freeze the last commit on the default (or linked) branch at the deadline into out_dir."""
    branch = preferred or default_branch(dest)
    if not branch:
        return {"ok": False, "error": "Repository has no commits"}
    _, head, _ = git(["rev-parse", branch], cwd=dest)
    head = head.strip()
    method = "commit date"
    _, sha, _ = git(["rev-list", "-1", f"--before={deadline_iso}", branch], cwd=dest)
    sha = sha.strip()
    if push_sha and commit_exists(dest, push_sha):
        sha, method = push_sha, "GitHub push log"
    notes = []
    if not sha:
        sha = head
        notes.append("Every commit is dated after the deadline; reviewed the latest commit instead")
    _, cnt, _ = git(["rev-list", "--count", f"{sha}..{branch}"], cwd=dest)
    post = int(cnt.strip() or 0)
    out_dir = Path(out_dir) if out_dir else Path(str(dest) + "__snapshot")
    ex = extract_tree(dest, sha, out_dir)
    if not ex.get("ok"):
        return {"ok": False, "error": ex.get("error", "could not read the files")}
    if ex["skipped"]:
        notes.append(f"Skipped {len(ex['skipped'])} file(s) with names Windows cannot use or paths too long: "
                     + ", ".join(ex["skipped"][:5]))
    _, cdate, _ = git(["log", "-1", "--format=%cI", sha], cwd=dest)
    return {
        "ok": True,
        "sha": sha,
        "short": sha[:8],
        "branch": branch.replace("origin/", "", 1),
        "head": head,
        "commit_date": cdate.strip(),
        "commits_after_deadline": post,
        "method": method,
        "notes": notes,
        "dir": str(out_dir),
        "skipped_paths": ex["skipped"][:50],
        "large_files": ex["large"][:50],
        "symlinks": ex["symlinks"][:50],
        "submodules": ex["submodules"][:50],
    }


def other_branches(dest: Path, branch: str) -> list:
    """Branches with commits that never made it into the reviewed branch."""
    code, out, _ = git(["for-each-ref", "--format=%(refname:short)|%(committerdate:iso-strict)",
                        "refs/remotes/origin"], cwd=dest)
    res = []
    for line in out.splitlines():
        name, _, date = line.partition("|")
        short = name.replace("origin/", "", 1)
        if not name or name.endswith("/HEAD") or short == branch:
            continue
        _, cnt, _ = git(["rev-list", "--count", f"origin/{branch}..{name}"], cwd=dest)
        n = int(cnt.strip() or 0)
        if n:
            res.append({"branch": short, "unmerged_commits": n, "last_commit": date})
    return res


def commit_log(dest: Path, sha: str) -> list:
    """All commits reachable from sha, oldest first, with per-file line counts."""
    fmt = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s"
    code, out, err = git(["log", "--no-renames", f"--format={fmt}", "--numstat", sha], cwd=dest, timeout=600)
    if code != 0:
        return []
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        head, _, rest = rec.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 7:
            continue
        files = []
        for line in rest.splitlines():
            bits = line.split("\t")
            if len(bits) != 3:
                continue
            a, d, p = bits
            files.append({"path": p, "added": int(a) if a.isdigit() else None,
                          "deleted": int(d) if d.isdigit() else None})
        commits.append({
            "sha": parts[0], "parents": [x for x in parts[1].split() if x],
            "author": parts[2], "email": parts[3].lower(),
            "author_date": parts[4], "commit_date": parts[5], "subject": parts[6][:120],
            "files": files,
        })
    commits.reverse()
    return commits


def history_patch(dest: Path, max_bytes=25_000_000) -> str:
    """Added lines across all history (for secrets that were committed and later removed)."""
    excludes = [f":(exclude,glob)**/{pat}" for pat in (
        "*.csv", "*.tsv", "*.geojson", "*.jsonl", "*.ndjson", "*.svg", "*.min.js", "*.map", "package-lock.json",
        "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "*.parquet", "*.xml",
        "node_modules/**", ".venv/**", "venv/**", "site-packages/**", "vendor/**", "bower_components/**")]
    code, out, _ = git(["log", "--all", "-p", "--no-color", "--no-renames", "--unified=0", "--no-textconv",
                        "--format=%x1eCOMMIT %H", "--", ".", *excludes], cwd=dest, timeout=600)
    if code != 0:
        return ""
    return out[:max_bytes]


# ---------------------------------------------------------------- GitHub API

class GitHub:
    def __init__(self, enabled=True, cache_dir=None):
        self.enabled = enabled
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        self.disabled_reason = ""
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.net_failures = 0

    def get(self, url: str, timeout=20):
        data, err, _ = self.get_page(url, timeout)
        return data, err

    def get_page(self, url: str, timeout=20):
        """Returns (data, error, next_page_url)."""
        if not self.enabled or self.disabled_reason:
            return None, self.disabled_reason or "disabled", ""
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hfg-techscan",
        })
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
                m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", "") or "")
                self.net_failures = 0
                return json.loads(body), "", (m.group(1) if m else "")
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code}"
            try:
                detail = json.loads(e.read().decode("utf-8", errors="replace")).get("message", "")
                msg += f": {detail[:160]}"
            except (ValueError, AttributeError):
                pass
            if e.code in (401,) or "rate limit" in msg.lower():
                self.disabled_reason = msg + " (GitHub API checks skipped from here on)"
                log("GitHub API: " + self.disabled_reason)
            return None, msg, ""
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            self.net_failures += 1
            if self.net_failures >= 3:  # one hiccup should not switch the push-log checks off for everyone
                self.disabled_reason = f"GitHub API unreachable: {e}"
                log(self.disabled_reason + " (continuing without it)")
            return None, f"GitHub API error: {e}", ""

    def repo_meta(self, owner: str, repo: str) -> dict:
        cache = self.cache_dir / f"meta-{owner}-{repo}.json".lower() if self.cache_dir else None
        if cache and cache.exists() and (time.time() - cache.stat().st_mtime) < 12 * 3600:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                pass
        data, err = self.get(f"https://api.github.com/repos/{owner}/{repo}")
        if not data:
            return {"error": err}
        tpl = data.get("template_repository") or {}
        parent = data.get("parent") or {}
        meta = {
            "private": data.get("private"),
            "fork": data.get("fork"),
            "parent": parent.get("full_name", ""),
            "template": tpl.get("full_name", ""),
            "template_clone_url": tpl.get("clone_url", ""),
            "created_at": data.get("created_at", ""),
            "pushed_at": data.get("pushed_at", ""),
            "default_branch": data.get("default_branch", ""),
            "description": data.get("description") or "",
        }
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def activity(self, owner: str, repo: str, max_pages=5) -> dict:
        items, url = [], f"https://api.github.com/repos/{owner}/{repo}/activity?per_page=100&direction=desc"
        for _ in range(max_pages):
            data, err, nxt = self.get_page(url)
            if data is None:
                return {"error": err, "items": items}
            if not isinstance(data, list) or not data:
                break
            items.extend(data)
            if not nxt:
                break
            url = nxt
        return {"error": "", "items": items}

    def org_repos(self, org: str) -> list:
        out, url = [], f"https://api.github.com/orgs/{org}/repos?type=all&per_page=100&sort=created&direction=desc"
        for _ in range(10):
            data, err, nxt = self.get_page(url)
            if data is None:
                if not out:
                    raise RuntimeError(f"Could not list repos for {org}: {err}")
                break
            out.extend(data)
            if not nxt:
                break
            url = nxt
        return out


def summarize_activity(items: list, branch: str, start, deadline) -> dict:
    """Pushes, force pushes and the last pre-deadline push to the reviewed branch."""
    pushes, force, after_deadline, before_start = [], [], [], []
    last_before_deadline = None
    ref = f"refs/heads/{branch}"
    for it in items:
        kind = it.get("activity_type", "")
        if kind not in ("push", "force_push", "pr_merge", "merge_queue_merge", "branch_creation"):
            continue
        try:
            ts = parse_dt(it.get("timestamp", ""))
        except ValueError:
            continue
        rec = {"time": ts.isoformat(), "type": kind, "ref": it.get("ref", ""),
               "actor": (it.get("actor") or {}).get("login", ""), "after": it.get("after", "")}
        pushes.append(rec)
        if kind == "force_push":
            force.append(rec)
        if ts > deadline:
            after_deadline.append(rec)
        if ts < start:
            before_start.append(rec)
        if it.get("ref") == ref and ts <= deadline:
            if last_before_deadline is None or ts > parse_dt(last_before_deadline["time"]):
                last_before_deadline = rec
    pushes.sort(key=lambda r: r["time"])
    return {
        "pushes": len(pushes),
        "first_push": pushes[0]["time"] if pushes else "",
        "last_push": pushes[-1]["time"] if pushes else "",
        "force_pushes": force[:10],
        "pushes_after_deadline": after_deadline[:10],
        "pushes_before_start": before_start[:10],
        "last_push_before_deadline_sha": (last_before_deadline or {}).get("after", ""),
    }


def blob_url(repo_url: str, sha: str, path: str, lines: str = "") -> str:
    if not repo_url.startswith("https://github.com/") or not sha or not path:
        return ""
    url = f"{repo_url}/blob/{sha}/{urllib.parse.quote(path)}"
    if lines and not path.lower().endswith(".ipynb"):
        m = re.match(r"\s*(\d+)\s*(?:[-–]\s*(\d+))?", str(lines))
        if m:
            url += f"#L{m.group(1)}" + (f"-L{m.group(2)}" if m.group(2) else "")
    return url
