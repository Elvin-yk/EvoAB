"""Run one native LIBERO-PRO trial per launch in the pinned image."""
from importlib.metadata import version
from pathlib import Path

from evoab.envs.docker.config import Recipe
from evoab.envs.docker.runtime import query_python
from .demo_assets import prepare_previews as prepare_previews
from .server import REVISION, SUITES
from evoab.envs.libero.local import episode_plan, task_catalog

empty_catalog_hint = (
    "LIBERO-PRO lists only tasks with the official BDDL under "
    "the image's libero/libero/bddl_files/<suite>/ and the matching initial state under "
    "the image's libero/libero/init_files/<suite>/. Install those files, then rerun the same "
    "evoab images pull command."
)


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.runtime_profile is not None and args.runtime_profile not in SUITES:
        raise ValueError("Unknown LIBERO-PRO suite")


def image_data(args):
    return task_catalog(args, SUITES, lambda root, suite, name: root / suite / (name + ".pruned_init"))



def _verify_native(root):
    from evoab.envs.simulation import source_root
    from evoab.servers import hosting  # noqa: F401 - check service dependencies in the image

    source_root(Path(root), REVISION)
    if version("robosuite") != "1.4.0":
        raise ValueError("LIBERO-PRO requires robosuite 1.4.0")
    return {"native_revision": REVISION}


def verify(runtime):
    return query_python(runtime.python,
        "import json, sys; from evoab.envs.libero_pro.deployment import _verify_native; "
        "print(json.dumps(_verify_native(sys.argv[1])))",
        runtime.data["native_root"], environment=runtime.environment)


def plan(args, runtime, output, run_id):
    return episode_plan(args, runtime, output, run_id, "libero_pro", 18782)
