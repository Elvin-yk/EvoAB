"""Run a registered custom benchmark in the user's own Docker image.

The runtime profile is the benchmark name; ``evoab setup --integration custom
--runtime-profile NAME`` adds EvoAB's service layer to the manifest's image,
records its tasks and checks that the image imports both the benchmark and the
EvoAB runtime.
"""
import hashlib
import json

from evoab.services.deployment.config import LaunchPlan, Recipe
from evoab.services.deployment.runtime import query_python

from . import manifest as manifests


def _manifest(args):
    if not args.runtime_profile:
        raise ValueError("Select the custom benchmark with --runtime-profile NAME")
    return manifests.load(args.runtime_profile)


def recipe(args):
    text = manifests.path_for(args.runtime_profile or "").read_bytes() if args.runtime_profile else b""
    return Recipe(f"custom-{args.runtime_profile}", __package__, "manifest", {}, hashlib.sha256(text).hexdigest())


def validate(args):
    _manifest(args)


_LIST = ("import json, sys; from evoab.integrations.custom.adapter import list_tasks; "
         "print(json.dumps(list_tasks(json.loads(sys.argv[1]))))")


def _environment(manifest):
    return {key: str(value) for key, value in manifest.environment.items()}


def container_spec(args):
    """The user's image is the base; EvoAB's service layer is built on top of it."""
    from evoab.services.deployment.container import ContainerSpec
    manifest = _manifest(args)
    text = manifests.path_for(manifest.name).read_text(encoding="utf-8")
    return ContainerSpec(integration=f"custom-{manifest.name}", package=__package__, python=manifest.python,
                         image=manifest.image, gpu=manifest.gpu, environment=_environment(manifest), source=text)


def _tasks(manifest, tasks):
    return {t["id"]: {"spec": {
        "backend": manifest.name, "robots": [manifest.embodiment], "scenario": {"title": t["instruction"]},
        "max_steps": t.get("max_steps") or manifest.max_steps}} for t in tasks}


def container_data(args, root, report):
    """Host side: the manifest, its declared tasks, and the host directories mounted into the container."""
    from pathlib import Path
    manifest = _manifest(args)
    mounts = [str(Path(path).expanduser().resolve()) for path in ([manifest.path] if manifest.path else []) + manifest.mounts]
    missing = [path for path in mounts if not Path(path).is_dir()]
    if missing:
        raise ValueError(f"Custom benchmark directories not found: {', '.join(missing)}")
    data = manifest.model_dump(mode="json")
    return {"manifest": data, "tasks": _tasks(manifest, data["tasks"]) if manifest.tasks else {}, "mounts": mounts}


def container_finalize(python, data, report):
    """List ``tasks_from`` inside the built image."""
    manifest = manifests.Manifest.model_validate(data["manifest"])
    if not manifest.tasks:
        result = query_python(python, _LIST, json.dumps(data["manifest"]), environment=_environment(manifest))
        data["tasks"] = _tasks(manifest, result if isinstance(result, list) else [])
    if not data["tasks"]:
        raise ValueError("The benchmark declares no tasks")


_VERIFY = ("import json, sys; from evoab.integrations.custom.adapter import resolve; from evoab.services import hosting; "
           "m = json.loads(sys.argv[1]); resolve(m.get('path'), m['make']); print(json.dumps({'make': m['make']}))")


def verify(runtime):
    data = runtime.data["manifest"]
    return query_python(runtime.python, _VERIFY, json.dumps(data), environment=runtime.environment)


def plan(args, runtime, output, run_id):
    data = runtime.data["manifest"]
    if args.task not in runtime.data["tasks"]:
        raise ValueError(f"Unknown task {args.task!r} for custom benchmark {data['name']}")
    seed, host = args.seed or 0, args.host
    port = args.port or 18790
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "benchmark_manifest.json"
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    command = (runtime.python, "-m", "evoab.integrations.custom.server", "--manifest-json", str(manifest_path),
               "--task", args.task, "--seed", str(seed), "--run-id", run_id,
               "--output-dir", str(output / "environment"), "--host", host, "--port", str(port))
    expected = {"integration": "custom", "episode_id": run_id, "metadata.benchmark": data["name"],
                "metadata.task_id": args.task, "metadata.seed": seed}
    connect_host = "127.0.0.1" if host == "0.0.0.0" else host
    environment = {**runtime.environment, **{k: str(v) for k, v in data.get("environment", {}).items()}}
    return LaunchPlan(command, f"http://{connect_host}:{port}", expected, environment, output)
