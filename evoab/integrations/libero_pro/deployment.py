"""Adopt a pinned LIBERO-PRO installation and supervise one native trial per run."""
from importlib.metadata import version
from pathlib import Path

from evoab.services.deployment.config import Recipe
from evoab.services.deployment.runtime import query_python
from .demo_assets import prepare_previews as prepare_previews
from .server import REVISION, SUITES
from evoab.integrations.libero_plus.local import episode_plan, refresh_catalog, task_catalog

empty_catalog_hint = (
    "LIBERO-PRO lists only tasks with the official BDDL under "
    "<native-root>/libero/libero/bddl_files/<suite>/ and the matching initial state under "
    "<native-root>/libero/libero/init_files/<suite>/. Install those files, then rerun the same "
    "setup command."
)


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.runtime_profile is not None and args.runtime_profile not in SUITES:
        raise ValueError("Unknown LIBERO-PRO suite")
    if args.command == "setup" and not (args.runtime_python and args.native_root):
        raise ValueError("Supply --runtime-python and --native-root to adopt a LIBERO-PRO installation")


def external_data(args):
    return task_catalog(args, SUITES, lambda root, suite, name: root / suite / (name + ".pruned_init"))


def refresh_external_data(args, runtime):
    """Rescan the installation recorded by setup; an empty result disables its tasks."""
    return refresh_catalog(args, runtime, SUITES,
                           lambda root, suite, name: root / suite / (name + ".pruned_init"))


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
    return episode_plan(args, runtime, output, run_id, "libero_pro", 18782)
