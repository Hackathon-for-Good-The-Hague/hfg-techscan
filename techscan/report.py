"""Jury outputs: scorecard.xlsx, printable HTML packs (all teams and per challenge), per-team JSON."""
from __future__ import annotations

import html
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .gitops import blob_url
from .util import fmt_dt, log, parse_dt, read_json, truncate, write_json

BLUE, YELLOW = "1962A1", "FCCD02"
SCORE_FILL_5 = {1: "F4B6B6", 2: "F9D9C4", 3: "FFF2CC", 4: "DDEFD5", 5: "B9E0A5"}
SCORE_FILL_10 = {1: "F4B6B6", 2: "F4B6B6", 3: "F9D9C4", 4: "F9D9C4", 5: "FFF2CC",
                 6: "FFF2CC", 7: "DDEFD5", 8: "DDEFD5", 9: "B9E0A5", 10: "B9E0A5"}
STATUS_LABEL = {"verified": "Verified", "partial": "Partial", "mocked": "Mocked", "missing": "Missing", "unclear": "Unclear"}
STATUS_COLOR = {"verified": "#2E7D32", "partial": "#B26A00", "mocked": "#C62828", "missing": "#8E1B1B", "unclear": "#6B7280"}
SEV_COLOR = {"high": "#C62828", "medium": "#B26A00", "low": "#4B5563", "info": "#6B7280"}
AI_LABEL = {"none": "No AI", "hosted_model_api": "Calls a hosted AI model", "prompt_pipeline": "Prompt pipeline",
            "rag": "Retrieval (RAG)", "agent": "AI agent", "classical_ml": "Classical machine learning",
            "trained_or_finetuned_model": "Trained or fine-tuned model", "other": "Other"}
DATA_LABEL = {"yes": "Yes", "partly": "Partly", "no": "No", "unclear": "Unclear"}
FLAG_LABEL = {"undisclosed_mock": "Mock presented as real", "hardcoded_output": "Hardcoded output",
              "prebuilt_code": "Code likely built before the event", "ai_directed_text": "Text aimed at the judging",
              "secret_exposed": "Secret exposed", "copied_code": "Copied code",
              "broken_or_incomplete": "Broken or incomplete", "privacy_or_safety_risk": "Privacy or safety risk",
              "claim_mismatch": "Claims do not match the code", "other": "Other concern"}


def _local(cfg: dict, value) -> str:
    """Show any timestamp in the event's time zone."""
    try:
        dt = parse_dt(value)
    except (ValueError, TypeError):
        return str(value or "")
    tz = cfg["event"]["start_dt"].tzinfo
    return fmt_dt(dt.astimezone(tz))


def load_results(workdir: Path):
    run = read_json(workdir / "results" / "_run.json", {}) or {}
    keep = set(run.get("slugs") or [])
    results = {}
    for p in sorted((workdir / "results").glob("*.json")):
        if p.name.startswith("_") or (keep and p.stem not in keep):
            continue  # teams from an earlier, different submissions file
        r = read_json(p)
        if isinstance(r, dict):
            results[p.stem] = r
    return run, results


def _order(results: dict, cfg: dict, by_score: bool = True):
    """Scorecard: best technical score first. Jury packs: alphabetical, so the score does not set the agenda."""
    keys = list(cfg["challenges"].keys())

    def k(item):
        slug, r = item
        ch = r["submission"].get("challenge") or "~"
        score = ((r.get("review") or {}).get("technical_score")) or 0
        return (keys.index(ch) if ch in keys else 99, -score if by_score else 0, r["submission"]["project"].lower())
    return sorted(results.items(), key=k)


# AI flags that only repeat a hard fact the scanner already reports
AI_ECHOES = {"secret_exposed": {"secret_exposed", "possible_secret"},
             "prebuilt_code": {"pre_event_commits", "big_code_drop", "pushes_before_start", "fork_of_existing"},
             "copied_code": {"similar_code"}}
# scanner hunches that the AI review has judged (its verdict replaces the hunch)
AI_VERDICTS = {"ai_directed_text": {"ai_directed_text", "instruction_like_text"}}


def _all_flags(r: dict, dedupe: bool = False) -> list:
    scanner = [dict(f, source="Scanner") for f in r.get("flags") or []]
    ai = [{"code": f["type"], "severity": f["severity"], "title": FLAG_LABEL.get(f["type"], f["type"]),
           "detail": f["detail"], "evidence": f.get("evidence", []), "source": "AI review"}
          for f in ((r.get("review") or {}).get("red_flags") or [])]
    if dedupe:
        codes = {f["code"] for f in scanner}
        ai = [f for f in ai if not (codes & AI_ECHOES.get(f["code"], set()))]
        judged = set().union(*(v for k, v in AI_VERDICTS.items() if any(f["code"] == k for f in ai))) if ai else set()
        scanner = [f for f in scanner if f["code"] not in judged]
        if r.get("review"):  # reviewed, and the AI did not see it as aimed at the judging: keep as a footnote only
            for f in scanner:
                if f["code"] == "ai_directed_text" and f["severity"] != "low":
                    f["severity"] = "low"
                    f["detail"] = f["detail"].replace(". The AI review says whether it targets the judging.",
                                                      ". The AI review did not find it aimed at the judging.")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(scanner + ai, key=lambda f: order.get(f["severity"], 9))


