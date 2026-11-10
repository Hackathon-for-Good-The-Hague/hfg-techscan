"""Observatory: continuous repo monitoring during a hackathon event (Phase 3).

Polls GitHub repos via the API, records timeline events, and produces narrative
reports and cross-team analysis. Pure standard library, no LLM needed.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .util import log, parse_dt, slugify, utcnow, write_json

# ------------------------------------------------------------------ polling


class RepoPoller:
    """Polls a set of GitHub repos at a configurable interval and records timeline events."""

    def __init__(
        self,
        repos: list[dict],
        token: str = "",
        interval: int = 300,
        output_dir: Path | str = "observatory",
    ):
        """
        Parameters
        ----------
        repos : list[dict]
            Each dict must have ``slug``, ``owner``, ``repo``.
            Optional: ``branch`` (default branch is auto-detected).
        token : str
            GitHub personal access token (optional but recommended).
        interval : int
            Seconds between polling rounds (default 300 = 5 min).
        output_dir : Path
            Directory for timeline JSONL files (one per team).
        """
        self.repos = repos
        self.token = token
        self.interval = max(30, interval)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._last: dict[str, dict] = {}  # slug -> last recorded snapshot
        self._running = False

    # -- GitHub API helpers ---------------------------------------------------

    def _api_get(self, url: str, timeout: int = 20):
        """GET a GitHub API endpoint. Returns (data, error)."""
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hfg-techscan-observatory",
        })
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace")), ""
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code}"
            try:
                detail = json.loads(e.read().decode("utf-8", errors="replace")).get("message", "")
                msg += f": {detail[:160]}"
            except (ValueError, AttributeError):
                pass
            return None, msg
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return None, f"GitHub API error: {e}"

    def _fetch_repo_state(self, owner: str, repo: str, branch: str = "") -> dict:
        """Fetch current state of a repo via the GitHub API.

        Returns a dict with commit_count, latest_sha, last_commit_msg,
        languages dict, and contributor count. Missing fields are set to
        sensible defaults so callers never need to handle None.
        """
        state: dict = {
            "commits": 0,
            "last_commit_sha": "",
            "last_commit_msg": "",
            "languages": {},
            "contributors": 0,
        }

        # commits on the default (or specified) branch
        branch_q = f"&sha={branch}" if branch else ""
        data, err = self._api_get(
            f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=1{branch_q}"
        )
        if isinstance(data, list) and data:
            state["last_commit_sha"] = data[0].get("sha", "")
            state["last_commit_msg"] = (
                (data[0].get("commit") or {}).get("message") or ""
            ).split("\n", 1)[0][:120]

        # commit count: use the Contributors stats endpoint (returns total)
        # Faster: use the commits endpoint with per_page=1 and parse the Link
        # header for the last page number.
        data, err = self._api_get(
            f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=1{branch_q}"
        )
        # The above request is identical; re-parse Link header idea won't work
        # with urllib easily, so use the compare trick instead.
        # We already have the latest sha; count via rev-list is not API-based.
        # Simplest: list commits page by page is too slow. Use the repository
        # stats endpoint instead.
        stats, _ = self._api_get(
            f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=1&anon=true"
        )
        if isinstance(stats, list):
            state["contributors"] = max(len(stats), 1)
            # If there is a Link header with last page, that is the real count.
            # For simplicity, fetch up to 100 contributors.
            all_contribs, _ = self._api_get(
                f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=100&anon=true"
            )
            if isinstance(all_contribs, list):
                state["contributors"] = len(all_contribs)
                state["commits"] = sum(c.get("contributions", 0) for c in all_contribs)

        # languages
        langs, _ = self._api_get(f"https://api.github.com/repos/{owner}/{repo}/languages")
        if isinstance(langs, dict):
            state["languages"] = langs

        return state

    # -- timeline recording ---------------------------------------------------

    def _timeline_path(self, slug: str) -> Path:
        return self.output_dir / f"{slug}.jsonl"

    def _load_last(self, slug: str) -> dict:
        """Load the most recent event from the JSONL file for diffing."""
        path = self._timeline_path(slug)
        if not path.exists():
            return {}
        last = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        last = json.loads(line)
        except (OSError, ValueError):
            pass
        return last

    def _append_event(self, slug: str, event: dict) -> None:
        path = self._timeline_path(slug)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def _has_changed(self, slug: str, state: dict) -> bool:
        """Return True if the state differs from the last recorded event."""
        last = self._last.get(slug)
        if not last:
            return True
        return (
            state["last_commit_sha"] != last.get("last_commit_sha", "")
            or state["commits"] != last.get("commits", 0)
            or state["contributors"] != last.get("contributors", 0)
            or state["languages"] != last.get("languages", {})
        )

    # -- public interface -----------------------------------------------------

    def poll_once(self) -> list[dict]:
        """Run one polling round across all repos. Returns list of new events."""
        events = []
        for entry in self.repos:
            slug = entry["slug"]
            owner, repo = entry["owner"], entry["repo"]
            branch = entry.get("branch", "")

            # bootstrap last-known state from disk on first call
            if slug not in self._last:
                self._last[slug] = self._load_last(slug)

            state = self._fetch_repo_state(owner, repo, branch)
            if not self._has_changed(slug, state):
                continue

            event = {
                "ts": utcnow().isoformat(),
                "slug": slug,
                "commits": state["commits"],
                "languages": state["languages"],
                "contributors": state["contributors"],
                "last_commit_msg": state["last_commit_msg"],
                "last_commit_sha": state["last_commit_sha"],
            }
            self._append_event(slug, event)
            self._last[slug] = state
            events.append(event)
            log(f"Observatory: {slug} updated — {state['commits']} commits, "
                f"{state['contributors']} contributors")

        return events

    def run(self, duration: int = 0) -> None:
        """Poll in a loop. *duration* is max seconds (0 = run until stopped)."""
        self._running = True
        start = time.monotonic()
        log(f"Observatory: polling {len(self.repos)} repos every {self.interval}s")
        try:
            while self._running:
                try:
                    self.poll_once()
                except Exception as e:  # noqa: BLE001
                    log(f"Observatory: poll error: {e}")
                if duration and (time.monotonic() - start) >= duration:
                    break
                time.sleep(self.interval)
        except KeyboardInterrupt:
            log("Observatory: stopped by user")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False


# ------------------------------------------------------------------ narrative


def _read_timeline(path: Path) -> list[dict]:
    """Read all events from a team's JSONL timeline."""
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return events


