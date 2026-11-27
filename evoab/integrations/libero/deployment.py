"""Run one native LIBERO trial per launch in the pinned AllenAI image."""
from importlib.metadata import version
from pathlib import Path

from evoab.services.deployment.config import Recipe
from evoab.services.deployment.runtime import query_python
from .server import PORT, REVISION, SUITE_BUDGETS
from evoab.integrations.libero_plus.local import episode_plan, task_catalog

empty_catalog_hint = (
    "LIBERO lists only tasks with the official BDDL under "
    "the image's libero/libero/bddl_files/<suite>/ and the matching initial state under "
    "the image's libero/libero/init_files/<suite>/. Install those files, then rerun the same "
    "setup command."
)


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.runtime_profile is not None and args.runtime_profile not in SUITE_BUDGETS:
        raise ValueError("Unknown LIBERO suite")


def _state_path(root, suite, name):
    return root / suite / (name + ".pruned_init")


def image_data(args):
    return task_catalog(args, SUITE_BUDGETS, _state_path, budgets=SUITE_BUDGETS)



def _verify_native(root):
    from evoab.integrations.simulation import source_root
    from evoab.services import hosting  # noqa: F401 - check service dependencies in the image

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
