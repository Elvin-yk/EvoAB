"""Prepare and launch one OpenArm Gazebo episode."""
from evoab.envs.docker.config import LaunchPlan, Recipe
from evoab.envs.docker.runtime import query_python

TASKS = {"reach_left": "left", "reach_right": "right"}
DEFAULT_PORT = 18794


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.command != "setup" and getattr(args, "task", None) not in TASKS:
        raise ValueError(f"Choose an OpenArm reach task from {list(TASKS)}")
    if args.runtime_profile or getattr(args, "seed", None):
        raise ValueError("OpenArm reach uses one fixed configuration and seed 0")
    if getattr(args, "cameras", None) not in (None, ["scene"]) or getattr(args, "depth", False):
        raise ValueError("OpenArm reach provides the scene RGB camera without depth or calibration")


def image_data(args):
    return {"tasks": {task: {"spec": {"robots": ["OpenArm 2.0"], "scenario": {"title": f"Lift the {side} wrist"}}}
                      for task, side in TASKS.items()}}


def verify(runtime):
    return query_python(runtime.python, """
import json, subprocess
from evoab.envs.openarm_gazebo.server import ros_command
subprocess.run(ros_command("/usr/bin/python3", "-c",
    "import rclpy, tf2_ros; from robot_runtime.openarm_ik import step; "
    "from ament_index_python.packages import get_package_share_directory as share; "
    "from openarm2_minimal_gazebo.description import build_robot_description; "
    "build_robot_description('default_bimanual', share('openarm2_minimal_gazebo')+'/config/openarm2_controllers.yaml')"),
    check=True)
print(json.dumps({"solver": "robot_runtime.openarm_ik.solver.step", "ros": "humble"}))
""", environment=runtime.environment)


def plan(args, runtime, output, run_id):
    validate(args)
    port = args.port or DEFAULT_PORT
    host = args.host
    command = (runtime.python, "-m", "evoab.envs.openarm_gazebo.server",
               "--task", args.task, "--run-id", run_id, "--output-dir", str(output / "environment"),
               "--host", host, "--port", str(port))
    client_host = "127.0.0.1" if host == "0.0.0.0" else host
    return LaunchPlan(command, f"http://{client_host}:{port}",
                      {"integration": "openarm_gazebo", "episode_id": run_id,
                       "metadata.task_id": args.task, "metadata.seed": 0}, runtime.environment, output)