def _parse_event_ts(event: dict) -> datetime | None:
    try:
        return parse_dt(event["ts"])
    except (KeyError, ValueError):
        return None


def generate_narrative(timeline_dir: Path, scan_results: dict | None = None) -> dict[str, str]:
    """Generate a building-journey narrative per team from their timeline JSONL files.

    Parameters
    ----------
    timeline_dir : Path
        Directory containing ``<slug>.jsonl`` files written by RepoPoller.
    scan_results : dict, optional
        Final scan results keyed by slug (adds richer context to the narrative).

    Returns
    -------
    dict[str, str]
        Mapping of slug to narrative text (2-3 paragraphs).
    """
    narratives = {}
    timeline_dir = Path(timeline_dir)
    if not timeline_dir.is_dir():
        return narratives

    for jsonl in sorted(timeline_dir.glob("*.jsonl")):
        slug = jsonl.stem
        events = _read_timeline(jsonl)
        if not events:
            continue

        timestamps = [_parse_event_ts(e) for e in events]
        timestamps = [t for t in timestamps if t is not None]
        if not timestamps:
            continue

        first_ts, last_ts = min(timestamps), max(timestamps)
        span_hours = max((last_ts - first_ts).total_seconds() / 3600, 0.1)

        # commit velocity
        commit_counts = [e.get("commits", 0) for e in events]
        first_commits = commit_counts[0] if commit_counts else 0
        last_commits = commit_counts[-1] if commit_counts else 0
        total_new = last_commits - first_commits
        velocity = total_new / span_hours if span_hours > 0 else 0

        # language evolution
        first_langs = set(events[0].get("languages", {}).keys())
        last_langs = set(events[-1].get("languages", {}).keys())
        added_langs = last_langs - first_langs
        removed_langs = first_langs - last_langs

        # contributor growth
        first_contributors = events[0].get("contributors", 0)
        last_contributors = events[-1].get("contributors", 0)

        # sprint detection: find gaps > 2 hours between events, and bursts
        gaps = []
        bursts = []
        prev_ts = timestamps[0]
        burst_start = timestamps[0]
        burst_commits = first_commits
        for i, ts in enumerate(timestamps[1:], 1):
            gap_h = (ts - prev_ts).total_seconds() / 3600
            if gap_h > 2:
                gaps.append((prev_ts, ts, gap_h))
                if i > 1:
                    bc = events[i - 1].get("commits", 0) - burst_commits
                    if bc > 0:
                        bursts.append((burst_start, prev_ts, bc))
                burst_start = ts
                burst_commits = events[i].get("commits", 0)
            prev_ts = ts
        # final burst
        final_bc = last_commits - burst_commits
        if final_bc > 0:
            bursts.append((burst_start, timestamps[-1], final_bc))

        # -- compose narrative
        paras = []

        # paragraph 1: start and setup
        team_label = slug.replace("-", " ").title()
        scan_info = scan_results.get(slug, {}) if scan_results else {}
        project_name = (scan_info.get("submission") or {}).get("project", team_label)
        start_str = first_ts.strftime("%A %H:%M")
        p1 = (f"{project_name} started building at {start_str} UTC. "
               f"Their first recorded snapshot showed {first_commits} commit(s) "
               f"using {', '.join(sorted(first_langs)) or 'no detected languages'}.")
        if first_contributors:
            p1 += f" The team had {first_contributors} contributor(s) active from the start."
        paras.append(p1)

        # paragraph 2: velocity and pivots
        p2_parts = []
        p2_parts.append(
            f"Over the next {span_hours:.1f} hours they added {total_new} commit(s), "
            f"averaging {velocity:.1f} commits per hour."
        )
        if added_langs:
            p2_parts.append(
                f"Along the way they introduced {', '.join(sorted(added_langs))}, "
                f"suggesting a stack expansion or pivot."
            )
        if removed_langs:
            p2_parts.append(
                f"They moved away from {', '.join(sorted(removed_langs))}."
            )
        if last_contributors > first_contributors:
            p2_parts.append(
                f"The team grew from {first_contributors} to {last_contributors} contributors."
            )
        if bursts and len(bursts) > 1:
            p2_parts.append(
                f"Their work came in {len(bursts)} distinct sprints, "
                f"with the largest producing {max(b[2] for b in bursts)} commits."
            )
        paras.append(" ".join(p2_parts))

        # paragraph 3: final state
        final_langs = ", ".join(sorted(last_langs)) or "unknown"
        last_msg = events[-1].get("last_commit_msg", "")
        p3 = (f"At the final snapshot ({last_ts.strftime('%A %H:%M')} UTC), "
               f"the repo stood at {last_commits} commits across {final_langs} "
               f"with {last_contributors} contributor(s).")
        if last_msg:
            p3 += f' Their last commit message was: "{last_msg[:80]}".'
        paras.append(p3)

        narratives[slug] = "\n\n".join(paras)

    return narratives


