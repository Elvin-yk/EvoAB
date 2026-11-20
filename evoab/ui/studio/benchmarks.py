"""Benchmark registry, preparation status and setup jobs.

Every integration in ``evoab/integrations`` appears here with how it is
prepared: ``auto`` integrations run ``evoab setup`` (per runtime profile where
they have several), ``external`` ones connect to a separately prepared
service, and ``real`` ones are physical robots. Status comes from the runtime
records that setup writes under the cache directory.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evoab.ui.studio import jobs

#: Integrations Studio can launch runs for today (others are listed for setup only).
LAUNCHABLE = ("libero_pro",)


@dataclass(frozen=True)
class BenchmarkInfo:
    integration: str
    title: str
    kind: str  # "simulation" | "real"
    simulator: str
    embodiment: str
    setup: str  # "auto" | "external" | "real"
    description: str
    profiles: tuple[str, ...] = field(default_factory=tuple)
    requirements: str = ""


REGISTRY: tuple[BenchmarkInfo, ...] = (
    BenchmarkInfo("robodojo", "RoboDojo", "simulation", "Isaac Sim 5.1", "Dual ARX X5",
                  "auto", "42 bimanual tasks: generalization, memory, precision, long-horizon, open",
                  requirements="Isaac Sim / Isaac Lab runtime; NVIDIA GPU"),
    BenchmarkInfo("libero_pro", "LIBERO-PRO", "simulation", "MuJoCo (robosuite)", "Franka Panda",
                  "external", "LIBERO suites under instruction and position perturbations",
                  requirements="Adopt a pinned installation with evoab setup --integration libero_pro --runtime-python PYTHON --native-root ROOT"),
    BenchmarkInfo("libero_plus", "LIBERO-Plus", "simulation", "MuJoCo (robosuite 1.4)", "Franka Panda",
                  "external", "Official LIBERO-Plus perturbation suites",
                  requirements="Separately prepared LIBERO-Plus service (--env-url)"),
    BenchmarkInfo("robotwin", "RoboTwin 2.0", "simulation", "SAPIEN", "ALOHA AgileX dual-arm",
                  "external", "Dual-arm tasks with domain randomization",
                  requirements="Separately prepared RoboTwin service (--env-url)"),
    BenchmarkInfo("rmbench", "RMBench", "simulation", "SAPIEN", "Dual-arm, four cameras",
                  "external", "Memory-dependent dual-arm tasks",
                  requirements="Separately prepared RMBench service (--env-url)"),
    BenchmarkInfo("calvin", "CALVIN", "simulation", "PyBullet", "Franka Panda",
                  "external", "Single-task CALVIN episodes",
                  requirements="Separately prepared CALVIN service (--env-url)"),
    BenchmarkInfo("robolab", "RoboLab", "simulation", "Isaac Sim / Isaac Lab", "DROID (Franka)",
                  "external", "RoboLab tasks for generalist manipulation",
                  requirements="RoboLab runtime and service (--env-url)"),
    BenchmarkInfo("dexjoco", "DexJoCo", "simulation", "MuJoCo", "Panda + 16-DoF Allegro hand",
                  "auto", "Dexterous-hand manipulation (Hybrid Pi0.5)"),
    BenchmarkInfo("behavior", "BEHAVIOR", "simulation", "OmniGibson", "R1Pro wheeled",
                  "auto", "Experimental mobile manipulation (Hybrid Pi0.5)"),
    BenchmarkInfo("isaaclab_arena", "IsaacLab Arena", "simulation", "Isaac Lab", "G1 humanoid",
                  "auto", "Experimental humanoid loco-manipulation (Hybrid GR00T)"),
    BenchmarkInfo("navila", "NaVILA-Bench", "simulation", "Isaac Lab", "Go2 quadruped",
                  "auto", "Experimental quadruped navigation (Hybrid NaVILA)"),
    BenchmarkInfo("arx_one", "ARX ONE", "real", "—", "Dual ARX X5 + RealSense D405",
                  "real", "Physical dual-arm station behind a device process"),
    BenchmarkInfo("piper_x", "Piper-X", "real", "—", "Dual Piper arms + RealSense",
                  "real", "Physical dual-arm CAN control"),
)


def cache_root() -> Path:
    """Directory holding prepared runtimes (``EVOAB_CACHE_DIR`` or ``~/.cache/evoab``)."""
    return Path(os.environ.get("EVOAB_CACHE_DIR") or Path.home() / ".cache" / "evoab")


def _spec(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _task_metadata(path: str) -> dict[str, Any]:
    """Description and metadata from a task's ``task.md`` YAML front matter."""
    try:
        text = (Path(path) / "task.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        first = next((line.strip().lstrip("# ") for line in text.splitlines() if line.strip()), "")
        return {"description": first}
    try:
        front = yaml.safe_load(text.split("---", 2)[1]) or {}
    except (yaml.YAMLError, IndexError):
        return {}
    meta = front.get("metadata") or {}
    return {
        "description": (front.get("task") or {}).get("description", ""),
        "suite": meta.get("suite"),
        "category": meta.get("category"),
        "difficulty": meta.get("difficulty"),
        "source": meta.get("source_benchmark"),
    }


def _embodiment(spec: dict[str, Any]) -> str:
    robots = spec.get("robots")
    if isinstance(robots, (list, tuple)):
        return " + ".join(str(r) for r in robots)
    if robots:
        return str(robots)
    for key in ("embodiment", "robot"):
        if spec.get(key):
            return str(spec[key])
    return ""


@dataclass(frozen=True)
class PreparedRuntime:
    integration: str
    profile: str
    root: Path
    prepared_at: float
    package_version: str
    tasks: dict[str, dict[str, Any]]
    validation: dict[str, Any]


_parsed: dict[Path, tuple[float, PreparedRuntime | None]] = {}


def prepared_runtimes(root: Path | None = None) -> list[PreparedRuntime]:
    """Runtimes prepared by ``evoab setup``; parsed once per runtime.json change."""
    root = root or cache_root()
    runtimes = []
    for record in sorted(root.glob("*/*/runtime.json")):
        mtime = record.stat().st_mtime
        cached = _parsed.get(record)
        if cached is None or cached[0] != mtime:
            cached = (mtime, _parse_runtime(record))
            _parsed[record] = cached
        if cached[1] is not None:
            runtimes.append(cached[1])
    return runtimes


def _parse_runtime(record: Path) -> PreparedRuntime | None:
    known = sorted((b.integration for b in REGISTRY), key=len, reverse=True)
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = str(data.get("name", record.parent.parent.name))
    integration = next((k for k in known if name == k or name.startswith(k + "-")), None)
    if integration is None:
        return None
    profile = name[len(integration) + 1:] or "default"
    tasks = {}
    for task_id, entry in (data.get("data", {}).get("tasks") or {}).items():
        spec = _spec(entry.get("spec"))
        meta = _task_metadata(entry["path"]) if entry.get("path") else {}
        scenario = spec.get("scenario") if isinstance(spec.get("scenario"), dict) else {}
        tasks[task_id] = {
            "task": task_id,
            "suite": str(meta.get("suite") or spec.get("backend") or "default"),
            "backend": spec.get("backend"),
            "instruction": scenario.get("goal") or meta.get("description") or scenario.get("title") or "",
            "category": meta.get("category"),
            "difficulty": meta.get("difficulty"),
            "source": meta.get("source"),
            "embodiment": _embodiment(spec),
            "max_steps": spec.get("max_steps"),
        }
    try:
        validation = json.loads((record.parent / "validation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        validation = {}
    return PreparedRuntime(integration, profile, record.parent, record.stat().st_mtime,
                           str(data.get("package_version", "")), tasks, validation)


def _setup_record(runs_root: Path, integration: str, profile: str) -> Path:
    return runs_root / ".studio" / "setup" / f"{integration}-{profile}.json"


def overview(runs_root: Path) -> list[dict[str, Any]]:
    """Every registered benchmark with the status of each runtime profile."""
    prepared = {(r.integration, r.profile): r for r in prepared_runtimes()}
    items = []
    for info in REGISTRY:
        profiles = info.profiles or ("default",)
        entries = []
        for profile in profiles:
            runtime = prepared.get((info.integration, profile))
            if runtime is None and profile == "default":
                runtime = next((r for (i, _), r in prepared.items() if i == info.integration), None)
            record_path = _setup_record(runs_root, info.integration, profile)
            job = jobs.read_record(record_path)
            if job and jobs.is_running(job):
                status = "preparing"
            elif runtime is not None:
                status = "ready"
            elif job and job.get("exit_code") not in (None, 0):
                status = "setup_failed"
            elif info.setup == "auto":
                status = "not_prepared"
            else:
                status = info.setup  # external | real
            suites: dict[str, int] = {}
            for task in (runtime.tasks.values() if runtime else ()):
                suites[task["suite"]] = suites.get(task["suite"], 0) + 1
            entries.append({
                "profile": profile,
                "status": status,
                "task_count": len(runtime.tasks) if runtime else 0,
                "suites": suites,
                "prepared_at": runtime.prepared_at if runtime else None,
                "package_version": runtime.package_version if runtime else None,
                "launchable": runtime is not None and info.integration in LAUNCHABLE and bool(runtime.tasks),
                "setup_log": jobs.log_tail(record_path, 12) if job else "",
            })
        items.append({
            "integration": info.integration,
            "title": info.title,
            "kind": info.kind,
            "simulator": info.simulator,
            "embodiment": info.embodiment,
            "setup": info.setup,
            "description": info.description,
            "requirements": info.requirements,
            "profiles": entries,
        })
    return items


def tasks_for(integration: str, profile: str) -> list[dict[str, Any]]:
    for runtime in prepared_runtimes():
        if runtime.integration == integration and runtime.profile == profile:
            return sorted(runtime.tasks.values(), key=lambda t: (t["suite"], t["task"]))
    return []


def task_info(integration: str, profile: str, task: str) -> dict[str, Any] | None:
    return next((t for t in tasks_for(integration, profile) if t["task"] == task), None)


def start_setup(runs_root: Path, integration: str, profile: str, wheel: str | None = None) -> int:
    """Run ``evoab setup`` for one profile in the background.

    ``wheel`` is the evoab-runtime wheel installed into the isolated runtime;
    without it setup installs ``evoab-runtime`` from the package index.
    """
    info = next((b for b in REGISTRY if b.integration == integration), None)
    if info is None or info.setup != "auto":
        raise ValueError(f"{integration} cannot be prepared automatically")
    if info.profiles and profile not in info.profiles:
        raise ValueError(f"unknown profile {profile!r} for {integration}")
    record_path = _setup_record(runs_root, integration, profile)
    existing = jobs.read_record(record_path)
    if existing and jobs.is_running(existing):
        raise ValueError(f"{integration}/{profile} is already being prepared")
    command = [sys.executable, "-m", "evoab", "setup", "--integration", integration]
    if info.profiles:
        command += ["--runtime-profile", profile]
    if wheel:
        wheel_path = Path(wheel).expanduser()
        if not wheel_path.is_file():
            raise ValueError(f"runtime wheel not found: {wheel_path}")
        command += ["--wheel", str(wheel_path)]
    return jobs.start(command, record_path, integration=integration, profile=profile, requested_at=time.time())
