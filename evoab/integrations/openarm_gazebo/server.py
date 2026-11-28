"""Serve one headless Gazebo episode through EvoAB's v4 environment service."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess

from evoab.integrations.simulation import serve_episode
from .deployment import DEFAULT_PORT, TASKS


def ros_command(*command):
    return ["bash", "-ec", 'source /opt/ros/humble/setup.bash; '
            'source /opt/openarm/ws/install/setup.bash; '
            'export PYTHONPATH="/opt/openarm/ruboclaw:$PYTHONPATH"; exec "$@"',
            "openarm-ros", *command]


@contextmanager
def simulator(log_path, side):
    # Local deployment uses host networking. Reserve a domain per episode and
    # refuse to attach to a graph already serving someone else's controllers.
    with socket.socket() as lease, log_path.open("w") as log:
        for domain in range(80, 100):
            try:
                lease.bind(("127.0.0.1", 31000 + domain))
            except OSError:
                continue
            break
        else:
            raise RuntimeError("All OpenArm simulation domains are in use")
        env = {**os.environ, "ROS_LOCALHOST_ONLY": "1", "ROS_DOMAIN_ID": str(domain),
               "GAZEBO_MASTER_URI": f"http://127.0.0.1:{32000 + domain}",
               "LIBGL_ALWAYS_SOFTWARE": "1", "RCUTILS_LOGGING_USE_STDOUT": "0"}
        check = subprocess.run(ros_command("ros2", "node", "list", "--no-daemon", "--spin-time", "2"),
                               env=env, capture_output=True, text=True, check=True, timeout=15)
        if check.stdout.strip():
            raise RuntimeError(f"ROS domain {domain} already contains nodes: {check.stdout.strip()}")
        # Keep the sensors' X display alive until Gazebo has released its rendering context.
        display = subprocess.Popen(["Xvfb", "-displayfd", "1", "-screen", "0", "640x480x24", "-nolisten", "tcp"],
                                   stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            number = display.stdout.readline().strip()
            if not number.isdigit():
                raise RuntimeError("Could not start the Gazebo virtual display")
            env["DISPLAY"] = f":{number}"
            launch = subprocess.Popen(ros_command("ros2", "launch", "openarm2_minimal_gazebo",
                                                  "openarm2_gazebo.launch.py", "gui:=false", "moveit:=false"),
                                      env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                camera = Path(__file__).with_name("scene_camera.sdf")
                y, yaw = ("-0.99", "2.134") if side == "right" else ("0.99", "-2.134")
                subprocess.run(ros_command("ros2", "run", "gazebo_ros", "spawn_entity.py",
                                           "-entity", "evoab_scene_camera", "-file", str(camera),
                                           "-x", "0.67", "-y", y, "-z", "0.78", "-P", "0.14", "-Y", yaw),
                               env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
                yield env
            finally:
                if launch.poll() is None:
                    launch.send_signal(signal.SIGINT)
                    try:
                        launch.wait(timeout=18)
                    except subprocess.TimeoutExpired:
                        os.killpg(launch.pid, signal.SIGKILL)
                        launch.wait()
        finally:
            display.terminate()
            display.wait()
            display.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=TASKS, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    from .adapter import OpenArmAdapter

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    side = TASKS[args.task]
    with simulator(args.output_dir.with_suffix(".gazebo.log"), side) as env:
        def native(operation, *target):
            command = ros_command("/usr/bin/python3", str(Path(__file__).with_name("ros_step.py")),
                                  side, operation, "--", *map(str, target))
            result = subprocess.run(command, env=env, stdout=subprocess.PIPE, text=True, check=True,
                                    timeout=120 if operation == "home" else 60)
            return json.loads(result.stdout)

        serve_episode(lambda: OpenArmAdapter(native, side, args.run_id), args, "openarm_gazebo",
                      capabilities=("pose", "published_observations"))


if __name__ == "__main__":
    main()
