"""Prepared runtimes and their admitted tasks, read from setup records."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Integrations whose prepared runtimes Studio can launch locally.
LAUNCHABLE = ("robouse",)


def cache_root() -> Path:
    """Directory holding prepared runtimes (``EVOAB_CACHE_DIR`` or ``~/.cache/evoab``)."""
    return Path(os.environ.get("EVOAB_CACHE_DIR") or Path.home() / ".cache" / "evoab")


@dataclass(frozen=True)
class Task:
    task_id: str
    benchmark: str
    instruction: str
    embodiment: str
    max_steps: int | None


@dataclass(frozen=True)
class Runtime:
    integration: str
    profile: str
    simulated: bool
    tasks: dict[str, Task]


def _spec(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _instruction(path: str) -> str:
    try:
        text = (Path(path) / "task.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = next((line.lstrip("# ") for line in lines if line.startswith("#")), "")
    return title or (lines[0] if lines else "")


def _embodiment(spec: dict[str, Any]) -> str:
    robots = spec.get("robots")
    if isinstance(robots, (list, tuple)):
        return " + ".join(str(r) for r in robots)
    if robots:
        return str(robots)
    for key in ("embodiment", "robot"):
        if spec.get(key):
            return str(spec[key])
    if spec.get("backend") == "tabletop":
        return "floating parallel gripper"
    return "Panda"


def load_runtimes(root: Path | None = None) -> list[Runtime]:
    """Read every prepared runtime record under the cache root."""
    root = root or cache_root()
    runtimes: list[Runtime] = []
    for record in sorted(root.glob("*/*/runtime.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = str(data.get("name", ""))
        integration, _, profile = name.partition("-")
        if integration not in LAUNCHABLE:
            continue
        tasks: dict[str, Task] = {}
        for task_id, entry in (data.get("data", {}).get("tasks") or {}).items():
            spec = _spec(entry.get("spec"))
            tasks[task_id] = Task(
                task_id=task_id,
                benchmark=str(spec.get("backend") or "unknown"),
                instruction=_instruction(str(entry.get("path", ""))),
                embodiment=_embodiment(spec),
                max_steps=spec.get("max_steps"),
            )
        runtimes.append(Runtime(integration, profile or "default", True, tasks))
    return runtimes


def catalog_payload(runtimes: list[Runtime]) -> list[dict[str, Any]]:
    """JSON-ready catalog grouped by runtime and benchmark."""
    payload = []
    for runtime in runtimes:
        benchmarks: dict[str, list[dict[str, Any]]] = {}
        for task in sorted(runtime.tasks.values(), key=lambda t: t.task_id):
            benchmarks.setdefault(task.benchmark, []).append(
                {
                    "task": task.task_id,
                    "instruction": task.instruction,
                    "embodiment": task.embodiment,
                    "max_steps": task.max_steps,
                }
            )
        payload.append(
            {
                "integration": runtime.integration,
                "profile": runtime.profile,
                "simulated": runtime.simulated,
                "benchmarks": [
                    {"name": name, "tasks": tasks}
                    for name, tasks in sorted(benchmarks.items())
                ],
            }
        )
    return payload
