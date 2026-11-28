"""Experience carried between the episodes of one method.

A method that carries experience (``loop.episodes.carry``) keeps a persistent
directory. Each episode works on a staged copy inside its run directory:
``skills/`` (a complete skills library, seeded from the packaged one) and
``programs/`` (reusable code-channel programs). After the official outcome is
known, the staged copy replaces the persistent one when ``keep`` allows it, so
a failed episode cannot overwrite what earlier successes learned.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from evoab.tools.skills import RESOURCES

PROGRAMS_NOTE = (
    "Programs kept from earlier episodes of this method are in {path}. Read them before writing new "
    "code; reuse and improve them. Save programs worth keeping in that directory (one purpose per file, "
    "with a docstring saying when it applies). Programs are kept for the next episode only when the "
    "configured outcome allows it."
)


def prepare(args, session_dir: Path) -> dict | None:
    """Stage experience into the run directory; point skills/programs at the staged copy."""
    root = getattr(args, "experience_dir", None)
    recipe = getattr(args, "recipe", None)
    if not root or recipe is None:
        return None
    carry = recipe.loop.episodes.carry
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    staged = Path(session_dir) / "experience"
    staged.mkdir(exist_ok=True)
    plan = {"root": str(root), "staged": str(staged), "carry": list(carry)}
    if "skills" in carry:
        source = root / "skills"
        if not source.is_dir():
            shutil.copytree(RESOURCES / "skills", source)
        shutil.copytree(source, staged / "skills", dirs_exist_ok=True)
        args.skills_root = str(staged / "skills")
    if "programs" in carry:
        target = Path(session_dir) / "scratch" / "programs"
        target.mkdir(parents=True, exist_ok=True)
        if (root / "programs").is_dir():
            shutil.copytree(root / "programs", target, dirs_exist_ok=True)
        args.programs_dir = str(target)
    (staged / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan


def persist(args, session_dir: Path, success: bool | None) -> dict | None:
    """Promote the staged experience when the episode outcome allows it; always log the episode."""
    root = getattr(args, "experience_dir", None)
    recipe = getattr(args, "recipe", None)
    if not root or recipe is None:
        return None
    episodes = recipe.loop.episodes
    keep = episodes.keep == "always" or success is True
    root, session_dir = Path(root), Path(session_dir)
    promoted = []
    if keep and "skills" in episodes.carry and (session_dir / "experience" / "skills").is_dir():
        _replace(session_dir / "experience" / "skills", root / "skills")
        promoted.append("skills")
    if keep and "programs" in episodes.carry and (session_dir / "scratch" / "programs").is_dir():
        _replace(session_dir / "scratch" / "programs", root / "programs")
        promoted.append("programs")
    record = {"run_dir": str(session_dir), "finished_at": time.time(), "success": success,
              "kept": promoted, "keep_policy": episodes.keep}
    with (root / "episodes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def _replace(source: Path, target: Path) -> None:
    temporary = target.with_name(target.name + ".incoming")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    if target.exists():
        shutil.rmtree(target)
    temporary.rename(target)