def _partners(r):
    return ", ".join(f"{v['label']} ({'code' if v['level'].startswith('used') else 'docs'})"
                     for v in ((r.get("facts") or {}).get("partners") or {}).values())


def _ev_link(r, ev: dict) -> str:
    sub, snap = r["submission"], r.get("snapshot") or {}
    if ev.get("commit"):
        if sub.get("repo_url", "").startswith("https://github.com/"):
            return f"{sub['repo_url']}/commit/{ev['commit']}"
        return ""
    return blob_url(sub.get("repo_url", ""), snap.get("sha", ""), ev.get("file", ""), ev.get("lines", ""))


# ------------------------------------------------------------------ XLSX

def _append(ws, row: list) -> None:
    """Text from repos must stay text: strip characters Excel rejects, never let it become a formula."""
    clean = []
    for v in row:
        if isinstance(v, str):
            v = ILLEGAL_CHARACTERS_RE.sub("", v)
            if len(v) > 32000:
                v = v[:32000] + "..."
        clean.append(v)
    ws.append(clean)
    for c in ws[ws.max_row]:
        if isinstance(c.value, str) and c.value[:1] in ("=", "+", "-", "@"):
            c.data_type = "s"


def write_xlsx(path: Path, cfg: dict, run: dict, results: dict) -> Path:
    wb = Workbook()
    hdr_font = Font(name="Calibri", bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor=BLUE)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    def sheet(ws, headers, widths):
        _append(ws, headers)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
            c = ws.cell(row=1, column=i)
            c.font, c.fill, c.alignment = hdr_font, hdr_fill, Alignment(wrap_text=True, vertical="center")
        ws.freeze_panes = "B2"
        ws.row_dimensions[1].height = 32

    ws = wb.active
    ws.title = "Scorecard"
    rubric = cfg.get("rubric") or {}
    criteria = rubric.get("criteria") or {}
    show_scores = rubric.get("show_scores_to_jury", False)
    if show_scores:
        crit_headers = [f"{v.get('label', k)} (×{v.get('weight', 1)})" for k, v in criteria.items()]
        headers = (["Project", "Challenge", "Weighted total"] + crit_headers +
                   ["Tech score", "Confidence", "In short", "Claims verified", "Claims mocked or missing",
                    "Uses challenge data", "AI approach", "High flags", "All flags", "README", "Partner tech",
                    "Built with Lovable", "Commits", "Contributors", "Team code lines", "Demo link", "Video",
                    "Repository", "Snapshot", "Review status", "Questions for the jury"])
        crit_widths = [12] * len(criteria)
        sheet(ws, headers, [30, 16, 12] + crit_widths + [10, 11, 50, 11, 13, 12, 22, 8, 50, 28, 26, 10, 9, 11, 11, 11, 8, 40, 11, 12, 70])
    else:
        headers = ["Project", "Challenge", "Claims verified", "Claims mocked or missing",
                   "Uses challenge data", "AI approach", "High flags", "All flags", "README", "Partner tech",
                   "Built with Lovable", "Commits", "Contributors", "Team code lines", "Demo link", "Video",
                   "Repository", "Snapshot", "Review status", "Questions for the jury"]
        sheet(ws, headers, [30, 16, 11, 13, 12, 22, 8, 50, 28, 26, 10, 9, 11, 11, 11, 8, 40, 11, 12, 70])
    for slug, r in _order(results, cfg):
        sub, f, rv = r["submission"], r.get("facts") or {}, r.get("review") or {}
        chal = (cfg["challenges"].get(sub.get("challenge")) or {}).get("name", sub.get("challenge_raw") or "?")
        cs = rv.get("claims_summary") or {}
        n_claims = len(rv.get("claims") or [])
        flags = _all_flags(r)
        tl = f.get("timeline") or {}
        rd = (f or {}).get("readme") or {}
        scores = rv.get("scores") or {}
        weighted = sum(scores.get(k, 0) * v.get("weight", 1) for k, v in criteria.items()) if scores else None
        crit_scores = [scores.get(k) for k in criteria]
        common_cols = [
             f"{cs.get('verified', 0)} of {n_claims}" if n_claims else "",
             (cs.get("mocked", 0) + cs.get("missing", 0)) if n_claims else "",
             DATA_LABEL.get((rv.get("challenge_data") or {}).get("used", ""), ""),
             AI_LABEL.get((rv.get("ai_approach") or {}).get("type", ""), ""),
             sum(1 for x in flags if x["severity"] == "high"),
             "\n".join(f"[{x['severity']}] {x['title']}" for x in flags if x["severity"] != "info"),
             _readme_quality(rd) if rd.get("exists") and rd.get("status") != "template" else ("template" if rd.get("status") == "template" else "none"),
             _partners(r),
             "Yes" if (f.get("lovable") or {}).get("lovable") else "",
             tl.get("commits", ""), tl.get("contributors", ""), f.get("team_code_lines", ""),
             (f.get("demo") or {}).get("status", "") if sub.get("demo") else "none",
             "Yes" if sub.get("video") else "",
             sub.get("repo_url", ""), (r.get("snapshot") or {}).get("short", ""),
             r.get("review_status", r.get("status", "")),
             "\n".join(f"{i}. {q}" for i, q in enumerate(rv.get("jury_questions") or [], 1)),
        ]
        if show_scores:
            row = ([sub["project"], chal, round(weighted, 1) if weighted else None] + crit_scores +
                   [rv.get("technical_score"), rv.get("confidence", ""), _score_summary(r)] + common_cols)
        else:
            row = [sub["project"], chal] + common_cols
        _append(ws, row)
        i = ws.max_row
        for c in ws[i]:
            c.alignment, c.border, c.font = wrap, border, Font(name="Calibri")
        # Color the weighted total cell
        if weighted:
            bucket = max(1, min(10, round(weighted / (rubric.get("max_total", 100) / 10))))
            if bucket in SCORE_FILL_10:
                ws.cell(row=i, column=3).fill = PatternFill("solid", fgColor=SCORE_FILL_10[bucket])
                ws.cell(row=i, column=3).font = Font(name="Calibri", bold=True)
        # Color each criterion score cell
        for ci, k in enumerate(criteria):
            v = scores.get(k)
            if v and v in SCORE_FILL_10:
                ws.cell(row=i, column=4 + ci).fill = PatternFill("solid", fgColor=SCORE_FILL_10[v])
        # Repo hyperlink (column shifts with dynamic criteria)
        repo_col = 4 + len(criteria) + 18  # after criteria + fixed columns
        if sub.get("repo_url", "").startswith("http"):
            ws.cell(row=i, column=repo_col).hyperlink = sub["repo_url"]
            ws.cell(row=i, column=repo_col).font = Font(name="Calibri", color="0563C1", underline="single")
    ws.auto_filter.ref = ws.dimensions

    ws2 = wb.create_sheet("Flags")
    sheet(ws2, ["Project", "Severity", "Found by", "Flag", "Detail", "Evidence"], [30, 9, 11, 45, 70, 50])
    for slug, r in _order(results, cfg):
        for fl in _all_flags(r):
            ev = (fl.get("evidence") or [{}])[0] if fl.get("evidence") else {}
            label = f"{ev.get('file', '')}:{ev.get('lines', '')}".strip(":") if ev.get("file") else (ev.get("commit", "") or "")
            _append(ws2, [r["submission"]["project"], fl["severity"], fl.get("source", ""), fl["title"],
                        truncate(fl.get("detail", ""), 900), label])
            i = ws2.max_row
            for c in ws2[i]:
                c.alignment, c.font = wrap, Font(name="Calibri")
            link = _ev_link(r, ev) if ev else ""
            if link:
                ws2.cell(row=i, column=6).hyperlink = link
                ws2.cell(row=i, column=6).font = Font(name="Calibri", color="0563C1", underline="single")
    ws2.auto_filter.ref = ws2.dimensions

    ws3 = wb.create_sheet("Claims")
    sheet(ws3, ["Project", "Claim", "Status", "Evidence", "Note"], [30, 60, 11, 45, 70])
    for slug, r in _order(results, cfg):
        for c in ((r.get("review") or {}).get("claims") or []):
            ev = (c.get("evidence") or [{}])[0] if c.get("evidence") else {}
            _append(ws3, [r["submission"]["project"], c["claim"], STATUS_LABEL.get(c["status"], c["status"]),
                        f"{ev.get('file', '')}:{ev.get('lines', '')}".strip(":") if ev else "", c.get("note", "")])
            i = ws3.max_row
            for cell in ws3[i]:
                cell.alignment, cell.font = wrap, Font(name="Calibri")
            link = _ev_link(r, ev) if ev else ""
            if link and ev.get("valid", True):
                ws3.cell(row=i, column=4).hyperlink = link
                ws3.cell(row=i, column=4).font = Font(name="Calibri", color="0563C1", underline="single")
    ws3.auto_filter.ref = ws3.dimensions

    ws4 = wb.create_sheet("Questions")
    sheet(ws4, ["Project", "Challenge", "Question 1", "Question 2", "Question 3"], [30, 16, 60, 60, 60])
    for slug, r in _order(results, cfg):
        qs = ((r.get("review") or {}).get("jury_questions") or []) + ["", "", ""]
        chal = (cfg["challenges"].get(r["submission"].get("challenge")) or {}).get("name", "")
        _append(ws4, [r["submission"]["project"], chal] + qs[:3])
        for cell in ws4[ws4.max_row]:
            cell.alignment, cell.font = wrap, Font(name="Calibri")

    ws5 = wb.create_sheet("Partner tech")
    partners = cfg["partners"]
    sheet(ws5, ["Project"] + [p.get("label", k) for k, p in partners.items()], [30] + [18] * len(partners))
    for slug, r in _order(results, cfg):
        found = (r.get("facts") or {}).get("partners") or {}
        _append(ws5, [r["submission"]["project"]] + [(found.get(k) or {}).get("level", "") for k in partners])

    ws6 = wb.create_sheet("About")
    ws6.column_dimensions["A"].width = 28
    ws6.column_dimensions["B"].width = 100
    about = [
        ("What this is", "Advisory technical evidence for the jury. It does not pick winners. Humans decide."),
        ("Snapshot", f"Last commit on each team's main branch before the deadline ({run.get('deadline', '')})."),
        ("Tech score", "1 = little working code, 2 = mostly UI or mocked, 3 = narrow but real, "
                       "4 = working end to end, 5 = exceptional for the time. Covers technical execution only."),
        ("Flags", "Scanner flags come from fixed checks (no AI). AI review flags come from reading the code. "
                  "Both can have innocent explanations: ask the team."),
        ("Review engine", f"{run.get('backend', '')} / {run.get('model', '')}"),
        ("GitHub push log", f"{run.get('push_log_checked', '')} (API: {run.get('github_api', '')})"),
        ("Generated", run.get("finished", "")),
    ]
    for k, v in about:
        _append(ws6, [k, v])
        ws6.cell(row=ws6.max_row, column=1).font = Font(name="Calibri", bold=True)
        ws6.cell(row=ws6.max_row, column=2).alignment = wrap
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:  # usually: the file is open in Excel
        from datetime import datetime
        alt = path.with_name(f"{path.stem}-{datetime.now().strftime('%H%M%S')}{path.suffix}")
        wb.save(alt)
        log(f"{path.name} is open in another program; saved as {alt.name} instead")
        return alt
    return path


