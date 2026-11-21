"""Run one CALVIN task (scene D, native reset) from a pinned installation or container image.

The benchmark checkout (mees/calvin) contains calvin_env as a submodule; the
native root is that submodule and the benchmark root its parent.
"""
from pathlib import Path

import yaml

from evoab.services.deployment.config import LaunchPlan, Recipe
from evoab.services.deployment.runtime import check_python, query_python

from .server import BENCHMARK_REVISION, REVISION

DEFAULT_PORT = 18788
MAX_STEPS = 360


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if getattr(args, "runtime_profile", None):
        raise ValueError("CALVIN uses one default runtime profile")
    if args.command == "setup" and not getattr(args, "container", False) and not (args.runtime_python and args.native_root):
        raise ValueError("Adopt CALVIN with --runtime-python and --native-root (the calvin_env submodule), or use --container")
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("Local CALVIN deployment requires --task")


def external_data(args):
    if not args.native_root:
        raise ValueError("CALVIN requires --native-root (the calvin_env checkout inside mees/calvin)")
    native = Path(args.native_root).expanduser().resolve()
    benchmark = native.parent
    annotations = yaml.safe_load((benchmark / "calvin_models/conf/annotations/new_playtable_validation.yaml").read_text())
    defined = yaml.safe_load((native / "conf/tasks/new_playtable_tasks.yaml").read_text()).get("tasks", {})
    tasks = {name: {"spec": {"backend": "calvin", "robots": ["Franka Panda"],
                             "scenario": {"title": (annotations[name] or [name.replace("_", " ")])[0]},
                             "max_steps": MAX_STEPS}}
             for name in sorted(annotations) if name in defined}
    if not tasks:
        raise ValueError("No CALVIN task has both a validation annotation and a task definition")
    return {"native_root": str(native), "benchmark_root": str(benchmark), "tasks": tasks}


def verify(runtime):
    info = check_python(runtime.python, ["calvin_env", "hydra", "omegaconf", "pybullet"],
                        environment=runtime.environment)
    info.update(query_python(runtime.python,
        "import json, sys; from pathlib import Path; from evoab.integrations.simulation import source_root; "
        "source_root(Path(sys.argv[1]), sys.argv[3]); source_root(Path(sys.argv[2]), sys.argv[4]); "
        "print(json.dumps({'native_revision': sys.argv[3], 'benchmark_revision': sys.argv[4]}))",
        runtime.data["native_root"], runtime.data["benchmark_root"], REVISION, BENCHMARK_REVISION,
        environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    if args.task not in runtime.data["tasks"]:
        raise ValueError(f"Unknown CALVIN task: {args.task}")
    seed = args.seed or 0
    port = args.port or DEFAULT_PORT
    command = (runtime.python, "-m", "evoab.integrations.calvin.server",
               "--native-root", runtime.data["native_root"], "--benchmark-root", runtime.data["benchmark_root"],
               "--task", args.task, "--scene", "D", "--scene-reset", "--max-steps", str(MAX_STEPS),
               "--seed", str(seed), "--run-id", run_id, "--output-dir", str(output / "environment"),
               "--host", args.host, "--port", str(port))
    connect_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{connect_host}:{port}",
                      {"integration": "calvin", "episode_id": run_id, "metadata.task_id": args.task},
                      runtime.environment, output)
