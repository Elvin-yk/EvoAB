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
SETTING_KEYS = ("default_model", "default_effort", "default_method", "runtime_wheel")


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

    def prepare(self, integration: str, profile: str = "default") -> dict[str, Any]:
        try:
            pid = benchmarks.start_setup(self.runs_root, integration, profile, self.settings().get("runtime_wheel"))
        except ValueError as error:
            raise Conflict(str(error)) from error
        return {"pid": pid, "integration": integration, "profile": profile}

    def add_benchmark(self, text: str) -> dict[str, Any]:
        """Register a custom benchmark manifest and check it in its interpreter."""
        try:
            return benchmarks.add_custom(self.runs_root, text)
        except ValueError as error:
            raise Invalid(str(error)) from error

    def remove_benchmark(self, name: str) -> dict[str, Any]:
        try:
            return benchmarks.remove_custom(name)
        except ValueError as error:
            raise NotFound(str(error)) from error

    def benchmark_manifest(self, name: str | None = None) -> str:
        """A registered manifest, or the documented template when ``name`` is empty."""
        if not name:
            from importlib import resources
            return resources.files("evoab.integrations.custom").joinpath("template.toml").read_text(encoding="utf-8")
        try:
            return benchmarks.custom_manifest(name)
        except ValueError as error:
            raise NotFound(str(error)) from error

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