# ------------------------------------------------------------------ HTML

CSS = """
*{box-sizing:border-box}body{font-family:Calibri,'Segoe UI',Arial,sans-serif;color:#1f2937;margin:0;background:#f4f6f9;font-size:15px;line-height:1.45}
a{color:#1962A1}.wrap{max-width:980px;margin:0 auto;padding:24px}
.cover{background:#1962A1;color:#fff;padding:32px 24px}.cover .wrap{padding:0 24px}.brand{font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:13px;color:#FCCD02}
.cover h1{margin:6px 0 4px;font-size:30px}.cover p{margin:0;opacity:.9}
.howto{background:#fff;border-left:6px solid #FCCD02;padding:14px 18px;margin:20px 0;border-radius:6px}
.howto p{margin:4px 0}
table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:7px 8px;border-bottom:1px solid #e5e7eb}
th{background:#eef3f9;font-size:13px;color:#374151}
.summary td.score,.summary th.score{text-align:center;width:70px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:700;color:#fff;white-space:nowrap}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;background:#eef3f9;color:#1962A1;margin-right:4px;white-space:nowrap}
.team{background:#fff;border-radius:10px;padding:22px 24px;margin:22px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.team h2{margin:0 0 6px;font-size:24px}.links a{margin-right:14px;font-size:14px}
.top{display:flex;gap:18px;align-items:flex-start;margin:14px 0}
.badge{min-width:96px;text-align:center;border-radius:12px;padding:10px 6px;background:#1962A1;color:#fff}
.badge .n{font-size:40px;font-weight:700;line-height:1}.badge .l{font-size:12px;opacity:.9}
.badge.na{background:#9ca3af}
.subs{flex:1}.subs .row{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:13px}
.subs .lab{width:120px;color:#4b5563}.bar{height:9px;border-radius:5px;background:#e5e7eb;flex:0 0 160px;position:relative}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:5px;background:#1962A1}
.summarytxt{font-size:16px;margin:6px 0 12px}
.box{border-radius:8px;padding:12px 16px;margin:12px 0}.box h3{margin:0 0 6px;font-size:15px}
.flags{background:#fff5f5;border:1px solid #f3caca}.qs{background:#fffbe6;border:1px solid #FCCD02}
.qs ol{margin:4px 0 0 18px;padding:0}.qs li{margin:4px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid .box{background:#f8fafc;border:1px solid #e5e7eb}
ul.tight{margin:4px 0 0 18px;padding:0}ul.tight li{margin:3px 0}
.flag{margin:6px 0}.flag .why{color:#4b5563;font-size:13px}
.small{font-size:12.5px;color:#6b7280}.ev a,.ev span{font-family:Consolas,Menlo,monospace;font-size:12px;margin-right:8px}
.ev .bad{color:#9ca3af;text-decoration:line-through}
h3.sec{font-size:15px;margin:16px 0 6px;color:#1962A1}
.facts td:first-child{width:200px;color:#4b5563}
@page{size:A4;margin:14mm}
@media print{body{background:#fff;font-size:12.5px}.team{box-shadow:none;page-break-before:always;margin:0;padding:8px 0}
.cover{-webkit-print-color-adjust:exact;print-color-adjust:exact}.badge,.pill,.bar i{-webkit-print-color-adjust:exact;print-color-adjust:exact}
a{text-decoration:none}}
@media (max-width:700px){.grid{grid-template-columns:1fr}.top{flex-direction:column}.bar{flex-basis:110px}}
"""


