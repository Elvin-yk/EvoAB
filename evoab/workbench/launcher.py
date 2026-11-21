"""Start ``evoab run`` processes for Studio requests."""

from __future__ import annotations

import os
import secrets
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from evoab.workbench import benchmarks, jobs
from evoab.workbench.runs import STUDIO_DIR

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
# The runtime tool-profile ID is retained for compatibility with saved launches.
METHODS = (
    {"id": "direct_policy", "agent": "native", "action_profile": "direct", "label": "Direct Policy",
     "interaction": "direct_policy", "tool_profile": "direct",
     "description": "模型每次直接输出一段末端位姿动作块，由该评测集的控制器逐步执行，再观察、再输出。不经过任何工具调用。"},
    {"id": "agentic", "agent": "native", "action_profile": "direct", "label": "Agentic Policy",
     "interaction": "tool_calling", "tool_profile": "direct",
     "description": "智能体在同一会话中观察场景、调用机器人工具（移动、抓取、感知等），并根据执行反馈决定下一步。"},
)
# Direct-policy options Studio may set; each maps to one ``evoab run`` flag.
DIRECT_OPTIONS = {
    "representation": ("--direct-representation", ("abs_eef", "delta_eef")),
    "horizon": ("--direct-horizon", None),
    "history": ("--direct-history", None),
    "prompt_blocks": ("--direct-prompt-blocks", None),
}


@dataclass(frozen=True)
class LaunchRequest:
    integration: str
    profile: str
    task: str
    model: str
    reasoning_effort: str = "low"
    method: str = "direct_policy"
    direct: dict | None = None


def free_port() -> int:
    """A currently free local port for the run's environment service, so runs can overlap."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_command(request: LaunchRequest, output_root: Path) -> list[str]:
    """The exact ``evoab run`` command for one Studio launch."""
    method = next(m for m in METHODS if m["id"] == request.method)
    profile, task = benchmarks.launch_target(request.integration, request.profile, request.task)
    command = [
        sys.executable, "-m", "evoab", "run",
        "--integration", request.integration,
        "--deploy", "local",
        "--env-port", str(free_port()),
        *(["--runtime-profile", profile] if profile is not None else []),
        "--task", task,
        "--tool-profile", method["tool_profile"],
        "--no-sam3",
        "--agent", method["agent"],
        "--model", request.model,
        "--reasoning-effort", request.reasoning_effort,
        "--interaction-mode", method["interaction"],
    ]
    runtime = benchmarks.runtime_for_launch(request.integration, request.profile)
    if runtime is not None and runtime.container:
        command.append("--container")
    if method["interaction"] == "direct_policy":
        for key, value in (request.direct or {}).items():
            command += [DIRECT_OPTIONS[key][0], str(value)]
    return command + [
        "--output-dir", str(output_root),
        # Port 0 picks a free port; the CLI then prints the run directory at start.
        "--monitor", "--monitor-port", "0",
    ]


def validate(request: LaunchRequest) -> dict:
    benchmarks.launch_target(request.integration, request.profile, request.task)
    task = benchmarks.task_info(request.integration, request.profile, request.task)
    if task is None:
        raise ValueError(f"task {request.task!r} is not prepared in {request.integration}/{request.profile}")
    method = next((m for m in METHODS if m["id"] == request.method), None)
    if method is None:
        raise ValueError(f"unknown method {request.method!r}")
    if request.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning effort must be one of {REASONING_EFFORTS}")
    from evoab.provider import catalog
    if request.model not in {p["name"] for p in catalog.presets()}:
        raise ValueError(f"unknown model preset {request.model!r}")
    if request.direct and method["interaction"] != "direct_policy":
        raise ValueError("Direct options apply only to the Direct Policy method")
    for key, value in (request.direct or {}).items():
        if key not in DIRECT_OPTIONS:
            raise ValueError(f"unknown Direct option {key!r}")
        choices = DIRECT_OPTIONS[key][1]
        if choices and value not in choices:
            raise ValueError(f"{key} must be one of {choices}")
        if not isinstance(value, str) or not value or " " in value:
            raise ValueError(f"invalid {key} value {value!r}")
    return task


def launch(request: LaunchRequest, task: dict, runs_root: Path) -> str:
    """Start one run in its own process group; return its launch id.

    The run writes its artifacts to a directory it creates under ``runs_root``;
    Studio resolves that directory from the launch log.
    """
    launch_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}"
    method = next(m for m in METHODS if m["id"] == request.method)
    jobs.start(
        build_command(request, runs_root),
        runs_root / STUDIO_DIR / f"{launch_id}.json",
        env=dict(os.environ),
        launch_id=launch_id,
        integration=request.integration,
        profile=request.profile,
        simulated=True,
        benchmark=task["suite"],
        instruction=task.get("instruction"),
        embodiment=task["embodiment"],
        max_steps=task.get("max_steps"),
        task=request.task,
        model=request.model,
        agent=method["agent"],
        reasoning_effort=request.reasoning_effort,
        action_profile=method["action_profile"],
        tool_profile=method["tool_profile"],
        method=method["id"],
        direct=request.direct,
    )
    return launch_id