# ------------------------------------------------------------------ cross-team analysis


def cross_team_analysis(scan_results: dict) -> dict[str, object]:
    """Analyse the full cohort of scan results and return insight sections.

    Parameters
    ----------
    scan_results : dict
        Mapping of slug to per-team result dicts (as produced by the pipeline).

    Returns
    -------
    dict
        Keys are section names, values are dicts/lists of insights.
    """
    if not scan_results:
        return {}

    # -- tech stacks
    lang_counter: Counter = Counter()
    framework_counter: Counter = Counter()
    for slug, r in scan_results.items():
        snap = r.get("snapshot") or {}
        facts = r.get("facts") or {}
        for lang, count in (facts.get("languages") or {}).items():
            lang_counter[lang] += 1
        for lib in (facts.get("notable_libs") or []):
            name = lib if isinstance(lib, str) else lib.get("name", "")
            if name:
                framework_counter[name] += 1

    # -- challenge data usage
    partner_usage: dict[str, list[str]] = defaultdict(list)
    for slug, r in scan_results.items():
        facts = r.get("facts") or {}
        for partner in (facts.get("partner_tech") or []):
            label = partner if isinstance(partner, str) else partner.get("label", str(partner))
            partner_usage[label].append(slug)

    # -- deployment detection
    deployed = []
    for slug, r in scan_results.items():
        facts = r.get("facts") or {}
        sub = r.get("submission") or {}
        demo_status = (facts.get("demo") or {}).get("status", "")
        demo_url = sub.get("demo", "")
        if demo_status == "ok" or demo_url:
            deployed.append({"slug": slug, "demo_url": demo_url})

    # -- contributor counts
    contributor_ranking = []
    for slug, r in scan_results.items():
        facts = r.get("facts") or {}
        tl = facts.get("timeline") or {}
        n = tl.get("contributors", 0)
        contributor_ranking.append({"slug": slug, "contributors": n})
    contributor_ranking.sort(key=lambda x: -x["contributors"])

    # -- code stats
    total_lines = []
    total_commits = []
    for slug, r in scan_results.items():
        facts = r.get("facts") or {}
        tl = facts.get("timeline") or {}
        total_lines.append(facts.get("team_code_lines", 0))
        total_commits.append(tl.get("commits", 0))

    n_teams = len(scan_results)
    avg_lines = sum(total_lines) / n_teams if n_teams else 0
    avg_commits = sum(total_commits) / n_teams if n_teams else 0

    # -- teams with tests
    teams_with_tests = []
    for slug, r in scan_results.items():
        facts = r.get("facts") or {}
        if facts.get("tests"):
            teams_with_tests.append(slug)

    return {
        "team_count": n_teams,
        "tech_stacks": {
            "languages": lang_counter.most_common(20),
            "frameworks_and_libs": framework_counter.most_common(30),
        },
        "challenge_data_usage": dict(partner_usage),
        "deployed_teams": deployed,
        "contributor_ranking": contributor_ranking[:20],
        "code_stats": {
            "average_lines": round(avg_lines),
            "average_commits": round(avg_commits, 1),
            "total_lines_all_teams": sum(total_lines),
            "total_commits_all_teams": sum(total_commits),
        },
        "teams_with_tests": teams_with_tests,
    }


