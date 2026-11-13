"""Custom check plugin system.

Users drop .py files in a configured directory. Each file defines one or more
Check subclasses with a run() method that returns a list of flags.

Example (file: checks/license_check.py):

    from techscan.checks import Check, CheckContext

    class LicenseCheck(Check):
        code = "missing_license"
        label = "License file check"

        def run(self, ctx: CheckContext) -> list[dict]:
            has_license = any(
                f["path"].lower().startswith("license")
                for f in ctx.inventory.files
            )
            if not has_license:
                return [self.flag("low", "No LICENSE file in the repository")]
            return []
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .util import log, truncate

if TYPE_CHECKING:
    from .facts import Inventory


# ------------------------------------------------------------------ context

@dataclass(frozen=True)
class CheckContext:
    """Immutable context passed to every custom check."""
    submission: dict
    snapshot: dict
    facts: dict
    flags: list
    inventory: "Inventory"
    github: dict
    config: dict


# ------------------------------------------------------------------ base class

class Check:
    """Base class for custom checks. Subclass and implement run().

    Attributes (set as class variables):
        code:  unique identifier (defaults to lowercased class name)
        label: human-readable name shown in logs
    """
    code: str = ""
    label: str = ""

    def run(self, ctx: CheckContext) -> list[dict]:
        """Return a list of flag dicts. Use self.flag() to build them."""
        raise NotImplementedError

    def flag(self, severity: str, title: str, detail: str = "",
             evidence: list | None = None) -> dict:
        """Build a flag dict matching the format of build_flags()."""
        code = self.code or type(self).__name__.lower()
        if severity not in ("high", "medium", "low", "info"):
            severity = "info"
        return {
            "code": f"custom_{code}",
            "severity": severity,
            "title": truncate(title, 300),
            "detail": truncate(detail, 500),
            "evidence": (evidence or [])[:10],
            "source": "custom",
        }


# ------------------------------------------------------------------ loader

def _load_module(path: Path):
    """Import a .py file as a module. Returns the module or None on error."""
    name = "techscan_check_" + path.stem
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        log(f"  Warning: failed to load check plugin {path.name}: "
            f"{type(e).__name__}: {truncate(str(e), 200)}")
        del sys.modules[name]
        return None
    return mod


def discover_checks(directory: str | Path) -> list[Check]:
    """Find all Check subclasses in .py files in the given directory."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    checks = []
    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        mod = _load_module(py_file)
        if mod is None:
            continue
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (isinstance(obj, type) and issubclass(obj, Check) and obj is not Check):
                try:
                    checks.append(obj())
                except Exception as e:
                    log(f"  Warning: could not instantiate {attr_name} "
                        f"from {py_file.name}: {type(e).__name__}: {e}")
    if checks:
        log(f"  Loaded {len(checks)} custom check(s) from {directory}")
    return checks


def run_checks(checks: list[Check], ctx: CheckContext) -> list[dict]:
    """Run all custom checks. A crashing check never stops the scan."""
    flags = []
    for check in checks:
        name = check.label or check.code or type(check).__name__
        try:
            result = check.run(ctx)
            if isinstance(result, list):
                flags.extend(result)
        except Exception as e:
            log(f"  Warning: custom check '{name}' crashed: "
                f"{type(e).__name__}: {truncate(str(e), 200)}")
            flags.append({
                "code": f"custom_{check.code or 'unknown'}_error",
                "severity": "info",
                "title": f"Custom check '{name}' failed",
                "detail": f"{type(e).__name__}: {truncate(str(e), 200)}",
                "evidence": [],
                "source": "custom",
            })
    return flags
