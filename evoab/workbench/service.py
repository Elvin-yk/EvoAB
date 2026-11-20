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

from evoab.workbench import benchmarks, jobs, providers, trace
from evoab.workbench.launcher import METHODS, REASONING_EFFORTS, LaunchRequest, launch, validate
from evoab.workbench.runs import STUDIO_DIR, load_launches, scan_runs, usage_totals

SETTING_KEYS = ("default_provider", "default_model", "default_effort", "default_method", "runtime_wheel")


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
        return providers.config_dir() / "studio.json"

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
            "config_dir": str(providers.config_dir()),
            "reasoning_efforts": REASONING_EFFORTS,
            "methods": METHODS,
            "wire_profiles": providers.agent_profiles(),
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

    # -- providers ------------------------------------------------------------

    @staticmethod
    def provider(provider_id: str) -> providers.Provider:
        provider = providers.load_providers().get(provider_id)
        if provider is None:
            raise NotFound(f"unknown provider {provider_id!r}")
        return provider

    def providers(self) -> list[dict[str, Any]]:
        return [providers.public_view(p) for p in providers.load_providers().values()]

    def save_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return providers.public_view(providers.upsert({**payload, "id": provider_id}))
        except ValueError as error:
            raise Invalid(str(error)) from error

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        try:
            providers.delete(provider_id)
        except KeyError as error:
            raise NotFound(f"unknown provider {provider_id!r}") from error
        return {"deleted": provider_id}

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        return providers.test_connection(self.provider(provider_id))

    def quota(self, provider_id: str) -> dict[str, Any]:
        try:
            return providers.token_quota(self.provider(provider_id))
        except WorkbenchError:
            raise
        except Exception as error:  # noqa: BLE001 - provider-specific failures
            raise Unavailable(f"could not retrieve provider quota: {error}") from error

    def models(self, provider_id: str) -> dict[str, Any]:
        provider = self.provider(provider_id)
        try:
            models = providers.list_models(provider)
        except Exception as error:  # noqa: BLE001 - report provider failures to the caller
            raise Unavailable(f"model listing failed: {type(error).__name__}: {error}") from error
        return {"models": models, "capabilities": providers.capabilities(provider_id),
                "recommended": providers.public_view(provider)["recommended"]}

    def probe(self, provider_id: str, model: str) -> dict[str, Any]:
        if not model:
            raise Invalid("model is required")
        return providers.probe_model(self.provider(provider_id), model)

    # -- runs -----------------------------------------------------------------

    def runs(self, live=None) -> list[dict[str, Any]]:
        """All runs, newest first. ``live(monitor_id)`` may add live progress to running ones."""
        items = scan_runs(self.runs_root)
        registered = providers.load_providers()
        for run in items:
            provider = registered.get(run.get("provider_id"))
            if provider is not None:
                run["provider"] = provider.label
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
            task, provider = validate(request, providers.load_providers())
        except (TypeError, ValueError) as error:
            raise Invalid(str(error)) from error
        launch_id = launch(request, task, provider, self.runs_root)
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
        return trace.build_trace(self.run_dir(run_id))

    def round(self, run_id: str, index: int) -> dict[str, Any]:
        try:
            return trace.round_detail(self.run_dir(run_id), index)
        except IndexError as error:
            raise NotFound("unknown round") from error

    def run_file(self, run_id: str, relative: str) -> Path:
        try:
            return trace.run_file(self.run_dir(run_id), relative)
        except (PermissionError, FileNotFoundError) as error:
            raise NotFound("file unavailable") from error