# ------------------------------------------------------------------ challenge-owner report


def _esc(text) -> str:
    """HTML-escape a string."""
    import html as _html
    return _html.escape(str(text or ""))


def challenge_owner_report(
    scan_results: dict,
    challenge_key: str = "",
    partner_key: str = "",
    event_name: str = "Hackathon for Good",
) -> str:
    """Generate an HTML report for challenge partners.

    Focused on "which teams could we work with after this?" — highlights teams
    that used the challenge data, have deployed demos, test suites, and
    compatible tech stacks.

    Parameters
    ----------
    scan_results : dict
        Full scan results keyed by slug.
    challenge_key : str
        If set, only include teams assigned to this challenge.
    partner_key : str
        If set, highlight teams using this partner's technology.
    event_name : str
        Name of the event for the report header.

    Returns
    -------
    str
        Complete HTML document as a string.
    """
    # filter to relevant teams
    teams = {}
    for slug, r in scan_results.items():
        sub = r.get("submission") or {}
        if challenge_key and sub.get("challenge", "") != challenge_key:
            continue
        teams[slug] = r

    if not teams:
        return f"<html><body><h1>No teams found for challenge '{_esc(challenge_key)}'</h1></body></html>"

    analysis = cross_team_analysis(teams)

    # categorise teams
    rows = []
    for slug, r in sorted(teams.items()):
        sub = r.get("submission") or {}
        facts = r.get("facts") or {}
        review = r.get("review") or {}

        project = sub.get("project", slug)
        repo_url = sub.get("repo_url", "")

        # partner usage
        used_partner = False
        partners = facts.get("partners") or {}
        if partner_key and partner_key in partners:
            used_partner = True
        elif partner_key:
            for pk, pv in partners.items():
                if partner_key.lower() in (pv.get("label", "") or "").lower():
                    used_partner = True

        # demo
        demo_info = facts.get("demo") or {}
        demo_url = sub.get("demo", "")
        has_demo = demo_info.get("status") == "ok" or bool(demo_url)

        # tests
        has_tests = bool(facts.get("tests"))

        # languages
        langs = ", ".join(sorted((facts.get("languages") or {}).keys()))

        # contributors and commits from timeline
        tl = facts.get("timeline") or {}
        contributors = tl.get("contributors", 0)
        commits = tl.get("commits", 0)

        # code lines
        code_lines = facts.get("team_code_lines", 0)

        # notable libs
        deps = facts.get("dependencies") or {}
        notable = deps.get("notable") or {}
        libs = [lib for cat_libs in notable.values() for lib in cat_libs]

        rows.append({
            "slug": slug,
            "project": project,
            "repo_url": repo_url,
            "used_partner": used_partner,
            "has_demo": has_demo,
            "demo_url": demo_url,
            "has_tests": has_tests,
            "langs": langs,
            "contributors": contributors,
            "commits": commits,
            "code_lines": code_lines,
            "libs": libs,
        })

    # sort: partner users first, then by commits descending
    rows.sort(key=lambda r: (not r["used_partner"], -r["commits"]))

    # build HTML
    parts = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        f"<title>Challenge Partner Report — {_esc(event_name)}</title>",
        "<style>",
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; "
        "       max-width: 1100px; margin: 2em auto; padding: 0 1em; color: #1a1a1a; }",
        "h1 { color: #1962A1; } h2 { color: #333; border-bottom: 2px solid #FCCD02; padding-bottom: .3em; }",
        "table { border-collapse: collapse; width: 100%; margin: 1em 0; }",
        "th, td { border: 1px solid #ddd; padding: .5em .7em; text-align: left; font-size: .9em; }",
        "th { background: #1962A1; color: white; }",
        "tr:nth-child(even) { background: #f8f9fa; }",
        ".yes { color: #2E7D32; font-weight: bold; } .no { color: #999; }",
        ".tag { display: inline-block; background: #e8f0fe; padding: .1em .4em; border-radius: 3px; "
        "       font-size: .8em; margin: .1em; }",
        ".summary { background: #f0f4f8; padding: 1em; border-radius: 6px; margin: 1em 0; }",
        ".summary dt { font-weight: bold; } .summary dd { margin: 0 0 .5em 1em; }",
        "</style></head><body>",
    ]

    # header
    title = f"Challenge Partner Report"
    if challenge_key:
        title += f" — {_esc(challenge_key)}"
    parts.append(f"<h1>{title}</h1>")
    parts.append(f"<p>Event: {_esc(event_name)} | Generated: {utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>")

    # summary
    stats = analysis.get("code_stats", {})
    parts.append("<h2>Summary</h2>")
    parts.append("<dl class='summary'>")
    parts.append(f"<dt>Teams</dt><dd>{len(teams)}</dd>")
    if partner_key:
        n_using = sum(1 for r in rows if r["used_partner"])
        parts.append(f"<dt>Teams using {_esc(partner_key)} technology</dt><dd>{n_using}</dd>")
    n_demo = sum(1 for r in rows if r["has_demo"])
    parts.append(f"<dt>Teams with deployed demos</dt><dd>{n_demo}</dd>")
    n_tests = sum(1 for r in rows if r["has_tests"])
    parts.append(f"<dt>Teams with test suites</dt><dd>{n_tests}</dd>")
    parts.append(f"<dt>Average commits</dt><dd>{stats.get('average_commits', 0)}</dd>")
    parts.append(f"<dt>Average code lines</dt><dd>{stats.get('average_lines', 0)}</dd>")
    parts.append("</dl>")

    # team table
    parts.append("<h2>Team Details</h2>")
    parts.append("<table><thead><tr>")
    headers = ["Team", "Languages", "Contributors", "Commits", "Code Lines"]
    if partner_key:
        headers.insert(1, f"Uses {_esc(partner_key)}")
    headers += ["Demo", "Tests", "Key Libraries"]
    for h in headers:
        parts.append(f"<th>{h}</th>")
    parts.append("</tr></thead><tbody>")

    for row in rows:
        parts.append("<tr>")
        # team name + link
        name = _esc(row["project"])
        if row["repo_url"]:
            name = f"<a href='{_esc(row['repo_url'])}' target='_blank'>{name}</a>"
        parts.append(f"<td>{name}</td>")

        if partner_key:
            yn = "<span class='yes'>Yes</span>" if row["used_partner"] else "<span class='no'>No</span>"
            parts.append(f"<td>{yn}</td>")

        parts.append(f"<td>{_esc(row['langs'])}</td>")
        parts.append(f"<td>{row['contributors']}</td>")
        parts.append(f"<td>{row['commits']}</td>")
        parts.append(f"<td>{row['code_lines']:,}</td>")

        # demo
        if row["has_demo"] and row["demo_url"]:
            parts.append(f"<td><a href='{_esc(row['demo_url'])}' target='_blank' class='yes'>Live</a></td>")
        elif row["has_demo"]:
            parts.append("<td><span class='yes'>Yes</span></td>")
        else:
            parts.append("<td><span class='no'>No</span></td>")

        # tests
        yn = "<span class='yes'>Yes</span>" if row["has_tests"] else "<span class='no'>No</span>"
        parts.append(f"<td>{yn}</td>")

        # libs
        libs_html = " ".join(f"<span class='tag'>{_esc(l)}</span>" for l in row["libs"][:10])
        parts.append(f"<td>{libs_html}</td>")

        parts.append("</tr>")

    parts.append("</tbody></table>")

    # tech stack summary
    parts.append("<h2>Common Tech Stacks</h2>")
    tech = analysis.get("tech_stacks", {})
    if tech.get("languages"):
        parts.append("<h3>Languages</h3><ul>")
        for lang, count in tech["languages"]:
            parts.append(f"<li>{_esc(lang)}: {count} team(s)</li>")
        parts.append("</ul>")
    if tech.get("frameworks_and_libs"):
        parts.append("<h3>Frameworks and Libraries</h3><ul>")
        for lib, count in tech["frameworks_and_libs"][:15]:
            parts.append(f"<li>{_esc(lib)}: {count} team(s)</li>")
        parts.append("</ul>")

    parts.append("</body></html>")
    return "\n".join(parts)
