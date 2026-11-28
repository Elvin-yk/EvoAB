"""Environment prompt builders and shared runtime assembly."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from evoab.tools.toolkit import ImageAttachment

#: Evaluation-side episode facts the model must never receive.
EVALUATION_ONLY = ("max_episode_steps", "remaining_steps")


def environment_for_model(environment, *, indent: int | None = None) -> str:
    """The environment description as JSON, without the episode step budget."""
    import json
    data = environment.model_dump(mode="json")
    data.pop("max_episode_steps", None)
    for key in EVALUATION_ONLY:
        data.get("metadata", {}).pop(key, None)
    return json.dumps(data, ensure_ascii=False, indent=indent)


def without_evaluation_facts(state: dict[str, Any]) -> dict[str, Any]:
    """A copy of an environment state without evaluation-side metadata."""
    import copy
    result = copy.deepcopy(state)
    for key in EVALUATION_ONLY:
        result.get("metadata", {}).pop(key, None)
    return result


def state_json_for_model(state) -> str:
    """An environment state as JSON for prompts, without evaluation-side metadata."""
    import json
    return json.dumps(without_evaluation_facts(state.model_dump(mode="json")), ensure_ascii=False)


_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class Prompt:
    """Initial system/user input for one agent session."""

    user: str
    system: str | None = None
    sources: tuple[str, ...] = ()
    images: tuple[ImageAttachment, ...] = ()

    def flatten(self) -> str:
        """Flatten input for agents accepting a single string."""
        if self.system is None:
            return self.user
        return f"{self.system.rstrip()}\n\n{self.user.lstrip()}"

    def append(
        self,
        *,
        system: str | None = None,
        user: str | None = None,
        source: str | None = None,
    ) -> Prompt:
        """Append a section with consistent paragraph spacing and source tracking."""
        next_system = self.system
        if system:
            next_system = _append_text(next_system, system)
        next_user = self.user
        if user:
            next_user = _append_text(next_user, user)
        sources = self.sources + ((source,) if source else ())
        return replace(self, user=next_user, system=next_system, sources=sources)


@dataclass(frozen=True)
class PromptContext:
    """Complete prompt inputs for one agent session."""

    integration_name: str
    task: str
    action_model_name: str | None = None
    episode: Any | None = None
    session_dir: Path | None = None
    observation_profile: str = "none"
    # How the role acts: any of "tools" (calls), "code" (programs) and "action" (chunks).
    act: tuple[str, ...] = ("tools",)
    # "compact" is the minimal four-tool baseline presentation.
    style: str = "full"
    # The first user message already carries a calibrated RGB observation.
    initial_observation: bool = False
    skill_writing: bool = False
    tool_return_mode: str = "background"
    resource_scheduling: str = "per-resource"
    tool_names: tuple[str, ...] = ()
    tool_prompt_resources: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptContribution:
    """Capability-profile content that can be composed into the final prompt."""

    system: str | None = None
    user: str | None = None
    source: str | None = None


PromptBuilder = Callable[[PromptContext], Prompt]
TaskPromptBuilder = Callable[[str], Prompt]


def assemble_prompt(
    base: Prompt,
    context: PromptContext,
    contributions: tuple[PromptContribution, ...] = (),
) -> Prompt:
    """Append enabled runtime guidance, then persist the complete agent input."""
    prompts = get_prompts_dir()
    compact = context.style == "compact"
    prompt = _append_resource(base, prompts / "runtime" / ("clean.md" if compact else "common.md"))
    if context.tool_return_mode not in {"immediate", "background"}:
        raise ValueError(f"Invalid tool return mode: {context.tool_return_mode}")
    if context.resource_scheduling not in {"per-resource", "global-serial"}:
        raise ValueError(f"Invalid resource scheduling: {context.resource_scheduling}")
    if not context.act or set(context.act) - {"tools", "code", "action"}:
        raise ValueError(f"Invalid ways of acting: {context.act}")
    prompt = _append_resource(prompt, prompts / "runtime" / "process_control.md")
    calls = ("tool_calling.md", "batch.md", f"resource_{context.resource_scheduling}.md",
             f"return_{context.tool_return_mode}.md")
    if "code" in context.act:
        resources = (calls + ("code_as_policy.md", "tools_and_code.md") if "tools" in context.act
                     else ("code_as_policy.md",))
    else:
        resources = calls
    if compact:
        resources = ("tool_calling.md", "return_immediate.md")
    for name in resources:
        prompt = _append_resource(prompt, prompts / "runtime" / name)
    for name in context.tool_prompt_resources:
        prompt = _append_resource(prompt, prompts / "runtime" / name)
        prompt = _append_integration_resource(prompt, context.integration_name, name)
    if context.skill_writing:
        namespace = context.integration_name
        if context.episode is not None:
            namespace = context.episode.description.metadata.get("skill_namespace", namespace)
        path = prompts / "guides" / "meta_skill.md"
        prompt = prompt.append(
            system=_render(path, {"skill_namespace": namespace}), source=str(path),
        )
    if context.observation_profile != "none":
        for contribution in contributions:
            prompt = prompt.append(
                system=contribution.system,
                user=contribution.user,
                source=contribution.source,
            )
    if context.session_dir is not None and not compact:
        path = prompts / "runtime" / "storage_access.md"
        prompt = prompt.append(
            system=_render(path, {
                "images_dir": str(context.session_dir / "images"),
                "scratch_dir": str(context.session_dir / "scratch"),
            }),
            source=str(path),
        )
    if context.session_dir is not None:
        (context.session_dir / "effective_prompt.md").write_text(
            prompt.flatten(), encoding="utf-8",
        )
    return prompt


def _append_resource(prompt: Prompt, path: Path) -> Prompt:
    return prompt.append(system=path.read_text(encoding="utf-8"), source=str(path))


def _append_integration_resource(prompt: Prompt, integration: str, filename: str) -> Prompt:
    """Read optional environment-specific runtime notes only from bundled resources."""
    root = get_prompts_dir().resolve()
    path = (root / integration / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Integration prompt path must remain inside bundled resources")
    return _append_resource(prompt, path) if path.is_file() else prompt


def get_prompts_dir() -> Path:
    """Resolve bundled prompt resources independently of the working directory."""
    return Path(__file__).resolve().parents[2] / "resources" / "prompts"


def render_prompt_files(
    prompt_dir: str | Path,
    *,
    variables: Mapping[str, object],
) -> Prompt:
    """Render system.md and user.md while preserving message roles."""
    prompt_path = Path(prompt_dir)
    return Prompt(
        system=_render(prompt_path / "system.md", variables),
        user=_render(prompt_path / "user.md", variables),
        sources=(str(prompt_path / "system.md"), str(prompt_path / "user.md")),
    )


def _render(path: Path, variables: Mapping[str, object]) -> str:
    text = path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(f"missing prompt variable {name!r} for {path}")
        return str(variables[name])

    return _PLACEHOLDER.sub(replace, text)


def _append_text(current: str | None, addition: str) -> str:
    """Join prompt sections using stable blank-line rules."""
    if not current:
        return addition
    return f"{current.rstrip()}\n\n{addition.lstrip()}"


_PROMPT_REGISTRY: dict[str, PromptBuilder] = {}


def register_prompt_builder(integration: str) -> Callable[[PromptBuilder], PromptBuilder]:
    """Register the environment builder for one integration."""
    def wrapper(builder: PromptBuilder) -> PromptBuilder:
        _PROMPT_REGISTRY[integration] = builder
        return builder

    return wrapper


def get_prompt_builder(integration: str) -> PromptBuilder:
    """Resolve an environment builder, falling back to the generic base prompt."""
    return _PROMPT_REGISTRY.get(integration, _PROMPT_REGISTRY["*"])


@register_prompt_builder("*")
def _default_prompt(context: PromptContext) -> Prompt:
    return Prompt(
        system=(
            f"You are an embodied agent interacting with a prepared environment through the {context.integration_name!r} integration. "
            "Use the registered tools to complete the instruction and verify effects with current observations. "
            + (
                "Do not inspect simulator internals or service implementation sources. "
                "Use your own scripts to call the registered robot API."
                if "code" in context.act else
                "Do not inspect source code, scripts, processes, servers, or simulator internals. "
                "Do not use shell/exec or bypass the registered tools."
            )
        ),
        user=context.task,
    )
