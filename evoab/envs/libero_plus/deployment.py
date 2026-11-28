"""Run LIBERO-Plus episodes in the locally built image of the pinned revision."""
import re

from evoab.envs.docker.config import Recipe
from evoab.envs.docker.runtime import check_python, query_python
from .local import episode_plan, task_catalog
from .server import BUDGETS, REVISION

empty_catalog_hint = (
    "LIBERO-Plus lists only tasks with the official BDDL under "
    "the image's libero/libero/bddl_files/<suite>/ and the matching initial state under "
    "the image's libero/libero/init_files/<suite>/ (new-object perturbations use "
    "init_files/libero_newobj/<suite>/). Unpack the official assets under libero/libero/assets "
    "and add those files, then rerun the same setup command."
)


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.runtime_profile is not None and args.runtime_profile not in BUDGETS:
        raise ValueError("Unknown LIBERO-Plus suite")


def _state_path(root, suite, name):
    # Mirror the pinned benchmark's perturbation-to-initial-state mapping.
    for marker in ("_language_", "_view_"):
        if marker in name:
            return root / suite / (name.split(marker, 1)[0] + ".pruned_init")
    if "_add_" in name or "_level" in name:
        return root / "libero_newobj" / suite / (name + ".pruned_init")
    if "_light_" in name:
        return root / suite / (name.split("_light_", 1)[0] + ".pruned_init")
    for marker in ("_tb_", "_table_"):
        if marker in name:
            return root / suite / (re.sub(marker + r"\d+", "", name) + ".pruned_init")
    return None


def image_data(args):
    return task_catalog(args, BUDGETS, _state_path)



def verify(runtime):
    # The image's interpreter must run this checkout's evoab distribution.
    check_python(runtime.python, environment=runtime.environment)
    code = (
        "import json, sys; from pathlib import Path; from importlib.metadata import version; "
        "from evoab.envs.simulation import source_root; from evoab.servers import hosting; "
        "source_root(Path(sys.argv[1]), sys.argv[2]); "
        "print(json.dumps({'robosuite': version('robosuite'), 'native_revision': sys.argv[2]}))"
    )
    result = query_python(runtime.python, code, runtime.data["native_root"], REVISION,
                          environment=runtime.environment)
    if result["robosuite"] != "1.4.0":
        raise ValueError("LIBERO-Plus requires robosuite 1.4.0")
    return result


def plan(args, runtime, output, run_id):
    return episode_plan(args, runtime, output, run_id, "libero_plus", 18781)
