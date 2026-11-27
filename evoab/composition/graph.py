"""Project a recipe onto nodes and edges for display (Studio graph view, docs).

The graph has two layers. The assembly layer shows, for every role, its brain,
channel, context and capabilities, all wired to the body (the environment). The
loop layer shows delegation edges between roles and the episode sequence.
Run traces annotate nodes with call counts (``annotate_with_trace``).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from evoab.composition.recipe import Recipe


def recipe_graph(recipe: Recipe) -> dict[str, Any]:
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(id_: str, kind: str, label: str, **extra):
        nodes.append({"id": id_, "kind": kind, "label": label, **extra})
        return id_

    body = node("body", "body", "Environment", detail="Simulation benchmark or real embodiment")
    for name, role in recipe.roles.items():
        role_id = node(f"role:{name}", "role", name, entry=name == recipe.loop.entry,
                       detail=role.description)
        brain = role.brain
        label = "model" if role.channel == "action" else "agent"
        edges.append({"from": node(f"{name}:brain", "brain", label,
                                   detail=" · ".join(filter(None, [brain.model_preset or brain.model, brain.reasoning_effort]))),
                      "to": role_id, "kind": "drives"})
        channel_detail = ""
        if role.channel == "action" and role.action is not None:
            channel_detail = (f"{role.action.representation}, {role.action.horizon[0]}–{role.action.horizon[1]} rows, "
                              f"history {role.action.history[0]}:{role.action.history[1]}")
        channel = node(f"{name}:channel", "channel", role.channel, detail=channel_detail)
        edges.append({"from": role_id, "to": channel, "kind": "acts through"})
        context = "stateless_window" if role.channel == "action" else "identity"
        edges.append({"from": node(f"{name}:context", "context", context), "to": role_id, "kind": "context"})
        if role.channel == "action":
            edges.append({"from": channel, "to": body, "kind": "action chunks"})
        if role.job_tools:
            jobs = node(f"{name}:cap:jobs", "capability", "jobs", tools=role.job_tools,
                        detail=", ".join(role.job_tools))
            edges.append({"from": channel, "to": jobs, "kind": "calls"})
        for capability in role.capabilities:
            if capability.id == "delegate":
                for target in capability.options.get("roles", []):
                    edges.append({"from": channel, "to": f"role:{target}", "kind": "delegates"})
                continue
            detail = ", ".join(capability.tools) if capability.tools else ""
            if capability.options:
                detail = "; ".join(filter(None, [detail, json.dumps(capability.options, sort_keys=True)]))
            cap = node(f"{name}:cap:{capability.id}", "capability", capability.id, detail=detail,
                       tools=capability.tools, style=role.style)
            edges.append({"from": channel, "to": cap, "kind": "calls"})
            edges.append({"from": cap, "to": body, "kind": "operates"})
    episodes = recipe.loop.episodes
    if episodes.repeat > 1 or episodes.carry:
        loop = node("loop", "loop", f"{episodes.repeat} episodes",
                    detail=f"carry {', '.join(episodes.carry) or 'nothing'} (keep: {episodes.keep})")
        edges.append({"from": loop, "to": f"role:{recipe.loop.entry}", "kind": "repeats"})
    return {"recipe": recipe.id, "nodes": nodes, "edges": edges}


def annotate_with_trace(graph: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Count executed tool calls per capability from a run's trajectory (read-only)."""
    tools = Counter()
    role_calls = Counter()
    has_role_records = False
    trajectory = Path(run_dir) / "trajectory.jsonl"
    if trajectory.is_file():
        for line in trajectory.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "ToolExecution" and event.get("phase") == "started" and event.get("tool_name"):
                has_role_records |= "role" in event
                name = event["tool_name"]
                tools[name] += 1
                role_calls[event.get("role") or "", name] += 1

    capabilities = {item["id"]: item["tools"] if item.get("tools") is not None else
                    [name for name in tools if _owner(name) == item["label"]]
                    for item in graph["nodes"] if item["kind"] == "capability"}
    owners = Counter(name for names in capabilities.values() for name in names)
    for item in graph["nodes"]:
        if item["kind"] == "role" and not item.get("entry"):
            item["calls"] = tools.get(f"delegate_{item['label']}", 0)
        if item["kind"] == "capability":
            names = capabilities[item["id"]]
            role = item["id"].split(":", 1)[0]
            ambiguous = any(role_calls["", name] and owners[name] > 1 for name in names)
            if item["label"] == "jobs" and not has_role_records and not any(tools[name] for name in names):
                ambiguous = True
            item["calls"] = None if ambiguous else sum(role_calls[role, name] + role_calls["", name] for name in names)
    graph["tool_calls"] = tools
    return graph


_OWNERS = {"move": "gripper", "grasp": "gripper", "release": "gripper", "hold": "gripper",
           "view_env_state": "scene", "capture_scene": "scene", "locate": "scene",
           "list_skills": "skills", "read_skill": "skills", "write_skill": "skills",
           "finish": "episode", "actor_act": "hybrid"}


def _owner(tool: str) -> str:
    if tool.startswith("delegate_"):
        return "delegate"
    return _OWNERS.get(tool, "")
