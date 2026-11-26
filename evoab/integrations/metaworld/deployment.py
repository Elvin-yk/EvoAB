"""Build the MetaWorld MT50 image (CPU only) and launch one seeded episode in it."""
from __future__ import annotations

from evoab.services.deployment.config import LaunchPlan, Recipe
from evoab.services.deployment.runtime import check_python, query_python

DEFAULT_PORT = 18786
DEFAULT_MAX_STEPS = 500

def mt50_tasks():
    """The MT50 catalog; runs inside the image."""
    import metaworld

    mt50 = metaworld.MT50(seed=0)
    tasks = {}
    for name in sorted(mt50.train_classes):
        title = name[:-3] if name.endswith(("-v3", "-v2")) else name
        tasks[name] = {"spec": {"backend": "mt50", "robots": ["Sawyer"],
                                "scenario": {"title": title.replace("-", " ")}, "max_steps": DEFAULT_MAX_STEPS}}
    return tasks


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
    if getattr(args, "layout", None) or getattr(args, "task_dir", None):
        raise ValueError("MetaWorld MT50 uses seeded native MT50 tasks")
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("Local MetaWorld MT50 deployment requires --task")
    seed = getattr(args, "seed", None)
    if seed is not None and seed < 0:
        raise ValueError("MetaWorld seed must be nonnegative")


def image_data(args):
    return {"tasks": mt50_tasks()}


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
        runtime.environment,
        output,
    )
