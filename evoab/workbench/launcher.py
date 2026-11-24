"""Start ``evoab run`` processes for Studio requests."""

from __future__ import annotations

import os
import secrets
import socket
import sys
import time
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from evoab.agents.contracts import AgentLaunch
from evoab.agents.registry import agent_launch, get_agent_backend, list_agent_launches
from evoab.workbench import benchmarks, jobs
from evoab.workbench.runs import STUDIO_DIR

if TYPE_CHECKING:
    from evoab.composition.recipe import Recipe

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class LaunchRequest:
    integration: str
    profile: str
    task: str
    model: str
    reasoning_effort: str = "low"
    method: str = "agentic"
    # Values for the method's declared parameters, by parameter name.
    params: dict | None = None
    # Overrides the entry role's agent runtime; None keeps the recipe's.
    agent: str | None = None
    agent_options: dict[str, int] | None = None
    visible_tools: list[str] | None = None
    team: list[dict] | None = None
    # Evaluation set and frozen layout index, for benchmarks whose tasks declare them (RoboDojo).
    seed: int | None = None
    layout_id: int | None = None


def _agent_choice(name: str, launch: AgentLaunch) -> dict:
    return {"id": name, "label": launch.label, "description": launch.description,
            "integrations": launch.integrations, "cli_flags": launch.cli_flags,
            "tool_selection_flags": launch.tool_selection_flags,
            "options": [asdict(option) for option in launch.options],
            "filters_presets": launch.validate_preset is not None, "model_help": launch.model_help}


def methods(integration: str | None = None) -> tuple[dict, ...]:
    """Library methods with what a launcher needs: parameters, compatible agents and the graph."""
    from evoab.composition.components import is_evaluator
    from evoab.composition.graph import recipe_graph
    from evoab.composition.methods import list_methods

    launches = list_agent_launches()
    spec = None
    if integration is not None:
        from evoab.integrations import load_integration
        spec = load_integration(integration).get_integration_spec()
    result = []
    for method in list_methods():
        recipe = method.recipe
        if integration is not None and recipe.integrations is not None and integration not in recipe.integrations:
            continue
        unavailable = None
        if spec is not None:
            from evoab.composition.compiler import compile_recipe
            try:
                compile_recipe(recipe, spec, base_dir=method.root)
            except ValueError as error:
                unavailable = str(error)
        entry = recipe.entry
        def usable(name, launch):
            if integration is not None and launch.integrations is not None and integration not in launch.integrations:
                return False
            # External evaluators (RUA, Q0-Preview) run only their own method. Everything else Studio
            # launches runs on the built-in agent; other runtimes stay available from the CLI (--agent).
            if is_evaluator(name) or entry.brain.agent and is_evaluator(entry.brain.agent):
                return name == entry.brain.agent
            return name == "native"

        agents = [_agent_choice(name, launch) for name, launch in launches.items() if usable(name, launch)]
        default_agent = entry.brain.agent or "native"
        agents.sort(key=lambda agent: agent["id"] != default_agent)
        result.append({
            "id": recipe.id, "label": recipe.label, "summary": recipe.summary, "paradigms": recipe.paradigms,
            "status": recipe.status, "source": method.source, "integrations": recipe.integrations,
            "external_evaluator": is_evaluator(default_agent),
            "fixed_effort": entry.brain.reasoning_effort if is_evaluator(default_agent) else None,
            "channel": entry.channel, "agent": default_agent, "roles": list(recipe.roles),
            "parameters": [{**parameter.model_dump(mode="json"), "default": _parameter_default(recipe, parameter)}
                           for parameter in recipe.parameters],
            "references": [reference.model_dump(mode="json") for reference in recipe.references],
            "episodes": recipe.loop.episodes.model_dump(mode="json"),
            "agents": agents if unavailable is None else [], "unavailable": unavailable,
            "graph": recipe_graph(recipe),
        })
    return tuple(result)


def _parameter_default(recipe, parameter):
    from evoab.composition.recipe import get_path
    value = get_path(recipe.model_dump(mode="json"), parameter.path)
    if parameter.type == "pair" and isinstance(value, list):
        return f"{value[0]}:{value[1]}"
    return value


def preferred_choice(integration: str, method: str | None, agent: str | None) -> tuple[str, str | None]:
    """Pick a usable saved selection for the current benchmark."""
    available = {item["id"]: item for item in methods(integration)}
    for candidate in dict.fromkeys((method, "agentic", "direct_policy", *available)):
        item = available.get(candidate)
        if item is None or not item["agents"]:
            continue
        ids = [choice["id"] for choice in item["agents"]]
        return candidate, agent if candidate == method and agent in ids else None
    return "agentic", None


