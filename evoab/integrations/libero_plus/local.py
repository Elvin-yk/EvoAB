"""Shared catalog and launch contract for pinned LIBERO-family installations."""
import ast
from pathlib import Path

from evoab.integrations.simulation import materialized_asset
from evoab.services.deployment.config import LaunchPlan
from .server import BUDGETS


def refresh_catalog(args, runtime, suites, state_path, *, budgets=BUDGETS):
    """Rescan only the installation named by the validated runtime record."""
    recorded = runtime.data.get("native_root")
    if not args.native_root or not recorded:
        raise ValueError("This runtime record has no native LIBERO installation to rescan; "
                         "rerun setup with --native-root.")
    recorded_root = Path(recorded).expanduser().resolve()
    requested = Path(args.native_root).expanduser().resolve()
    if requested != recorded_root:
        raise ValueError(f"This cache entry records the LIBERO installation at {recorded_root}; "
                         f"--native-root resolves to {requested}. Use the recorded installation "
                         "or a separate --cache-dir.")
    return task_catalog(args, suites, state_path, allow_empty=True, budgets=budgets)


def task_catalog(args, suites, state_path, *, allow_empty=False, budgets=BUDGETS):
    if not args.native_root:
        raise ValueError("LIBERO requires a pinned --native-root")
    root = args.native_root.expanduser().resolve()
    native = root / "libero/libero"
    # Read the pinned upstream task map as data without importing the simulator.
    module = ast.parse((native / "benchmark/libero_suite_task_map.py").read_text())
    mapping = next(ast.literal_eval(stmt.value) for stmt in module.body
                   if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "libero_task_map"
                                                          for t in stmt.targets))
    tasks = {}
    for suite in suites:
        base = next(s for s in budgets if suite == s or suite.startswith(s + "_"))
        for name in mapping.get(suite, []):
            bddl = native / "bddl_files" / suite / (name + ".bddl")
            states = state_path(native / "init_files", suite, name)
            if states is not None and materialized_asset(bddl) and materialized_asset(states):
                tasks[f"{suite}/{name}"] = {"spec": {"backend": suite, "robots": ["Franka Panda"],
                    "scenario": {"title": name.replace("_", " ")}, "max_steps": budgets[base]}}
    if not tasks and not allow_empty:
        raise ValueError("No official tasks with matching BDDL and initial states were found")
    return {"native_root": str(root), "tasks": tasks}


def episode_plan(args, runtime, output, run_id, integration, default_port, *, init_state_index=0):
    suite, task = args.runtime_profile, args.task
    if f"{suite}/{task}" not in runtime.data["tasks"]:
        raise ValueError("Select a prepared LIBERO suite with --runtime-profile and a native name with --task")
    seed = args.seed or 0
    port = args.port or default_port
    host = args.host
    command = (runtime.python, "-m", f"evoab.integrations.{integration}.server",
               "--native-root", runtime.data["native_root"], "--suite", suite, "--task", task,
               "--init-state-index", str(init_state_index), "--seed", str(seed), "--run-id", run_id,
               "--output-dir", str(output / "environment"), "--host", host, "--port", str(port),
               "--depth" if args.depth else "--no-depth")
    expected = {"integration": integration, "episode_id": run_id, "metadata.suite": suite,
                "metadata.task_id": task, "metadata.seed": seed, "metadata.init_state_index": init_state_index}
    connect_host = "127.0.0.1" if host == "0.0.0.0" else host
    return LaunchPlan(command, f"http://{connect_host}:{port}", expected, runtime.environment, output)
