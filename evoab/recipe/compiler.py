"""Compile a recipe against one integration into resolved run settings.

The compiler is the only place where a recipe becomes runtime settings. It
checks every role against the integration's module catalog and the ways it
acts, then writes the entry role's settings onto the run namespace that
``session.py`` assembles. Other roles are compiled too and reached through the
delegate capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evoab.recipe.components import HARNESS_CAPABILITIES
from evoab.recipe.schema import Recipe, Role
from evoab.envs.composition import SelectedModule, module_catalog


@dataclass(frozen=True)
class CompiledRole:
    """One role, checked against an integration and ready to assemble."""

    name: str
    role: Role
    modules: tuple[SelectedModule, ...] = ()
    delegates: tuple[str, ...] = ()
    observation_profile: str = "none"
    direct: Any | None = None
    prompt_text: str = ""

    @property
    def act(self) -> tuple[str, ...]:
        return tuple(self.role.act)

    @property
    def stateless(self) -> bool:
        return self.role.stateless

    @property
    def emits_chunks(self) -> bool:
        """Action chunks as a tool in an agent session (``action`` next to tools or code)."""
        return "action" in self.role.act and not self.role.stateless

    @property
    def tool_modules(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.modules)

    @property
    def selected_tools(self) -> tuple[str, ...]:
        # A module without selectable tools is selected whole and shown as "<module>.*".
        return tuple(name for item in self.modules
                     for name in (item.selection.tools or item.module.tools or (f"{item.name}.*",)))

    @property
    def skill_writing(self) -> bool:
        return any(item.name == "skills" and getattr(item.selection.options, "write", False)
                   for item in self.modules)


@dataclass(frozen=True)
class CompiledRecipe:
    recipe: Recipe
    roles: dict[str, CompiledRole]
    base_dir: Path | None = None

    @property
    def entry(self) -> CompiledRole:
        return self.roles[self.recipe.loop.entry]


def compile_recipe(recipe: Recipe, spec: Any, *, base_dir: Path | None = None) -> CompiledRecipe:
    """Check a recipe against an integration without opening any service."""
    if recipe.integrations is not None and spec.name not in recipe.integrations:
        raise ValueError(f"method {recipe.id!r} supports {', '.join(recipe.integrations)}, not {spec.name!r}")
    catalog = module_catalog(spec)
    roles = {name: _compile_role(name, role, catalog, spec, base_dir) for name, role in recipe.roles.items()}
    if len({repr(role.direct) for role in roles.values() if role.emits_chunks}) > 1:
        raise ValueError("roles that emit action chunks in an agent session must share one action setting")
    for name, role in roles.items():
        if role.observation_profile != "none" and name != recipe.loop.entry:
            raise ValueError("the observation capability is available to the loop entry role only")
        for target in role.delegates:
            if not roles[target].stateless and recipe.execution.resource_scheduling == "global-serial":
                raise ValueError(f"role {name!r} delegates to the agent role {target!r}, whose "
                                 "tool calls would wait behind the delegation under global-serial scheduling")
    return CompiledRecipe(recipe, roles, base_dir)


def _compile_role(name: str, role: Role, catalog: dict, spec: Any, base_dir: Path | None) -> CompiledRole:
    where = f"role {name!r}"
    modules, delegates, observation = [], (), "none"
    for capability in role.capabilities:
        if capability.id == "delegate":
            delegates = tuple(capability.options.get("roles") or ())
            if not delegates or set(capability.options) - {"roles"} or capability.tools:
                raise ValueError(f"{where}: delegate takes options.roles only")
            continue
        if capability.id == "action":
            raise ValueError(f"{where}: action chunks are a way of acting; add action to act instead of capabilities")
        if capability.id == "observation":
            if set(capability.options) - {"profile"} or capability.tools:
                raise ValueError(f"{where}: observation takes options.profile only")
            observation = capability.options.get("profile") or spec.name
            continue
        module = catalog.get(capability.id)
        if module is None and capability.optional:
            continue
        if module is None:
            available = sorted(set(catalog) | set(HARNESS_CAPABILITIES))
            raise ValueError(f"{where}: integration {spec.name!r} has no capability {capability.id!r}; "
                             f"choose from {', '.join(available)}")
        options = capability.options
        if capability.id == "user_tools":
            from evoab.tools.user import load_tools, resolve_file
            if not isinstance(options.get("file"), str):
                raise ValueError(f"{where}: user_tools needs options.file, a Python file of @tool functions")
            options = {**options, "file": str(resolve_file(options["file"], base_dir))}
            load_tools(options["file"])  # import and check every signature offline
        modules.append(module.select(capability.tools, options, role.style))
    if role.initial_observation and not any(item.name == "scene" for item in modules):
        raise ValueError(f"{where}: initial_observation requires the scene capability")
    direct = _direct_config(role, base_dir) if "action" in role.act else None
    if "action" in role.act and not role.stateless:
        from evoab.agent.act.action.tool import ACTION_MODULE
        modules.append(ACTION_MODULE.select())
    return CompiledRole(name, role, tuple(modules), delegates, observation, direct,
                        _prompt_text(role, base_dir))


def _direct_config(role: Role, base_dir: Path | None):
    from evoab.agent.act.action import prompts
    from evoab.agent.act.action.actions import REPRESENTATIONS
    from evoab.agent.act.action.runner import DirectConfig

    action = role.action
    if action.representation not in REPRESENTATIONS:
        raise ValueError(f"unknown action representation {action.representation!r}; "
                         f"choose from {', '.join(REPRESENTATIONS)}")
    blocks = tuple(action.blocks) if action.blocks is not None else prompts.BLOCKS
    if unknown := sorted(set(blocks) - set(prompts.BLOCKS)):
        raise ValueError(f"unknown direct prompt blocks {unknown}; choose from {list(prompts.BLOCKS)}")
    if not {"robot", "timing"} <= set(blocks):
        raise ValueError("action.blocks must include robot and timing")
    robot = None
    if action.robot_prompt_file:
        robot = _resolve(action.robot_prompt_file, base_dir).read_text(encoding="utf-8")
    # A stateless role has no agent prompt; its method text joins the action prompt instead.
    method_text = _prompt_text(role, base_dir) if role.stateless else ""
    custom = "\n\n".join(part for part in (action.custom_prompt, method_text) if part)
    return DirectConfig(
        representation=action.representation, min_horizon=action.horizon[0], max_horizon=action.horizon[1],
        history_length=action.history[0], history_stride=action.history[1], blocks=blocks,
        robot_prompt=robot, custom_prompt=custom, translation_limit_m=float(action.step_limits[0]),
        rotation_limit_rad=float(action.step_limits[1]), joint_step_limit_rad=action.joint_step_limit_rad,
        repair_attempts=action.repair_attempts, max_calls=action.max_calls)


def _resolve(path: str, base_dir: Path | None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        if base_dir is None:
            raise ValueError(f"relative prompt file {path!r} needs a method directory")
        candidate = (base_dir / candidate).resolve()
        if not candidate.is_relative_to(base_dir.resolve()):
            raise ValueError(f"prompt file {path!r} must stay inside its method directory")
    if not candidate.is_file():
        raise ValueError(f"prompt file {path!r} does not exist")
    return candidate


def _prompt_text(role: Role, base_dir: Path | None) -> str:
    parts = [_resolve(path, base_dir).read_text(encoding="utf-8").strip() for path in role.prompt.files]
    if role.prompt.append.strip():
        parts.append(role.prompt.append.strip())
    return "\n\n".join(part for part in parts if part)


# -- run settings -----------------------------------------------------------------

#: CLI flags that override the entry role's brain when given explicitly.
BRAIN_FLAGS = {"--model": "model", "--reasoning-effort": "reasoning_effort"}


def apply_to_args(args: Any, compiled: CompiledRecipe, *, explicit: set[str] = frozenset()) -> None:
    """Write the entry role's settings onto the run namespace.

    Explicit ``--model``/``--reasoning-effort`` win over the recipe (which model);
    everything about how the agent works comes from the recipe, overridden only
    through ``--set``. Every role runs on the built-in agent.
    """
    recipe, entry = compiled.recipe, compiled.entry
    brain = entry.role.brain
    args.agent_backend = "native"
    if ("--model" not in explicit or not getattr(args, "model", None)) and (brain.model or brain.model_preset):
        args.model = brain.model or brain.model_preset
    if "--reasoning-effort" not in explicit and brain.reasoning_effort is not None:
        args.reasoning_effort = brain.reasoning_effort
    args.max_turns = entry.role.max_turns
    args.recipe = recipe
    args.recipe_id = recipe.id
    args.recipe_dir = str(compiled.base_dir) if compiled.base_dir else None
    args.compiled_recipe = compiled
    args.act = entry.act
    args.tool_style = entry.role.style
    args.initial_observation = entry.role.initial_observation
    args.selected_modules = entry.modules
    args.tool_modules = entry.tool_modules
    args.selected_tools = entry.selected_tools
    args.skill_writing = entry.skill_writing
    args.delegates = entry.delegates
    args.direct_config = entry.direct
    args.prompt_append = entry.prompt_text
    selections = [item for role in compiled.roles.values() for item in role.modules]
    args.segmentation = any(item.name == "scene" and item.selection.options.segmentation for item in selections)
    policy = next((item.selection.options for item in selections if item.name == "hybrid"), None)
    args.action_model_binding = policy.binding if policy else None
    args.vla_review = policy.review if policy else None
    inputs = recipe.inputs
    args.depth = inputs.depth
    execution = recipe.execution
    args.tool_return_mode = execution.return_mode
    args.resource_scheduling = execution.resource_scheduling
    args.execution_mode = execution.mode
    args.observation_profile = entry.observation_profile if entry.observation_profile != "none" \
        else execution.observation_profile
    if args.tool_style == "compact":
        # The compact style is the four-tool baseline contract: completed results only.
        if execution.return_mode != "immediate":
            raise ValueError("the compact tool style requires execution.return_mode: immediate")
        if args.observation_profile != "none" or args.skill_writing or entry.act != ("tools",):
            raise ValueError("the compact tool style requires act: [tools] without observation "
                             "or skill writing")


def check_inputs(inputs: Any, description: Any) -> None:
    """Fail when a connected environment gives the agent other inputs than the recipe asks for."""
    cameras = getattr(getattr(description, "environment", None), "cameras", None) or ()
    wanted = inputs.depth
    actual = {getattr(camera, "has_depth", None) for camera in cameras} - {None}
    if wanted is not None and actual and actual != {wanted}:
        raise ValueError(f"the recipe sets inputs.depth: {str(wanted).lower()}, but the environment "
                         f"{'provides' if True in actual else 'does not provide'} depth; start it to match "
                         f"(evoab run applies the recipe, or evoab deploy env --{'' if wanted else 'no-'}depth)")


# -- process boundary ---------------------------------------------------------------

#: Compiled objects on the run namespace; they cross process boundaries as recipe data.
RUNTIME_OBJECTS = ("compiled_recipe", "selected_modules", "direct_config")


def portable_args(args: Any) -> dict:
    """The run namespace as JSON-ready data: the recipe as a document, compiled objects dropped."""
    values = {name: value for name, value in vars(args).items() if name not in RUNTIME_OBJECTS}
    if isinstance(values.get("recipe"), Recipe):
        values["recipe"] = values["recipe"].model_dump(mode="json")
    return values


def restore_compiled(args: Any) -> None:
    """Recompile the recipe a parent process resolved; every other setting is already resolved."""
    from evoab.envs import load_integration

    recipe = Recipe.model_validate(args.recipe)
    spec = load_integration(args.integration).get_integration_spec()
    compiled = compile_recipe(recipe, spec, base_dir=Path(args.recipe_dir) if args.recipe_dir else None)
    entry = compiled.entry
    args.recipe, args.compiled_recipe = recipe, compiled
    args.selected_modules, args.direct_config = entry.modules, entry.direct