def resolve_selection(request: LaunchRequest) -> tuple[LaunchRequest, dict, AgentLaunch]:
    if not isinstance(request.method, str) or (request.agent is not None and not isinstance(request.agent, str)):
        raise ValueError("method and agent must be string identifiers")
    method_id, agent_id = request.method, request.agent
    method = next((item for item in methods(request.integration) if item["id"] == method_id), None)
    if method is None:
        raise ValueError(f"unknown method {request.method!r} for {request.integration}")
    chosen = agent_id or method["agent"]
    if chosen not in {agent["id"] for agent in method["agents"]}:
        raise ValueError(f"Agent {chosen!r} cannot run {method['label']} on {request.integration}")
    agent = agent_launch(chosen)
    if agent is None:
        raise ValueError(f"Agent {chosen!r} has no Studio launch configuration")
    return replace(request, method=method_id, agent=agent_id,
                   reasoning_effort=method["fixed_effort"] or request.reasoning_effort), method, agent


def _parameter_flags(request: LaunchRequest, method: dict) -> list[str]:
    declared = {parameter["name"]: parameter for parameter in method["parameters"]}
    if request.params is not None and not isinstance(request.params, dict):
        raise ValueError("params must be an object")
    flags = []
    for name, value in (request.params or {}).items():
        parameter = declared.get(name)
        if parameter is None:
            raise ValueError(f"unknown parameter {name!r} for {method['label']}")
        if isinstance(value, str) and ("\x00" in value or len(value) > 8192):
            raise ValueError(f"{name} must be text of at most 8192 characters without NUL")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"{name} must be a scalar value")
        text = json.dumps(value) if parameter["type"] == "text" and isinstance(value, str) else str(value)
        if isinstance(value, bool):
            text = "true" if value else "false"
        flags.append(f"--set={name}={text}")
    return flags


def tool_groups(integration: str, method_id: str, agent_id: str) -> dict[str, list[str]]:
    """The entry role's tools by capability, for Studio's per-run tool picker (``--visible-tools``)."""
    from evoab.composition.compiler import compile_recipe
    from evoab.composition.methods import get_method
    from evoab.integrations import load_integration
    from evoab.tools.selection import TOOL_GROUPS

    if integration not in {info.integration for info in benchmarks.REGISTRY}:
        raise ValueError("Unknown benchmark")
    request = LaunchRequest(integration, "", "", "", method=method_id, agent=agent_id)
    _, method, agent = resolve_selection(request)
    if method["channel"] == "action" or len(method["roles"]) > 1:
        raise ValueError("Tool selection applies to one-role tools or code methods")
    if agent.tool_groups is not None:
        # External evaluators list their own tools (RUA's harness still uses its direct profile).
        groups = agent.tool_groups(integration, "direct")
    elif method.get("external_evaluator"):
        raise ValueError(f"Agent {agent.label!r} owns its own tools and does not support tool selection")
    else:
        entry = get_method(method_id)
        compiled = compile_recipe(entry.recipe, load_integration(integration).get_integration_spec(),
                                  base_dir=entry.root).entry
        groups = {item.name: list(item.policies) for item in compiled.modules}
        if entry.recipe.execution.return_mode == "background":
            groups["jobs"] = list(TOOL_GROUPS["@jobs"])
    # Studio starts runs with skill writing disabled.
    return {group: [name for name in names if name != "write_skill"] for group, names in groups.items()}


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


def _build_command(request: LaunchRequest, method: dict, agent: AgentLaunch, output_root: Path,
                   recipe_path: Path | None = None) -> list[str]:
    profile, task = benchmarks.launch_target(request.integration, request.profile, request.task)
    command = [
        sys.executable, "-m", "evoab", "run",
        "--integration", request.integration,
        "--deploy", "local",
        "--env-port", str(free_port()),
        *(["--runtime-profile", profile] if profile is not None else []),
        "--task", task,
        "--recipe", str(recipe_path) if recipe_path else method["id"],
        "--no-sam3",
        *(["--agent", request.agent] if request.agent else []),
        "--model", request.model,
        "--reasoning-effort", request.reasoning_effort,
        *agent.cli_flags,
        *_agent_option_flags(request, agent),
        *_parameter_flags(request, method),
    ]
    if request.visible_tools is not None:
        command.extend((*agent.tool_selection_flags, *request.visible_tools))
    if request.seed is not None:
        command.extend(("--seed", str(request.seed)))
    if request.layout_id is not None:
        command.extend(("--layout-id", str(request.layout_id)))
    runtime = benchmarks.runtime_for_launch(request.integration, request.profile)
    if runtime is not None and runtime.container:
        command.append("--container")
    return command + [
        "--output-dir", str(output_root),
        # Port 0 picks a free port; the CLI then prints the run directory at start.
        "--monitor", "--monitor-port", "0",
    ]


