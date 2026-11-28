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

from evoab.agent.contracts import AgentLaunch
from evoab.agent.loop import agent_launch, list_agent_launches
from evoab.apps.workbench import benchmarks, jobs
from evoab.apps.workbench.runs import STUDIO_DIR

if TYPE_CHECKING:
    from evoab.recipe.schema import Recipe

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class LaunchRequest:
    integration: str
    profile: str
    task: str
    model: str
    reasoning_effort: str = "low"
    method: str = "tools"
    # Recipe overrides by dotted path, e.g. {"roles.main.action.horizon": [16, 32]}.
    params: dict | None = None
    # Overrides the entry role's agent runtime; None keeps the recipe's.
    agent: str | None = None
    agent_options: dict[str, int] | None = None
    visible_tools: list[str] | None = None
    team: list[dict] | None = None
    # Evaluation seed, for benchmarks whose tasks declare selectable seeds.
    seed: int | None = None
    instruction: str | None = None


def _agent_choice(name: str, launch: AgentLaunch) -> dict:
    return {"id": name, "label": launch.label, "description": launch.description,
            "integrations": launch.integrations, "cli_flags": launch.cli_flags,
            "options": [asdict(option) for option in launch.options],
            "filters_presets": launch.validate_preset is not None, "model_help": launch.model_help,
            "probes_presets": launch.probe_preset is not None}


def methods(integration: str | None = None) -> tuple[dict, ...]:
    """Library methods with what a launcher needs: parameters, compatible agents and the graph."""
    from evoab.recipe.graph import recipe_graph
    from evoab.recipe.methods import list_methods

    launches = list_agent_launches()
    spec = None
    if integration is not None:
        from evoab.envs import load_integration
        spec = load_integration(integration).get_integration_spec()
    result = []
    for method in list_methods():
        recipe = method.recipe
        if integration is not None and recipe.integrations is not None and integration not in recipe.integrations:
            continue
        unavailable = None
        if spec is not None:
            from evoab.recipe.compiler import compile_recipe
            try:
                compile_recipe(recipe, spec, base_dir=method.root)
            except ValueError as error:
                unavailable = str(error)
        entry = recipe.entry
        # Every method runs on the built-in agent.
        agents = [_agent_choice(name, launch) for name, launch in launches.items()]
        default_agent = "native"
        result.append({
            "id": recipe.id, "label": recipe.label, "summary": recipe.summary,
            "status": recipe.status, "source": method.source, "integrations": recipe.integrations,
            "file": _recipe_file(method),
            "external_evaluator": False,
            "fixed_effort": None,
            "act": list(entry.act), "stateless": entry.stateless, "agent": default_agent, "roles": list(recipe.roles),
            "references": [reference.model_dump(mode="json") for reference in recipe.references],
            "episodes": recipe.loop.episodes.model_dump(mode="json"),
            "agents": agents if unavailable is None else [], "unavailable": unavailable,
            "graph": recipe_graph(recipe),
        })
    return tuple(result)


def _recipe_file(method) -> str:
    """Where the method's YAML lives: package-relative for built-in methods, absolute otherwise."""
    if method.root is None:
        return "—"
    path = Path(method.root) / "method.yaml"
    package = Path(__file__).resolve().parents[3]
    try:
        return str(path.resolve().relative_to(package))
    except ValueError:
        return str(path)


def preferred_choice(integration: str, method: str | None, agent: str | None) -> tuple[str, str | None]:
    """Pick a usable saved selection for the current benchmark."""
    available = {item["id"]: item for item in methods(integration)}
    for candidate in dict.fromkeys((method, "tools", "action", *available)):
        item = available.get(candidate)
        if item is None or not item["agents"]:
            continue
        ids = [choice["id"] for choice in item["agents"]]
        return candidate, agent if candidate == method and agent in ids else None
    return "tools", None


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
    custom_task = benchmarks.entry_prompt_as_task(request.integration)
    if request.instruction is not None and not custom_task:
        raise ValueError("This benchmark supplies a fixed task instruction")
    if custom_task and isinstance(request.team, list) and request.team:
        from evoab.apps.workbench.team import AgentCard
        entry = AgentCard.model_validate(request.team[0])
        if entry.instructions.strip():
            if request.instruction is not None and request.instruction != entry.instructions:
                raise ValueError("The entry prompt and the explicit episode instruction must agree")
            request = replace(request, instruction=entry.instructions,
                              team=[{**request.team[0], "instructions": ""}, *request.team[1:]])
    return replace(request, method=method_id, agent=agent_id,
                   reasoning_effort=method["fixed_effort"] or request.reasoning_effort), method, agent


def _override_flags(request: LaunchRequest) -> list[str]:
    """Recipe overrides as ``--set PATH=VALUE``; values are written as JSON, which the run reads as YAML."""
    if request.params is not None and not isinstance(request.params, dict):
        raise ValueError("params must be an object of recipe paths")
    flags = []
    for path, value in (request.params or {}).items():
        if not isinstance(path, str) or not path or "=" in path:
            raise ValueError(f"invalid recipe path {path!r}")
        if not isinstance(value, (str, int, float, bool, list)):
            raise ValueError(f"{path} must be a scalar or a list")
        text = json.dumps(value)
        if "\x00" in text or len(text) > 8192:
            raise ValueError(f"{path} must be at most 8192 characters without NUL")
        flags.append(f"--set={path}={text}")
    return flags


