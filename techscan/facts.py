"""Deterministic checks. No AI here: these cannot be talked out of their result."""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from datetime import timedelta
from pathlib import Path

from .util import mask_secret, norm_hash, parse_dt, read_text, truncate

# ------------------------------------------------------------------ file kinds

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", ".env.d", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "dist", "build", ".next", ".nuxt", ".svelte-kit", ".output", "out", "coverage", ".cache",
    ".parcel-cache", "target", "vendor", ".idea", ".vscode", ".gradle", "Pods", "bower_components",
    "site-packages", ".terraform", ".turbo", ".vercel", ".netlify", ".expo", ".dart_tool", ".ipynb_checkpoints",
}
DEPENDENCY_DIRS = {"node_modules", ".venv", "venv", "site-packages", "bower_components", "vendor"}
LOCK_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock", "poetry.lock", "pipfile.lock",
    "uv.lock", "composer.lock", "gemfile.lock", "cargo.lock", "go.sum", "pdm.lock", "conda-lock.yml",
}
CODE_LANG = {
    ".py": "Python", ".ipynb": "Python (notebook)", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue", ".svelte": "Svelte",
    ".astro": "Astro", ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".sass": "CSS",
    ".less": "CSS", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C",
    ".hpp": "C++", ".swift": "Swift", ".dart": "Dart", ".r": "R", ".jl": "Julia", ".scala": "Scala",
    ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".sol": "Solidity", ".lua": "Lua",
    ".ex": "Elixir", ".exs": "Elixir", ".graphql": "GraphQL", ".gql": "GraphQL", ".prisma": "Prisma",
    ".tf": "Terraform", ".m": "MATLAB", ".rmd": "R Markdown", ".qmd": "Quarto",
}
DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXT = {".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env", ".lock"}
CONFIG_NAMES = {
    "dockerfile", "makefile", "procfile", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pipfile",
    "environment.yml", "package.json", "tsconfig.json", "docker-compose.yml", "docker-compose.yaml",
    "compose.yaml", "vercel.json", "netlify.toml", "app.json", "firebase.json", ".env.example", ".env.sample",
}
DATA_EXT = {
    ".csv", ".tsv", ".parquet", ".feather", ".xlsx", ".xls", ".geojson", ".shp", ".dbf", ".gpkg", ".jsonl",
    ".ndjson", ".npy", ".npz", ".pkl", ".pickle", ".h5", ".hdf5", ".db", ".sqlite", ".sav", ".dta", ".arrow",
    ".tif", ".tiff", ".nc", ".xml", ".kml",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svgz", ".pdf", ".zip", ".gz", ".tar", ".7z",
    ".rar", ".mp4", ".mov", ".avi", ".mp3", ".wav", ".ogg", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".exe",
    ".dll", ".so", ".dylib", ".bin", ".pt", ".pth", ".onnx", ".safetensors", ".ckpt", ".joblib", ".docx",
    ".pptx", ".key", ".psd", ".fig", ".sketch", ".jar", ".class", ".pyc", ".whl", ".lockb", ".mp4",
}
GENERATOR_SCAFFOLD_RE = re.compile(
    r"^(src/)?(hooks/use-toast\.ts|hooks/use-mobile\.tsx|lib/utils\.ts|main\.tsx|vite-env\.d\.ts|pages/NotFound\.tsx|"
    r"App\.css|index\.css|setupTests\.[jt]s|reportWebVitals\.[jt]s|App\.test\.[jt]sx?)$|"
    r"^(tailwind\.config\.(ts|js|cjs)|postcss\.config\.(js|cjs|mjs)|eslint\.config\.(js|mjs)|vite\.config\.(ts|js)|"
    r"tsconfig(\.app|\.node)?\.json|components\.json)$")
SCAFFOLD_RE = re.compile(r"(^|/)components/ui/[^/]+\.(tsx|jsx|ts|js|vue|svelte)$|(^|/)integrations/supabase/types\.ts$|(^|/)database\.types\.ts$|\.d\.ts$")
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.(js|jsx|ts|tsx)$")
ENTRY_RE = re.compile(r"(^|/)(main|app|server|index|api|cli|run|pipeline|agent|model|train|predict|streamlit_app)\.(py|js|ts|tsx|jsx|mjs)$|(^|/)App\.(tsx|jsx|vue|svelte)$|(^|/)(pages|app|api|routes|src)/")

NOTABLE_LIBS = OrderedDict([
    ("LLM APIs", ["openai", "anthropic", "@anthropic-ai/sdk", "google-generativeai", "@google/generative-ai",
                  "google-genai", "@google/genai", "mistralai", "@mistralai/mistralai", "groq", "groq-sdk", "cohere",
                  "together", "ollama", "litellm", "replicate", "huggingface_hub", "@huggingface/inference", "ai"]),
    ("Agent and RAG frameworks", ["langchain", "langchain-core", "langchain-openai", "langchain-community",
                                  "@langchain/core", "langgraph", "@langchain/langgraph", "llama-index", "llama_index",
                                  "llamaindex", "crewai", "autogen", "pyautogen", "smolagents", "haystack-ai", "dspy",
                                  "dspy-ai", "semantic-kernel", "mastra", "@mastra/core", "pydantic-ai"]),
    ("Machine learning", ["scikit-learn", "sklearn", "torch", "tensorflow", "keras", "xgboost", "lightgbm",
                          "catboost", "transformers", "sentence-transformers", "spacy", "nltk", "prophet",
                          "statsmodels", "pytorch-lightning", "datasets", "peft", "unsloth"]),
    ("Vector stores", ["chromadb", "pinecone", "pinecone-client", "@pinecone-database/pinecone", "faiss-cpu",
                       "faiss-gpu", "qdrant-client", "weaviate-client", "pgvector", "lancedb"]),
    ("Data", ["pandas", "polars", "numpy", "geopandas", "shapely", "duckdb", "pyarrow", "folium", "plotly",
              "matplotlib", "seaborn", "altair", "leaflet", "react-leaflet", "mapbox-gl", "d3", "recharts"]),
    ("App frameworks", ["streamlit", "gradio", "fastapi", "flask", "django", "express", "next", "react", "vue",
                        "svelte", "@sveltejs/kit", "nuxt", "vite", "dash", "chainlit", "panel"]),
    ("Backend services", ["@supabase/supabase-js", "supabase", "firebase", "firebase-admin", "mongodb", "pymongo",
                          "prisma", "@prisma/client", "redis", "psycopg2", "psycopg2-binary", "sqlalchemy"]),
    ("Scraping", ["requests", "httpx", "beautifulsoup4", "bs4", "scrapy", "playwright", "selenium", "puppeteer",
                  "cheerio", "crawlee"]),
])

