"""The component catalog: every building block a recipe can name.

Components are grouped by the slot they fill in a role. Tool-module components
come from each integration's catalog (``IntegrationSpec.tool_modules``); the
others are owned by the harness and listed here. The catalog is what Studio's
graph view and ``evoab methods components`` show, and what the compiler checks
recipes against. Register additional harness components with
:func:`register_component`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Slot = Literal["brain", "channel", "capability", "action", "context", "loop"]


@dataclass(frozen=True)
class Component:
    id: str
    slot: Slot
    label: str
    description: str
    # Free-form facts shown to users: required environment capabilities, compatible channels, ...
    requires: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    options: dict = field(default_factory=dict)
    owner: Literal["harness", "integration", "agent"] = "harness"

    def to_dict(self) -> dict:
        return asdict(self)


_COMPONENTS: dict[tuple[str, str], Component] = {}


def register_component(component: Component) -> Component:
    key = (component.slot, component.id)
    if key in _COMPONENTS and _COMPONENTS[key] != component:
        raise ValueError(f"component {component.slot}/{component.id} is already registered")
    _COMPONENTS[key] = component
    return component


for _component in (
    Component("tools", "channel", "Tool calls",
              "The agent calls robot tools natively and reads each result before deciding.",
              channels=("tools",)),
    Component("code", "channel", "Code",
              "The agent writes ordinary Python programs against a generated robot API, runs them, "
              "inspects receipts and images, and writes the next program.", channels=("code",)),
    Component("action", "channel", "Direct action",
              "Each inference is one stateless request that must return exactly one action chunk; "
              "the environment controller executes it row by row.", channels=("action",)),
    Component("abs_eef", "action", "Absolute EEF",
              "Absolute grasp-site poses [x, y, z, qw, qx, qy, qz, gripper], solved by the environment's IK.",
              requires=("pose control",), channels=("action",)),
    Component("delta_eef", "action", "Delta EEF",
              "Stepwise world-frame grasp-site deltas [dx, dy, dz, drx, dry, drz, gripper].",
              requires=("pose control",), channels=("action",)),
    Component("joint_abs", "action", "Absolute joints",
              "Absolute joint positions per arm plus gripper closure; bypasses IK.",
              requires=("joint control",), channels=("action",)),
    Component("identity", "context", "Full history",
              "Keep the complete transcript, text and images.", channels=("tools", "code")),
    Component("sliding_window", "context", "Sliding window",
              "Trim old blocks to a target input-token budget.", channels=("tools", "code")),
    Component("observation_window", "context", "Observation window",
              "Keep only the most recent tool observation groups.", channels=("tools", "code")),
    Component("stateless_window", "context", "Stateless frames",
              "No transcript; each request carries a sampled window of recent frames.", channels=("action",)),
    Component("delegate", "capability", "Delegate to role",
              "Expose other roles as tools: the caller hands a sub-goal to a role, which runs with its own "
              "brain, channel and capabilities against the same episode and returns a report.",
              channels=("tools", "code"), options={"roles": "list of role names"}),
    Component("observation", "capability", "Observation sidecar",
              "Integration observation profile: snapshots, freshness and observer insights.",
              channels=("tools",), options={"profile": "integration name"}),
    Component("single", "loop", "Single episode", "The entry role runs one episode."),
    Component("episodes", "loop", "Episode sequence",
              "Repeat episodes and carry skills or programs forward between them."),
):
    register_component(_component)

#: Harness capabilities that are not integration tool modules.
HARNESS_CAPABILITIES = ("delegate", "observation")


def components(slot: Slot | None = None, *, integration: str | None = None) -> list[Component]:
    """Harness components plus, when an integration is given, its tool modules and brains."""
    result = [c for c in _COMPONENTS.values() if slot in (None, c.slot)]
    if slot in (None, "brain"):
        result.extend(_brains())
    if integration is not None and slot in (None, "capability"):
        result.extend(integration_capabilities(integration))
    return result


def integration_capabilities(integration: str) -> list[Component]:
    from evoab.integrations import load_integration
    spec = load_integration(integration).get_integration_spec()
    return [module_component(module) for module in spec.tool_modules]


def module_component(module) -> Component:
    options = {name: str(field.annotation) for name, field in module.options.model_fields.items()}
    return Component(module.name, "capability", module.label or module.name, module.description,
                     channels=("tools", "code"), tools=module.tools, options=options, owner="integration")


def _brains() -> list[Component]:
    from evoab.agents.registry import _BUILTIN_AGENTS, _AGENT_REGISTRY
    names = sorted(set(_BUILTIN_AGENTS) | set(_AGENT_REGISTRY))
    labels = {
        "native": ("EvoAB native agent", "Built-in runtime with model presets."),
        "codex": ("Codex", "OpenAI Codex SDK through the EvoAB MCP adapter; runs programs in its own terminal."),
        "claude_code": ("Claude Code", "Claude Agent SDK with in-process tools; runs programs in its own terminal."),
        "opencode": ("OpenCode", "OpenCode adapter."),
        "gemini_cli": ("Gemini CLI", "Gemini CLI adapter."),
        "antigravity": ("Antigravity", "Antigravity SDK adapter."),
        "rua": ("RUA", "External evaluator running the original uni-robot-agent package (rua_upstream)."),
    }
    return [Component(name, "brain", *labels.get(name, (name, "Registered agent adapter.")),
                      channels=agent_channels(name), owner="agent")
            for name in names]


ALL_CHANNELS = ("tools", "code", "action")


def agent_channels(agent: str) -> tuple[str, ...]:
    """Channels an agent can run: all of them, except external evaluators (tools only).

    ``tools`` is the agent's own loop; ``code`` runs programs in the agent's terminal
    (``runs_programs``) or through the harness ``run_program`` tool; ``action`` is one
    stateless request to the model per chunk and needs no agent loop.
    """
    return ("tools",) if is_evaluator(agent) else ALL_CHANNELS


def is_evaluator(agent: str) -> bool:
    """An external evaluator owns its whole episode loop and runs only the methods that pin it."""
    from evoab.agents.registry import get_agent_backend
    return hasattr(get_agent_backend(agent), "evaluator_plan")


def catalog(integration: str | None = None) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for component in components(integration=integration):
        grouped.setdefault(component.slot, []).append(component.to_dict())
    return grouped
