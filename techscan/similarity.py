"""Cross-team checks: shared boilerplate, and code that two teams have in common."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .facts import notebook_to_text
from .util import sha256_text

TRIVIAL_RE = re.compile(r"^[\s{}()\[\];,.:<>/\\'\"`=+*-]*$")
IMPORT_RE = re.compile(r"^\s*(import |from \S+ import |#include|using |require\(|export \* from|export \{)")
MIN_LINE_CHARS = 20
FILE_MIN_LINES = 10      # a copied module needs at least this many distinctive lines...
FILE_MIN_SHARE = 0.6     # ...and this share of them found in the other team's code


def _lines_for(inv, f) -> list:
    try:
        text = inv.read(f["path"])
    except OSError:
        return []
    if f["kind"] == "notebook":
        text, _ = notebook_to_text(text)
        text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("# [output]") and not ln.startswith("# ---- cell"))
    out = []
    for ln in text.splitlines():
        s = " ".join(ln.split())
        if len(s) < MIN_LINE_CHARS or TRIVIAL_RE.match(s) or IMPORT_RE.match(s):
            continue
        out.append(sha256_text(s)[:16])
    return out


THIRD_PARTY_PATH = re.compile(r"(?i)(^|/)(vendor|vendors|lib|libs|third[_-]?party|external|static|assets|public)/")
LICENSE_HINT = re.compile(r"(?i)@license|copyright \(c\)|copyright \d{4}|mit license|licensed under|spdx-license")


def _looks_third_party(inv, path: str) -> bool:
    if THIRD_PARTY_PATH.search(path):
        return True
    try:
        head = "\n".join(inv.read(path).splitlines()[:30])
    except OSError:
        return False
    return bool(LICENSE_HINT.search(head))


def template_line_hashes(inv) -> set:
    """Every distinctive line in the organisers' template (so edits to starter files are not 'shared code')."""
    out = set()
    for f in inv.files:
        if f["kind"] in ("code", "notebook", "doc", "config") and not f["binary"] and f["size"] <= 1_000_000:
            out.update(_lines_for(inv, f))
    return out


def cross_repo(inventories: dict, min_repos: int, min_shared: int, min_ratio: float,
               template_lines: dict = None, repo_ids: dict = None) -> tuple:
    """inventories: slug -> Inventory (template statuses already set).
    template_lines: slug -> line hashes of that team's challenge template.
    repo_ids: slug -> repository identity; the same repo submitted twice is not compared with itself.

    Returns (boilerplate_hashes, similar) where similar maps slug -> list of matches.
    """
    template_lines = template_lines or {}
    repo_ids = repo_ids or {}
    file_df = Counter()
    for inv in inventories.values():
        for h in {f["hash"] for f in inv.files if f["hash"] and f["kind"] in ("code", "notebook", "config", "doc")}:
            file_df[h] += 1
    threshold = max(min_repos, 3) if len(inventories) >= 4 else 99
    boilerplate = {h for h, c in file_df.items() if c >= threshold}

    file_lines, line_df = {}, Counter()
    for slug, inv in inventories.items():
        per_file = {}
        for f in inv.files:
            if f["status"] in ("template", "scaffold") or f["hash"] in boilerplate:
                continue
            if f["kind"] not in ("code", "notebook") or f["size"] > 1_000_000:
                continue
            lines = set(_lines_for(inv, f)) - template_lines.get(slug, set())
            if lines:
                per_file[f["path"]] = lines
        file_lines[slug] = per_file
        for h in set().union(*per_file.values()) if per_file else set():
            line_df[h] += 1
    common = {h for h, c in line_df.items() if c >= threshold}
    repo_lines = {s: (set().union(*pf.values()) - common) if pf else set() for s, pf in file_lines.items()}

    def copied_files(a, b):
        out = []
        for path, lines in file_lines[a].items():
            mine = lines - common
            if len(mine) >= FILE_MIN_LINES and len(mine & repo_lines[b]) / len(mine) >= FILE_MIN_SHARE:
                out.append(path)
        return out

    hashes = {slug: {f["path"]: f["hash"] for f in inv.files} for slug, inv in inventories.items()}
    similar = defaultdict(list)
    slugs = sorted(repo_lines)
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            if repo_ids.get(a) and repo_ids.get(a) == repo_ids.get(b):
                continue  # the same repository submitted twice
            la, lb = repo_lines[a], repo_lines[b]
            if not la or not lb:
                continue
            shared = la & lb
            ratio = len(shared) / min(len(la), len(lb))
            fa, fb = copied_files(a, b), copied_files(b, a)
            if not ((len(shared) >= min_shared and ratio >= min_ratio) or fa or fb):
                continue
            # one identical file explains (almost) all of the overlap: usually a vendored library or snippet
            ca = Counter(p for p, lines in file_lines[a].items() for h in lines if h in shared)
            cb = Counter(p for p, lines in file_lines[b].items() for h in lines if h in shared)
            identical = ""
            if ca and cb:
                (pa, na), (pb, nb) = ca.most_common(1)[0], cb.most_common(1)[0]
                if (na >= 0.9 * len(shared) and nb >= 0.9 * len(shared)
                        and hashes[a].get(pa) and hashes[a].get(pa) == hashes[b].get(pb)
                        and _looks_third_party(inventories[a], pa)):
                    identical = pa  # a library both teams bundled; a copied team module stays a real flag
            for me, other, files, counts in ((a, b, fa, ca), (b, a, fb, cb)):
                if not files:
                    files = [p for p, _ in counts.most_common(5)]
                if identical:
                    id_file = pa if me == a else pb
                else:
                    id_file = ""
                similar[me].append({"other": other, "shared_lines": len(shared), "ratio": round(ratio, 2),
                                    "files": files[:5], "identical_file": id_file})
    return boilerplate, dict(similar)
