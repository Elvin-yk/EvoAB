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

from evoab.agents.registry import agent_launch
from evoab.provider.contracts import PresetCompatibilityError
from evoab.workbench import benchmarks, direct_trace, jobs, trace, trash
from evoab.workbench.launcher import (
    DIRECT_OPTIONS, REASONING_EFFORTS, LaunchRequest, launch, method_identity, methods, resolve_selection, tool_groups, validate,
)
from evoab.workbench.runs import STUDIO_DIR, load_launches, scan_runs, usage_totals

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
        defaults["default_method"], defaults["default_agent"] = method_identity(
            defaults["default_method"], defaults["default_agent"],
        )
        return defaults

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(SETTING_KEYS)
        if unknown:
            raise Invalid(f"unknown settings: {', '.join(sorted(unknown))}")
        if any(value is not None and not isinstance(value, str) for value in changes.values()):
            raise Invalid("Studio defaults must be strings or null")
        if "image_import_dir" in changes:
            from evoab.services.deployment.image_import import normalize_directory
            try:
                changes = {**changes, "image_import_dir": normalize_directory(changes["image_import_dir"])}
            except ValueError as error:
                # Raised before writing, so an invalid path never replaces the saved one.
                raise Invalid(str(error)) from error
        current = self.settings()
        if "default_method" in changes and "default_agent" not in changes:
            current["default_agent"] = None
        current.update(changes)
        current["default_method"], current["default_agent"] = method_identity(
            current["default_method"], current["default_agent"],
        )
        path = self.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        return current

    def config(self) -> dict[str, Any]:
        return {
            "runs_root": str(self.runs_root),
            "reasoning_efforts": REASONING_EFFORTS,
            "methods": methods(),
            "direct_options": {k: list(v[1]) if v[1] else None for k, v in DIRECT_OPTIONS.items()},
            "settings": self.settings(),
        }

    # -- benchmarks -----------------------------------------------------------

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

    def prepare(self, integration: str, profile: str = "default", container: bool = False,
                accept_license: str | None = None, image_archive: str | None = None) -> dict[str, Any]:
        """Prepare a runtime; ``container`` pulls or builds the benchmark's Docker image instead."""
        with _admission:
            try:
                directory = None
                if image_archive:
                    from evoab.services.deployment.image_import import import_directory
                    # Resolved once, here: the setup child receives this path and later setting
                    # edits cannot redirect a job that is already queued.
                    directory = import_directory(directory=self.settings().get("image_import_dir"))
                pid = benchmarks.start_setup(self.runs_root, integration, profile, None, container=container,
                                             environment=build_mirrors(), accept_license=accept_license,
                                             image_archive=image_archive, image_directory=directory)
            except ValueError as error:
                raise Conflict(str(error)) from error
        return {"pid": pid, "integration": integration, "profile": profile, "container": container}

    def uninstall(self, integration: str, profile: str = "default") -> dict[str, Any]:
        """Remove one prepared Docker deployment's records and local runtime image.

        Experiment runs, import archives, shared upstream images, build caches and
        host assets stay; a busy setup, run or container refuses the removal.
        """
        from evoab.services.deployment import container
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
        from evoab.services.deployment.image_import import available_archives, default_directory, import_directory
        setting = self.settings().get("image_import_dir")
        try:
            listing = available_archives(import_directory(directory=setting))
        except ValueError as error:
            raise Unavailable(str(error)) from error
        return {**listing, "configured": setting, "default_directory": str(default_directory())}

    def system_check(self) -> dict[str, Any]:
        """Report native readiness separately from optional container prerequisites."""
        import shutil
        import subprocess
        from evoab.provider import catalog
        checks = []

        launchable = {info.integration for info in benchmarks.REGISTRY if info.task_selector}
        native = [runtime for runtime in benchmarks.prepared_runtimes()
                  if runtime.kind == "native" and runtime.tasks
                  and runtime.integration in launchable]

        def add(key, ok, detail, fix="", *, required=True):
            checks.append({"key": key, "ok": bool(ok), "required": required,
                           "detail": detail, "fix": "" if ok or not required else fix})

        add("native_runtimes", native,
            ", ".join(sorted({runtime.integration for runtime in native})) or "No prepared native task runtimes",
            required=False)

        docker = shutil.which("docker")
        add("docker", docker, docker or "docker not found", "Install Docker Engine (docs.docker.com/engine/install).",
            required=not native)
        info = None
        if docker:
            result = subprocess.run([docker, "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True,
                                    timeout=30)
            info = result.stdout if result.returncode == 0 else None
            add("docker_access", info is not None, "Docker daemon reachable" if info else (result.stderr.strip()[-200:]),
                "Add this user to the docker group (sudo usermod -aG docker $USER), then log in again.",
                required=not native)
        toolkit = shutil.which("nvidia-ctk") or shutil.which("nvidia-container-runtime")
        add("gpu_containers", toolkit and info and "nvidia" in info,
            "NVIDIA Container Toolkit configured" if toolkit and info and "nvidia" in info else "GPU containers unavailable",
            "Install nvidia-container-toolkit, run sudo nvidia-ctk runtime configure --runtime=docker, restart Docker.",
            required=not native)
        if native:
            driver = shutil.which("nvidia-smi")
            try:
                result = subprocess.run([driver, "--query-gpu=name", "--format=csv,noheader"],
                                        capture_output=True, text=True, timeout=15) if driver else None
                gpu_ok = result is not None and result.returncode == 0 and bool(result.stdout.strip())
                detail = result.stdout.strip() if gpu_ok else "NVIDIA driver unavailable"
            except (OSError, subprocess.TimeoutExpired):
                gpu_ok, detail = False, "NVIDIA driver check failed"
            add("native_gpu", gpu_ok, detail, "A working NVIDIA driver is required for GPU rendering.")
        storage = benchmarks.cache_root()
        while not storage.exists():
            storage = storage.parent
        free = shutil.disk_usage(storage).free / 2**30
        add("disk", free >= 120, f"{free:.0f} GiB free at {storage}", "Free at least 120 GiB (images and assets).")
        try:
            presets = catalog.presets()
        except RuntimeError as error:
            presets = []
            add("agent_runtime", False, str(error), "pip install 'evoab[agent]'")
        add("model_presets", presets, f"{len(presets)} model presets",
            "Add a provider and a model preset under 设置 → 模型.")
        mirrors = build_mirrors()
        add("network", True, "mainland China mirrors (Tsinghua, hf-mirror)" if mirrors else "public package and model hubs")
        return {"ok": all(c["ok"] for c in checks if c["required"]), "checks": checks,
                "runtime_mode": "native" if native else "container"}

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

    def research_campaigns(self) -> list[dict[str, Any]]:
        from evoab.workbench.research import list_campaigns
        return list_campaigns(self.runs_root)

    def start_research(self, payload: dict[str, Any]) -> dict[str, Any]:
        from evoab.workbench.research import start
        with _admission:
            try:
                return start(self.runs_root, payload)
            except (ValueError, RuntimeError) as error:
                raise Invalid(str(error)) from error

    def research_action(self, ref: str, action: str) -> dict[str, Any]:
        from evoab.workbench import research
        with _admission, trash.locked(self.runs_root):
            try:
                return (research.stop if action == "stop" else research.resume)(self.runs_root, ref)
            except (ValueError, OSError) as error:
                raise Conflict(str(error)) from error

    def recycle_research(self, refs: list[str]) -> dict[str, Any]:
        from evoab.workbench.research import directory, summary
        refs = self._selection(refs)
        with _admission, trash.locked(self.runs_root):
            try:
                records = []
                for ref in refs:
                    campaign = summary(directory(self.runs_root, ref))
                    config = campaign['config']
                    records.append({'research_id': ref, 'id': None, 'launch_id': None,
                        'short_id': ref.removeprefix('research-'), 'task': config.get('task'),
                        'instruction': campaign.get('label') or config.get('task') or ref,
                        'status': campaign['state'], 'model': config.get('model'),
                        'integration': config.get('integration'),
                        'completed_rounds': campaign['completed_rounds'], 'rounds': config.get('rounds'),
                        'started_at': campaign.get('started_at')})
                items = trash.recycle(self.runs_root, records, category='research')
            except (ValueError, OSError) as error:
                raise Conflict(str(error)) from error
        return {'recycled': [e['trash_id'] for e in items]}

    @staticmethod
    def _selection(values: list[str]) -> list[str]:
        if (not isinstance(values, list) or not values
                or any(not isinstance(v, str) or not v for v in values)):
            raise Invalid("Select at least one task record")
        return list(dict.fromkeys(values))

    def recycle_runs(self, refs: list[str]) -> dict[str, Any]:
        refs = self._selection(refs)
        with _admission, trash.locked(self.runs_root):
            visible = self.runs()
            runs = [self._find_run(ref, visible) for ref in refs]
            runs = list({r["key"]: r for r in runs}.values())
            try:
                items = trash.recycle(self.runs_root, runs)
            except (trash.TrashError, OSError) as error:
                raise Conflict(str(error)) from error
        return {"recycled": [e["trash_id"] for e in items]}

    def recycled_runs(self) -> list[dict[str, Any]]:
        return [{**e["run"], "category": e.get("category", "run"),
                 "trash_id": e["trash_id"], "deleted_at": e["deleted_at"]}
                for e in sorted(trash.entries(self.runs_root), key=lambda e: e["deleted_at"], reverse=True)]

    def restore_runs(self, ids: list[str]) -> dict[str, Any]:
        ids = self._selection(ids)
        with _admission, trash.locked(self.runs_root):
            try:
                trash.restore(self.runs_root, ids)
            except (trash.TrashError, OSError) as error:
                raise Conflict(str(error)) from error
        return {"restored": ids}

    def purge_runs(self, ids: list[str]) -> dict[str, Any]:
        ids = self._selection(ids)
        with _admission, trash.locked(self.runs_root):
            try:
                trash.purge(self.runs_root, ids)
            except (trash.TrashError, OSError) as error:
                raise Conflict(str(error)) from error
        return {"deleted": ids}

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
        return self._find_run(ref, self.runs())

    @staticmethod
    def _find_run(ref: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
        ref = ref.lstrip("#")
        matches = [r for r in runs if ref in (r.get("id"), r.get("short_id"), r.get("launch_id"))]
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
        if launch_id in trash.hidden_ids(self.runs_root)[1]:
            raise NotFound("task is in the recycle bin")
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
        if run_id in trash.hidden_ids(self.runs_root)[0]:
            raise NotFound("task is in the recycle bin")
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
