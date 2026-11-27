"""Run one RoboTwin 2.0 task (aloha-agilex, demo_clean) in the locally built image of the pinned revision."""
import json
from pathlib import Path

from evoab.services.deployment.config import LaunchPlan, Recipe
from evoab.services.deployment.runtime import check_python, query_python

from .server import REVISION

DEFAULT_PORT = 18783
#: Task setup's launch check starts; the upstream expert admits its default seed (1).
CHECK_TASK = "beat_block_hammer"


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if getattr(args, "runtime_profile", None):
        raise ValueError("RoboTwin uses one default runtime profile")
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("Local RoboTwin deployment requires --task")


def image_data(args):
    """Tasks that have both an upstream task class and instruction templates."""
    if not args.native_root:
        raise ValueError("The RoboTwin container recipe names no native_root")
    native = Path(args.native_root).expanduser().resolve()
    tasks = {}
    for path in sorted((native / "description/task_instruction").glob("*.json")):
        if not (native / "envs" / f"{path.stem}.py").is_file():
            continue
        description = json.loads(path.read_text(encoding="utf-8")).get("full_description") or path.stem.replace("_", " ")
        tasks[path.stem] = {"spec": {"backend": "robotwin", "robots": ["ALOHA AgileX"],
                                     "scenario": {"title": description}}}
    if not tasks:
        raise ValueError(f"No RoboTwin tasks found under {native}")
    return {"native_root": str(native), "tasks": tasks}


def verify(runtime):
    info = check_python(runtime.python, ["sapien", "mplib", "curobo", "torch", "toppra"],
                        environment=runtime.environment)
    info.update(query_python(runtime.python,
        "import json, sys; from pathlib import Path; from evoab.integrations.simulation import source_root; "
        "root = source_root(Path(sys.argv[1]), sys.argv[2]); "
        "missing = [p for p in ('assets/embodiments', 'assets/objects', 'assets/background_texture') if not (root / p).is_dir()]; "
        "assert not missing, f'missing RoboTwin assets: {missing}'; "
        "print(json.dumps({'native_revision': sys.argv[2]}))",
        runtime.data["native_root"], REVISION, environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    if args.task not in runtime.data["tasks"]:
        raise ValueError(f"Unknown RoboTwin task: {args.task}")
    seed = args.seed if args.seed is not None else 1
    port = args.port or DEFAULT_PORT
    command = (runtime.python, "-m", "evoab.integrations.robotwin.server",
               "--native-root", runtime.data["native_root"], "--task", args.task, "--task-config", "demo_clean",
               "--seed", str(seed), "--run-id", run_id, "--output-dir", str(output / "environment"),
               "--no-depth", "--host", args.host, "--port", str(port))
    connect_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{connect_host}:{port}",
                      {"integration": "robotwin", "episode_id": run_id, "metadata.task_id": args.task},
                      runtime.environment, output)
