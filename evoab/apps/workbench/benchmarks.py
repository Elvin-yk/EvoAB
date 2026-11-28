"""Benchmark registry, preparation status and setup jobs.

Every integration in ``evoab/envs`` appears here with how it is
prepared: ``container`` integrations run ``evoab images pull`` to pull or build their
Docker image (per runtime profile where they have several), ``custom`` ones
wrap a user's own image. Status comes
from the runtime records that setup writes under the cache directory.
"""

from __future__ import annotations

import ast
import json
import shlex
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import yaml

from evoab import __version__
from evoab.envs.docker.config import adapter_for
from evoab.envs.docker.runtime import cache_root, runtime_root
from evoab.apps.workbench import jobs


@dataclass(frozen=True)
class BenchmarkInfo:
    integration: str
    title: str
    simulator: str
    embodiment: str
    setup: str  # "container" | "custom"
    description: str
    profiles: tuple[str, ...] = field(default_factory=tuple)
    requirements: str = ""
    task_selector: Literal["task", "suite/task", "profile"] | None = None


REGISTRY: tuple[BenchmarkInfo, ...] = (
    BenchmarkInfo("libero", "LIBERO", "MuJoCo (robosuite 1.4)", "Franka Panda",
                  "container", "Original LIBERO task suites and official initial states",
                  requirements="Public AllenAI LIBERO image; NVIDIA GPU", task_selector="suite/task"),
    BenchmarkInfo("libero_pro", "LIBERO-PRO", "MuJoCo (robosuite)", "Franka Panda",
                  "container", "LIBERO suites under instruction and position perturbations",
                  requirements="Public AllenAI LIBERO-PRO image; NVIDIA GPU", task_selector="suite/task"),
    BenchmarkInfo("calvin", "CALVIN", "PyBullet", "Franka Panda",
                  "container", "Single-task CALVIN episodes",
                  requirements="Public AllenAI CALVIN image; NVIDIA GPU", task_selector="task"),
    BenchmarkInfo("custom", "Custom benchmark", "—", "—", "custom",
                  "User-defined Gym-style benchmark in the user's Docker image", task_selector="profile"),
)


_BY_INTEGRATION = {info.integration: info for info in REGISTRY}


def launch_target(integration: str, profile: str, task: str) -> tuple[str | None, str]:
    info = _BY_INTEGRATION.get(integration)
    if not info or not info.task_selector:
        raise ValueError(f"Studio cannot launch {integration} runs yet")
    if info.task_selector == "suite/task":
        suite, name = task.split("/", 1)
        return suite, name
    return (profile if info.task_selector == "profile" else None), task


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
    python: str
    recipe_digest: str
    native_root: Path | None
    container: dict[str, Any] | None = None


_parsed: dict[Path, tuple[tuple[int, int, int, int], PreparedRuntime | None]] = {}


def prepared_runtimes(root: Path | None = None) -> list[PreparedRuntime]:
    """Runtimes prepared by ``evoab images pull``; parsed once per runtime.json change."""
    root = root or cache_root()
    runtimes = []
    for record in sorted(root.glob("*/*/runtime.json")):
        stat = record.stat()
        # Setup replaces records atomically, even on mounts with coarse timestamps.
        fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = _parsed.get(record)
        if cached is None or cached[0] != fingerprint:
            cached = (fingerprint, _parse_runtime(record))
            _parsed[record] = cached
        runtime = cached[1]
        if runtime is None or not runtime.container:
            continue  # records of native interpreters predate container-only runtimes
        if runtime.container.get("imported_image") and not runtime.validation.get("launch_verified"):
            continue
        if _BY_INTEGRATION[runtime.integration].task_selector:
            args = SimpleNamespace(integration=runtime.integration, runtime_profile=runtime.profile, cache_dir=root)
            try:
                recipe = adapter_for(args).recipe(args)
            except (FileNotFoundError, ValueError):
                continue
            from evoab.envs.docker.container import adapter_spec
            # A runtime built from an older container recipe must be deployed again.
            try:
                spec = adapter_spec(args, adapter_for(args))
            except ValueError:
                continue
            expected_root = runtime_root(args, recipe)
            local = runtime.container.get("recipe_digest") == spec.digest
            if (runtime.root.resolve() != expected_root.resolve() or runtime.recipe_digest != recipe.digest
                    or runtime.package_version != __version__ or not local):
                continue
        runtimes.append(runtime)
    return runtimes