# ------------------------------------------------------------------ secrets

SECRET_PATTERNS = [
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("Google OAuth token", re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("Groq API key", re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b")),
    ("Maritime API key", re.compile(r"\bmk_[A-Za-z0-9_\-]{24,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("JWT (service role)", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("Hardcoded secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|secret|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd)"
        r"\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']")),
]
PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,8000}?-----END [A-Z ]*PRIVATE KEY-----")
PLACEHOLDER_RE = re.compile(r"(?i)your|xxx|example|placeholder|changeme|dummy|sample|insert|replace|<|>|\*\*\*|\.\.\.|\$\{|process\.env|os\.environ|getenv|import\.meta")
ENV_FILE_RE = re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$")
ENV_SAFE_RE = re.compile(r"(?i)\.(example|sample|template|dist|defaults?)$")

# ------------------------------------------------------------------ AI-directed text

JUDGE_PATTERNS = [
    re.compile(r"(?i)\b(ai|llm|gpt|claude|chatgpt|gemini|language model|model)s?\b.{0,30}\b(judge|judges|reviewer|reviewers|grader|graders|evaluator|evaluators|assessor)s?\b"),
    re.compile(r"(?i)\bnote to (the )?(ai|llm|judges?|jury|reviewers?|graders?|evaluators?)\b"),
    re.compile(r"(?i)\b(judges?|reviewers?|graders?|evaluators?|jury)\b.{0,50}\b(score|rate|grade|rank|give)\b.{0,50}\b(highest|maximum|max|perfect|full marks|top|10\s*/\s*10|5\s*/\s*5|100\s*%|winner)\b"),
    re.compile(r"(?i)\b(give|award|assign|rate)\b.{0,30}\b(this|our|the)\s+(project|team|submission|repo(sitory)?)\b.{0,40}\b(highest|maximum|max|perfect|top|full|5/5|10/10)\b"),
]
GENERIC_INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|system)\b.{0,25}\b(instructions?|prompts?|rules?|guidelines?)\b"),
    re.compile(r"(?i)<\s*/?\s*(system|instructions?|admin)\s*>"),
    re.compile(r"(?i)\bnote to (the )?(ai|llm|model|assistant)\b"),
]
HIDDEN_CHARS_RE = re.compile("[​‌‍⁠-⁤‪-‮⁦-⁩]")
MD_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.S)

RUN_HINT_RE = re.compile(r"(?i)\b(pip3? install|npm (install|i|run|start)|yarn|pnpm|bun (install|run)|python3? [\w./-]+|streamlit run|uvicorn|flask run|docker(-compose)? (run|up|build)|poetry (install|run)|uv (run|sync)|jupyter|conda (env|install)|make\b|go run|cargo run|npx )")
MOCK_DISCLOSURE_RE = re.compile(r"(?i)\b(mock(ed|s|up)?|fake|dummy|hard-?coded|simulated|placeholder|sample data|synthetic data|stub(bed)?|not (yet )?(implemented|connected|real)|proof of concept|work in progress|wip|todo)\b")
BOT_RE = re.compile(r"(?i)\[bot\]|lovable|gpt-engineer|noreply@github\.com.*actions|dependabot")