def _check_episode_choice(request: LaunchRequest, task: dict) -> None:
    """Seed and layout must be ones the task declares (bool is not an index)."""
    for name in ("seed", "layout_id"):
        value = getattr(request, name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"{name} must be an integer")
    if request.seed is None and request.layout_id is None:
        return
    if "seeds" not in task:
        raise ValueError("This task has no selectable evaluation seeds or layouts")
    if request.seed is not None and request.seed not in task["seeds"]:
        raise ValueError(f"seed must be one of {task['seeds']}")
    if request.layout_id is not None and not 0 <= request.layout_id < task["layouts_per_seed"]:
        raise ValueError(f"layout_id must be between 0 and {task['layouts_per_seed'] - 1}")


def validate(request: LaunchRequest) -> dict:
    request, method, agent = resolve_selection(request)
    benchmarks.launch_target(request.integration, request.profile, request.task)
    task = benchmarks.task_info(request.integration, request.profile, request.task)
    if task is None:
        raise ValueError(f"task {request.task!r} is not prepared in {request.integration}/{request.profile}")
    if request.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning effort must be one of {REASONING_EFFORTS}")
    _check_episode_choice(request, task)
    from evoab.provider import catalog

    if not isinstance(request.model, str) or not request.model:
        raise ValueError("A model preset is required")
    _agent_option_flags(request, agent)
    if request.visible_tools is not None:
        if not isinstance(request.visible_tools, list) or not all(isinstance(name, str) for name in request.visible_tools):
            raise ValueError("visible_tools must be a list of tool names")
        groups = tool_groups(request.integration, request.method, request.agent)
        allowed = {name for names in groups.values() for name in names}
        if set(request.visible_tools) - allowed:
            raise ValueError("Selection contains tools not available for this benchmark and Agent")
    _parameter_flags(request, method)
    recipe = resolve_run_recipe(request, method)
    if request.team and recipe.entry.brain.model_preset not in (None, request.model):
        raise ValueError("The entry Agent preset must match the launch model")
    presets = {request.model}
    if request.team:
        presets.update(role.brain.model_preset for role in recipe.roles.values() if role.brain.model_preset)
    if agent.validate_preset is not None:
        for preset in presets:
            agent.validate_preset(preset)
    else:
        unknown = presets - {p["name"] for p in catalog.presets()}
        if unknown:
            raise ValueError(f"unknown model preset {sorted(unknown)[0]!r}")
    return task


def resolve_run_recipe(request: LaunchRequest, method: dict) -> Recipe:
    """Compile the method with the requested parameters before starting a process."""
    from evoab.composition.compiler import compile_recipe
    from evoab.composition.methods import get_method
    from evoab.composition.recipe import with_overrides
    from evoab.integrations import load_integration

    entry = get_method(method["id"])
    recipe = with_overrides(entry.recipe, dict(request.params or {}))
    if request.team is not None:
        if method.get("external_evaluator"):
            raise ValueError("An external evaluator owns its planner and tools.")
        from evoab.workbench.team import team_roles
        if request.params or request.visible_tools is not None:
            raise ValueError("Agent cards already define the tools and role parameters")
        if recipe.entry.channel != "tools" or recipe.execution.return_mode != "immediate" or len(recipe.roles) != 1:
            raise ValueError("Agent cards require a single tool-calling role with immediate results")
        roles = team_roles(request.integration, request.team)
        return_mode = "background" if any(role.get("job_tools") for role in roles.values()) else "immediate"
        recipe = with_overrides(recipe, {"roles": roles, "execution.return_mode": return_mode})
    if request.agent:
        recipe = with_overrides(recipe, {f"roles.{recipe.loop.entry}.brain.agent": request.agent})
    compile_recipe(recipe, load_integration(request.integration).get_integration_spec(), base_dir=entry.root)
    return recipe


def launch(request: LaunchRequest, task: dict, runs_root: Path) -> str:
    """Start one run in its own process group; return its launch id.

    The run writes its artifacts to a directory it creates under ``runs_root``;
    Studio resolves that directory from the launch log.
    """
    launch_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}"
    request, method, agent = resolve_selection(request)
    recipe_path = None
    if request.team is not None:
        recipe = resolve_run_recipe(request, method)
        recipe_path = (runs_root / STUDIO_DIR / "recipes" / f"{launch_id}.json").resolve()
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    jobs.start(
        _build_command(request, method, agent, runs_root, recipe_path),
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
        agent=request.agent or method["agent"],
        reasoning_effort=request.reasoning_effort,
        method=request.method,
        method_label=method["label"],
        channel=method["channel"],
        params=request.params,
        agent_options=request.agent_options,
        visible_tools=request.visible_tools,
    )
    return launch_id
