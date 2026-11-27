"""Compile a recipe against one integration into resolved run settings.

The compiler is the only place where a recipe becomes runtime settings. It
checks every role against the integration's module catalog and the agent's
channels, then writes the entry role's settings onto the run namespace that
``session.py`` assembles. Other roles are compiled too and reached through the
delegate capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evoab.composition.components import HARNESS_CAPABILITIES
from evoab.composition.recipe import Capability, FlowAPI, Recipe, Role
from evoab.integrations.composition import SelectedModule, module_catalog


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
    start_prompt: str = ""

    @property
    def channel(self) -> str:
        return self.role.channel

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
    if recipe.inputs.task_supplements and not getattr(spec, "task_supplements", False):
        raise ValueError(f"inputs.task_supplements: {spec.name} publishes no task supplements")
    catalog = module_catalog(spec)
    from evoab.integrations.api_modules import extend_catalog
    extend_catalog(catalog, recipe.apis)
    roles = {name: _compile_role(name, role, catalog, spec, base_dir, recipe)
             for name, role in recipe.controllers.items()}
    for name, role in roles.items():
        if role.observation_profile != "none" and name != recipe.loop.entry:
            raise ValueError("the observation capability is available to the loop entry role only")
        for target in role.delegates:
            if roles[target].channel != "action" and recipe.execution.resource_scheduling == "global-serial":
                raise ValueError(f"role {name!r} delegates to the {roles[target].channel} role {target!r}, whose "
                                 "tool calls would wait behind the delegation under global-serial scheduling")
    return CompiledRecipe(recipe, roles, base_dir)


def _compile_role(name: str, role: Role, catalog: dict, spec: Any, base_dir: Path | None, recipe: Recipe) -> CompiledRole:
    where = f"role {name!r}"
    modules, delegates, observation = [], (), "none"
    calls = recipe.call_targets(role)
    declared = {cap.id for cap in role.capabilities}
    named_services = [Capability(id=ref) for ref in calls
                      if ref in recipe.apis and ref not in recipe.controllers and ref not in declared]
    for capability in [*role.capabilities, *named_services]:
        if capability.id == "delegate":
            delegates = tuple(capability.options.get("roles") or ())
            if not delegates or set(capability.options) - {"roles"} or capability.tools:
                raise ValueError(f"{where}: delegate takes options.roles only")
            continue
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
    delegates = tuple(dict.fromkeys([*delegates, *(ref for ref in calls if ref in recipe.controllers)]))
    if isinstance(role, FlowAPI):
        exposed = {t for m in modules for t in (m.selection.tools or m.module.tools)}
        if unknown := set(calls) - set(delegates) - exposed:
            raise ValueError(f"{where}: flow calls unavailable tools {sorted(unknown)}")
    start = _read_files([*recipe.prompts.start, *role.prompts.start], base_dir)
    each = _read_files([*recipe.prompts.each_call, *role.prompts.each_call], base_dir)
    direct = _direct_config(role, base_dir, start, each) if role.channel == "action" else None
    return CompiledRole(name, role, tuple(modules), delegates, observation, direct,
                        "\n\n".join(filter(None, (_prompt_text(role, base_dir), each))), start)


def _direct_config(role: Role, base_dir: Path | None, start: str, each: str):
    from evoab.direct import prompts
    from evoab.direct.actions import REPRESENTATIONS
    from evoab.direct.runner import DirectConfig

    action = role.action
    output = role.output or action
    budget = role.budget or action
    if output.representation not in REPRESENTATIONS:
        raise ValueError(f"unknown action representation {output.representation!r}; "
                         f"choose from {', '.join(REPRESENTATIONS)}")
    if role.inputs.blocks is not None:
        blocks = tuple(role.inputs.blocks)
    elif action.blocks is not None:
        # Legacy action.blocks only toggled optional gripper and camera details.
        blocks = tuple(dict.fromkeys(("robot", "timing", *action.blocks)))
    else:
        blocks = prompts.BLOCKS
    if unknown := sorted(set(blocks) - set(prompts.BLOCKS)):
        raise ValueError(f"unknown direct prompt blocks {unknown}; choose from {list(prompts.BLOCKS)}")
    robot = None
    if path := role.inputs.robot_prompt_file or action.robot_prompt_file:
        robot = _resolve(path, base_dir).read_text(encoding="utf-8")
    custom = "\n\n".join(part for part in (action.custom_prompt, _prompt_text(role, base_dir), each) if part)
    history = role.inputs.history or action.history
    return DirectConfig(
        representation=output.representation, min_horizon=output.horizon[0], max_horizon=output.horizon[1],
        history_length=history[0], history_stride=history[1], blocks=blocks, start_prompt=start,
        robot_prompt=robot, custom_prompt=custom, translation_limit_m=float(action.step_limits[0]),
        rotation_limit_rad=float(action.step_limits[1]), joint_step_limit_rad=action.joint_step_limit_rad,
        repair_attempts=budget.repair_attempts, max_calls=budget.max_calls)


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


def _read_files(paths, base_dir):
    return "\n\n".join(_resolve(path, base_dir).read_text(encoding="utf-8").strip() for path in paths)


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
    args.channel = entry.channel
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
    args.depth, args.camera_calibration = inputs.depth, inputs.calibration
    args.task_supplements = inputs.task_supplements
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
        if args.observation_profile != "none" or args.skill_writing or entry.channel != "tools":
            raise ValueError("the compact tool style requires the tools channel without observation "
                             "or skill writing")


def check_inputs(inputs: Any, description: Any) -> None:
    """Fail when a connected environment gives the agent other inputs than the recipe asks for."""
    cameras = getattr(getattr(description, "environment", None), "cameras", None) or ()
    for name, attribute in (("depth", "has_depth"), ("calibration", "has_calibration")):
        wanted = getattr(inputs, name)
        actual = {getattr(camera, attribute, None) for camera in cameras} - {None}
        if wanted is not None and actual and actual != {wanted}:
            raise ValueError(f"the recipe sets inputs.{name}: {str(wanted).lower()}, but the environment "
                             f"{'provides' if True in actual else 'does not provide'} {name}; start it to match "
                             f"(--deploy local applies the recipe, or evoab deploy env --{'' if wanted else 'no-'}"
                             f"{'depth' if name == 'depth' else 'camera-calibration'})")


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
    from evoab.integrations import load_integration

    recipe = Recipe.model_validate(args.recipe)
    spec = load_integration(args.integration).get_integration_spec()
    compiled = compile_recipe(recipe, spec, base_dir=Path(args.recipe_dir) if args.recipe_dir else None)
    entry = compiled.entry
    args.recipe, args.compiled_recipe = recipe, compiled
    args.selected_modules, args.direct_config = entry.modules, entry.direct
