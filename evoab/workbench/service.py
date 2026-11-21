"""One facade for runs, providers, benchmarks, usage and settings.

Studio's HTTP routes and the CLI resource commands are thin wrappers around
these methods. Failures raise :class:`WorkbenchError` subclasses that each
front end maps to its own form (HTTP status, exit code).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evoab.workbench import benchmarks, direct_trace, jobs, trace
from evoab.workbench.launcher import DIRECT_OPTIONS, METHODS, REASONING_EFFORTS, LaunchRequest, launch, validate
from evoab.workbench.runs import STUDIO_DIR, load_launches, scan_runs, usage_totals

# Launch defaults (remembered from the last launch) and the benchmark runtime wheel.
SETTING_KEYS = ("default_model", "default_effort", "default_method", "runtime_wheel", "hf_endpoint", "build_mirror")

#: Mirror presets for container builds: each maps to variables upstream recipes and EvoAB's layer read.
BUILD_MIRRORS = {
    "tuna": {
        "UBUNTU_MIRROR": "mirrors.tuna.tsinghua.edu.cn",
        "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "UV_DEFAULT_INDEX": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "MINICONDA_URL": "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh",
        "CONDA_CHANNEL_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/anaconda",
        "HF_ENDPOINT": "https://hf-mirror.com",
    },
}


class WorkbenchError(Exception):
    status = 400


class Invalid(WorkbenchError):
    status = 422


class NotFound(WorkbenchError):
    status = 404


class Conflict(WorkbenchError):
    status = 409


class Unavailable(WorkbenchError):
    status = 502


def default_runs_root() -> Path:
    """Where runs are stored: ``EVOAB_RUNS_ROOT`` or ``~/evoab-runs``."""
    return Path(os.environ.get("EVOAB_RUNS_ROOT") or Path.home() / "evoab-runs").expanduser()


class Workbench:
    def __init__(self, runs_root: Path | None = None):
        self.runs_root = Path(runs_root or default_runs_root()).expanduser()

    # -- settings -------------------------------------------------------------

    @staticmethod
    def settings_path() -> Path:
        return Path(os.environ.get("EVOAB_CONFIG_DIR") or Path.home() / ".config" / "evoab").expanduser() / "studio.json"

    def settings(self) -> dict[str, Any]:
        try:
            value = json.loads(self.settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        return {key: value.get(key) for key in SETTING_KEYS}

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(SETTING_KEYS)
        if unknown:
            raise Invalid(f"unknown settings: {', '.join(sorted(unknown))}")
        current = self.settings()
        current.update(changes)
        path = self.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        return current

    def config(self) -> dict[str, Any]:
        return {
            "runs_root": str(self.runs_root),
            "reasoning_efforts": REASONING_EFFORTS,
            "methods": METHODS,
            "direct_options": {k: list(v[1]) if v[1] else None for k, v in DIRECT_OPTIONS.items()},
            "settings": self.settings(),
        }

    # -- benchmarks -----------------------------------------------------------

    def benchmarks(self) -> list[dict[str, Any]]:
        return benchmarks.overview(self.runs_root)

    def tasks(self, integration: str, profile: str = "default") -> list[dict[str, Any]]:
        return benchmarks.tasks_for(integration, profile)

    def demo(self, integration: str, profile: str, task: str) -> Path:
        for runtime in benchmarks.prepared_runtimes():
            if runtime.integration == integration and runtime.profile == profile:
                if path := benchmarks.demo_file(runtime, task):
                    return path
        raise NotFound("expert preview is not installed")

    def prepare(self, integration: str, profile: str = "default", container: bool = False,
                accept_license: str | None = None) -> dict[str, Any]:
        """Prepare a runtime; ``container`` pulls or builds the benchmark's Docker image instead."""
        settings = self.settings()
        environment = dict(BUILD_MIRRORS.get(settings.get("build_mirror") or "", {}))
        if settings.get("hf_endpoint"):
            environment["HF_ENDPOINT"] = settings["hf_endpoint"]
        try:
            pid = benchmarks.start_setup(self.runs_root, integration, profile, settings.get("runtime_wheel"),
                                         container=container, environment=environment, accept_license=accept_license)
        except ValueError as error:
            raise Conflict(str(error)) from error
        return {"pid": pid, "integration": integration, "profile": profile, "container": container}

    def system_check(self) -> dict[str, Any]:
        """What a machine needs before one-click benchmark deployment, each with a fix."""
        import shutil
        import subprocess
        from evoab.provider import catalog
        checks = []

        def add(key, ok, detail, fix=""):
            checks.append({"key": key, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

        docker = shutil.which("docker")
        add("docker", docker, docker or "docker not found", "Install Docker Engine (docs.docker.com/engine/install).")
        info = None
        if docker:
            result = subprocess.run([docker, "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True,
                                    timeout=30)
            info = result.stdout if result.returncode == 0 else None
            add("docker_access", info is not None, "Docker daemon reachable" if info else (result.stderr.strip()[-200:]),
                "Add this user to the docker group (sudo usermod -aG docker $USER), then log in again.")
        toolkit = shutil.which("nvidia-ctk") or shutil.which("nvidia-container-runtime")
        add("gpu_containers", toolkit and info and "nvidia" in info,
            "NVIDIA Container Toolkit configured" if toolkit and info and "nvidia" in info else "GPU containers unavailable",
            "Install nvidia-container-toolkit, run sudo nvidia-ctk runtime configure --runtime=docker, restart Docker.")
        free = shutil.disk_usage(Path.home()).free / 2**30
        add("disk", free >= 120, f"{free:.0f} GiB free", "Free at least 120 GiB (images and assets).")
        try:
            presets = catalog.presets()
        except RuntimeError as error:
            presets = []
            add("agent_runtime", False, str(error), "pip install 'evoab[agent]'")
        add("model_presets", presets, f"{len(presets)} model presets",
            "Add a provider and a model preset under 设置 → 模型.")
        return {"ok": all(c["ok"] for c in checks), "checks": checks,
                "mirror": self.settings().get("build_mirror"), "mirrors": sorted(BUILD_MIRRORS)}

    # -- models: providers, presets and usage ---------------------------------

    @staticmethod
    def _catalog(operation, *args, **kwargs):
        from evoab.provider import catalog
        try:
            return operation(catalog)(*args, **kwargs)
        except catalog.CatalogError as error:
            raise (Conflict if error.status == 409 else NotFound if error.status == 404 else Invalid)(str(error)) from error
        except RuntimeError as error:
            raise Unavailable(str(error)) from error

    def model_settings(self) -> dict[str, Any]:
        return self._catalog(lambda c: c.settings)

    def save_preset(self, fields: dict[str, Any], existing: str | None = None) -> dict[str, Any]:
        return self._catalog(lambda c: c.save_preset, fields, existing=existing)

    def delete_preset(self, name: str) -> dict[str, Any]:
        return self._catalog(lambda c: c.delete_preset, name)

    def save_provider(self, fields: dict[str, Any], existing: str | None = None) -> dict[str, Any]:
        return self._catalog(lambda c: c.save_provider, fields, existing=existing)

    def delete_provider(self, name: str) -> dict[str, Any]:
        return self._catalog(lambda c: c.delete_provider, name)

    def provider_models(self, name: str, refresh: bool = False) -> dict[str, Any]:
        return self._catalog(lambda c: c.provider_models, name, refresh)

    def model_usage(self) -> dict[str, Any]:
        return self._catalog(lambda c: c.usage)

    # -- runs -----------------------------------------------------------------

    def runs(self, live=None) -> list[dict[str, Any]]:
        """All runs, newest first. ``live(monitor_id)`` may add live progress to running ones."""
        items = scan_runs(self.runs_root)
        for run in items:
            if not run["instruction"] and run.get("task") and run.get("profile"):
                entry = benchmarks.task_info(run["integration"], run["profile"], run["task"])
                if entry:
                    run["instruction"] = entry.get("instruction")
            if live and run["status"] == "running" and run.get("monitor_id"):
                run["live"] = live(run["monitor_id"])
        return items

    def usage(self, runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return usage_totals(self.runs() if runs is None else runs)

    def find_run(self, ref: str) -> dict[str, Any]:
        """A run by directory name, short ID or launch ID (also as ``#short``)."""
        ref = ref.lstrip("#")
        matches = [r for r in self.runs() if ref in (r.get("id"), r.get("short_id"), r.get("launch_id"))]
        if not matches:
            raise NotFound(f"unknown run {ref!r}")
        if len(matches) > 1:
            raise Invalid(f"run reference {ref!r} is ambiguous")
        return matches[0]

    def launch(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = LaunchRequest(**payload)
            task = validate(request)
        except (TypeError, ValueError) as error:
            raise Invalid(str(error)) from error
        launch_id = launch(request, task, self.runs_root)
        self.update_settings({"default_model": request.model, "default_effort": request.reasoning_effort,
                              "default_method": request.method})
        return {"launch_id": launch_id, "short_id": launch_id.rsplit("_", 1)[-1], "request": asdict(request)}

    def _launch_record(self, launch_id: str) -> dict[str, Any]:
        record = next((r for r in load_launches(self.runs_root) if r.get("launch_id") == launch_id), None)
        if record is None:
            raise NotFound(f"unknown launch {launch_id!r}")
        return record

    def stop(self, launch_id: str) -> dict[str, Any]:
        record = self._launch_record(launch_id)
        if not jobs.is_running(record):
            raise Conflict("run is not running")
        try:
            jobs.interrupt(record)
        except OSError as error:
            raise Conflict(f"could not stop: {error}") from error
        return {"stopped": launch_id}

    def log(self, launch_id: str, lines: int = 200) -> str:
        self._launch_record(launch_id)
        return jobs.log_tail(self.runs_root / STUDIO_DIR / f"{launch_id}.json", lines)

    # -- trace ----------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[\w.\-]+", run_id) or run_id == STUDIO_DIR:
            raise NotFound("unknown run")
        path = self.runs_root / run_id
        if not path.is_dir():
            raise NotFound("unknown run")
        return path

    def trace(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        return direct_trace.build(run_dir) if direct_trace.is_direct(run_dir) else trace.build_trace(run_dir)

    def round(self, run_id: str, index: int) -> dict[str, Any]:
        """One model round (agent runs) or one inference (Direct runs), 0-based."""
        run_dir = self.run_dir(run_id)
        try:
            if direct_trace.is_direct(run_dir):
                return direct_trace.call_detail(run_dir, index + 1)
            return trace.round_detail(run_dir, index)
        except IndexError as error:
            raise NotFound("unknown round") from error

    def run_file(self, run_id: str, relative: str) -> Path:
        try:
            return trace.run_file(self.run_dir(run_id), relative)
        except (PermissionError, FileNotFoundError) as error:
            raise NotFound("file unavailable") from error
