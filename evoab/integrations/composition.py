"""Tool modules an integration offers, and their assembly for one recipe role.

An integration publishes a catalog of :class:`ToolModule` objects. A recipe role
selects modules by name (its capabilities), optionally narrowing a module to some
of its tools and choosing a presentation style. Execution modes remain owned by
the executor.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from evoab.execution.scheduler import ToolExecutionPolicy
from evoab.tools.toolkit import ToolDef, ToolProvider

Style = Literal["full", "compact"]


class NoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class ModuleSelection:
    """What a role asked of one module: a tool subset, typed options and a style."""

    tools: tuple[str, ...] | None = None
    options: BaseModel = field(default_factory=NoOptions)
    style: Style = "full"


@dataclass(frozen=True)
class ToolModule:
    """One provider factory with its policies, selectable tools and typed options.

    ``create(session, selection)`` must honour ``selection.tools`` when the module
    declares ``tools``; modules without ``tools`` are selected as a whole.
    """

    name: str
    create: Callable[[Any, ModuleSelection], ToolProvider]
    policies: Mapping[str, ToolExecutionPolicy] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    options: type[BaseModel] = NoOptions
    label: str = ""
    description: str = ""
    runtime_prompts: tuple[str, ...] = ()
    model_view: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    prompt_contributions: tuple | None = None
    # Replaces the provider's own contributions under the compact style.
    compact_prompt_contributions: tuple | None = None

    def select(self, tools=None, options: Mapping[str, Any] | None = None, style: Style = "full") -> "SelectedModule":
        if tools is not None:
            tools = tuple(tools)
            if not self.tools:
                raise ValueError(f"capability {self.name!r} cannot be narrowed to individual tools")
            if unknown := sorted(set(tools) - set(self.tools)):
                raise ValueError(f"capability {self.name!r} has no tools {unknown}; choose from {list(self.tools)}")
        try:
            parsed = self.options.model_validate(dict(options or {}))
        except ValueError as error:
            raise ValueError(f"capability {self.name!r} options: {error}") from error
        return SelectedModule(self, ModuleSelection(tools=tools, options=parsed, style=style))


@dataclass(frozen=True)
class SelectedModule:
    module: ToolModule
    selection: ModuleSelection

    @property
    def name(self) -> str:
        return self.module.name

    @property
    def policies(self) -> dict[str, ToolExecutionPolicy]:
        names = self.selection.tools
        return {name: policy for name, policy in self.module.policies.items() if names is None or name in names}

    @property
    def model_view(self):
        if self.module.model_view is not None:
            return self.module.model_view
        if self.selection.style == "compact":
            from evoab.tools.clean import result_view
            return result_view
        return None


@dataclass(frozen=True)
class ToolAssembly:
    """The selected modules, instantiated once against one prepared episode."""

    selected: tuple[SelectedModule, ...]
    providers: tuple[ToolProvider, ...]

    @property
    def modules(self) -> tuple[ToolModule, ...]:
        return tuple(item.module for item in self.selected)

    @property
    def tools(self) -> list[ToolDef]:
        return [replace(tool, model_view=item.model_view) if item.model_view is not None else tool
                for item, provider in zip(self.selected, self.providers)
                for tool in provider.get_tools()]

    @property
    def prompt_contributions(self):
        return self.contributions_for(None)

    def contributions_for(self, names):
        """Prompt contributions of the named modules (all modules when ``names`` is None)."""
        result = []
        for item, provider in zip(self.selected, self.providers):
            module = item.module
            if names is not None and module.name not in names:
                continue
            if item.selection.style == "compact" and module.compact_prompt_contributions is not None:
                result.extend(module.compact_prompt_contributions)
            elif module.prompt_contributions is not None:
                result.extend(module.prompt_contributions)
            else:
                result.extend(provider.prompt_contributions)
        return tuple(result)

    @property
    def policies(self) -> dict[str, ToolExecutionPolicy]:
        return {name: policy for item in self.selected for name, policy in item.policies.items()}

    @property
    def runtime_prompts(self) -> tuple[str, ...]:
        return tuple(path for module in self.modules for path in module.runtime_prompts)


def validate_modules(modules) -> None:
    names, policies = set(), set()
    for module in modules:
        if module.name in names:
            raise ValueError(f"Duplicate tool module: {module.name}")
        names.add(module.name)
        if overlap := policies.intersection(module.policies):
            raise ValueError(f"Duplicate tool policies: {sorted(overlap)}")
        policies.update(module.policies)


def assemble_tools(session: Any, selected: tuple[SelectedModule, ...]) -> ToolAssembly:
    validate_modules([item.module for item in selected])
    return ToolAssembly(selected, tuple(item.module.create(session, item.selection) for item in selected))


def module_catalog(spec: Any) -> dict[str, ToolModule]:
    """Every module an integration can assemble, by name."""
    validate_modules(spec.tool_modules)
    return {module.name: module for module in spec.tool_modules}
