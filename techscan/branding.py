"""Event branding for the jury pack, scorecard and challenge reports.

Configure under `branding:`. Every value is optional; the defaults are the Hackathon for Good look.
Logo and font files are embedded as data URIs so a jury pack still renders offline or when mailed.
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

DEFAULTS = {
    "primary": "#1962A1",          # cover, links, score badge, bars, section headings, XLSX header
    "accent": "#FCCD02",           # highlight borders and the event name on the cover
    "background": "#f4f6f9",       # page background (team cards stay white)
    "tint": "#eef3f9",             # table headers and chips
    "highlight": "#fffbe6",        # background of the "questions to ask" box
    "cover_background": "",        # default: primary
    "cover_text": "#ffffff",
    "cover_accent": "",            # default: accent
    "font_family": "Calibri,'Segoe UI',Arial,sans-serif",
    "font_files": {},              # {400: path/to/Regular.ttf, 700: path/to/Bold.ttf} for font_family's first name
    "logos": [],                   # image paths shown top right on the cover
    "footer": "",                  # one line at the bottom of each jury pack
    "no_em_dashes": False,         # house style: replace em dashes in all output text
}
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2"}


def brand(cfg: dict) -> dict:
    """The resolved branding: defaults filled in, cover colours derived."""
    b = dict(DEFAULTS)
    b.update({k: v for k, v in (cfg.get("branding") or {}).items() if v not in (None, "")})
    b["cover_background"] = b["cover_background"] or b["primary"]
    b["cover_accent"] = b["cover_accent"] or b["accent"]
    return b


def resolve_paths(branding: dict, base: Path) -> None:
    """Make logo and font paths absolute (relative to the config file). Called by load_config."""
    branding["logos"] = [str((base / p).resolve()) for p in (branding.get("logos") or [])]
    branding["font_files"] = {int(w): str((base / p).resolve()) for w, p in (branding.get("font_files") or {}).items()}


def check(branding: dict) -> list:
    """Problems for preflight: bad colours, missing files, poor contrast."""
    b = dict(DEFAULTS) | {k: v for k, v in (branding or {}).items() if v not in (None, "")}
    out = []
    for k in ("primary", "accent", "background", "tint", "highlight", "cover_background", "cover_text", "cover_accent"):
        if b.get(k) and not HEX.match(str(b[k])):
            out.append(f"branding.{k} must be a hex colour like #2C55A1, got {b[k]!r}")
    if out:
        return out
    for p in list(b.get("logos") or []) + list((b.get("font_files") or {}).values()):
        if not Path(p).exists():
            out.append(f"branding file not found (it will be left out): {p}")
    b["cover_background"] = b["cover_background"] or b["primary"]
    b["cover_accent"] = b["cover_accent"] or b["accent"]
    pairs = [("cover text on cover background", b["cover_text"], b["cover_background"], 4.5),
             ("white score badge text on primary", "#ffffff", b["primary"], 4.5),
             ("links (primary) on white", b["primary"], "#ffffff", 4.5),
             ("event name (cover_accent) on cover background", b["cover_accent"], b["cover_background"], 3.0)]
    for label, fg, bg, need in pairs:
        ratio = contrast(fg, bg)
        if ratio < need:
            out.append(f"branding contrast {ratio:.1f}:1 for {label} (aim for {need}:1 or more)")
    return out


def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chans = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        chans.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = chans
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _data_uri(path: str, mime: str = "") -> str:
    p = Path(path)
    if not p.exists():
        return ""
    mime = mime or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def theme_css(b: dict) -> str:
    """:root variables plus embedded @font-face rules, placed before the pack's stylesheet."""
    family = (b["font_family"].split(",")[0]).strip().strip("'\"")
    faces = []
    for weight, path in sorted((b.get("font_files") or {}).items()):
        uri = _data_uri(path, FONT_TYPES.get(Path(path).suffix.lower(), ""))
        if uri:
            faces.append(f"@font-face{{font-family:'{family}';font-weight:{int(weight)};font-style:normal;"
                         f"font-display:swap;src:url({uri})}}")
    root = (f":root{{--primary:{b['primary']};--accent:{b['accent']};--bg:{b['background']};--tint:{b['tint']};"
            f"--highlight:{b['highlight']};--cover-bg:{b['cover_background']};--cover-text:{b['cover_text']};--cover-accent:{b['cover_accent']};"
            f"--font:{b['font_family']}}}")
    return "\n".join(faces + [root])


def logos_html(b: dict) -> str:
    imgs = [f'<img src="{uri}" alt="">' for uri in (_data_uri(p) for p in b.get("logos") or []) if uri]
    return f'<div class="logos">{"".join(imgs)}</div>' if imgs else ""


def dedash(text: str) -> str:
    """House style without em dashes: ' — ' becomes ' - ', a bare em dash becomes '-'."""
    return text.replace(" — ", " - ").replace("—", "-") if text else text


def xlsx_hex(colour: str) -> str:
    """'#2C55A1' -> '2C55A1' for openpyxl."""
    h = colour.lstrip("#")
    return "".join(c * 2 for c in h).upper() if len(h) == 3 else h.upper()
