"""The recipe: one serializable description of how agents control a robot.

A recipe is the single source of truth for a run's method. It names one or more
roles (each an agent with a brain, a channel, capabilities and a context policy),
the loop that connects them, and the execution settings shared by their tools.
Recipes are validated offline, before any environment or model service starts.

Three orthogonal axes replace a single "interaction mode":

* channel: how a role reaches the robot (``tools`` = native tool calls,
  ``code`` = programs against a generated Python API, ``action`` = one forced
  action-chunk call per stateless inference);
* capabilities: which tool modules the role may use (scene, gripper, skills,
  delegation to other roles, ...);
* context: how much history each request carries.

The loop connects roles: the entry role runs the episode and other roles are
reachable only through ``delegate`` capabilities. ``loop.episodes`` repeats
whole episodes while carrying experience (skills, programs) between them.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Channel = Literal["tools", "code", "action"]
Style = Literal["full", "compact"]
ROLE_NAME = r"^[a-z][a-z0-9_]{0,31}$"


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brain(Spec):
    """The model runtime of one role; agent, model and effort may be left to the launcher.

    ``agent: null`` (the default) means the agent chosen for the run, so a method
    is independent of the agent that executes it. Pin an agent only when the
    method is that agent (an external evaluator). ``action`` roles have no agent
    loop: each inference is one request to the role's model.
    """

    agent: str | None = None
    model: str | None = None
    model_preset: str | None = Field(default=None, min_length=1)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None

    @model_validator(mode="after")
    def exclusive_model_source(self):
        if self.model is not None and self.model_preset is not None:
            raise ValueError("Choose either brain.model or brain.model_preset")
        return self


class Capability(Spec):
    """One tool module selected for a role, optionally narrowed to some of its tools."""

    id: str = Field(min_length=1)
    tools: list[str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    # Skip this capability, instead of rejecting the recipe, where the integration lacks it.
    optional: bool = False

    @field_validator("tools")
    @classmethod
    def distinct_tools(cls, value):
        if value is not None and (not value or len(set(value)) != len(value)):
            raise ValueError("tools must be a non-empty list of distinct names")
        return value


class ActionSpec(Spec):
    """The action channel: each inference returns exactly one action chunk."""

    representation: str = "abs_eef"
    horizon: tuple[int, int] = (3, 15)
    history: tuple[int, int] = (15, 7)
    step_limits: tuple[float, float] = (0.02, 0.1)
    joint_step_limit_rad: float = Field(default=0.05, gt=0)
    blocks: list[str] | None = None
    robot_prompt_file: str | None = None
    custom_prompt: str = ""
    repair_attempts: int = Field(default=2, ge=0)
    max_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_ranges(self):
        low, high = self.horizon
        if not 1 <= low <= high <= 64:
            raise ValueError("action.horizon must satisfy 1 <= MIN <= MAX <= 64")
        length, stride = self.history
        if length < 1 or stride < 1:
            raise ValueError("action.history values must be positive")
        if min(self.step_limits) <= 0:
            raise ValueError("action.step_limits must be positive")
        return self


class ContextSpec(Spec):
    """History retained across requests of a tool or code role."""

    policy: Literal["identity", "sliding_window", "observation_window"] = "identity"
    observation_horizon: int = Field(default=2, gt=0)
    image_token_allowance: int = Field(default=1536, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    target_input_tokens: int | None = Field(default=None, gt=0)
    keep_recent_blocks: int = Field(default=4, ge=0)
    keep_observation_blocks: int = Field(default=1, ge=0)
    min_recent_tool_images: int = Field(default=3, ge=0)
    image_eviction_batch_size: int = Field(default=20, gt=0)


class PromptSpec(Spec):
    """Method text appended to the environment prompt of a role."""

    append: str = ""
    files: list[str] = Field(default_factory=list)


class Role(Spec):
    description: str = ""
    brain: Brain = Field(default_factory=Brain)
    channel: Channel = "tools"
    capabilities: list[Capability] = Field(default_factory=list)
    # None inherits the executor's controls. An empty list requests completed results.
    job_tools: list[str] | None = None
    style: Style = "full"
    initial_observation: bool = False
    action: ActionSpec | None = None
    context: ContextSpec = Field(default_factory=ContextSpec)
    prompt: PromptSpec = Field(default_factory=PromptSpec)
    max_turns: int = Field(default=0, ge=0)

    @field_validator("capabilities", mode="before")
    @classmethod
    def shorthand(cls, value):
        """Accept ``- scene`` as shorthand for ``- {id: scene}``."""
        return [{"id": item} if isinstance(item, str) else item for item in (value or [])]

    @model_validator(mode="after")
    def channel_requirements(self):
        if self.job_tools is not None:
            from evoab.tools.selection import TOOL_GROUPS
            if self.channel == "action" or set(self.job_tools) - set(TOOL_GROUPS["@jobs"]):
                raise ValueError("job_tools must select executor controls for a tools or code role")
        ids = [capability.id for capability in self.capabilities]
        if len(set(ids)) != len(ids):
            raise ValueError("a role selects each capability at most once")
        if self.channel == "action":
            if self.capabilities:
                raise ValueError("an action role emits action chunks only; remove its capabilities")
            if self.action is None:
                self.action = ActionSpec()
        elif self.action is not None:
            raise ValueError("action settings require channel: action")
        if self.initial_observation and self.channel == "action":
            raise ValueError("action roles always observe; initial_observation applies to tool and code roles")
        return self

    def capability(self, name: str) -> Capability | None:
        return next((item for item in self.capabilities if item.id == name), None)


class Experience(Spec):
    """What a sequence of episodes carries forward."""

    repeat: int = Field(default=1, ge=1, le=1000)
    carry: list[Literal["skills", "programs"]] = Field(default_factory=list)
    keep: Literal["success", "always"] = "success"
    stop_on_success: bool = False


class Loop(Spec):
    entry: str = "main"
    episodes: Experience = Field(default_factory=Experience)


class ExecutionSpec(Spec):
    return_mode: Literal["immediate", "background"] = "background"
    resource_scheduling: Literal["per-resource", "global-serial"] = "per-resource"
    # None selects the integration's mode (IntegrationSpec.execution_mode).
    mode: Literal["parallel", "replanning"] | None = None
    observation_profile: str = "none"
    # Whether the model may request several tool calls in one response.
    parallel_tool_calls: bool = True


class Inputs(Spec):
    """What the episode gives every role. ``None`` keeps the environment's default.

    ``depth`` and ``calibration`` are applied when EvoAB starts the environment
    (``--deploy local``) and checked against the description of a connected one.
    ``task_supplements`` appends the environment's extra task clarifications
    (only environments that publish them, such as RoboDojo).
    """

    depth: bool | None = None
    calibration: bool | None = None
    task_supplements: bool = False

    @model_validator(mode="after")
    def calibration_needs_no_depth(self):
        if self.calibration is False and self.depth is not False:
            raise ValueError("inputs.calibration: false requires inputs.depth: false")
        return self


class Reference(Spec):
    title: str
    url: str
    kind: Literal["paper", "code", "project", "doc"] = "paper"


class Recipe(Spec):
    """A complete method: roles, loop and execution, plus presentation metadata."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str
    summary: str = ""
    paradigms: list[Literal["orchestrate", "direct", "code", "policy"]] = Field(default_factory=list)
    status: Literal["builtin", "reproduction", "experimental"] = "builtin"
    integrations: list[str] | None = None
    references: list[Reference] = Field(default_factory=list)
    inputs: Inputs = Field(default_factory=Inputs)
    roles: dict[str, Role]
    loop: Loop = Field(default_factory=Loop)
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)

    @model_validator(mode="after")
    def roles_and_loop(self):
        import re
        if not self.roles:
            raise ValueError("a recipe needs at least one role")
        for name in self.roles:
            if not re.fullmatch(ROLE_NAME, name):
                raise ValueError(f"invalid role name {name!r}")
        if self.loop.entry not in self.roles:
            raise ValueError(f"loop.entry names an unknown role {self.loop.entry!r}")
        if self.execution.return_mode != "background" and any(role.job_tools for role in self.roles.values()):
            raise ValueError("job tools require execution.return_mode: background")
        entry = self.roles[self.loop.entry]
        if "programs" in self.loop.episodes.carry and entry.channel != "code":
            raise ValueError("carrying programs requires a code entry role")
        if "skills" in self.loop.episodes.carry:
            skills = entry.capability("skills")
            if skills is None or not skills.options.get("write"):
                raise ValueError("carrying skills requires the entry role's skills capability with write: true")
        reachable = {self.loop.entry}
        frontier = [self.loop.entry]
        while frontier:
            role = self.roles[frontier.pop()]
            delegate = role.capability("delegate")
            for target in (delegate.options.get("roles", []) if delegate else []):
                if target not in self.roles:
                    raise ValueError(f"delegate names an unknown role {target!r}")
                if target == self.loop.entry:
                    raise ValueError("a role cannot delegate back to the loop entry")
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        if unreachable := sorted(set(self.roles) - reachable):
            raise ValueError(f"roles not reachable from loop.entry through delegate: {unreachable}")
        return self

    @property
    def entry(self) -> Role:
        return self.roles[self.loop.entry]