def _integration_task_metadata(integration: str, task: str, native_root: str | None = None) -> dict[str, Any]:
    try:
        from evoab.envs import load_integration
        describe = getattr(load_integration(integration), "task_metadata", None)
        if describe is None:
            return {}
        return describe(task, native_root) if native_root else describe(task)
    except Exception:  # noqa: BLE001 - a catalog without extra facts still lists its tasks.
        return {}


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
    payload = data.get("data") or {}
    selector = _BY_INTEGRATION[integration].task_selector
    from evoab.envs.docker.config import RECORD_SCHEMA
    if selector and (data.get("schema_version") != RECORD_SCHEMA or name != record.parent.parent.name
                     or Path(data.get("root") or ".").resolve() != record.parent.resolve()):
        return None
    tasks = {}
    for task_id, entry in (payload.get("tasks") or {}).items():
        if selector == "suite/task" and "/" not in task_id:
            continue
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
        if selector:
            runtime_profile, task_name = launch_target(integration, profile, task_id)
            tasks[task_id].update(runtime_profile=runtime_profile, native_task=task_name)
        # Integration-owned catalog facts.
        facts = _integration_task_metadata(integration, task_id, payload.get("native_root"))
        tasks[task_id].update({key: value for key, value in facts.items()
                               if value is not None or key not in tasks[task_id]})
    try:
        validation = json.loads((record.parent / "validation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        validation = {}
    return PreparedRuntime(integration, profile, record.parent, record.stat().st_mtime,
                           str(data.get("package_version", "")), tasks, validation, str(data.get("python") or "."),
                           str(data.get("recipe_digest", "")), Path(payload["native_root"]) if payload.get("native_root") else None,
                           payload.get("container"))


def _setup_dir(runs_root: Path) -> Path:
    return runs_root / ".studio" / "setup"


def _setup_record(runs_root: Path, integration: str, profile: str) -> Path:
    return _setup_dir(runs_root) / f"{integration}-{profile}.json"


def preferred_runtimes() -> dict[tuple[str, str], PreparedRuntime]:
    """One runtime per (integration, profile)."""
    result: dict[tuple[str, str], PreparedRuntime] = {}
    for runtime in prepared_runtimes():
        result.setdefault((runtime.integration, runtime.profile), runtime)
    return result


def container_info(integration: str) -> dict[str, Any] | None:
    """The integration's container recipe as Studio shows it, or None."""
    import shutil
    from evoab.envs.docker.container import spec_for
    try:
        spec = spec_for(integration)
    except ValueError:
        return None
    if spec is None:
        return None
    return {"source": "image" if spec.image else "build", "image": spec.image, "dockerfile": spec.dockerfile,
            "gpu": spec.gpu, "license": spec.license, "docker": shutil.which("docker") is not None}


def overview(runs_root: Path) -> list[dict[str, Any]]:
    """Every registered benchmark with the status of each runtime profile."""
    prepared = preferred_runtimes()
    items = []
    for info in REGISTRY:
        if info.integration == "custom":
            items += _custom_items(runs_root, prepared)
            continue
        container = container_info(info.integration)
        profiles = info.profiles or ("default",)
        entries = []
        for profile in profiles:
            runtime = prepared.get((info.integration, profile))
            if runtime is None and profile == "default":
                runtime = next((r for (i, _), r in prepared.items() if i == info.integration), None)
            record_path = _setup_record(runs_root, info.integration, profile)
            job = jobs.read_record(record_path)
            failed = job is not None and job.get("exit_code") not in (None, 0) and not job.get("uninstalled_at")
            if job and jobs.is_running(job):
                status = "preparing"
            elif failed and runtime is not None and runtime.prepared_at >= float(job.get("started_at") or 0):
                # This deployment published its runtime but the launch check then failed.
                status = "setup_failed"
            elif runtime is not None:
                status = "ready"
            elif failed:
                status = "setup_failed"
            elif container is not None:
                status = "not_prepared"
            else:
                status = info.setup  # an integration without a container recipe
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
                "launchable": (status == "ready" and runtime is not None and info.task_selector is not None
                               and bool(runtime.tasks)),
                "empty": (status == "ready" and runtime is not None and info.task_selector is not None
                          and not runtime.tasks),
                "last_setup_failed": failed,
                "installed": runtime is not None,
                "setup_log": jobs.log_tail(record_path, 12) if job else "",
                "image": (runtime.container or {}).get("image") if runtime else None,
            })
        items.append({
            "container": container,
            # Studio deploys a benchmark in one step when it has a Docker recipe.
            "deploy": "container" if container else None,
            "integration": info.integration,
            "title": info.title,
            "kind": "simulation",  # Studio groups benchmarks by kind
            "simulator": info.simulator,
            "embodiment": info.embodiment,
            "setup": info.setup,
            "description": info.description,
            "requirements": info.requirements,
            "profiles": entries,
        })
    return items