def tool_groups(integration: str, method_id: str, agent_id: str) -> dict[str, list[str]]:
    """The entry role's tools by capability, for Studio's per-run tool picker (written into the run recipe)."""
    from evoab.recipe.compiler import compile_recipe
    from evoab.recipe.methods import get_method
    from evoab.envs import load_integration
    from evoab.tools.selection import TOOL_GROUPS

    if integration not in {info.integration for info in benchmarks.REGISTRY}:
        raise ValueError("Unknown benchmark")
    request = LaunchRequest(integration, "", "", "", method=method_id, agent=agent_id)
    _, method, agent = resolve_selection(request)
    if method["stateless"] or len(method["roles"]) > 1:
        raise ValueError("Tool selection applies to one-role methods that act through tools or code")
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
        *([f"--instruction={request.instruction}"] if request.instruction is not None else []),
        "--recipe", str(recipe_path) if recipe_path else method["id"],
        "--model", request.model,
        "--reasoning-effort", request.reasoning_effort,
        *agent.cli_flags,
        *_agent_option_flags(request, agent),
        *_override_flags(request),
    ]
    if request.seed is not None:
        command.extend(("--seed", str(request.seed)))
    return command + [
        "--output-dir", str(output_root),
        # Port 0 picks a free port; the CLI then prints the run directory at start.
        "--monitor", "--monitor-port", "0",
    ]


def _check_episode_choice(request: LaunchRequest, task: dict) -> None:
    """A seed must be one the task declares (bool is not an index)."""
    if request.seed is None:
        return
    if isinstance(request.seed, bool) or not isinstance(request.seed, int):
        raise ValueError("seed must be an integer")
    if "seeds" not in task:
        raise ValueError("This task has no selectable evaluation seeds")
    if request.seed not in task["seeds"]:
        raise ValueError(f"seed must be one of {task['seeds']}")


def validate(request: LaunchRequest) -> dict:
    request, method, agent = resolve_selection(request)
    benchmarks.launch_target(request.integration, request.profile, request.task)
    task = benchmarks.task_info(request.integration, request.profile, request.task)
    if task is None:
        raise ValueError(f"task {request.task!r} is not prepared in {request.integration}/{request.profile}")
    if request.instruction is not None:
        text = request.instruction
        if not isinstance(text, str) or not text.strip() or "\x00" in text or len(text) > 8192:
            raise ValueError("instruction must be 1–8192 characters of nonblank text without NUL")
    if request.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning effort must be one of {REASONING_EFFORTS}")
    _check_episode_choice(request, task)
    from evoab.agent.models import catalog

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
    _override_flags(request)
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
    from evoab.recipe.compiler import compile_recipe
    from evoab.recipe.methods import get_method
    from evoab.recipe.schema import with_overrides
    from evoab.envs import load_integration

    entry = get_method(method["id"])
    recipe = with_overrides(entry.recipe, dict(request.params or {}))
    if request.team is not None:
        from evoab.apps.workbench.team import team_roles
        if request.params or request.visible_tools is not None:
            raise ValueError("Agent cards already define the tools and role parameters")
        if recipe.entry.act != ["tools"] or len(recipe.roles) != 1:
            raise ValueError("Agent cards require a single role that acts through tools")
        roles = team_roles(request.integration, request.team)
        return_mode = "background" if any(role.get("job_tools") for role in roles.values()) else "immediate"
        recipe = with_overrides(recipe, {"roles": roles, "execution.return_mode": return_mode})
    spec = load_integration(request.integration).get_integration_spec()
    if request.visible_tools is not None:
        recipe = _narrow_entry_tools(recipe, compile_recipe(recipe, spec, base_dir=entry.root),
                                     set(request.visible_tools))
    compile_recipe(recipe, spec, base_dir=entry.root)
    return recipe


def _narrow_entry_tools(recipe: Recipe, compiled, chosen: set[str]) -> Recipe:
    """The picker's choice written into the recipe: capabilities keep only the chosen tools."""
    from evoab.tools.selection import TOOL_GROUPS

    entry = compiled.entry
    available = {item.name: item.selection.tools or item.module.tools for item in entry.modules}
    capabilities = []
    for capability in entry.role.capabilities:
        if capability.id not in available:  # harness capabilities (delegate) are not narrowed
            capabilities.append(capability)
            continue
        kept = [name for name in available[capability.id] if name in chosen]
        if kept:
            capabilities.append(capability.model_copy(update={"tools": kept}))
    jobs = [name for name in TOOL_GROUPS["@jobs"] if name in chosen] \
        if recipe.execution.return_mode == "background" else None
    role = entry.role.model_copy(update={"capabilities": capabilities, "job_tools": jobs})
    return recipe.model_copy(update={"roles": {**recipe.roles, recipe.loop.entry: role}})


def launch(request: LaunchRequest, task: dict, runs_root: Path) -> str:
    """Start one run in its own process group; return its launch id.

    The run writes its artifacts to a directory it creates under ``runs_root``;
    Studio resolves that directory from the launch log.
    """
    launch_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}"
    request, method, agent = resolve_selection(request)
    recipe_path = None
    if request.team is not None or request.visible_tools is not None:
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
        instruction=request.instruction if request.instruction is not None else task.get("instruction"),
        embodiment=task["embodiment"],
        max_steps=task.get("max_steps"),
        task=request.task,
        model=request.model,
        agent=request.agent or method["agent"],
        reasoning_effort=request.reasoning_effort,
        method=request.method,
        method_label=method["label"],
        act=method["act"],
        params=request.params,
        agent_options=request.agent_options,
        visible_tools=request.visible_tools,
    )
    return launch_id