# -- paths and overrides ------------------------------------------------------


def _segments(path: str) -> list[str]:
    parts = path.split(".")
    if not path or any(not part for part in parts):
        raise ValueError(f"invalid recipe path {path!r}")
    return parts


def get_path(data: dict, path: str) -> Any:
    node: Any = data
    for part in _segments(path):
        if isinstance(node, list):
            node = _list_item(node, part, path)
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise ValueError(f"recipe path {path!r} does not exist")
    return node


def _list_item(items: list, part: str, path: str):
    """Capabilities are addressed by id (``capabilities.skills.options.write``)."""
    for item in items:
        if isinstance(item, dict) and item.get("id") == part:
            return item
    if part.isdigit() and int(part) < len(items):
        return items[int(part)]
    raise ValueError(f"recipe path {path!r} does not exist")


def set_path(data: dict, path: str, value: Any) -> None:
    parts = _segments(path)
    node: Any = data
    for part in parts[:-1]:
        if isinstance(node, list):
            node = _list_item(node, part, path)
        else:
            if not isinstance(node, dict):
                raise ValueError(f"recipe path {path!r} does not exist")
            node = node.setdefault(part, {})
            if node is None:
                raise ValueError(f"recipe path {path!r} does not exist")
    last = parts[-1]
    if isinstance(node, list):
        raise ValueError(f"recipe path {path!r} must end at a field")
    if not isinstance(node, dict):
        raise ValueError(f"recipe path {path!r} does not exist")
    node[last] = value