def e(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def _evid_html(r, items) -> str:
    parts = []
    for ev in items or []:
        label = f"{ev.get('file', '')}" + (f":{ev['lines']}" if ev.get("lines") else "")
        if ev.get("commit") and not ev.get("file"):
            label = f"commit {ev['commit']}"
        if ev.get("valid") is False:
            parts.append(f'<span class="bad" title="Citation not found in the snapshot">{e(label)}</span>')
            continue
        link = _ev_link(r, ev)
        parts.append(f'<a href="{e(link)}" target="_blank" rel="noopener">{e(label)}</a>' if link else f"<span>{e(label)}</span>")
    return f'<div class="ev">{"".join(parts)}</div>' if parts else ""


def _readme_quality(rd: dict) -> str:
    """Short summary of README completeness."""
    q = rd.get("quality", 0)
    parts = []
    if q >= 5:
        parts.append("Well-documented")
    elif q >= 3:
        parts.append("Adequate")
    elif q >= 1:
        parts.append("Minimal")
    else:
        parts.append("Empty or missing")
    has = []
    if rd.get("has_run_instructions"):
        has.append("setup instructions")
    if rd.get("has_architecture"):
        has.append("architecture")
    if rd.get("has_screenshots"):
        has.append("screenshots")
    missing = []
    if not rd.get("has_run_instructions"):
        missing.append("setup instructions")
    if not rd.get("has_architecture"):
        missing.append("architecture")
    if not rd.get("has_screenshots"):
        missing.append("visuals")
    detail = ""
    if has:
        detail += f" (has {', '.join(has)})"
    if missing:
        detail += f" — missing {', '.join(missing)}"
    return f"{parts[0]} ({q}/6){detail}"


def _score_summary(r: dict) -> str:
    """One plain-language paragraph explaining what the tech score means, for non-technical judges."""
    rv = r.get("review") or {}
    score = rv.get("technical_score")
    f = r.get("facts") or {}
    tl = f.get("timeline") or {}
    flags = [x for x in _all_flags(r, dedupe=True) if x["severity"] in ("high", "medium")]
    lines = f.get("team_code_lines", 0)
    contributors = tl.get("contributors", 0)
    lov = (f.get("lovable") or {}).get("lovable", False)
    parts = []
    if score is None:
        return ""
    max_score = (r.get("_rubric") or {}).get("max_score", 10)
    if max_score <= 5:
        label = {1: "very little working code", 2: "mostly user interface or mocked functionality",
                 3: "a narrow but real working feature", 4: "a working end-to-end solution",
                 5: "an exceptionally complete solution for the time available"}.get(score, "")
    else:
        label = {1: "no working code", 2: "very little beyond template", 3: "minimal effort",
                 4: "early stage with major gaps", 5: "reasonable attempt with clear gaps",
                 6: "works for a narrow case", 7: "strong for a hackathon", 8: "most promises delivered",
                 9: "exceptional", 10: "would surprise a professional"}.get(score, "")
    parts.append(f"This submission scores {score}/{max_score} on technical execution ({label}).")
    if lines:
        parts.append(f"The team wrote about {lines:,} lines of code" +
                     (f" with {contributors} contributor(s)." if contributors else "."))
    if lov:
        parts.append("The project was generated with Lovable (an AI app builder); check how much the team customised beyond the scaffold.")
    if flags:
        high = [x for x in flags if x["severity"] == "high"]
        if high:
            parts.append(f"There {'is' if len(high) == 1 else 'are'} {len(high)} high-priority flag(s) to check before scoring.")
        elif flags:
            parts.append(f"There {'is' if len(flags) == 1 else 'are'} {len(flags)} flag(s) worth a quick look.")
    cs = rv.get("claims_summary") or {}
    n_claims = sum(cs.values())
    verified = cs.get("verified", 0)
    mocked = cs.get("mocked", 0) + cs.get("missing", 0)
    if n_claims:
        parts.append(f"Of {n_claims} claims checked, {verified} {'was' if verified == 1 else 'were'} verified in the code"
                     + (f" and {mocked} {'was' if mocked == 1 else 'were'} mocked or missing" if mocked else "") + ".")
    return " ".join(parts)


def _team_html(r: dict, cfg: dict) -> str:
    sub, f, rv, snap = r["submission"], r.get("facts") or {}, r.get("review") or {}, r.get("snapshot") or {}
    chal = (cfg["challenges"].get(sub.get("challenge")) or {}).get("name", sub.get("challenge_raw") or "Challenge unknown")
    tl = f.get("timeline") or {}
    rd = f.get("readme") or {}
    out = [f'<section class="team" id="{e(sub["slug"])}">']
    chips = [f'<span class="chip">{e(chal)}</span>']
    if (f.get("lovable") or {}).get("lovable"):
        chips.append('<span class="chip">Built with Lovable</span>')
    if rv.get("concept_only"):
        chips.append('<span class="chip">Concept only</span>')
    if (sub.get("status") or "").lower() == "draft":
        chips.append('<span class="chip">Still a draft in Junction</span>')
    out.append(f'<h2>{e(sub["project"])}</h2><div>{"".join(chips)}</div>')
    links = []
    if sub.get("repo_url", "").startswith("http"):
        if snap.get("sha") and "github.com" in sub["repo_url"]:
            url, label = f"{sub['repo_url']}/tree/{snap['sha']}", f"Code at deadline ({snap.get('short', '')})"
        else:
            url, label = sub["repo_url"], "Repository"
        links.append(f'<a href="{e(url)}" target="_blank" rel="noopener">{e(label)}</a>')
    if sub.get("demo"):
        links.append(f'<a href="{e(sub["demo"])}" target="_blank" rel="noopener">Demo</a>')
    if sub.get("video"):
        links.append(f'<a href="{e(sub["video"])}" target="_blank" rel="noopener">Video</a>')
    if sub.get("junction_url"):
        links.append(f'<a href="{e(sub["junction_url"])}" target="_blank" rel="noopener">Junction page</a>')
    out.append(f'<div class="links" style="margin-top:6px">{"".join(links)}</div>')

    rubric = cfg.get("rubric") or {}
    criteria = rubric.get("criteria") or {}
    max_sc = rubric.get("max_score", 10)
    show_scores = rubric.get("show_scores_to_jury", False)
    scores = rv.get("scores") or {}
    weighted = sum(scores.get(k, 0) * v.get("weight", 1) for k, v in criteria.items()) if scores else None
    score = rv.get("technical_score")
    if show_scores:
        if weighted:
            badge = (f'<div class="badge"><div class="n">{round(weighted)}<span style="font-size:14px">/{rubric.get("max_total", 100)}</span></div>'
                     f'<div class="l">Weighted total</div></div>')
        elif score:
            badge = (f'<div class="badge"><div class="n">{e(score)}<span style="font-size:18px">/{max_sc}</span></div>'
                     f'<div class="l">Technical score</div></div>')
        else:
            badge = f'<div class="badge na"><div class="n">–</div><div class="l">{e(r.get("review_status") or r.get("status") or "No review")}</div></div>'
    else:
        status_label = "Reviewed" if rv else (r.get("review_status") or r.get("status") or "No review")
        badge = f'<div class="badge" style="background:#4b5563"><div class="n" style="font-size:22px">{e(status_label)}</div></div>'
    subs_html = ""
    if show_scores and scores and criteria:
        rows = []
        for k, crit in criteria.items():
            v = scores.get(k) or 0
            lab = crit.get("label", k)
            pct = v * (100 / max_sc) if v else 0
            rows.append(f'<div class="row"><span class="lab">{e(lab)}</span>'
                        f'<span class="bar"><i style="width:{pct}%"></i></span>'
                        f'<b>{v or "–"}</b><span class="small" style="margin-left:4px">×{crit.get("weight", 1)}</span></div>')
        rows.append(f'<div class="row"><span class="lab">Confidence</span><b>{e(rv.get("confidence", ""))}</b></div>')
        subs_html = "".join(rows)
    out.append(f'<div class="top">{badge}<div class="subs">{subs_html}</div></div>')
    tldr = _score_summary(r) if show_scores else ""
    if tldr:
        out.append(f'<p class="small" style="margin:6px 0 4px;color:#4b5563"><b>In short:</b> {e(tldr)}</p>')
    if rv.get("summary"):
        out.append(f'<p class="summarytxt">{e(rv["summary"])}</p>')
    elif r.get("errors"):
        out.append(f'<p class="summarytxt">{e("; ".join(r["errors"]))}</p>')

    flags = [x for x in _all_flags(r, dedupe=True) if x["severity"] in ("high", "medium")]
    if flags:
        items = []
        for fl in flags[:8]:
            items.append(f'<div class="flag"><span class="pill" style="background:{SEV_COLOR.get(fl["severity"], "#6B7280")}">{e(fl["severity"])}</span> '
                         f'<b>{e(fl["title"])}</b> <span class="small">({e(fl.get("source", ""))})</span>'
                         f'<div class="why">{e(fl.get("detail", ""))}</div>{_evid_html(r, fl.get("evidence"))}</div>')
        out.append(f'<div class="box flags"><h3>Check these first</h3>{"".join(items)}</div>')

    if rv.get("jury_questions"):
        qs = "".join(f"<li>{e(q)}</li>" for q in rv["jury_questions"])
        out.append(f'<div class="box qs"><h3>Questions to ask in the Q&amp;A</h3><ol>{qs}</ol></div>')

    if rv.get("claims"):
        rows = []
        for c in rv["claims"]:
            st = c["status"]
            rows.append(f'<tr><td>{e(c["claim"])}</td><td><span class="pill" style="background:{STATUS_COLOR.get(st, "#6B7280")}">{e(STATUS_LABEL.get(st, st))}</span></td>'
                        f'<td>{e(c.get("note", ""))}{_evid_html(r, c.get("evidence"))}</td></tr>')
        out.append('<h3 class="sec">What the team claims, checked against the code</h3>'
                   f'<table><tr><th>Claim</th><th>Status</th><th>What we found</th></tr>{"".join(rows)}</table>')

    if rv:
        cd, ai = rv.get("challenge_data") or {}, rv.get("ai_approach") or {}
        out.append('<div class="grid">')
        out.append(f'<div class="box"><h3>Strengths</h3><ul class="tight">{"".join(f"<li>{e(s)}</li>" for s in rv.get("strengths") or []) or "<li>None noted</li>"}</ul></div>')
        out.append(f'<div class="box"><h3>Weaknesses</h3><ul class="tight">{"".join(f"<li>{e(s)}</li>" for s in rv.get("weaknesses") or []) or "<li>None noted</li>"}</ul></div>')
        out.append(f'<div class="box"><h3>Challenge data used: {e(DATA_LABEL.get(cd.get("used", ""), "?"))}</h3>{e(cd.get("detail", ""))}{_evid_html(r, cd.get("evidence"))}</div>')
        out.append(f'<div class="box"><h3>AI approach: {e(AI_LABEL.get(ai.get("type", ""), "?"))}</h3>{e(ai.get("detail", ""))}{_evid_html(r, ai.get("evidence"))}</div>')
        out.append(f'<div class="box"><h3>Built by the team vs generated</h3>{e(rv.get("team_vs_generated", ""))}</div>')
        out.append(f'<div class="box"><h3>Privacy and responsible AI</h3>{e(rv.get("responsible_ai", ""))}</div>')
        out.append("</div>")

    def tr(k, v):
        return f"<tr><td>{e(k)}</td><td>{v}</td></tr>" if v not in ("", None) else ""
    per_day = ", ".join(f"{k}: {v}" for k, v in (tl.get("per_day") or {}).items())
    langs = ", ".join(f"{k} ({v})" for k, v in (f.get("languages") or {}).items())
    nb = f.get("notebooks") or {}
    pack = r.get("pack") or {}
    rows = [
        tr("Architecture", e(rv.get("architecture", ""))),
        tr("Commits", e(f"{tl.get('commits', '')} by {tl.get('contributors', '')} contributor(s)"
                        + (f", {tl['bot_commits']} by bots" if tl.get("bot_commits") else "")) if tl else ""),
        tr("Commits per day", e(per_day)),
        tr("First and last commit", e(f"{_local(cfg, tl.get('first_commit'))} to {_local(cfg, tl.get('last_commit'))}") if tl.get("first_commit") else ""),
        tr("Team-written code", e(f"{f.get('team_code_lines', 0)} lines" + (f" ({langs})" if langs else "")) if f else ""),
        tr("Notebooks", e(f"{nb.get('notebooks')} notebook(s), {nb.get('executed_cells')} of {nb.get('code_cells')} code cells run") if nb.get("notebooks") else ""),
        tr("Partner tech", e(_partners(r))),
        tr("README quality", e(_readme_quality(rd)) if rd.get("exists") and rd.get("status") != "template" else ""),
        tr("Tests", e(f.get("tests")) if f.get("tests") else ""),
        tr("Demo link", e((f.get("demo") or {}).get("status", "")) if sub.get("demo") else ""),
        tr("Review coverage", e(f"{pack.get('files_included', 0)} file(s) read in full or in part, {pack.get('files_omitted', 0)} left out for size") if pack else ""),
        tr("Reviewed by", e(" ".join(x for x in [(r.get("review_meta") or {}).get("model", ""),
                                                  "(agent mode)" if (r.get("review_meta") or {}).get("deep") else ""] if x)) if rv else ""),
        tr("Snapshot", e(f"{snap.get('short', '')} on {snap.get('branch', '')}, {_local(cfg, snap.get('commit_date'))} (chosen by {snap.get('method', '')})") if snap.get("ok") else ""),
    ]
    if any(rows):
        out.append(f'<h3 class="sec">Facts from the repository</h3><table class="facts">{"".join(rows)}</table>')
    narrative = r.get("narrative")
    if narrative:
        out.append(f'<div class="box" style="background:#f0f7ff;border:1px solid #c3dafe"><h3>Building journey</h3>'
                   f'<p style="white-space:pre-line">{e(narrative)}</p></div>')
    if show_scores and rv.get("technical_score_rationale"):
        out.append(f'<p class="small"><b>Why this score:</b> {e(rv["technical_score_rationale"])}'
                   + (f' <b>Confidence note:</b> {e(rv["confidence_note"])}' if rv.get("confidence_note") else "") + "</p>")
    out.append("</section>")
    return "\n".join(out)


def render_html(cfg: dict, run: dict, results: dict, title_suffix: str = "") -> str:
    ev = cfg["event"]
    rows = []
    rubric = cfg.get("rubric") or {}
    criteria = rubric.get("criteria") or {}
    max_total = rubric.get("max_total", 100)
    show_scores = rubric.get("show_scores_to_jury", False)
    for slug, r in _order(results, cfg, by_score=False):
        sub, rv = r["submission"], r.get("review") or {}
        chal = (cfg["challenges"].get(sub.get("challenge")) or {}).get("name", "?")
        cs = rv.get("claims_summary") or {}
        n = len(rv.get("claims") or [])
        high = [x for x in _all_flags(r, dedupe=True) if x["severity"] == "high"]
        if show_scores:
            scores = rv.get("scores") or {}
            weighted = sum(scores.get(k, 0) * v.get("weight", 1) for k, v in criteria.items()) if scores and criteria else None
            score_display = f"{round(weighted)}/{max_total}" if weighted else (str(rv.get("technical_score", "–")))
        else:
            score_display = "✓" if rv else "–"
        verified = "{} of {}".format(cs.get("verified", 0), n) if n else ""
        high_html = "".join("<div>" + e(x["title"]) + "</div>" for x in high[:3])
        rows.append(f'<tr><td><a href="#{e(sub["slug"])}">{e(sub["project"])}</a></td><td>{e(chal)}</td>'
                    f'<td class="score"><b>{e(score_display)}</b></td>'
                    f'<td>{e(verified)}</td><td>{high_html}</td></tr>')
    deadline = _local(cfg, run.get("deadline", ""))
    head = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(ev['name'])} technical review{e(title_suffix)}</title><style>{CSS}</style></head><body>
