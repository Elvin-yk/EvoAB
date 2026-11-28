"""EvoAB robot tools for the agent runtime, and a hook that records every model round."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from nanobot.agent.hook import AgentHook
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.utils.helpers import build_image_content_blocks

from evoab.runtime import ToolExecutor


class ImageInbox:
    """Images returned by tools, delivered as one user message before the next model request.

    Chat Completions tool messages are text-only; gateways flatten image parts in
    them to base64 text, which the model cannot see and which costs ~100k tokens.
    """

    def __init__(self):
        self.pending: list[dict[str, Any]] = []

    def add(self, call: str, images) -> str:
        for image in images:
            self.pending += build_image_content_blocks(image.data, image.mime_type, image.name or "image",
                                                       f"(from {call}: {image.name or 'camera'})")
        return f"{len(images)} image(s) from this call are attached in the next user message."

    async def drain(self):
        if not self.pending:
            return None
        blocks, self.pending = self.pending, []
        return [{"role": "user", "content": [{"type": "text", "text": "Images returned by the previous tool calls:"}, *blocks]}]


class RobotTool(Tool):
    """One EvoAB tool; calls go through the session executor (scheduling, resources, events)."""

    def __init__(self, spec: dict[str, Any], executor: ToolExecutor, inbox: ImageInbox):
        self._spec, self._executor, self._inbox = spec, executor, inbox

    @property
    def name(self) -> str:
        return self._spec["name"]

    @property
    def description(self) -> str:
        return self._spec.get("description") or ""

    @property
    def parameters(self) -> dict[str, Any]:
        return self._spec.get("input_schema") or self._spec.get("parameters") or {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        result = await asyncio.to_thread(self._executor.call, self.name, kwargs)
        output = result.output
        if output is None:
            return json.dumps(result.to_dict(), ensure_ascii=False)
        text = output.model_json()
        if output.images:
            text += "\n" + self._inbox.add(self.name, output.images)
        return text if output.success and not output.error else ToolResult.error(text)


def robot_tools(executor, inbox: ImageInbox) -> list[RobotTool]:
    return [RobotTool(spec, executor, inbox) for spec in executor.get_tool_schemas()]


def _scrub(content: Any) -> Any:
    if isinstance(content, list):
        return [{"type": "image", "omitted": True} if isinstance(b, dict) and b.get("type") in ("image_url", "image")
                else b for b in content]
    return content


class RecordingHook(AgentHook):
    """Append one record per model round to ``rounds.jsonl``."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "rounds.jsonl"
        self.started = 0.0
        self.seen = 0

    async def before_iteration(self, context) -> None:
        self.started = time.time()

    async def after_iteration(self, context) -> None:
        response = context.response
        usage = context.usage
        new = context.messages[self.seen:]
        self.seen = len(context.messages)
        record = {
            "iteration": context.iteration, "started_at": self.started, "finished_at": time.time(),
            "input": [{"role": m.get("role"), "name": m.get("name"), "content": _scrub(m.get("content"))} for m in new],
            "content": getattr(response, "content", None), "reasoning": getattr(response, "reasoning_content", None),
            "finish_reason": getattr(response, "finish_reason", None),
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in context.tool_calls],
            "tool_events": context.tool_events, "stop_reason": context.stop_reason, "error": context.error,
            "usage": None if usage is None else {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                                 "total_tokens": usage.total_tokens,
                                                 "cached_input_tokens": usage.cache_read_tokens},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