def parse_value(text: str) -> Any:
    """Read a path override as a YAML 1.2-style scalar or list (``3``, ``true``, ``[3, 15]``, ``low``)."""
    if text == "":
        return ""
    if re_sexagesimal(text):
        return text  # PyYAML would read "3:10" as a base-60 integer.
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def re_sexagesimal(text: str) -> bool:
    import re
    return re.fullmatch(r"[-+]?\d+(:\d+)+(\.\d*)?", text.strip()) is not None


def parse_assignment(text: str) -> tuple[str, str]:
    """Split ``PATH=VALUE``; the value stays raw text until its target type is known."""
    path, sep, value = text.partition("=")
    if not sep or not path.strip():
        raise ValueError(f"--set expects PATH=VALUE, got {text!r}")
    return path.strip(), value


def with_overrides(recipe: Recipe, assignments: dict[str, Any]) -> Recipe:
    """Return a new validated recipe with values replaced at dotted paths.

    Text values are read as YAML (``--set roles.main.action.horizon=[16,32]``).
    """
    if not assignments:
        return recipe
    data = copy.deepcopy(recipe.model_dump(mode="json"))
    for key, value in assignments.items():
        set_path(data, key, parse_value(value) if isinstance(value, str) else value)
    try:
        return Recipe.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, item['loc'])) or 'recipe'}: {item['msg']}"
                             for item in error.errors())
        raise ValueError(f"invalid recipe override ({', '.join(assignments)}): {problems}") from None


# -- files ---------------------------------------------------------------------


def read_recipe_file(path: str | Path) -> Recipe:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    # Method directories describe the card next to the recipe; only the recipe is validated here.
    data.pop("card", None)
    try:
        return Recipe.model_validate(data)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def recipe_document(recipe: Recipe) -> str:
    return yaml.safe_dump(recipe.model_dump(mode="json", exclude_defaults=True), sort_keys=False,
                          allow_unicode=True)
