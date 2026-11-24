"""One owned upstream policy server per trial, in its separate GPU environment."""

from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from evoab.services.deployment.process import gpu_environment
from evoab.artifacts.run import write_json_atomic

from . import REVISION


def _owns_listener(pid, port):
    # Upstream's readiness file checks for any listener on the selected port.
    # Verify its child owns that listener before sending the first reset.
    proc = Path("/proc") / str(pid)
    inodes = set()
    for table in ("tcp", "tcp6"):
        path = proc / "net" / table
        if path.exists():
            for line in path.read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A" and int(fields[1].split(":")[1], 16) == port:
                    inodes.add(f"socket:[{fields[9]}]")
    for descriptor in (proc / "fd").iterdir():
        try:
            if os.readlink(descriptor) in inodes:
                return True
        except FileNotFoundError:
            continue
    return False


@contextmanager
def policy_session(config, info, output):
    if not config["root"] or not config["python"]:
        raise ValueError("Configure EVOAB_Q0_ROOT and EVOAB_Q0_PYTHON for the upstream policy.")
    if not config.get("api_key"):
        raise ValueError("Q0-Preview needs an API key for the selected upstream provider.")
    root = Path(config["root"]).expanduser().resolve()
    python = Path(config["python"]).expanduser().absolute()
    if root.name != "XPolicyLab" or not (root.parent / "env_cfg/arx_x5.yml").is_file():
        raise ValueError("Use a benchmark workspace with sibling XPolicyLab/ and env_cfg/ directories.")
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != REVISION or subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True
    ):
        raise ValueError(f"Q0-Preview requires the unmodified XPolicyLab revision {REVISION}")
    tasks = root / "policy/Q0_Preview/assets/tasks/task_cards.json"
    card = json.loads(tasks.read_text())["cards"].get(info.task)
    if not card or card["control_step_limit"] != info.environment.max_episode_steps:
        raise ValueError("The native step limit must match the upstream task card.")
    # This evaluator is a dedicated process. Use the matching public WS client here
    # without importing the policy, CuRobo, or simulator in the harness interpreter.
    sys.path.insert(0, str(root))
    from client_server.ws.model_client import WsModelClient

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    archive = output / "q0"
    gpu = gpu_environment(config["gpu"])["CUDA_VISIBLE_DEVICES"]
    command = [str(python), "-u", str(root / "policy/Q0_Preview/tools/policy_service.py"),
               "--root", str(archive), "--host", "127.0.0.1", "--port", str(port),
               "--task", info.task, "--seed", str(info.metadata["seed"]), "--gpu", gpu,
               "--python-prefix", os.environ.get("Q0_PREVIEW_AGENT_PREFIX", str(python.parent.parent))]
    agent_config = config.get("agent_config") or {}
    if agent_config:
        config_path = output / "q0-agent-config.json"
        write_json_atomic(config_path, agent_config)
        command.extend(["--config", str(config_path.resolve())])
    environment = dict(os.environ, Q0_PREVIEW_XPL_ROOT=str(root.parent),
                       Q0_PREVIEW_API_BASE_URL=config["api_base"], PYTHONDONTWRITEBYTECODE="1")
    environment[agent_config.get("api_key_env", "Q0_PREVIEW_API_KEY")] = config["api_key"]
    # A debug policy only echoes measured joints. Always load the actual planner.
    environment.pop("EVAL_ENV_TYPE", None)
    with (output / "q0-service.log").open("w") as log:
        server = subprocess.Popen(command, env=environment, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 200
            while not (archive / "SERVICE_READY.json").is_file():
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Q0 policy startup failed. See q0-service.log and q0/server.log.")
                time.sleep(0.2)
            ready = json.loads((archive / "SERVICE_READY.json").read_text())
            if ready["port"] != port or ready["smoke"] or not _owns_listener(ready["pid"], port):
                raise RuntimeError("Q0 service did not publish a matching production endpoint.")
            with WsModelClient(url=f"ws://127.0.0.1:{port}", evaluation_id="evoab", trial_id=info.episode_id,
                               max_connect_attempts=1, request_timeout_s=10800) as client:
                client.call("reset")
                try:
                    yield client
                except BaseException:
                    # Match upstream deploy.py: close the trial without hiding the control failure.
                    with suppress(Exception):
                        client.call("trial_end", obs={})
                    raise
                else:
                    client.call("trial_end", obs={})
        finally:
            # The upstream supervisor owns the WS server and each per-episode agent.
            if archive.is_dir():
                (archive / "STOP").touch()
            try:
                status = server.wait(timeout=25)
            except subprocess.TimeoutExpired:
                server.terminate()
                status = server.wait(timeout=25)
            if status and sys.exc_info()[0] is None:
                raise RuntimeError(f"Q0 policy service exited with code {status}. See q0-service.log.")