def _custom_items(runs_root: Path, prepared: dict) -> list[dict[str, Any]]:
    """One entry per registered custom benchmark (runtime profile = benchmark name)."""
    from evoab.envs.custom import manifest as manifests
    items = []
    for name in manifests.registered():
        try:
            manifest = manifests.load(name)
        except ValueError as error:
            items.append({"integration": "custom", "title": name, "kind": "simulation", "simulator": "", "embodiment": "",
                          "setup": "custom", "description": f"Invalid manifest: {error}", "requirements": "", "profiles": []})
            continue
        runtime = prepared.get(("custom", name))
        record_path = _setup_record(runs_root, "custom", name)
        job = jobs.read_record(record_path)
        status = ("preparing" if job and jobs.is_running(job) else "ready" if runtime is not None
                  else "setup_failed" if job and job.get("exit_code") not in (None, 0) else "not_prepared")
        items.append({
            "integration": "custom", "title": manifest.title, "kind": "simulation", "simulator": manifest.simulator,
            "embodiment": manifest.embodiment, "setup": "custom", "description": manifest.description,
            "requirements": f"{manifest.make} · {manifest.image}", "name": name,
            "profiles": [{"profile": name, "status": status, "task_count": len(runtime.tasks) if runtime else 0,
                          "suites": {name: len(runtime.tasks)} if runtime else {},
                          "prepared_at": runtime.prepared_at if runtime else None,
                          "package_version": runtime.package_version if runtime else None,
                          "launchable": bool(runtime and runtime.tasks),
                          "empty": status == "ready" and runtime is not None and not runtime.tasks,
                          "setup_log": jobs.log_tail(record_path, 12) if job else ""}],
        })
    return items


def demo_file(runtime: PreparedRuntime, task: str) -> Path | None:
    if runtime.integration == "libero_pro" and task in runtime.tasks:
        from evoab.envs.libero_pro.demo_assets import preview_path
        return preview_path(runtime.root, task)
    return None


def runtime_for_launch(integration: str, profile: str) -> PreparedRuntime | None:
    return preferred_runtimes().get((integration, profile))


def _shown_runtime(integration: str, profile: str, prepared: dict) -> PreparedRuntime | None:
    """The runtime Studio's row for this profile shows (its overview fallback included)."""
    runtime = prepared.get((integration, profile))
    if runtime is None and profile == "default":
        runtime = next((entry for (name, _), entry in prepared.items() if name == integration), None)
    return runtime


def _recorded_images(root: Path) -> list[tuple[str, str]]:
    """(deployment, image) for every other runtime record naming a Docker image.

    Raw records count, not only currently ready ones: an older or stale record still
    depends on its image, so its reference must survive this deployment's cleanup.
    """
    from evoab.envs.docker.container import is_container, parse

    target = Path(root).resolve()
    found = []
    for record in sorted(cache_root().glob("*/*/runtime.json")):
        if record.parent.resolve() == target:
            continue
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        payload = data.get("data") if isinstance(data, dict) else None
        deployment = (payload or {}).get("container") if isinstance(payload, dict) else None
        image = deployment.get("image") if isinstance(deployment, dict) else None
        python = str(data.get("python") or "") if isinstance(data, dict) else ""
        if not image and is_container(python):
            image = parse(python)[0]
        if image:
            found.append((record.parent.parent.name, str(image)))
    return found


