"""Feedback for teams after the event: one private Alkemio message per team, only after a person approved it.

  python -m techscan feedback -c config/onegov3.yaml          write drafts to <output>/team-feedback.yaml
      read and edit every message, set `send: true` on the ones that may go out
  python -m techscan feedback -c config/onegov3.yaml --send   send the approved ones

A message holds the review's strengths and points to improve, nothing else: no scores, red flags, automated
flags or jury questions. Those are leads for the jury, not verdicts. It goes to everyone who sent the team's
Alkemio form, as a 1:1 chat message (or e-mail with --channel email) from the logged-in account.

Alkemio allows 2048 characters per message and 20 recipients an hour per sender. --send stops at that limit
and records what it sent in the file, so running it again an hour later carries on without sending twice.

Optional config:
  feedback:
    channel: chat            # or email
    max_points: 3            # strengths and points to improve, each
    intro: "..."             # {team}, {challenge}, {event}, {repo} are filled in
    closing: "..."
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

from .alkemio import MAX_MESSAGE, SEND_LIMIT, AlkemioClient, AlkemioError, LimitReached, form_submitters, send_message
from .branding import brand, dedash
from .intake import normalize_repo
from .report import load_results
from .util import log

TEXT = {
    "nl": {
        "intro": ("Hoi {team},\n\nBedankt voor jullie inzending voor de challenge {challenge} tijdens {event}. "
                  "Hieronder technische feedback op jullie repository ({repo}). De eerste versie is gemaakt door "
                  "TechScan, een AI-review van de code; de organisatie heeft hem nagelezen voordat we hem stuurden."),
        "strengths": "Wat sterk is:",
        "improve": "Wat beter kan:",
        "closing": ("Deel dit gerust met de rest van je team. Vragen, of ben je het ergens niet mee eens? "
                    "Reageer op dit bericht.\n\nNamens de organisatie van {event}"),
    },
    "en": {
        "intro": ("Hi {team},\n\nThank you for your submission to the {challenge} challenge at {event}. Below is "
                  "technical feedback on your repository ({repo}). The first version was written by TechScan, an AI "
                  "review of the code; the organisers read it before sending it."),
        "strengths": "What is strong:",
        "improve": "What could be better:",
        "closing": ("Feel free to share this with the rest of your team. Questions, or do you disagree with "
                    "something? Reply to this message.\n\nOn behalf of the {event} organisers"),
    },
}

HEADER = """\
# TechScan feedback for teams, {event}. Written {written}.
#
# Read and edit every message before it goes out: it is AI-written. Check especially points that sound like an
# accusation (copied work, work done before the start, AI-generated code): those are leads for the jury, not
# feedback for a team. At most {max_chars} characters per message (Alkemio's limit).
# Set `send: true` on each message that may go out, then run:
#   python -m techscan feedback -c {config} --send
# Teams with `recipients: 0` did not send the Alkemio form: send their text yourself (for example on their PR).
# This file is rewritten when you draft or send again; your edits to `message` and `send` are kept, comments
# you add are not.
"""


class _Literal(str):
    pass


class _Fill(dict):
    """Placeholders for intro and closing; an unknown {name} stays as written instead of failing."""

    def __missing__(self, key):
        return "{" + key + "}"


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(_Literal, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"))


def _opts(cfg: dict) -> dict:
    fb = cfg.get("feedback") or {}
    lang = cfg["review"].get("language", "en")
    text = dict(TEXT.get(lang, TEXT["en"]))
    text.update({k: fb[k] for k in ("intro", "closing") if fb.get(k)})
    return {"text": text, "max_points": int(fb.get("max_points") or 3), "channel": fb.get("channel") or "chat",
            "dedash": bool(brand(cfg).get("no_em_dashes"))}


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", str(text or "")).strip()


def draft_message(r: dict, cfg: dict, opts: dict = None) -> str:
    """The message for one team, within Alkemio's length limit."""
    opts = opts or _opts(cfg)
    sub, review = r["submission"], r.get("review") or {}
    chal = (cfg["challenges"].get(sub.get("challenge") or "") or {}).get("name") or sub.get("challenge_raw") or ""
    fill = _Fill(team=sub.get("project", ""), challenge=chal, event=cfg["event"]["name"], repo=sub.get("repo_url", ""))
    t = opts["text"]
    strengths = [_clean(x) for x in review.get("strengths") or [] if _clean(x)][:opts["max_points"]]
    improve = [_clean(x) for x in review.get("weaknesses") or [] if _clean(x)][:opts["max_points"]]

    def render():
        parts = [t["intro"].format_map(fill)]
        if strengths:
            parts.append(t["strengths"] + "\n" + "\n".join(f"- {x}" for x in strengths))
        if improve:
            parts.append(t["improve"] + "\n" + "\n".join(f"- {x}" for x in improve))
        parts.append(t["closing"].format_map(fill))
        text = "\n\n".join(parts)
        return dedash(text) if opts["dedash"] else text

    text = render()
    while len(text) > MAX_MESSAGE and (len(strengths) > 1 or len(improve) > 1):
        (strengths if len(strengths) > len(improve) else improve).pop()
        text = render()
    while len(text) > MAX_MESSAGE:  # very long single points: shorten the longest
        lst = max((strengths, improve), key=lambda xs: max((len(x) for x in xs), default=0))
        i = max(range(len(lst)), key=lambda j: len(lst[j]))
        lst[i] = lst[i][: max(40, len(lst[i]) - (len(text) - MAX_MESSAGE) - 3)].rstrip() + "..."
        text = render()
    return text


