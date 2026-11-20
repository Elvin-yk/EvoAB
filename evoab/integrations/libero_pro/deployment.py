"""Adopt a pinned LIBERO-PRO installation and supervise one native trial per run."""
import ast
from importlib.metadata import version
from pathlib import Path

from evoab.services.deployment.config import LaunchPlan, Recipe
from evoab.services.deployment.runtime import query_python
from .demo_assets import prepare_previews as prepare_previews
from .server import BUDGETS, REVISION, SUITES


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.runtime_profile is not None and args.runtime_profile not in SUITES:
        raise ValueError("Unknown LIBERO-PRO suite")
    if args.command == "setup" and not (args.runtime_python and args.native_root):
        raise ValueError("Supply --runtime-python and --native-root to adopt a LIBERO-PRO installation")


def external_data(args):
    if not args.native_root:
        raise ValueError("LIBERO-PRO requires a pinned --native-root")
    root = args.native_root.expanduser().resolve()
    native = root / "libero/libero"
    # Read the pinned upstream task map as data without importing the simulator.
    module = ast.parse((native / "benchmark/libero_suite_task_map.py").read_text())
    mapping = next(ast.literal_eval(stmt.value) for stmt in module.body
                   if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "libero_task_map"
                                                          for t in stmt.targets))
    tasks = {}
    for suite in SUITES:
        base = next(s for s in BUDGETS if suite == s or suite.startswith(s + "_"))
        for name in mapping.get(suite, []):
            bddl = native / "bddl_files" / suite / (name + ".bddl")
            states = native / "init_files" / suite / (name + ".pruned_init")
            if bddl.is_file() and states.is_file():
                tasks[f"{suite}/{name}"] = {"spec": {"backend": suite, "robots": ["Franka Panda"],
                    "scenario": {"title": name.replace("_", " ")}, "max_steps": BUDGETS[base]}}
    if not tasks:
        raise ValueError("No official tasks with matching BDDL and initial states were found")
    return {"native_root": str(root), "tasks": tasks}


def _verify_native(root):
    from evoab.integrations.simulation import source_root
    from evoab.services import hosting  # noqa: F401 - check service dependencies in the native interpreter

    source_root(Path(root), REVISION)
    if version("robosuite") != "1.4.0":
        raise ValueError("LIBERO-PRO requires robosuite 1.4.0")
    return {"native_revision": REVISION}


def verify(runtime):
    return query_python(runtime.python,
        "import json, sys; from evoab.integrations.libero_pro.deployment import _verify_native; "
        "print(json.dumps(_verify_native(sys.argv[1])))",
        runtime.data["native_root"], environment=runtime.environment)


def plan(args, runtime, output, run_id):
    suite, task = args.runtime_profile, args.task
    if f"{suite}/{task}" not in runtime.data["tasks"]:
        raise ValueError("Select a prepared LIBERO-PRO suite with --runtime-profile and a native name with --task")
    seed = args.seed or 0
    port = args.port or 18782
    host = args.host
    command = (runtime.python, "-m", "evoab.integrations.libero_pro.server",
               "--native-root", runtime.data["native_root"], "--suite", suite, "--task", task,
               "--init-state-index", "0", "--seed", str(seed), "--run-id", run_id,
               "--output-dir", str(output / "environment"), "--host", host, "--port", str(port),
               "--depth" if args.depth else "--no-depth")
    expected = {"integration": "libero_pro", "episode_id": run_id, "metadata.suite": suite,
                "metadata.task_id": task, "metadata.seed": seed, "metadata.init_state_index": 0}
    connect_host = "127.0.0.1" if host == "0.0.0.0" else host
    return LaunchPlan(command, f"http://{connect_host}:{port}", expected, runtime.environment, output)