class Inventory:
    def __init__(self, root: Path, extra_ignore=()):
        self.root = Path(root)
        self.files = []          # dicts: path, size, ext, kind, lang, binary, lines, hash
        self.symlinks = []
        self.committed_dirs = set()
        self.ignore = IGNORE_DIRS | set(extra_ignore or [])
        self._walk()

    def _walk(self):
        root = self.root
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            keep = []
            for d in dirnames:
                full = Path(dirpath) / d
                if os.path.islink(full):
                    self.symlinks.append(f"{rel_dir}/{d}".lstrip("./"))
                    continue
                if d in self.ignore:
                    if d in DEPENDENCY_DIRS:
                        self.committed_dirs.add(d)
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(root).as_posix()
                if os.path.islink(full):  # never follow: could point outside the repo
                    self.symlinks.append(rel)
                    continue
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                self.files.append(self._describe(full, rel, size))

    def _describe(self, full: Path, rel: str, size: int) -> dict:
        name = rel.rsplit("/", 1)[-1]
        low = name.lower()
        ext = os.path.splitext(low)[1]
        info = {"path": rel, "size": size, "ext": ext, "lang": "", "binary": False, "lines": 0,
                "hash": "", "kind": "other", "status": "team"}
        if low in LOCK_FILES:
            info["kind"] = "lock"
        elif ext in BINARY_EXT:
            info["kind"], info["binary"] = "binary", True
        elif ext in DATA_EXT or (ext == ".json" and size > 300_000):
            info["kind"] = "data"
        elif ext == ".ipynb":
            info["kind"], info["lang"] = "notebook", CODE_LANG[".ipynb"]
        elif ext in CODE_LANG or low in ("dockerfile", "makefile"):
            info["kind"], info["lang"] = "code", CODE_LANG.get(ext, "Config")
            if low.endswith((".min.js", ".min.css", ".map")) or ext == ".map":
                info["kind"] = "minified"
        elif low in CONFIG_NAMES or low.startswith("requirements") or low.startswith(".env"):
            info["kind"] = "config"
        elif ext in DOC_EXT:
            info["kind"] = "doc"
        elif ext in CONFIG_EXT:
            info["kind"] = "config"
        if info["kind"] not in ("binary",) and size <= 5_000_000:
            try:
                with open(full, "rb") as f:
                    head = f.read(8192)
                utf16 = head[:2] in (b"\xff\xfe", b"\xfe\xff")
                if b"\x00" in head and not utf16:
                    info["kind"], info["binary"] = "binary", True
                elif info["kind"] not in ("data",) or size <= 2_000_000:
                    text = read_text(full)
                    info["lines"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
                    info["hash"] = norm_hash(text)
                    if info["kind"] == "code" and info["lines"] and size / max(info["lines"], 1) > 400:
                        info["kind"] = "minified"
            except OSError:
                pass
        if SCAFFOLD_RE.search(rel) and info["kind"] == "code":
            info["status"] = "scaffold"
        return info

    def add_listed(self, large=(), symlinks=()) -> None:
        """Files too big to extract (listed with their size only) and symlinks (never followed)."""
        for item in large or []:
            rel = item["path"]
            ext = os.path.splitext(rel.rsplit("/", 1)[-1].lower())[1]
            kind = "data" if (ext in DATA_EXT or ext == ".json") else ("binary" if ext in BINARY_EXT else "other")
            self.files.append({"path": rel, "size": item["size"], "ext": ext, "lang": "", "binary": kind == "binary",
                               "lines": 0, "hash": "", "kind": kind, "status": "team", "not_extracted": True})
        self.symlinks.extend(symlinks or [])

    def by_path(self):
        return {f["path"]: f for f in self.files}

    def text_files(self, kinds=("code", "doc", "config", "notebook")):
        for f in self.files:
            if f["kind"] in kinds and not f["binary"] and f["size"] <= 2_000_000:
                yield f

    def read(self, rel: str) -> str:
        return read_text(self.root / rel)


def notebook_to_text(raw: str) -> tuple:
    """Code and markdown cells with short output summaries. Returns (text, stats)."""
    stats = {"cells": 0, "code_cells": 0, "executed": 0, "errors": 0, "code_lines": 0}
    try:
        nb = json.loads(raw)
    except ValueError:
        return raw[:20000], stats
    if not isinstance(nb, dict):
        return raw[:20000], stats
    cells = nb.get("cells")
    if cells is None:
        cells = [c for ws in (nb.get("worksheets") or []) if isinstance(ws, dict)
                 for c in (ws.get("cells") or [])]
    if not isinstance(cells, list):
        return raw[:20000], stats
    out = []
    for i, c in enumerate(cells, 1):
        if not isinstance(c, dict):
            continue
        ctype = str(c.get("cell_type", ""))
        src = c.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        stats["cells"] += 1
        if ctype == "code":
            stats["code_cells"] += 1
            stats["code_lines"] += len([ln for ln in src.splitlines() if ln.strip()])
            ec = c.get("execution_count")
            outputs = c.get("outputs") or []
            if not isinstance(outputs, list):
                outputs = []
            if ec is not None or outputs:
                stats["executed"] += 1
            out.append(f"# ---- cell {i} [code] exec={ec} ----")
            out.append(src)
            for o in outputs[:3]:
                if not isinstance(o, dict):
                    continue
                t = o.get("output_type")
                if t == "stream":
                    txt = "".join(o.get("text", "")) if isinstance(o.get("text"), list) else str(o.get("text", ""))
                elif t in ("execute_result", "display_data"):
                    data = o.get("data") if isinstance(o.get("data"), dict) else {}
                    if "text/plain" in data:
                        tp = data["text/plain"]
                        txt = "".join(tp) if isinstance(tp, list) else str(tp)
                    elif any(k.startswith("image/") for k in data):
                        txt = "[image output]"
                    else:
                        txt = "[rich output]"
                elif t == "error":
                    stats["errors"] += 1
                    txt = f"ERROR {o.get('ename', '')}: {o.get('evalue', '')}"
                else:
                    continue
                txt = truncate(txt.strip(), 300).replace("\n", "\n# ")
                out.append(f"# [output] {txt}")
        else:
            out.append(f"# ---- cell {i} [{ctype or 'markdown'}] ----")
            out.append(src)
    return "\n".join(out), stats


# ------------------------------------------------------------------ dependencies

def parse_dependencies(inv: Inventory) -> dict:
    names = set()
    for f in inv.files:
        p, low = f["path"], f["path"].rsplit("/", 1)[-1].lower()
        if f["binary"] or f["size"] > 1_000_000:
            continue
        try:
            if low == "package.json":
                data = json.loads(inv.read(p))
                if not isinstance(data, dict):
                    continue
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    deps = data.get(key)
                    if isinstance(deps, dict):
                        names.update(str(k) for k in deps.keys())
            elif low.startswith("requirements") and low.endswith(".txt"):
                for line in inv.read(p).splitlines():
                    m = re.match(r"\s*([A-Za-z0-9_.\-\[\]]+)", line)
                    if m and not line.strip().startswith(("#", "-")):
                        names.add(re.sub(r"\[.*\]", "", m.group(1)).lower())
            elif low in ("pyproject.toml", "pipfile"):
                text = inv.read(p)
                try:
                    import tomllib  # Python 3.11+
                    data = tomllib.loads(text)
                    proj = data.get("project") if isinstance(data.get("project"), dict) else {}
                    for dep in proj.get("dependencies") or []:
                        names.add(re.split(r"[<>=~!;\[ ]", str(dep), 1)[0].lower())
                    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
                    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
                    for block in (poetry.get("dependencies"), data.get("packages")):
                        if isinstance(block, dict):
                            names.update(str(k).lower() for k in block.keys())
                except Exception:
                    names.update(m.lower() for m in re.findall(r"^\s*\"?([A-Za-z0-9_.\-]+)\"?\s*[<>=~]", text, re.M))
            elif low in ("environment.yml", "environment.yaml"):
                for m in re.findall(r"^\s*-\s*([A-Za-z0-9_.\-]+)", inv.read(p), re.M):
                    names.add(m.lower())
        except (ValueError, OSError, AttributeError, TypeError):
            continue
    names.discard("python")
    found = OrderedDict()
    lowered = {n.lower() for n in names}
    for cat, libs in NOTABLE_LIBS.items():
        hits = [lib for lib in libs if lib.lower() in lowered or any(n.startswith(lib + "/") for n in lowered if lib.startswith("@"))]
        if cat == "LLM APIs" and "ai" in hits and not any(n.startswith("@ai-sdk/") for n in lowered):
            hits.remove("ai")
        hits += sorted(n for n in lowered if n.startswith("@ai-sdk/")) if cat == "LLM APIs" else []
        if hits:
            found[cat] = sorted(set(hits))
    return {"count": len(names), "notable": found}


# ------------------------------------------------------------------ main fact gathering

def detect_lovable(inv: Inventory) -> dict:
    res = {"lovable": False, "shadcn": False, "evidence": []}
    for f in inv.files:
        low = f["path"].lower()
        name = low.rsplit("/", 1)[-1]
        if f["binary"] or f["size"] > 500_000:
            continue
        if name == "package.json" or name.startswith("vite.config") or name == "index.html" or name == "readme.md":
            text = inv.read(f["path"])
            if re.search(r"lovable-tagger|lovable\.dev/projects|gptengineer|lovableproject\.com", text, re.I):
                res["lovable"] = True
                res["evidence"].append(f["path"])
        if name == "components.json":
            if "ui.shadcn.com" in inv.read(f["path"]):
                res["shadcn"] = True
    return res


def scan_secrets(inv: Inventory, history: str) -> tuple:
    """Returns (findings, raw_values). Raw values never leave memory."""
    findings, raw_values, seen = [], set(), set()

    def check(kind, value):
        if kind == "JWT (service role)":
            try:
                payload = value.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "replace"))
                return claims.get("role") == "service_role"
            except Exception:
                return False
        v = value.strip()
        if PLACEHOLDER_RE.search(v) or len(set(v)) < 6:
            return False
        return True

    def scan_line(line, path, lineno, where, commit=""):
        for kind, rx in SECRET_PATTERNS:
            for m in rx.finditer(line):
                value = m.group(1) if (kind == "Hardcoded secret" and m.groups()) else m.group(0)
                if kind == "OpenAI API key" and value.startswith("sk-ant-"):
                    continue
                if not check(kind, value):
                    continue
                key = (kind, value)
                if key in seen:
                    continue
                seen.add(key)
                raw_values.add(value)
                findings.append({"type": kind, "file": path, "line": lineno, "where": where,
                                 "commit": commit[:8], "value": mask_secret(value),
                                 "severity": "medium" if kind == "Hardcoded secret" else "high"})

    for f in inv.text_files(kinds=("code", "doc", "config", "notebook", "data")):
        try:
            text = inv.read(f["path"])
        except OSError:
            continue
        for block in PRIVATE_KEY_BLOCK.findall(text):  # redact the whole key, not just its first line
            raw_values.add(block)
        if f["kind"] == "data":
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if len(line) > 5000:
                line = line[:5000]
            scan_line(line, f["path"], i, "current files")
    commit, path = "", ""
    for line in (history or "").splitlines():
        if line.startswith("\x1eCOMMIT ") or line.startswith("COMMIT "):
            commit = line.split()[-1]
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            if ENV_SAFE_RE.search(path) or path.rsplit("/", 1)[-1].lower() in LOCK_FILES:
                continue
            scan_line(line[1:5001], path, None, "git history", commit)
    for f in inv.files:
        if ENV_FILE_RE.search(f["path"]) and not ENV_SAFE_RE.search(f["path"]):
            findings.append({"type": "Environment file committed", "file": f["path"], "line": None,
                             "where": "current files", "commit": "", "value": "", "severity": "medium"})
    return findings[:40], raw_values


