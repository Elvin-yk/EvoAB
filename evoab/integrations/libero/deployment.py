"""Adopt a pinned LIBERO installation and supervise one native trial per run."""
from importlib.metadata import version
from pathlib import Path

from evoab.services.deployment.config import Recipe
from evoab.services.deployment.runtime import query_python
from .server import PORT, REVISION, SUITE_BUDGETS
from evoab.integrations.libero_plus.local import episode_plan, refresh_catalog, task_catalog

empty_catalog_hint = (
    "LIBERO lists only tasks with the official BDDL under "
    "<native-root>/libero/libero/bddl_files/<suite>/ and the matching initial state under "
    "<native-root>/libero/libero/init_files/<suite>/. Install those files, then rerun the same "
    "setup command."
)


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.runtime_profile is not None and args.runtime_profile not in SUITE_BUDGETS:
        raise ValueError("Unknown LIBERO suite")
    if args.command == "setup" and not getattr(args, "container", False) and not (args.runtime_python and args.native_root):
        raise ValueError("Supply --runtime-python and --native-root to adopt a LIBERO installation, or use --container")


def _state_path(root, suite, name):
    return root / suite / (name + ".pruned_init")


def external_data(args):
    return task_catalog(args, SUITE_BUDGETS, _state_path, budgets=SUITE_BUDGETS)


def refresh_external_data(args, runtime):
    """Rescan the installation recorded by setup; an empty result disables its tasks."""
    return refresh_catalog(args, runtime, SUITE_BUDGETS, _state_path, budgets=SUITE_BUDGETS)


def _verify_native(root):
    from evoab.integrations.simulation import source_root
    from evoab.services import hosting  # noqa: F401 - check service dependencies in the native interpreter

    source_root(Path(root), REVISION)
    if version("robosuite") != "1.4.0":
        raise ValueError("LIBERO requires robosuite 1.4.0")
    return {"native_revision": REVISION}


def verify(runtime):
    return query_python(runtime.python,
        "import json, sys; from evoab.integrations.libero.deployment import _verify_native; "
        "print(json.dumps(_verify_native(sys.argv[1])))",
        runtime.data["native_root"], environment=runtime.environment)


def plan(args, runtime, output, run_id):
    index = getattr(args, "init_state_index", 0)
    if index < 0:
        raise ValueError("--init-state-index must be nonnegative")
    return episode_plan(args, runtime, output, run_id, "libero", PORT, init_state_index=index)