<div class="cover"><div class="wrap"><div class="brand">{e(ev['name'])}</div><h1>Technical review for the jury{e(title_suffix)}</h1>
<p>Code frozen at the deadline ({e(deadline)}). Generated {e(_local(cfg, run.get('finished', '')))}.</p></div></div>
<div class="wrap"><div class="howto"><p><b>How to use this.</b> Each team's page shows what their code really does, which claims hold up, and three questions to ask.
This is advisory evidence, not a verdict: the score covers technical execution only, and every flag can have an innocent explanation. Ask the team before you decide.</p>
<p class="small">{"Scores are 1-10 per criterion, weighted to a total out of " + e(str(max_total)) + "." if show_scores else "AI scores are hidden until the jury has scored independently."} Links open the exact lines on GitHub as they were at the deadline.</p></div>
<table class="summary"><tr><th>Team</th><th>Challenge</th><th class="score">Score</th><th>Claims verified</th><th>High flags</th></tr>{''.join(rows)}</table>"""
    parts = []
    for _, r in _order(results, cfg, by_score=False):
        try:
            parts.append(_team_html(r, cfg))
        except Exception:  # noqa: BLE001  (one corrupt result must not kill the whole pack)
            slug = r.get("submission", {}).get("slug", "?")
            parts.append(f'<section class="team"><h2>{e(slug)}</h2>'
                         f'<p>Could not render this team. Check {slug}.json for errors.</p></section>')
    body = "\n".join(parts)
    return head + body + "</div></body></html>"


def write_outputs(cfg: dict, workdir: Path, outdir: Path, only: str = "") -> list:
    run, results = load_results(workdir)
    if not results:
        raise SystemExit("No results found. Run 'scan' first.")
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    if only:  # e.g. the finalists, for the grand jury
        keys = {k.strip().lower() for k in only.split(",") if k.strip()}
        subset = {s: r for s, r in results.items() if s in keys or r["submission"]["project"].lower() in keys}
        if not subset:
            raise SystemExit("None of those teams were found in the last scan.")
        p = outdir / "jury-pack-selection.html"
        p.write_text(render_html(cfg, run, subset, " · selected teams"), encoding="utf-8")
        return [p]
    page = outdir / "jury-pack-all.html"
    page.write_text(render_html(cfg, run, results), encoding="utf-8")
    written.append(page)
    for key, ch in cfg["challenges"].items():
        subset = {s: r for s, r in results.items() if r["submission"].get("challenge") == key}
        if subset:
            p = outdir / f"jury-pack-{key}.html"
            p.write_text(render_html(cfg, run, subset, f" · {ch.get('name', key)}"), encoding="utf-8")
            written.append(p)
    unknown = {s: r for s, r in results.items() if not r["submission"].get("challenge")}
    if unknown:
        p = outdir / "jury-pack-unassigned.html"
        p.write_text(render_html(cfg, run, unknown, " · challenge not recognised"), encoding="utf-8")
        written.append(p)
    for slug, r in results.items():
        write_json(outdir / "teams" / f"{slug}.json", r)
    try:  # last, so a spreadsheet problem never costs the jury packs
        written.insert(0, write_xlsx(outdir / "scorecard.xlsx", cfg, run, results))
    except Exception as exc:  # noqa: BLE001
        log(f"Could not write scorecard.xlsx ({type(exc).__name__}: {exc}); the HTML packs are complete")
    return written
