"""Alkemio (alkem.io) source: the submission form in each challenge subspace.

Alkemio has no API keys. TechScan uses the GraphQL API the website itself uses, logged in with the browser
session of a space admin (only admins can read every form response; anyone else sees only their own):

  1. Log in at https://alkem.io (any login method).
  2. F12 -> Application (Chrome/Edge) or Storage (Firefox) -> Cookies -> https://alkem.io -> alkemio_session
  3. Put its value in the ALKEMIO_SESSION environment variable. Never in a file in this repository: the
     cookie gives full access to your Alkemio account. Logging out ends it; it also expires after about
     14 days without use (30 days at most), so copy a fresh one on the morning of the event.

Config (under sources:):
  - type: alkemio
    space: onegov-hackathon     # the space's nameID, as in https://alkem.io/<space>
    callout: inzending          # nameID of the submission form in each challenge subspace (default)
    label: Alkemio
    # challenges: {subspace-nameID: challenge-key}   only when subspace names differ from the config's keys
    # questions: {repo: "Link naar de repository"}   only when the automatic question matching misses one

Answers are matched to TechScan columns by the wording of the question (team name, repository link, demo
link, one-sentence summary; problem, what really works and what is simulated, and assumptions go into the
description). The names of team members are only counted, never stored or sent to the model.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .intake import match_challenge
from .util import fmt_dt, log, parse_dt

API_URL = "https://alkem.io/api/private/graphql"
COOKIE = "alkemio_session"
SESSION_ENV = "ALKEMIO_SESSION"
PAGE = 50
MAX_DESCRIPTION = 6000

# question wording -> column; the first matching role wins, so the order matters
ROLES = [
    ("project", r"team\s*-?\s*naam|team\s*name|naam van (?:het|je|jullie) team|project\s*-?\s*naam|project\s*name"),
    ("members", r"teamleden|team\s*members|namen van|names of|wie zitten"),
    ("repo", r"repo|github|gitlab|broncode|source\s*code"),
    ("demo", r"demo"),
    ("punchline", r"in een zin|in één zin|one sentence|in one line|pitch\s*zin|tagline"),
    ("description", r"probleem|problem|voor wie|for whom|gesimuleerd|simulated|werkt echt|really works|aanname|"
                    r"assumption|beperking|limitation|aanpak|approach|toelichting|explanation"),
]
GITHUB_URL = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[^\s<>()\[\]\"',]+", re.I)
ANY_URL = re.compile(r"https?://[^\s<>()\[\]\"',]+", re.I)

STRUCTURE = """query TechScanAlkemioStructure($space: NameID!) {
  me { user { id profile { displayName } } }
  lookupByName { space(NAMEID: $space) { id nameID about { profile { displayName url } }
    subspaces { id nameID about { profile { displayName url } }
      collaboration { calloutsSet { callouts { id nameID framing { profile { url }
        form { id settings { state } questions { id prompt required } } } } } } } } }
}"""

RESPONSES = """query TechScanAlkemioResponses($form: UUID!, $first: Int, $after: UUID) {
  lookup { calloutFormResponses(formID: $form, first: $first, after: $after) {
    canReadAll
    all { total pageInfo { hasNextPage endCursor } responses { id createdDate answers { questionID prompt text } } }
  } }
}"""


class AlkemioError(Exception):
    pass


# ------------------------------------------------------------------ client

class AlkemioClient:
    def __init__(self, src: dict, timeout: int = 30):
        self.url = src.get("api_url") or API_URL
        self.env = src.get("session_env") or SESSION_ENV
        self.cookie = src.get("cookie_name") or COOKIE
        self.timeout = timeout

    def session(self) -> str:
        value = os.environ.get(self.env, "").strip().strip('"').strip("'")
        if value.lower().startswith(self.cookie.lower() + "="):  # pasted as name=value
            value = value[len(self.cookie) + 1:]
        return value.split(";")[0].strip()

    def problem(self) -> str:
        if self.session():
            return ""
        return (f"set {self.env} to the '{self.cookie}' browser cookie of a space admin "
                f"(how: see techscan/alkemio.py)")

    def query(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "TechScan (hackathon review tool)"}
        if self.session():
            headers["Cookie"] = f"{self.cookie}={self.session()}"
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            with e:
                detail = e.read().decode("utf-8", "replace")[:300]
            raise AlkemioError(f"Alkemio API returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AlkemioError(f"Alkemio API not reachable ({e})") from e
        except ValueError as e:
            raise AlkemioError("Alkemio API returned something that is not JSON") from e
        errors = [x.get("message", "") for x in data.get("errors") or []]
        if errors and not data.get("data"):
            raise AlkemioError("Alkemio API: " + "; ".join(errors)[:400])
        for msg in errors:
            log(f"  WARN Alkemio: {msg[:200]}")
        return data.get("data") or {}


# ------------------------------------------------------------------ mapping answers to columns

def question_role(prompt: str, overrides: dict = None) -> str:
    p = (prompt or "").strip().lower()
    for role, wanted in (overrides or {}).items():
        for w in [wanted] if isinstance(wanted, str) else wanted or []:
            if w and w.strip().lower() in p:
                return role
    for role, pattern in ROLES:
        if re.search(pattern, p):
            return role
    return ""


def first_url(text: str, prefer_github: bool = False) -> str:
    text = text or ""
    m = GITHUB_URL.search(text) if prefer_github else None
    m = m or ANY_URL.search(text)
    if not m:
        return ""
    url = m.group(0).rstrip(".;:!?")
    return url if url.lower().startswith("http") else "https://" + url


def count_members(text: str) -> int:
    lines = [x for x in (text or "").splitlines() if x.strip(" -*•\t")]
    if len(lines) == 1 and re.search(r"[,;]", lines[0]):
        lines = [x for x in re.split(r"[,;]", lines[0]) if x.strip()]
    return len(lines)


def response_row(resp: dict, challenge: str, platform_url: str, cfg: dict, label: str, overrides: dict = None) -> dict:
    values, description = {}, []
    for a in resp.get("answers") or []:
        text = (a.get("text") or "").strip()
        if not text:
            continue
        role = question_role(a.get("prompt", ""), overrides)
        if role == "description":
            description.append(f"{a.get('prompt', '').strip()}\n{text}")
        elif role and role not in values:
            values[role] = text
    notes = []
    repo_text = values.get("repo", "")
    repo = first_url(repo_text, prefer_github=True)
    if not repo:
        notes.append("No repository link in the Alkemio form" + (f" (answer: {repo_text[:80]!r})" if repo_text else ""))
    created = resp.get("createdDate") or ""
    try:
        sent = parse_dt(created) if created else None
    except (ValueError, TypeError):
        sent = None
    if sent and sent > cfg["event"]["deadline_dt"]:
        notes.append(f"Alkemio form sent after the deadline ({fmt_dt(sent.astimezone(cfg['event']['deadline_dt'].tzinfo))})")
    size = count_members(values.get("members", ""))
    return {
        "project": values.get("project", "").splitlines()[0][:80] if values.get("project") else "",
        "repo": repo,
        "challenge": challenge,
        "description": "\n\n".join(description)[:MAX_DESCRIPTION],
        "punchline": values.get("punchline", "")[:300],
        "demo": first_url(values.get("demo", "")),
        "team_size": str(size) if size else "",
        "status": "form sent",
        "platform": label,
        "platform_url": platform_url,
        "notes": " | ".join(notes),
        "_sent": created,
    }


def dedupe(rows: list) -> list:
    """Every member can send the form, so one team can appear more than once: keep the newest per team."""
    rows = sorted(rows, key=lambda r: r.get("_sent") or "", reverse=True)
    out, seen = [], {}
    for r in rows:
        name = re.sub(r"\s+", " ", (r.get("project") or "").strip().lower())
        key = (r.get("challenge"), name) if name else None
        if key and key in seen:
            seen[key]["_extra"] += 1
            continue
        r["_extra"] = 0
        out.append(r)
        if key:
            seen[key] = r
    for r in out:
        if r["_extra"]:
            r["notes"] = " | ".join(x for x in (r["notes"], f"{r['_extra'] + 1} Alkemio form responses for this "
                                                             "team name; used the newest") if x)
        r.pop("_extra", None)
        r.pop("_sent", None)
    return out


# ------------------------------------------------------------------ reading the space

def _forms(cfg: dict, src: dict, data: dict) -> tuple:
    """[(challenge_key, subspace_name, callout_url, form)] for every challenge subspace with the form,
    plus the challenge keys without one."""
    space = (data.get("lookupByName") or {}).get("space")
    if not space:
        raise AlkemioError(f"space '{src.get('space')}' not found (use the nameID from https://alkem.io/<space>)")
    callout_name = (src.get("callout") or "inzending").lower()
    explicit = src.get("challenges") or {}
    found, unmapped = [], []
    for sub in space.get("subspaces") or []:
        callouts = ((sub.get("collaboration") or {}).get("calloutsSet") or {}).get("callouts") or []
        callout = next((c for c in callouts if (c.get("nameID") or "").lower() == callout_name
                        and ((c.get("framing") or {}).get("form"))), None)
        if not callout:
            continue
        name = ((sub.get("about") or {}).get("profile") or {}).get("displayName") or sub["nameID"]
        key = explicit.get(sub["nameID"]) or (sub["nameID"] if sub["nameID"] in cfg["challenges"] else "") \
            or match_challenge(name, cfg["challenges"])
        if not key:
            unmapped.append(name)
        url = ((callout.get("framing") or {}).get("profile") or {}).get("url") \
            or ((sub.get("about") or {}).get("profile") or {}).get("url", "")
        found.append((key or name, name, url, callout["framing"]["form"]))
    missing = [k for k in cfg["challenges"] if k not in {f[0] for f in found}]
    return found, missing, unmapped


def _responses(client: AlkemioClient, form_id: str) -> tuple:
    """(can_read_all, responses) for one form, following the pages."""
    out, after, can_read_all = [], None, False
    for _ in range(200):
        data = client.query(RESPONSES, {"form": form_id, "first": PAGE, "after": after})
        block = (data.get("lookup") or {}).get("calloutFormResponses") or {}
        can_read_all = bool(block.get("canReadAll"))
        page = block.get("all") or {}
        out += page.get("responses") or []
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor") or info.get("endCursor") == after:
            break
        after = info["endCursor"]
    return can_read_all, out


def _structure(client: AlkemioClient, src: dict) -> tuple:
    data = client.query(STRUCTURE, {"space": src.get("space", "")})
    user = (data.get("me") or {}).get("user")
    if not user:
        raise AlkemioError(f"the {client.env} cookie was not accepted (expired, logged out, or not copied whole); "
                           f"copy '{client.cookie}' again from a logged-in browser")
    return ((user.get("profile") or {}).get("displayName") or "?"), data


def pull(cfg: dict, src: dict, client: AlkemioClient = None) -> list:
    client = client or AlkemioClient(src)
    problem = client.problem()
    if problem:
        raise AlkemioError(problem)
    label = src.get("label") or "Alkemio"
    who, data = _structure(client, src)
    forms, missing, unmapped = _forms(cfg, src, data)
    log(f"  Alkemio: logged in as {who}; {len(forms)} submission form(s) in '{src.get('space')}'")
    if missing:
        log(f"  WARN Alkemio: no '{src.get('callout') or 'inzending'}' form found for challenge(s): {', '.join(missing)}")
    if unmapped:
        log(f"  WARN Alkemio: form(s) in subspace(s) not matched to a challenge: {', '.join(unmapped)} "
            "(map them under the source's 'challenges:')")
    rows = []
    for key, name, url, form in forms:
        can_read_all, responses = _responses(client, form["id"])
        if not can_read_all:
            raise AlkemioError(f"logged in as {who}, but this account cannot read all responses of the form in "
                               f"'{name}'. Make it an admin of the space (or of that challenge).")
        log(f"  Alkemio {name}: {len(responses)} form response(s)")
        rows += [response_row(r, key, url, cfg, label, src.get("questions")) for r in responses]
    return dedupe(rows)


def check(cfg: dict, src: dict, client: AlkemioClient = None) -> tuple:
    """Preflight with --ping: (summary, problem). Logs in, finds the forms and checks admin access."""
    client = client or AlkemioClient(src)
    problem = client.problem()
    if problem:
        return "", problem
    try:
        who, data = _structure(client, src)
        forms, missing, unmapped = _forms(cfg, src, data)
        problems, total = [], 0
        for key, name, _, form in forms:
            roles = {question_role(q.get("prompt", ""), src.get("questions")) for q in form.get("questions") or []}
            lacking = [r for r in ("project", "repo") if r not in roles]
            if lacking:
                problems.append(f"the form in '{name}' has no question for: {', '.join(lacking)}")
            can_read_all, responses = _responses(client, form["id"])
            if not can_read_all:
                problems.append(f"cannot read all responses in '{name}' (make {who} a space admin)")
            total += len(responses)
        if missing:
            problems.append(f"no submission form for: {', '.join(missing)}")
        if unmapped:
            problems.append(f"form(s) not matched to a challenge: {', '.join(unmapped)}")
        return f"logged in as {who}; {len(forms)} form(s), {total} response(s) so far", "; ".join(problems)
    except AlkemioError as e:
        return "", str(e)