# ------------------------------------------------------------------ who gets which message

def _name(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _repo_key(url: str) -> str:
    parsed = normalize_repo(url or "")
    return f"{parsed[2]}/{parsed[3]}".lower() if parsed and parsed[2] else ""


def _pr_key(url: str) -> str:
    m = re.search(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)", url or "", re.I)
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)}".lower() if m else ""


def recipients_for(r: dict, submitters: list, templates: set) -> list:
    """Alkemio user IDs of everyone who sent this team's form: same team name in the same challenge, or the
    same repository (a link to a challenge's starter repo says nothing about the team, so it does not count)."""
    sub = r["submission"]
    name = _name(sub.get("project"))
    repo = f"{sub.get('owner', '')}/{sub.get('repo', '')}".lower() if sub.get("owner") else _repo_key(sub.get("repo_url"))
    pr = _pr_key(sub.get("pr_url") or "")
    ids = []
    for s in submitters:
        same_name = name and _name(s["team"]) == name and s["challenge"] == sub.get("challenge")
        s_repo, s_pr = _repo_key(s["repo"]), _pr_key(s["repo"])
        same_repo = (repo and s_repo == repo and repo not in templates) or (pr and s_pr == pr)
        if (same_name or same_repo) and s["user_id"] not in ids:
            ids.append(s["user_id"])
    return ids


def _templates(cfg: dict) -> set:
    return {_repo_key(ch.get("template", "")) for ch in cfg["challenges"].values() if ch.get("template")} - {""}


def _alkemio_source(cfg: dict) -> dict:
    return next((s for s in cfg.get("sources") or [] if s.get("type") == "alkemio"), None)


# ------------------------------------------------------------------ the drafts file

def feedback_path(cfg: dict, path=None) -> Path:
    return Path(path) if path else Path(cfg["paths"]["output"]) / "team-feedback.yaml"


def load_file(path: Path) -> list:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("teams") or []


def save_file(path: Path, entries: list, cfg: dict) -> None:
    out = []
    for e in entries:
        e = dict(e)
        e["message"] = _Literal(_clean(e.get("message", "")))
        out.append(e)
    header = HEADER.format(event=cfg["event"]["name"], written=datetime.now().strftime("%d %b %Y %H:%M"),
                           max_chars=MAX_MESSAGE, config=cfg.get("_config_path", "<config>"))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump({"teams": out}, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=110)
    path.write_text(header + "\n" + body, encoding="utf-8")


