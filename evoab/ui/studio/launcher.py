"""Start ``evoab run`` processes for Studio requests."""

from __future__ import annotations

import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from evoab.ui.studio import benchmarks, jobs
from evoab.ui.studio.providers import Provider
from evoab.ui.studio.runs import STUDIO_DIR

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
# The runtime tool-profile ID is retained for compatibility with saved launches.
METHODS = (
    {"id": "direct", "agent": "codex", "action_profile": "direct", "label": "Agentic Policy",
     "description": "智能体在同一会话中观察场景、调用机器人动作，并根据执行反馈决定下一步。"},
)



@dataclass(frozen=True)
class LaunchRequest:
    integration: str
    profile: str
    task: str
    provider_id: str
    model: str
    reasoning_effort: str = "low"
    method: str = "direct"


def build_command(request: LaunchRequest, provider: Provider, output_root: Path) -> list[str]:
    """The exact ``evoab run`` command for one Studio launch."""
    method = next(m for m in METHODS if m["id"] == request.method)
    profile, task = request.profile, request.task
    if request.integration == "libero_pro":
        profile, task = task.split("/", 1)
    return [
        sys.executable, "-m", "evoab", "run",
        "--integration", request.integration,
        "--deploy", "local",
        "--runtime-profile", profile,
        "--task", task,
        "--tool-profile", method["id"],
        "--no-sam3",
        "--agent", method["agent"],
        "--model", request.model,
        "--reasoning-effort", request.reasoning_effort,
        "--provider-url", provider.base_url,
        "--provider-profile", provider.profile,
        "--codex-web-search", "disabled",
        "--output-dir", str(output_root),
        # Port 0 picks a free port; the CLI then prints the run directory at start.
        "--monitor", "--monitor-port", "0",
    ]


def validate(request: LaunchRequest, providers: dict[str, Provider]) -> tuple[dict, Provider]:
    if request.integration not in benchmarks.LAUNCHABLE:
        raise ValueError(f"Studio cannot launch {request.integration} runs yet")
    task = benchmarks.task_info(request.integration, request.profile, request.task)
    if task is None:
        raise ValueError(f"task {request.task!r} is not prepared in {request.integration}/{request.profile}")
    provider = providers.get(request.provider_id)
    if provider is None:
        raise ValueError(f"unknown provider {request.provider_id!r}")
    if not provider.api_key():
        raise ValueError(f"provider {provider.label} has no API key")
    if not request.model:
        raise ValueError("model is required")
    if request.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning effort must be one of {REASONING_EFFORTS}")
    if request.method not in {m["id"] for m in METHODS}:
        raise ValueError(f"unknown method {request.method!r}")
    return task, provider


def launch(request: LaunchRequest, task: dict, provider: Provider, runs_root: Path) -> str:
    """Start one run in its own process group; return its launch id.

    The run writes its artifacts to a directory it creates under ``runs_root``;
    Studio resolves that directory from the launch log.
    """
    launch_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}"
    method = next(m for m in METHODS if m["id"] == request.method)
    env = dict(os.environ, EVOAB_API_KEY=provider.api_key() or "")
    jobs.start(
        build_command(request, provider, runs_root),
        runs_root / STUDIO_DIR / f"{launch_id}.json",
        env=env,
        launch_id=launch_id,
        integration=request.integration,
        profile=request.profile,
        simulated=True,
        benchmark=task["suite"],
        embodiment=task["embodiment"],
        max_steps=task.get("max_steps"),
        task=request.task,
        provider_id=provider.id,
        provider_label=provider.label,
        model=request.model,
        agent=method["agent"],
        reasoning_effort=request.reasoning_effort,
        action_profile=method["action_profile"],
        tool_profile=method["id"],
    )
    return launch_id
