"""Start ``evoab run`` processes for Studio requests."""

from __future__ import annotations

import os
import secrets
import socket
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from evoab.agents.contracts import AgentLaunch
from evoab.agents.registry import agent_launch, list_agent_launches
from evoab.workbench import benchmarks, jobs
from evoab.workbench.runs import STUDIO_DIR

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
# Workbench modes map to the existing run CLI interaction and tool profiles.
METHODS = (
    {"id": "direct_policy", "agent": "native", "action_profile": "direct", "label": "Direct Policy",
     "interaction": "direct_policy", "tool_profile": "direct",
     "description": "模型每次直接输出一段末端位姿动作块，由该评测集的控制器逐步执行，再观察、再输出。不经过任何工具调用。"},
    {"id": "agentic", "agent": "native", "action_profile": "direct", "label": "Agentic Policy",
     "interaction": "tool_calling", "tool_profile": "direct",
     "description": "智能体在同一会话中观察场景、调用机器人工具（移动、抓取、感知等），并根据执行反馈决定下一步。"},
    {"id": "agent_policy", "agent": None, "action_profile": "hybrid", "label": "Agent + Policy",
     "interaction": "tool_calling", "tool_profile": "hybrid",
     "description": "智能体通过策略模型提出的动作完成任务。"},
)
LEGACY_METHODS = {"rua": ("agentic", "rua")}
# Direct-policy options Studio may set; each maps to one ``evoab run`` flag.
DIRECT_OPTIONS = {
    "representation": ("--direct-representation", ("abs_eef", "delta_eef")),
    "horizon": ("--direct-horizon", None),
    "history": ("--direct-history", None),
    "prompt_blocks": ("--direct-prompt-blocks", None),
    "custom_prompt": ("--direct-custom-prompt", None),
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
    agent: str | None = None
    agent_options: dict[str, int] | None = None


def methods() -> tuple[dict, ...]:
    """Studio mode metadata with implementations supplied by registered adapters."""
    agents = list_agent_launches()
    return tuple({**method, "agents": [
        {"id": name, "label": agent.label, "description": agent.description,
         "integrations": agent.integrations, "cli_flags": agent.cli_flags,
         "options": [asdict(option) for option in agent.options],
         "filters_presets": agent.validate_preset is not None, "model_help": agent.model_help}
        for name, agent in agents.items() if method["id"] in agent.modes
    ]} for method in METHODS)


def method_identity(method: str | None, agent: str | None) -> tuple[str | None, str | None]:
    """Translate historical method IDs and recover the implied agent on old records."""
    if isinstance(method, str) and method in LEGACY_METHODS:
        return LEGACY_METHODS[method]
    if method is None and isinstance(agent, str) and agent in LEGACY_METHODS:
        return LEGACY_METHODS[agent]
    if agent is None:
        mode = next((item for item in METHODS if item["id"] == method), None)
        if mode is not None:
            agent = mode["agent"]
    return method, agent


def preferred_choice(integration: str, method: str | None, agent: str | None) -> tuple[str, str | None]:
    """Pick a usable saved selection for the current benchmark."""
    available = {item["id"]: item for item in methods()}
    for mode in dict.fromkeys((method, "direct_policy", *available)):
        if mode not in available:
            continue
        item = available[mode]
        choices = [choice["id"] for choice in item["agents"]
                   if choice["integrations"] is None or integration in choice["integrations"]]
        if choices:
            preferred = agent if mode == method and agent in choices else item["agent"]
            return mode, preferred if preferred in choices else choices[0]
    return "direct_policy", None


def resolve_selection(request: LaunchRequest) -> tuple[LaunchRequest, dict, AgentLaunch]:
    if not isinstance(request.method, str) or (request.agent is not None and not isinstance(request.agent, str)):
        raise ValueError("method and agent must be string identifiers")
    method_id, agent_id = method_identity(request.method, request.agent)
    if request.agent is not None and request.agent != agent_id:
        raise ValueError(f"--method {request.method} selects Agent {agent_id!r}")
    method = next((item for item in METHODS if item["id"] == method_id), None)
    if method is None:
        raise ValueError(f"unknown method {request.method!r}")
    if agent_id is None:
        raise ValueError(f"{method['label']} requires an Agent implementation")
    agent = agent_launch(agent_id)
    if agent is None or method_id not in agent.modes:
        raise ValueError(f"Agent {agent_id!r} does not support {method['label']} in Workbench")
    if agent.integrations is not None and request.integration not in agent.integrations:
        raise ValueError(f"{agent.label} is not available for {request.integration}")
    return replace(request, method=method["id"], agent=agent_id), method, agent


def _agent_option_flags(request: LaunchRequest, agent: AgentLaunch) -> list[str]:
    options = {option.name: option for option in agent.options}
    if request.agent_options is not None and not isinstance(request.agent_options, dict):
        raise ValueError("agent_options must be an object")
    flags = []
    for name, value in (request.agent_options or {}).items():
        if name not in options:
            raise ValueError(f"unknown option {name!r} for Agent {request.agent!r}")
        option = options[name]
        if type(value) is not int or value < option.minimum:
            raise ValueError(f"{name} must be an integer greater than or equal to {option.minimum}")
        flags.extend((option.flag, str(value)))
    return flags


def free_port() -> int:
    """A currently free local port for the run's environment service, so runs can overlap."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_command(request: LaunchRequest, output_root: Path) -> list[str]:
    """The exact ``evoab run`` command for one Studio launch."""
    selection = resolve_selection(request)
    return _build_command(*selection, output_root)


def _build_command(request: LaunchRequest, method: dict, agent: AgentLaunch, output_root: Path) -> list[str]:
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
        "--agent", str(request.agent),
        "--model", request.model,
        "--reasoning-effort", request.reasoning_effort,
        "--interaction-mode", method["interaction"],
        *agent.cli_flags,
        *_agent_option_flags(request, agent),
    ]
    runtime = benchmarks.runtime_for_launch(request.integration, request.profile)
    if runtime is not None and runtime.container:
        command.append("--container")
    if method["interaction"] == "direct_policy":
        for key, value in (request.direct or {}).items():
            if key == "custom_prompt":
                command.append(f"--direct-custom-prompt={value}")
            else:
                command += [DIRECT_OPTIONS[key][0], str(value)]
    return command + [
        "--output-dir", str(output_root),
        # Port 0 picks a free port; the CLI then prints the run directory at start.
        "--monitor", "--monitor-port", "0",
    ]


def validate(request: LaunchRequest) -> dict:
    request, method, agent = resolve_selection(request)
    benchmarks.launch_target(request.integration, request.profile, request.task)
    task = benchmarks.task_info(request.integration, request.profile, request.task)
    if task is None:
        raise ValueError(f"task {request.task!r} is not prepared in {request.integration}/{request.profile}")
    if request.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning effort must be one of {REASONING_EFFORTS}")
    from evoab.provider import catalog

    if not isinstance(request.model, str) or not request.model:
        raise ValueError("A model preset is required")
    if agent.validate_preset is not None:
        agent.validate_preset(request.model)
    elif request.model not in {p["name"] for p in catalog.presets()}:
        raise ValueError(f"unknown model preset {request.model!r}")
    _agent_option_flags(request, agent)
    if request.direct is not None and not isinstance(request.direct, dict):
        raise ValueError("direct must be an object")
    if request.direct and method["interaction"] != "direct_policy":
        raise ValueError("Direct options apply only to the Direct Policy method")
    for key, value in (request.direct or {}).items():
        if key not in DIRECT_OPTIONS:
            raise ValueError(f"unknown Direct option {key!r}")
        choices = DIRECT_OPTIONS[key][1]
        if choices and value not in choices:
            raise ValueError(f"{key} must be one of {choices}")
        if key == "custom_prompt":
            if not isinstance(value, str) or "\x00" in value or len(value) > 8192:
                raise ValueError("custom_prompt must be text of at most 8192 characters without NUL")
            continue
        if not isinstance(value, str) or not value or " " in value:
            raise ValueError(f"invalid {key} value {value!r}")
    return task


def launch(request: LaunchRequest, task: dict, runs_root: Path) -> str:
    """Start one run in its own process group; return its launch id.

    The run writes its artifacts to a directory it creates under ``runs_root``;
    Studio resolves that directory from the launch log.
    """
    launch_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}"
    request, method, agent = resolve_selection(request)
    jobs.start(
        _build_command(request, method, agent, runs_root),
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
        agent=request.agent,
        reasoning_effort=request.reasoning_effort,
        action_profile=method["action_profile"],
        tool_profile=method["tool_profile"],
        method=request.method,
        direct=request.direct,
        agent_options=request.agent_options,
    )
    return launch_id
