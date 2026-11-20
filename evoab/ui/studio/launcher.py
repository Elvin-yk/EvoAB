"""Start ``evoab run`` processes for Studio requests."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from evoab.ui.studio.catalog import Runtime
from evoab.ui.studio.providers import Provider
from evoab.ui.studio.runs import STUDIO_DIR

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
ACTION_PROFILES = ("direct", "clean")


@dataclass(frozen=True)
class LaunchRequest:
    integration: str
    profile: str
    task: str
    provider_id: str
    model: str
    reasoning_effort: str = "low"
    action_profile: str = "direct"


def build_command(request: LaunchRequest, provider: Provider, output_root: Path) -> list[str]:
    """The exact ``evoab run`` command for one Studio launch."""
    return [
        sys.executable, "-m", "evoab", "run",
        "--integration", request.integration,
        "--deploy", "local",
        "--runtime-profile", request.profile,
        "--task", request.task,
        "--action-profile", request.action_profile,
        "--no-sam3",
        "--agent", "codex",
        "--model", request.model,
        "--reasoning-effort", request.reasoning_effort,
        "--provider-url", provider.base_url,
        "--provider-profile", provider.profile,
        "--codex-web-search", "disabled",
        "--output-dir", str(output_root),
        # Port 0 picks a free port; the CLI then prints the run directory at start.
        "--monitor", "--monitor-port", "0",
    ]


def validate(request: LaunchRequest, runtimes: list[Runtime], providers: dict[str, Provider]) -> tuple[Runtime, Provider]:
    runtime = next(
        (r for r in runtimes if r.integration == request.integration and r.profile == request.profile),
        None,
    )
    if runtime is None:
        raise ValueError(f"no prepared runtime {request.integration}/{request.profile}")
    if request.task not in runtime.tasks:
        raise ValueError(f"task {request.task!r} is not admitted in {request.profile}")
    provider = providers.get(request.provider_id)
    if provider is None:
        raise ValueError(f"unknown provider {request.provider_id!r}")
    if not request.model:
        raise ValueError("model is required")
    if request.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning effort must be one of {REASONING_EFFORTS}")
    if request.action_profile not in ACTION_PROFILES:
        raise ValueError(f"action profile must be one of {ACTION_PROFILES}")
    return runtime, provider


def launch(request: LaunchRequest, runtime: Runtime, provider: Provider, runs_root: Path) -> str:
    """Start one run in its own process group; return its launch id.

    The run writes its artifacts to a directory it creates under ``runs_root``;
    Studio resolves that directory from the launch log.
    """
    launch_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}"
    studio = runs_root / STUDIO_DIR
    studio.mkdir(parents=True, exist_ok=True)
    task = runtime.tasks[request.task]
    env = dict(os.environ, EVOAB_API_KEY=provider.api_key())
    log = (studio / f"{launch_id}.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        build_command(request, provider, runs_root),
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    record = {
        "launch_id": launch_id,
        "pid": process.pid,
        "started_at": time.time(),
        "integration": request.integration,
        "profile": request.profile,
        "simulated": runtime.simulated,
        "benchmark": task.benchmark,
        "embodiment": task.embodiment,
        "task": request.task,
        "provider_id": provider.id,
        "provider_label": provider.label,
        "model": request.model,
        "agent": "codex",
        "reasoning_effort": request.reasoning_effort,
        "action_profile": request.action_profile,
    }
    (studio / f"{launch_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return launch_id
