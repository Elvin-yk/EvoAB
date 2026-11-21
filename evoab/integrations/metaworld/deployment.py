"""Prepare an isolated MetaWorld MT50 runtime and launch one seeded episode."""
from __future__ import annotations

from evoab.services.deployment.config import LaunchPlan, Recipe, environment_options
from evoab.services.deployment.runtime import check_python, query_python

DEFAULT_PORT = 18786
DEFAULT_MAX_STEPS = 500

_TASKS_SCRIPT = """import json
import metaworld

mt50 = metaworld.MT50(seed=0)
tasks = {}
for name in sorted(mt50.train_classes):
    title = name[:-3] if name.endswith("-v3") else name[:-3] if name.endswith("-v2") else name
    title = title.replace("-", " ")
    tasks[name] = {
        "spec": {
            "backend": "mt50",
            "robots": ["Sawyer"],
            "scenario": {"title": title},
            "max_steps": 500,
        }
    }
print(json.dumps(tasks))
"""

_VERIFY_SCRIPT = """import importlib.metadata
import json
import metaworld

mt50 = metaworld.MT50(seed=0)
count = len(mt50.train_classes)
assert count == 50, f"Expected 50 MT50 tasks, found {count}"
versions = {name: importlib.metadata.version(name) for name in ("metaworld", "mujoco", "gymnasium")}
print(json.dumps({"mt50_tasks": count, "versions": versions}))
"""


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if getattr(args, "runtime_profile", None):
        raise ValueError("MetaWorld MT50 uses one default runtime profile")
    if getattr(args, "worker_python", None) or getattr(args, "native_root", None):
        raise ValueError("MetaWorld MT50 uses the installed metaworld package; omit --native-root/--worker-python")
    if getattr(args, "layout", None) or getattr(args, "task_dir", None):
        raise ValueError("MetaWorld MT50 uses seeded native MT50 tasks")
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("Local MetaWorld MT50 deployment requires --task")
    seed = getattr(args, "seed", None)
    if seed is not None and seed < 0:
        raise ValueError("MetaWorld seed must be nonnegative")


def _query_tasks(python, environment):
    return query_python(python, _TASKS_SCRIPT, environment=environment)


def external_data(args):
    env = environment_options(args.runtime_env)
    return {"tasks": _query_tasks(args.runtime_python, env)}


def refresh_external_data(args, runtime):
    return {"tasks": _query_tasks(runtime.python, runtime.environment)}


def prepare(context):
    packages = context.recipe.settings["packages"]
    context.step("native", lambda: context.pip(*packages))
    context.environment.update(MUJOCO_GL="egl", PYOPENGL_PLATFORM="egl")
    context.data["tasks"] = context.step("tasks", lambda: _query_tasks(context.python, context.environment))


def verify(runtime):
    info = check_python(
        runtime.python,
        ["metaworld", "mujoco", "gymnasium", "numpy"],
        environment=runtime.environment,
    )
    info.update(query_python(runtime.python, _VERIFY_SCRIPT, environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    validate(args)
    tasks = runtime.data.get("tasks") or {}
    if tasks and args.task not in tasks:
        raise ValueError(f"Unknown MetaWorld MT50 task: {args.task}")
    port = args.port or DEFAULT_PORT
    seed = 0 if args.seed is None else args.seed
    command = [
        runtime.python,
        "-m",
        "evoab.integrations.metaworld.server",
        "--task",
        args.task,
        "--run-id",
        run_id,
        "--seed",
        str(seed),
        "--output-dir",
        str(output / "environment"),
        "--host",
        args.host,
        "--port",
        str(port),
    ]
    if getattr(args, "depth", False):
        command.append("--depth")
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(
        tuple(command),
        f"http://{host}:{port}",
        {"integration": "metaworld", "episode_id": run_id, "metadata.task_id": args.task, "metadata.seed": seed},
        {"MUJOCO_GL": "egl", "PYOPENGL_PLATFORM": "egl", **runtime.environment},
        output,
    )