def scan_ai_directed(inv: Inventory) -> list:
    hits = []
    for f in inv.text_files(kinds=("code", "doc", "config", "notebook")):
        try:
            text = inv.read(f["path"])
        except OSError:
            continue
        is_doc = f["kind"] == "doc" or f["path"].lower().endswith((".html", ".htm"))
        if f["kind"] == "notebook":
            text, _ = notebook_to_text(text)
        for i, line in enumerate(text.splitlines(), 1):
            if len(hits) >= 25:
                return hits
            snippet = truncate(line.strip(), 160)
            if HIDDEN_CHARS_RE.search(line):
                hits.append({"file": f["path"], "line": i, "kind": "hidden characters", "strength": "possible",
                             "snippet": HIDDEN_CHARS_RE.sub("[?]", snippet)})
                continue
            if any(p.search(line) for p in JUDGE_PATTERNS):
                hits.append({"file": f["path"], "line": i, "kind": "text aimed at judges or AI reviewers",
                             "strength": "likely", "snippet": snippet})
            elif is_doc and any(p.search(line) for p in GENERIC_INJECTION_PATTERNS):
                hits.append({"file": f["path"], "line": i, "kind": "instruction-like text", "strength": "possible",
                             "snippet": snippet})
        if is_doc:
            for m in MD_COMMENT_RE.finditer(text):
                body = m.group(1)
                if re.search(r"(?i)\b(ai|llm|model|judge|jury|score|grade|review|instruction)", body):
                    line = text[: m.start()].count("\n") + 1
                    hits.append({"file": f["path"], "line": line, "kind": "hidden comment mentioning judging or AI",
                                 "strength": "possible", "snippet": truncate(" ".join(body.split()), 160)})
    return hits


