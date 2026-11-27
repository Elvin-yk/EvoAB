"""Review transport, independent of robot geometry and physical execution."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from jsonschema.exceptions import ValidationError

from .decisions import decision_schema


class ReviewerError(RuntimeError):
    """Review failed before any proposed motion was authorized."""


class ChunkReviewer:
    """Review action proposals with a model preset, without executing or retrying physical actions."""

    def __init__(self, *, enabled, model, prompt_path, validator, tracer=None):
        self.enabled, self.model, self.validator = enabled, model, validator
        self.prompt = Path(prompt_path).read_text() if enabled else ""
        self._client = None
        if enabled:
            if not model:
                raise ValueError("An enabled reviewer needs a model preset (--vla-review-model or the run's model)")
            from evoab.provider.catalog import ModelClient
            self._client = ModelClient(model, reasoning_effort="medium")

    def close(self):
        if self._client is not None:
            self._client.close()

    def decide(self, *, context, images, actions_per_chunk, record=None):
        if not self.enabled:
            return {"request_id": context["request_id"], "mode": "student", "steps": actions_per_chunk,
                    "assessment": None, "continuation": "continue", "reason": "Action model without review.", "controller_disabled": True}
        content = [{"type": "text", "text": json.dumps(context, ensure_ascii=False, allow_nan=False)}]
        for name, path in images.items():
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.extend([{"type": "text", "text": "Observation: " + name},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}])
        tools = [{"type": "function", "function": {
            "name": "submit_review", "description": "Submit exactly one review decision for the current proposal.",
            "parameters": decision_schema(context, actions_per_chunk)}}]
        messages = [{"role": "system", "content": self.prompt}, {"role": "user", "content": content}]
        started = time.monotonic()
        for attempt in range(2):
            attempt_started = time.monotonic()
            evidence = {"request_id": context["request_id"], "state_version": context.get("state_version"),
                        "model": self.model, "reasoning_effort": "medium",
                        "started_wall_ns": time.time_ns(), "repair": attempt > 0}
            try:
                completion = self._client.complete(messages, tools=tools, tool_choice="required")
                if completion.error:
                    raise ReviewerError(completion.error)
                evidence.update(finish_reason=completion.finish_reason, usage=completion.usage,
                                tool_calls=completion.tool_calls)
                decision = self.validator(review_arguments(completion.tool_calls), context, actions_per_chunk)
            except (ValueError, TypeError, ValidationError) as error:
                rejected = f"{type(error).__name__}: {error}"
                evidence["error"] = {"stage": "decision", "message": rejected}
            except Exception as error:
                evidence["error"] = {"stage": "transport", "message": f"{type(error).__name__}: {error}"}
                raise ReviewerError(evidence["error"]["message"]) from error
            else:
                decision.update(model=self.model, reasoning_effort="medium", review_attempts=attempt + 1,
                                elapsed_s=round(time.monotonic() - started, 3), usage=completion.usage)
                evidence["decision"] = decision
                return decision
            finally:
                evidence["elapsed_s"] = round(time.monotonic() - attempt_started, 3)
                if record is not None:
                    record(attempt, evidence)
            if attempt == 0:
                # Reassess the same immutable proposal; never predict or execute on repair.
                messages.append({"role": "user", "content":
                    "The previous review was rejected: " + rejected +
                    ". Call submit_review exactly once using its schema for the SAME request_id and proposal. "
                    "episode_started describes the whole native episode, not the local phase."})
        raise ReviewerError("Decision rejected after two review attempts: " + rejected)


def review_arguments(tool_calls):
    """Accept one explicit decision call; reasoning and prose cannot authorize motion."""
    if not isinstance(tool_calls, list) or len(tool_calls) != 1 or tool_calls[0].get("name") != "submit_review":
        raise ValueError("Reviewer must return exactly one submit_review function call")
    arguments = tool_calls[0].get("arguments")
    decision = json.loads(arguments) if isinstance(arguments, str) else arguments
    if not isinstance(decision, dict):
        raise TypeError("Reviewer function arguments must contain one JSON object")
    return decision
