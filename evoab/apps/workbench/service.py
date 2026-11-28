"""One facade for runs, providers, benchmarks, usage and settings.

Studio's HTTP routes and the CLI resource commands are thin wrappers around
these methods. Failures raise :class:`WorkbenchError` subclasses that each
front end maps to its own form (HTTP status, exit code).
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evoab.agent.loop import agent_launch
from evoab.agent.models.contracts import PresetCompatibilityError
from evoab.apps.workbench.build import build_info
from evoab.apps.workbench import benchmarks, direct_trace, jobs, trace
from evoab.apps.workbench.launcher import (
    REASONING_EFFORTS, LaunchRequest, launch, methods, resolve_selection, tool_groups, validate,
)
from evoab.apps.workbench.runs import STUDIO_DIR, load_launches, scan_runs, usage_totals

# Launch defaults, remembered from the last launch.
SETTING_KEYS = ("default_model", "default_effort", "default_method", "default_agent", "image_import_dir")

#: Mirrors for benchmark builds where the public package and model hubs are unreachable
#: (mainland China): variables that upstream recipes and EvoAB's layer read.
CHINA_MIRRORS = {
        "UBUNTU_MIRROR": "mirrors.tuna.tsinghua.edu.cn",
        "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "UV_DEFAULT_INDEX": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "MINICONDA_URL": "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh",
        "CONDA_CHANNEL_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/anaconda",
        "HF_ENDPOINT": "https://hf-mirror.com",
}
_mirrors: dict[str, str] | None = None


def build_mirrors() -> dict[str, str]:
    """Mirror variables for benchmark builds, chosen once per Studio process.

    Variables already set in Studio's environment win. Otherwise builds use the
    public hubs when huggingface.co answers directly (builds do not use a proxy),
    and the mainland China mirrors when it does not.
    """
    global _mirrors
    if any(os.environ.get(key) for key in CHINA_MIRRORS):
        return {}
    if _mirrors is None:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener.open(urllib.request.Request("https://huggingface.co/api/health", method="HEAD"), timeout=6)
            _mirrors = {}
        except Exception:  # unreachable, refused or timed out: use the mirrors
            _mirrors = dict(CHINA_MIRRORS)
    return dict(_mirrors)


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


#: Admission order for deploy, uninstall and launch inside this Studio process, so an
#: uninstall cannot slip between a launch's checks and its process start. The CLI is
#: not covered: its runs are protected by Docker's own non-force image removal.
_admission = threading.RLock()


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
        value = value if isinstance(value, dict) else {}
        defaults = {key: value[key] if isinstance(value.get(key), str) else None for key in SETTING_KEYS}
        return defaults

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(SETTING_KEYS)
        if unknown:
            raise Invalid(f"unknown settings: {', '.join(sorted(unknown))}")
        if any(value is not None and not isinstance(value, str) for value in changes.values()):
            raise Invalid("Studio defaults must be strings or null")
        if "image_import_dir" in changes:
            from evoab.envs.docker.image_import import normalize_directory
            try:
                changes = {**changes, "image_import_dir": normalize_directory(changes["image_import_dir"])}
            except ValueError as error:
                # Raised before writing, so an invalid path never replaces the saved one.
                raise Invalid(str(error)) from error
        current = self.settings()
        if "default_method" in changes and "default_agent" not in changes:
            current["default_agent"] = None
        current.update(changes)
        path = self.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        return current

    def config(self) -> dict[str, Any]:
        return {
            "runs_root": str(self.runs_root),
            "build": build_info(),
            "reasoning_efforts": REASONING_EFFORTS,
            "methods": methods(),
            "settings": self.settings(),
        }

    # -- methods --------------------------------------------------------------

    def methods(self, integration: str | None = None) -> list[dict[str, Any]]:
        try:
            return list(methods(integration))
        except ValueError as error:
            raise NotFound(str(error)) from error

    def method(self, method_id: str) -> dict[str, Any]:
        """One method with its reproduction card and recipe text."""
        from evoab.recipe.methods import get_method
        from evoab.recipe.schema import recipe_document
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", method_id):
            raise NotFound("unknown method")
        try:
            entry = get_method(method_id)
        except ValueError as error:
            raise NotFound(str(error)) from error
        summary = next((m for m in methods() if m["id"] == method_id), {})
        return {**summary, "card": entry.card, "recipe": recipe_document(entry.recipe)}

    def components(self, integration: str | None = None) -> dict[str, Any]:
        from evoab.recipe.components import catalog
        try:
            return catalog(integration)
        except ValueError as error:
            raise NotFound(str(error)) from error

    def run_graph(self, run_id: str) -> dict[str, Any]:
        """The recipe a run used, as a graph annotated with executed tool calls."""
        from evoab.recipe.graph import annotate_with_trace, recipe_graph
        from evoab.recipe.schema import Recipe
        run_dir = self.run_dir(run_id)
        try:
            recorded = json.loads((run_dir / "recipe.json").read_text(encoding="utf-8"))
            recipe = Recipe.model_validate(recorded["recipe"])
        except (OSError, ValueError, KeyError) as error:
            raise NotFound("this run has no recorded recipe") from error
        graph = annotate_with_trace(recipe_graph(recipe), run_dir)
        graph["overrides"] = recorded.get("overrides") or {}
        return graph

    # -- benchmarks -----------------------------------------------------------

    def team_tools(self, integration: str) -> dict[str, list[str]]:
        from evoab.apps.workbench.team import team_catalog
        try:
            return team_catalog(integration)
        except ValueError as error:
            raise Invalid(str(error)) from error

    def team_preview(self, integration: str, payload: dict, *, graph: bool = False) -> dict:
        from evoab.recipe.graph import recipe_graph
        from evoab.apps.workbench.launcher import resolve_run_recipe
        try:
            request = LaunchRequest(integration, "", "", "", method=payload["method"],
                                    agent=payload.get("agent"), team=payload["team"])
            request, method, _ = resolve_selection(request)
            recipe = resolve_run_recipe(request, method)
            return recipe_graph(recipe) if graph else recipe.model_dump(mode="json", exclude_defaults=True)
        except (KeyError, ValueError, TypeError) as error:
            raise Invalid(str(error)) from error

    def tool_groups(self, integration: str, method: str, agent: str) -> dict[str, list[str]]:
        try:
            return tool_groups(integration, method, agent)
        except ValueError as error:
            raise Invalid(str(error)) from error
        except RuntimeError as error:
            raise Unavailable(str(error)) from error

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

    def prepare(self, integration: str, profile: str = "default",
                accept_license: str | None = None, image_archive: str | None = None) -> dict[str, Any]:
        """Prepare a runtime: pull or build the benchmark's Docker image."""
        with _admission:
            try:
                directory = None
                if image_archive:
                    from evoab.envs.docker.image_import import import_directory
                    # Resolved once, here: the setup child receives this path and later setting
                    # edits cannot redirect a job that is already queued.
                    directory = import_directory(directory=self.settings().get("image_import_dir"))
                pid = benchmarks.start_setup(self.runs_root, integration, profile, None,
                                             environment=build_mirrors(), accept_license=accept_license,
                                             image_archive=image_archive, image_directory=directory)
            except ValueError as error:
                raise Conflict(str(error)) from error
        return {"pid": pid, "integration": integration, "profile": profile, "container": True}

    def uninstall(self, integration: str, profile: str = "default") -> dict[str, Any]:
        """Remove one prepared Docker deployment's records and local runtime image.

        Experiment runs, import archives, shared upstream images, build caches and
        host assets stay; a busy setup, run or container refuses the removal.
        """
        from evoab.envs.docker import container
        with _admission:
            try:
                return benchmarks.uninstall(self.runs_root, integration, profile)
            except ValueError as error:
                raise Conflict(str(error)) from error
            except container.RuntimeInUse as error:
                raise Conflict(str(error)) from error
            except container.ContainerError as error:
                raise Unavailable(str(error)) from error

    def image_imports(self) -> dict[str, Any]:
        """Archives in the configured server directory, with its expanded path."""
        from evoab.envs.docker.image_import import available_archives, default_directory, import_directory
        setting = self.settings().get("image_import_dir")
        try:
            listing = available_archives(import_directory(directory=setting))
        except ValueError as error:
            raise Unavailable(str(error)) from error
        return {**listing, "configured": setting, "default_directory": str(default_directory())}

    def system_check(self) -> dict[str, Any]:
        """What a machine needs before one-click benchmark deployment, each with a fix."""
        import shutil
        import subprocess
        from evoab.agent.models import catalog
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
        mirrors = build_mirrors()
        add("network", True, "mainland China mirrors (Tsinghua, hf-mirror)" if mirrors else "public package and model hubs")
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    # -- models: providers, presets and usage ---------------------------------

    @staticmethod
    def _catalog(operation, *args, **kwargs):
        from evoab.agent.models import catalog
        try:
            return operation(catalog)(*args, **kwargs)
        except catalog.CatalogError as error:
            raise (Conflict if error.status == 409 else NotFound if error.status == 404 else Invalid)(str(error)) from error
        except RuntimeError as error:
            raise Unavailable(str(error)) from error

    def model_settings(self, agent: str | None = None) -> dict[str, Any]:
        if agent is None:
            return self._catalog(lambda c: c.settings)
        try:
            launch_config = agent_launch(agent)
        except ValueError as error:
            raise Invalid(str(error)) from error
        if launch_config is None:
            raise Invalid(f"Agent {agent!r} is unavailable in Workbench")
        settings = self._catalog(lambda c: c.settings)
        if launch_config.validate_preset is None:
            return settings
        presets, incompatible = [], []
        for preset in settings["presets"]:
            try:
                launch_config.validate_preset(preset["name"])
            except ValueError as error:
                reason = (str(error) if isinstance(error, PresetCompatibilityError)
                          else "This preset could not be used with the selected Agent. Check its provider settings.")
                incompatible.append({"name": preset["name"], "model": preset.get("model"), "reason": reason})
            except RuntimeError as error:
                raise Unavailable(str(error)) from error
            else:
                presets.append(preset)
        return {**settings, "presets": presets, "incompatible_presets": incompatible}

    def probe_model(self, agent: str, preset: str, reasoning_effort: str) -> dict[str, Any]:
        if reasoning_effort not in REASONING_EFFORTS:
            raise Invalid(f"reasoning effort must be one of {REASONING_EFFORTS}")
        try:
            launch_config = agent_launch(agent)
        except ValueError as error:
            raise Invalid(str(error)) from error
        if launch_config is None or launch_config.probe_preset is None:
            raise Invalid("Model verification is unavailable for this Agent.")
        try:
            return launch_config.probe_preset(preset, reasoning_effort)
        except PresetCompatibilityError as error:
            raise Invalid(str(error)) from None
        except RuntimeError as error:
            raise Unavailable("Could not prepare model verification.") from error

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
        with _admission:
            try:
                request = LaunchRequest(**payload)
                request, _, _ = resolve_selection(request)
                task = validate(request)
            except (TypeError, ValueError) as error:
                raise Invalid(str(error)) from error
            except RuntimeError as error:
                raise Unavailable(str(error)) from error
            launch_id = launch(request, task, self.runs_root)
            self.update_settings({"default_model": request.model, "default_effort": request.reasoning_effort,
                                  "default_method": request.method, "default_agent": request.agent})
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
