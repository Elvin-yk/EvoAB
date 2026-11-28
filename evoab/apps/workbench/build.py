"""Which source this process runs, so Studio can show it next to the wordmark.

A git checkout reports its own HEAD and whether the working tree has changes.
A deployed copy without ``.git`` reports the ``BUILD.json`` written next to the
package by whoever deployed it (``commit``, ``date``, ``subject``, ``branch``).
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


@lru_cache(maxsize=1)
def build_info() -> dict[str, Any]:
    """Commit, date, subject, branch and dirty state; ``commit`` is None when unknown."""
    if (ROOT / ".git").exists() and (head := _git("log", "-1", "--format=%H%n%cI%n%s")):
        commit, date, subject = (head.splitlines() + ["", "", ""])[:3]
        return {"commit": commit, "date": date, "subject": subject, "source": "git",
                "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": bool(_git("status", "--porcelain", "--untracked-files=no"))}
    try:
        recorded = json.loads((ROOT / "BUILD.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"commit": None, "source": None}
    return {"commit": recorded.get("commit"), "date": recorded.get("date"), "subject": recorded.get("subject"),
            "branch": recorded.get("branch"), "dirty": bool(recorded.get("dirty")), "source": "BUILD.json",
            "deployed_at": recorded.get("deployed_at")}
