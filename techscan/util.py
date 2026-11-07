"""Small helpers shared by every stage. Pure standard library, Windows-safe."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

IS_WINDOWS = os.name == "nt"
_print_lock = threading.Lock()


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _print_lock:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:  # old Windows consoles
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)


def _kill_tree(p) -> None:
    """Kill a process and everything it started (claude.cmd -> node.exe on Windows)."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(p.pid, 9)
    except (OSError, subprocess.SubprocessError):
        try:
            p.kill()
        except OSError:
            pass


def run(cmd, cwd=None, timeout=600, input_bytes=None, env=None):
    """Run a command without a shell. Returns (returncode, stdout, stderr) as text.

    On timeout the whole process tree is killed, so a hung child cannot block the run.
    """
    kwargs = {} if IS_WINDOWS else {"start_new_session": True}
    try:
        p = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **kwargs,
        )
    except FileNotFoundError as e:
        return 127, "", f"command not found: {e}"
    except OSError as e:
        return 126, "", f"could not start command: {e}"
    try:
        out, err = p.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=15)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return 124, "", f"timed out after {timeout}s"
    return p.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def parse_dt(value) -> datetime:
    """Parse an ISO 8601 datetime that carries a UTC offset (works on Python 3.9+)."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(
            f"Date '{value}' has no UTC offset. Write it like 2026-11-08T10:00:00+01:00"
        )
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt_dt(dt, with_day=True) -> str:
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = parse_dt(dt)
        except ValueError:
            return dt
    return dt.strftime("%a %d %b %H:%M" if with_day else "%H:%M")


def slugify(text: str, maxlen: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    s = s[:maxlen].strip("-")
    return s or "team"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def norm_hash(text: str) -> str:
    """Hash of content with line endings and trailing whitespace normalised."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return sha256_text("\n".join(lines))


def decode_bytes(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 (e.g. 'pip freeze > requirements.txt' in PowerShell 5)
        return data.decode("utf-16", errors="replace")
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def read_text(path: Path, limit: int | None = None) -> str:
    with open(path, "rb") as f:
        data = f.read(limit) if limit else f.read()
    return decode_bytes(data)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def rmtree(path) -> None:
    """shutil.rmtree that also removes read-only files (git objects on Windows)."""
    path = Path(path)
    if not path.exists():
        return

    def _retry(func, p, *_):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


def mask_secret(value: str) -> str:
    v = value.strip()
    if len(v) <= 8:
        return "****"
    return f"{v[:4]}...{v[-2:]} (masked, {len(v)} chars)"


def truncate(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def ensure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