def detect_partners(inv: Inventory, partners: dict) -> dict:
    compiled = {k: [re.compile(p, re.I | re.M) for p in v.get("patterns", [])] for k, v in partners.items()}
    res = {}
    for key, pats in compiled.items():
        code_hits, doc_hits = [], []
        for f in inv.text_files(kinds=("code", "doc", "config", "notebook")):
            if f["path"].rsplit("/", 1)[-1].lower() in LOCK_FILES:
                continue
            if f["status"] in ("template", "boilerplate") or (f["status"] == "scaffold" and key != "lovable"):
                continue  # the organisers' starter files or generated code say nothing about this team
            try:
                text = inv.read(f["path"])
            except OSError:
                continue
            if not any(p.search(text) for p in pats):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if any(p.search(line) for p in pats):
                    target = doc_hits if f["kind"] == "doc" else code_hits
                    target.append({"file": f["path"], "line": i, "snippet": truncate(line.strip(), 120)})
                    if len(target) >= 5:
                        break
        if code_hits or doc_hits:
            res[key] = {
                "label": partners[key].get("label", key),
                "level": "used in code" if code_hits else "mentioned in docs only",
                "evidence": (code_hits + doc_hits)[:5],
            }
    return res


def analyze_readme(inv: Inventory) -> dict:
    cands = [f for f in inv.files if re.match(r"(?i)^readme(\.(md|rst|txt|markdown))?$", f["path"])]
    if not cands:
        cands = [f for f in inv.files if re.search(r"(?i)(^|/)readme(\.(md|rst|txt))?$", f["path"])]
    if not cands:
        return {"exists": False}
    f = sorted(cands, key=lambda x: x["path"].count("/"))[0]
    try:
        text = inv.read(f["path"])
    except OSError:
        return {"exists": True, "path": f["path"], "chars": 0, "status": f["status"],
                "has_run_instructions": False, "mock_disclosures": []}
    disclosures = []
    for i, line in enumerate(text.splitlines(), 1):
        if MOCK_DISCLOSURE_RE.search(line) and len(disclosures) < 8:
            disclosures.append({"line": i, "snippet": truncate(line.strip(), 140)})
    blocks = re.findall(r"```.*?```", text, re.S)
    has_run = bool(RUN_HINT_RE.search("\n".join(blocks))) or bool(RUN_HINT_RE.search(text) and len(text) > 400)
    headings = re.findall(r"(?m)^#{1,3}\s+(.+)", text)
    heading_text = " ".join(headings).lower()
    has_arch = bool(re.search(r"(?i)(architect|stack|tech|how it works|system design|overview|components|structure)", heading_text)
                    or re.search(r"(?i)(architect|stack|tech|how it works|system design)\b", text[:3000]))
    has_screenshots = bool(re.search(r"!\[.*?\]\(.*?\)|<img\s|screenshot|screen shot|demo\.gif|demo\.png|preview", text, re.I))
    has_env = bool(re.search(r"(?i)\.env|environment variable|API.?KEY|config(uration)?", text[:4000]))
    has_license = bool(re.search(r"(?im)^#{1,3}\s+licen[sc]e|^licen[sc]e", text) or
                       any(f["path"].lower().startswith("license") for f in inv.files))
    quality = sum([
        bool(text.strip()),                # exists and non-empty
        len(text) >= 300,                   # at least a few paragraphs
        len(headings) >= 3,                 # structured with sections
        has_run,                            # how to run it
        has_arch,                           # architecture or tech stack section
        has_screenshots,                    # visual evidence
    ])
    return {
        "exists": True,
        "path": f["path"],
        "chars": len(text),
        "status": f["status"],
        "has_run_instructions": has_run,
        "has_architecture": has_arch,
        "has_screenshots": has_screenshots,
        "has_env_setup": has_env,
        "has_license": has_license,
        "headings": len(headings),
        "quality": quality,       # 0-6 score
        "mock_disclosures": disclosures,
    }