def draft(cfg: dict, path=None, submitters=None) -> Path:
    """Write or update the drafts file. Existing messages and send choices are kept; recipients are refreshed
    for messages not sent yet. submitters: for tests; normally read from the Alkemio form."""
    path = feedback_path(cfg, path)
    _, results = load_results(Path(cfg["paths"]["workdir"]))
    if not results:
        raise SystemExit("No scan results yet: run a scan first.")
    if submitters is None:
        src = _alkemio_source(cfg)
        submitters = []
        if not src:
            log("WARN No alkemio source in the config: drafts get no recipients")
        else:
            try:
                who, submitters = form_submitters(cfg, src)
                log(f"Alkemio: logged in as {who}; {len(submitters)} form response(s)")
            except AlkemioError as e:
                log(f"WARN Alkemio: {e}. Drafts get no recipients; draft again once logged in.")
    opts, templates = _opts(cfg), _templates(cfg)
    entries = load_file(path)
    by_slug = {e.get("slug"): e for e in entries}
    added = skipped = 0
    for slug, r in sorted(results.items(), key=lambda kv: (kv[1]["submission"].get("challenge") or "",
                                                           (kv[1]["submission"].get("project") or "").lower())):
        if r.get("review_status") != "ok" or not r.get("review"):
            skipped += 1
            continue
        sub = r["submission"]
        ids = recipients_for(r, submitters, templates)
        fresh = {"slug": slug, "team": sub.get("project", ""),
                 "challenge": (cfg["challenges"].get(sub.get("challenge") or "") or {}).get("name", sub.get("challenge")),
                 "repo": sub.get("repo_url", ""), "recipients": len(ids), "recipient_ids": ids}
        cur = by_slug.get(slug)
        if cur is None:
            entries.append(fresh | {"send": False, "message": draft_message(r, cfg, opts)})
            added += 1
        elif not cur.get("sent"):
            cur.update(fresh)  # keep the edited message and the send choice
    save_file(path, entries, cfg)
    log(f"{added} new draft(s), {len(entries)} in total -> {path}"
        + (f" ({skipped} team(s) without a finished review left out)" if skipped else ""))
    no_one = [e["team"] for e in entries if not e.get("recipients") and not e.get("sent")]
    if no_one:
        log(f"No Alkemio form found for: {', '.join(no_one)} (send their text yourself)")
    log("Read and edit every message, set send: true on the ones that may go out, then run with --send")
    return path


# ------------------------------------------------------------------ sending

def send(cfg: dict, path=None, channel: str = "", yes: bool = False, client: AlkemioClient = None,
         confirm=None) -> int:
    """Send the approved, unsent messages. Returns 0 when everything approved went out."""
    path = feedback_path(cfg, path)
    entries = load_file(path)
    todo = [e for e in entries if e.get("send") is True and not e.get("sent")]
    if not todo:
        log(f"Nothing to send: no message in {path} has send: true and is still unsent.")
        return 0
    src = _alkemio_source(cfg) or {}
    client = client or AlkemioClient(src)
    channel = channel or _opts(cfg)["channel"]
    problems = []
    for e in todo:
        if not e.get("recipient_ids"):
            problems.append(f"{e.get('team')}: no recipients (no Alkemio form found)")
        if len(_clean(e.get("message", ""))) > MAX_MESSAGE:
            problems.append(f"{e.get('team')}: message is {len(_clean(e['message']))} characters, max {MAX_MESSAGE}")
        if not _clean(e.get("message", "")):
            problems.append(f"{e.get('team')}: empty message")
    if problems:
        for p in problems:
            log(f"FAIL {p}")
        log("Fix these in the file (or set send: false), then run again. Nothing was sent.")
        return 1
    if client.problem():
        log(f"FAIL {client.problem()}")
        return 1
    people = sum(len(e["recipient_ids"]) for e in todo)
    log(f"Ready to send {len(todo)} message(s) to {people} person(s) via Alkemio {channel}:")
    for e in todo:
        log(f"  {e['team']} ({e['challenge']}): {len(e['recipient_ids'])} recipient(s)")
    if people > SEND_LIMIT:
        log(f"Alkemio allows {SEND_LIMIT} recipients an hour: sending stops there; run --send again an hour later.")
    if not yes:
        ask = confirm or (lambda: sys.stdin.isatty() and input("Type 'send' to send them: ").strip().lower() == "send")
        if not ask():
            log("Not sent.")
            return 1
    sent_teams = 0
    for e in todo:
        try:
            status = send_message(client, e["recipient_ids"], _clean(e["message"]), channel)
        except LimitReached as err:
            log(f"STOP {err}")
            log(f"{sent_teams} team(s) sent; run --send again in an hour for the rest.")
            save_file(path, entries, cfg)
            return 1
        except AlkemioError as err:
            log(f"STOP {e['team']}: {err}")
            save_file(path, entries, cfg)
            return 1
        e["sent"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        e["channel"] = channel
        e["delivery"] = {u: s for u, s in status.items()}
        save_file(path, entries, cfg)  # after every team, so a stop never leads to sending twice
        sent_teams += 1
        blocked = [u for u, s in status.items() if s != "SENT"]
        log(f"  sent to {e['team']}" + (f" ({len(blocked)} recipient(s) not reached: "
                                         f"{', '.join(sorted(set(status[u] for u in blocked)))})" if blocked else ""))
    log(f"Done: {sent_teams} team(s). Recipients who were not reached are listed under 'delivery' in {path}: "
        "BLOCKED_NO_CONSENT means they switched these messages off, so send them the text another way.")
    return 0