def uninstall(runs_root: Path, integration: str, profile: str = "default", *, timeout: float = 5.0) -> dict[str, Any]:
    """Remove one Docker deployment's image and records; keep runs, assets and caches.

    The target is the runtime Studio shows for that profile, so this never removes
    another profile's installation. Active setup and Studio runs are refused here;
    the CLI's own launches are protected only by Docker's non-force image removal.
    """
    from evoab.envs.docker import container as runtime_images

    info = _BY_INTEGRATION.get(integration)
    if info is None or info.integration == "custom":
        raise ValueError(f"unknown benchmark {integration!r}")
    if container_info(integration) is None:
        raise ValueError(f"{integration} has no Docker recipe to uninstall")
    if profile not in (info.profiles or ("default",)):
        raise ValueError(f"unknown profile {profile!r} for {integration}")
    for record_path in _setup_dir(runs_root).glob(f"{integration}-*.json"):
        job = jobs.read_record(record_path)
        if job is not None and jobs.is_running(job):
            raise ValueError(f"{integration} is being prepared; wait for deployment to finish")
    from evoab.apps.workbench.runs import load_launches
    if any(item.get("integration") == integration and item.get("alive") for item in load_launches(runs_root)):
        raise ValueError(f"{integration} has a running evaluation; stop it first")
    runtime = _shown_runtime(integration, profile, preferred_runtimes())
    if runtime is None:
        raise ValueError(f"{integration}/{profile} is not deployed")
    if not runtime.root.resolve().is_relative_to(cache_root().resolve()):
        raise ValueError("The recorded runtime is outside the EvoAB cache; not uninstalling it")
    try:
        result = runtime_images.uninstall_runtime(runtime.root, integration,
                                                  others=_recorded_images(runtime.root), timeout=timeout)
    except TimeoutError as error:
        raise ValueError(f"{integration} is being prepared; wait for deployment to finish") from error
    # Keep the setup log; only its failed status would outlive the deployment it described.
    for name in {profile, runtime.profile}:
        record_path = _setup_record(runs_root, integration, name)
        if record_path.is_file():
            jobs.update_record(record_path, uninstalled_at=time.time())
    remaining = _shown_runtime(integration, profile, preferred_runtimes())
    return {**result, "integration": integration, "profile": profile, "runtime_profile": runtime.profile,
            "remaining": {"profile": remaining.profile} if remaining else None}


def tasks_for(integration: str, profile: str) -> list[dict[str, Any]]:
    runtime = runtime_for_launch(integration, profile)
    if runtime is not None:
        rows = []
        for task, entry in runtime.tasks.items():
            if demo_file(runtime, task):
                entry = {**entry, "demo_url": f"/api/benchmarks/{integration}/{profile}/demos/{task}"}
            rows.append(entry)
        return sorted(rows, key=lambda t: (t["suite"], t["task"]))
    return []


def task_info(integration: str, profile: str, task: str) -> dict[str, Any] | None:
    return next((t for t in tasks_for(integration, profile) if t["task"] == task), None)


def start_setup(runs_root: Path, integration: str, profile: str, wheel: str | None = None, *,
                environment: dict[str, str] | None = None,
                accept_license: str | None = None, image_archive: str | None = None,
                image_directory: str | Path | None = None) -> int:
    """Run ``evoab images pull`` (then ``evoab images check``) for one benchmark in the background.

    ``wheel`` is the evoab-runtime wheel baked into the image; without it the image
    holds only EvoAB's dependencies and runs mount the code. ``image_directory`` fixes the directory
    the archive name resolves in; the pull child receives it as
    ``EVOAB_IMAGE_IMPORT_DIR``, so both the pre-check and the child's import read
    the same path chosen when the job started.
    """
    if image_archive:
        from evoab.envs.docker.image_import import DIRECTORY_ENV, archive_path, import_directory
        directory = import_directory(directory=image_directory)
        archive_path(image_archive, directory=directory)
        if integration == "custom":
            raise ValueError("Image import requires a supported container deployment")
        environment = {**(environment or {}), DIRECTORY_ENV: str(directory)}
    info = next((b for b in REGISTRY if b.integration == integration), None)
    record_path = _setup_record(runs_root, integration, profile)
    existing = jobs.read_record(record_path)
    if existing and jobs.is_running(existing):
        raise ValueError(f"{integration}/{profile} is already being prepared")
    evoab = [sys.executable, "-m", "evoab", "images"]
    check = None
    if integration == "custom":
        from evoab.envs.custom import manifest as manifests
        manifests.load(profile)
        pull = [*evoab, "pull", profile]
    else:
        if container_info(integration) is None:
            raise ValueError(f"{integration} has no container recipe")
        if info is not None and info.profiles and profile not in info.profiles:
            raise ValueError(f"unknown profile {profile!r} for {integration}")
        pull = [*evoab, "pull", integration]
        if image_archive:
            pull += ["--archive", image_archive]  # an imported image is launch-checked while importing
        else:
            check = [*evoab, "check", integration]
        if accept_license:
            pull += ["--accept-license", accept_license]
    if wheel:
        wheel_path = Path(wheel).expanduser()
        if not wheel_path.is_file():
            raise ValueError(f"runtime wheel not found: {wheel_path}")
        pull += ["--wheel", str(wheel_path)]
    command = pull if check is None else ["sh", "-c", f"{shlex.join(pull)} && {shlex.join(check)}"]
    # Unbuffered so pull/build progress reaches the setup log Studio shows while it runs.
    return jobs.start(command, record_path, env={**os.environ, **(environment or {}), "PYTHONUNBUFFERED": "1"},
                      integration=integration, profile=profile, container=True, requested_at=time.time())