def check_url(url: str, timeout=10) -> dict:
    if not url:
        return {"url": "", "status": "none"}
    if not re.match(r"(?i)^https?://[^/\s]+", url):
        return {"url": url, "status": "error", "code": None, "detail": "Not a valid web link"}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hfg-techscan link check)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"url": url, "status": "ok", "code": r.status, "final_url": r.geturl()}
    except urllib.error.HTTPError as e:
        return {"url": url, "status": "error", "code": e.code, "detail": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001  (DNS, TLS, timeouts: all "not reachable")
        return {"url": url, "status": "error", "code": None, "detail": truncate(str(e), 120)}


def _codeish(path: str) -> float:
    low = path.lower()
    ext = os.path.splitext(low)[1]
    if low.rsplit("/", 1)[-1] in LOCK_FILES or SCAFFOLD_RE.search(path):
        return 0.0
    if any(f"/{d}/" in f"/{low}" for d in IGNORE_DIRS):
        return 0.0
    if ext == ".ipynb":
        return 0.25
    return 1.0 if ext in CODE_LANG else 0.0


def analyze_timeline(commits: list, start, deadline, template_paths: set, grace_hours: float,
                     big_lines: int, big_share: float, template_shas=frozenset()) -> dict:
    res = {"commits": len(commits), "contributors": 0, "bot_commits": 0, "first_commit": "", "last_commit": "",
           "template_root": False, "template_commits_skipped": 0, "pre_event_commits": [], "big_drops": [],
           "per_day": {}, "authors": [], "team_code_lines_added": 0}
    if not commits:
        return res
    # commits that came with the organisers' template (forked or cloned with history) are not the team's
    own = [c for c in commits if c["sha"] not in template_shas]
    res["template_commits_skipped"] = len(commits) - len(own)
    if not own:
        return res
    root = own[0]
    root_paths = [f["path"] for f in root["files"]]
    root_code = sum((f["added"] or 0) * _codeish(f["path"]) for f in root["files"]
                    if f["path"] not in template_paths)
    if not root["parents"] or res["template_commits_skipped"]:
        if template_paths and root_paths:
            share = sum(1 for p in root_paths if p in template_paths) / len(root_paths)
            res["template_root"] = share >= 0.7 and root_code < 200
        elif root["subject"].strip().lower() == "initial commit" and len(root_paths) <= 15 and root_code < 200:
            res["template_root"] = True  # GitHub's "Use this template" commit

    pseud, counts = {}, Counter()
    tz = start.tzinfo
    team_commits = own[1:] if res["template_root"] else own
    total = 0.0
    sizes = []
    for c in team_commits:
        bot = bool(BOT_RE.search(f"{c['author']} {c['email']}"))
        if bot:
            res["bot_commits"] += 1
            label = f"bot ({truncate(c['author'], 30)})"
        else:
            if c["email"] not in pseud:
                pseud[c["email"]] = f"Contributor {len(pseud) + 1}"
            label = pseud[c["email"]]
        counts[label] += 1
        try:
            ad = parse_dt(c["author_date"])
        except ValueError:
            continue
        day = ad.astimezone(tz).strftime("%a %d %b")
        res["per_day"][day] = res["per_day"].get(day, 0) + 1
        if ad < start - timedelta(hours=grace_hours):
            res["pre_event_commits"].append({"sha": c["sha"][:8], "date": c["author_date"], "author": label,
                                             "subject": c["subject"]})
        lines = sum((f["added"] or 0) * _codeish(f["path"]) for f in c["files"])
        total += lines
        sizes.append((lines, c, label))
    res["team_code_lines_added"] = int(total)
    for lines, c, label in sorted(sizes, key=lambda x: -x[0])[:3]:
        if lines >= big_lines and total and lines / total >= big_share:
            top = sorted(c["files"], key=lambda f: -((f["added"] or 0) * _codeish(f["path"])))[:6]
            res["big_drops"].append({
                "sha": c["sha"][:8], "date": c["author_date"], "author": label, "subject": c["subject"],
                "code_lines": int(lines), "share": round(lines / total, 2),
                "files": [f"{f['path']} (+{f['added']})" for f in top if f["added"]],
            })
    res["contributors"] = len(pseud)
    res["authors"] = [{"name": k, "commits": v} for k, v in counts.most_common()]
    res["first_commit"] = team_commits[0]["author_date"] if team_commits else ""
    res["last_commit"] = own[-1]["commit_date"]
    res["commits"] = len(own)
    res["pre_event_commits"] = res["pre_event_commits"][:15]
    return res


def _submodules(inv: Inventory) -> list:
    if getattr(inv, "submodules", None):
        return inv.submodules[:10]
    for f in inv.files:
        if f["path"] == ".gitmodules":
            try:
                return re.findall(r"(?m)^\s*path\s*=\s*(.+?)\s*$", inv.read(f["path"]))[:10]
            except OSError:
                return []
    return []


def challenge_data_signals(inv: Inventory, hints: list) -> dict:
    if not hints:
        return {"hints": [], "found": []}
    found = []
    for f in inv.files:
        if f["status"] == "template":
            continue  # the organisers' copy of the data proves nothing
        low = f["path"].lower()
        for h in hints:
            if h in low:
                found.append({"file": f["path"], "match": h, "how": "file name"})
    for f in inv.text_files(kinds=("code", "notebook", "config")):
        if f["status"] in ("template", "scaffold", "boilerplate"):
            continue
        try:
            text = inv.read(f["path"]).lower()
        except OSError:
            continue
        for h in hints:
            idx = text.find(h)
            if idx >= 0:
                line = text[:idx].count("\n") + 1
                found.append({"file": f["path"], "line": line, "match": h, "how": "referenced in code"})
        if len(found) > 30:
            break
    seen, unique = set(), []
    for x in found:
        if (x["file"], x["how"]) not in seen:
            seen.add((x["file"], x["how"]))
            unique.append(x)
    return {"hints": hints, "found": unique[:30]}


def apply_generator_status(inv: Inventory) -> dict:
    """Mark the standard files that Lovable / shadcn / Vite generate as scaffold (not team work)."""
    lov = detect_lovable(inv)
    if lov["lovable"] or lov["shadcn"]:
        for f in inv.files:
            if f["status"] == "team" and GENERATOR_SCAFFOLD_RE.search(f["path"]):
                f["status"] = "scaffold"
    return lov


def apply_template_status(inv: Inventory, template_hashes: dict) -> None:
    for f in inv.files:
        th = template_hashes.get(f["path"])
        if th is not None and f["status"] == "team":
            f["status"] = "template" if (th == f["hash"] and f["hash"]) else "template-modified"


def apply_boilerplate_status(inv: Inventory, boilerplate: set) -> None:
    for f in inv.files:
        if f["status"] == "team" and f["hash"] and f["hash"] in boilerplate:
            f["status"] = "boilerplate"


def gather(inv: Inventory, template: dict, commits: list, history: str, cfg: dict, challenge: dict,
           demo_url: str) -> tuple:
    ev, sc = cfg["event"], cfg["scan"]
    tmpl_hashes = template.get("hashes", {})
    lov = detect_lovable(inv)
    nb_stats = {"notebooks": 0, "code_cells": 0, "executed_cells": 0, "error_outputs": 0}
    lang_lines = Counter()
    team_code_files = 0
    for f in inv.files:
        if f["status"] in ("template", "scaffold", "boilerplate"):
            continue
        if f["kind"] == "notebook":
            _, st = notebook_to_text(inv.read(f["path"])) if f["size"] <= 20_000_000 else ("", {})
            nb_stats["notebooks"] += 1
            nb_stats["code_cells"] += st.get("code_cells", 0)
            nb_stats["executed_cells"] += st.get("executed", 0)
            nb_stats["error_outputs"] += st.get("errors", 0)
            lang_lines["Python (notebook)"] += st.get("code_lines", 0)
            team_code_files += 1
        elif f["kind"] == "code" and f["lines"]:
            n = f["lines"]
            if f["status"] == "template-modified" and template.get("lines", {}).get(f["path"]):
                try:
                    tl = template["lines"][f["path"]]
                    n = sum(1 for ln in inv.read(f["path"]).splitlines() if ln.strip() and ln.strip() not in tl)
                except OSError:
                    pass
            lang_lines[f["lang"]] += n
            team_code_files += 1 if n else 0

    secrets, raw_secrets = scan_secrets(inv, history)
    timeline = analyze_timeline(commits, ev["start_dt"], ev["deadline_dt"], set(tmpl_hashes.keys()),
                                sc["pre_event_grace_hours"], sc["big_drop_lines"], sc["big_drop_share"],
                                template.get("shas", frozenset()))
    data_files = sorted((f for f in inv.files if f["kind"] == "data"), key=lambda f: -f["size"])
    facts = {
        "files_total": len(inv.files),
        "files_by_status": dict(Counter(f["status"] for f in inv.files)),
        "team_code_files": team_code_files,
        "team_code_lines": sum(lang_lines.values()),
        "languages": dict(lang_lines.most_common(6)),
        "lovable": lov,
        "notebooks": nb_stats,
        "dependencies": parse_dependencies(inv),
        "readme": analyze_readme(inv),
        "tests": sum(1 for f in inv.files if TEST_RE.search(f["path"])),
        "data_files": [{"file": f["path"], "size_kb": round(f["size"] / 1024)} for f in data_files[:12]],
        "large_files": [{"file": f["path"], "size_mb": round(f["size"] / 1_048_576, 1)} for f in inv.files if f["size"] > 20_000_000][:10],
        "committed_dependency_dirs": sorted(inv.committed_dirs),
        "submodules": _submodules(inv),
        "symlinks": inv.symlinks[:10],
        "secrets": secrets,
        "ai_directed_text": scan_ai_directed(inv),
        "partners": detect_partners(inv, cfg["partners"]),
        "challenge_data": challenge_data_signals(inv, challenge.get("data_hints", []) if challenge else []),
        "timeline": timeline,
        "demo": check_url(demo_url) if (demo_url and sc.get("check_demo_urls", True)) else {"url": demo_url, "status": "not checked" if demo_url else "none"},
    }
    return facts, raw_secrets


def build_flags(sub: dict, snap: dict, gh: dict, facts: dict, similar: list, org: str = "",
                template: str = "") -> list:
    """Turn facts into a short list of things a judge should look at."""
    flags = []

    def add(code, severity, title, detail="", evidence=None):
        flags.append({"code": code, "severity": severity, "title": title, "detail": detail,
                      "evidence": evidence or []})

    meta = (gh or {}).get("meta") or {}
    parent = (meta.get("parent") or "").lower()
    if meta.get("fork") and parent:
        own_org = bool(org) and parent.startswith(org.lower() + "/")
        is_template = bool(template) and parent in template.lower()
        if not (own_org or is_template):
            add("fork_of_existing", "high", f"Repository is a fork of {meta['parent']}",
                "Forked code existed before the event. Ask what the team added.")
    tl = facts["timeline"]
    if tl["pre_event_commits"]:
        first = tl["pre_event_commits"][0]
        add("pre_event_commits", "high", f"{len(tl['pre_event_commits'])} commit(s) dated before the event started",
            f"Earliest: {first['date']} ({first['subject']}). Commit dates can be set by hand, so check the push log too.",
            [{"commit": c["sha"]} for c in tl["pre_event_commits"][:5]])
    act = (gh or {}).get("activity") or {}
    if act.get("pushes_before_start"):
        add("pushes_before_start", "medium", f"{len(act['pushes_before_start'])} push(es) to GitHub before kickoff",
            "Could be repo setup. Check what was pushed.")
    for d in tl["big_drops"]:
        add("big_code_drop", "medium",
            f"One commit added {d['code_lines']} lines of code ({int(d['share'] * 100)}% of all code added)",
            f"{d['sha']} at {d['date']}: \"{d['subject']}\". Often a pasted-in project or a generator export. Ask how it was built.",
            [{"commit": d["sha"]}])
    for note in snap.get("notes") or []:
        sev = "medium" if ("after the deadline" in note or note.startswith("Could not update")) else "info"
        add("snapshot_note", sev, note)
    if snap.get("commits_after_deadline"):
        add("commits_after_deadline", "info", f"{snap['commits_after_deadline']} commit(s) after the deadline were excluded",
            "The review uses the last commit before the deadline.")
    if act.get("pushes_after_deadline"):
        add("pushes_after_deadline", "info", f"{len(act['pushes_after_deadline'])} push(es) after the deadline",
            "Excluded from the review snapshot when the push log was available.")
    if act.get("force_pushes"):
        add("force_push", "low", f"History was rewritten {len(act['force_pushes'])} time(s) (force push)",
            "Not wrong in itself, but it makes commit dates less reliable.")
    high_secrets = [s for s in facts["secrets"] if s["severity"] == "high"]
    if high_secrets:
        add("secret_exposed", "high", f"{len(high_secrets)} live-looking key(s) in a public repo",
            "Tell the team to revoke and rotate them now. " + ", ".join(sorted({s['type'] for s in high_secrets})),
            [{"file": s["file"], "lines": str(s["line"] or ""), "commit": s["commit"]} for s in high_secrets[:5]])
    high_files = {s["file"] for s in high_secrets}
    other_secrets = [s for s in facts["secrets"] if s["severity"] != "high" and s["file"] not in high_files]
    if other_secrets:
        add("possible_secret", "medium", f"{len(other_secrets)} possible secret(s) or committed .env file(s)",
            "Worth a quick look.", [{"file": s["file"], "lines": str(s["line"] or "")} for s in other_secrets[:5]])
    likely = [h for h in facts["ai_directed_text"] if h["strength"] == "likely"]
    possible = [h for h in facts["ai_directed_text"] if h["strength"] != "likely"]
    if likely:
        add("ai_directed_text", "medium", "Text that may be aimed at judges or AI reviewers",
            "; ".join(f"{h['file']}:{h['line']} \"{h['snippet']}\"" for h in likely[:3])
            + ". The AI review says whether it targets the judging.",
            [{"file": h["file"], "lines": str(h["line"])} for h in likely[:5]])
    if possible:
        add("instruction_like_text", "low", f"{len(possible)} instruction-like or hidden text fragment(s)",
            "Often harmless (e.g. prompts inside an AI app). Check the AI review's verdict.",
            [{"file": h["file"], "lines": str(h["line"])} for h in possible[:5]])
    for s in similar:
        if s.get("identical_file"):
            add("similar_code", "low", f"Same file as {s['other']}: {s['identical_file']}",
                "One identical file, often a shared library or snippet. Check only if it is core logic.",
                [{"file": s["identical_file"]}])
            continue
        sev = "high" if s["ratio"] >= 0.5 else "medium"
        add("similar_code", sev, f"Shares {s['shared_lines']} distinctive code lines with {s['other']}",
            f"{int(s['ratio'] * 100)}% of the smaller codebase. Files: " + ", ".join(s["files"][:4]),
            [{"file": f} for f in s["files"][:4]])
    subs = facts.get("submodules") or []
    if subs:
        add("submodules", "medium", "Part of the code is in git submodules, which are not scanned",
            "Submodules: " + ", ".join(subs[:5]) + ". Review them by hand if they hold the main work.")
    if facts["team_code_lines"] == 0:
        add("no_team_code", "high" if not subs else "medium",
            "No code beyond the template or generated scaffolding" if not subs else "No team code outside the submodules",
            "Concept-only submission, or the code lives somewhere else.")
    if tl.get("contributors", 0) == 1 and tl.get("commits", 0) >= 5:
        add("solo_contributor", "info", "All commits are from one person",
            "Normal for small teams, but worth checking if the team has multiple members.")
    rd = facts["readme"]
    if not rd.get("exists"):
        add("no_readme", "low", "No README")
    elif rd.get("status") == "template":
        add("readme_unchanged", "low", "README is still the template version")
    elif not rd.get("has_run_instructions"):
        add("no_run_instructions", "low", "README has no instructions to run the project")
    if facts["committed_dependency_dirs"]:
        add("dependencies_committed", "info", "Dependency folders committed: " + ", ".join(facts["committed_dependency_dirs"]))
    if facts["notebooks"]["error_outputs"]:
        add("notebook_errors", "low", f"{facts['notebooks']['error_outputs']} notebook cell(s) show errors")
    demo = facts["demo"]
    if demo.get("status") == "error":
        add("demo_unreachable", "low", "Demo link did not load from the scanner", demo.get("detail", ""))
    for b in (gh or {}).get("unmerged_branches", [])[:3]:
        add("unmerged_branch", "info", f"Branch '{b['branch']}' has {b['unmerged_commits']} commit(s) not in the reviewed branch",
            "Only the default branch is reviewed.")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    flags.sort(key=lambda f: order.get(f["severity"], 9))
    return flags
