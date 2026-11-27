"""Run a recipe's explicit data flow through the episode's existing tool executor."""
from __future__ import annotations

import json
import time

from jsonschema import Draft202012Validator

from evoab.agents.contracts import AgentResult
from evoab.composition.interfaces import arguments, resolve
from evoab.execution.correlation import invocation_path
from evoab.tools.delegation.provider import tool_name


def checked_output(result, schema):
    if schema is None or not result.success:
        return None
    try:
        text = result.message.strip()
        lines = text.splitlines()
        if lines and lines[0].lower() in {"```json", "```"}:
            body = lines[1:-1] if lines[-1] == "```" else lines[1:]
            text = "\n".join(body)
        value = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError("API returned an invalid JSON document") from error
    problem = next(Draft202012Validator(schema).iter_errors(value), None)
    if problem:
        raise ValueError(f"API result violates {problem.validator} at {problem.json_path}")
    return value


def validate_output(result, schema):
    try:
        return checked_output(result, schema)
    except ValueError as error:
        result.success = False
        result.error = str(error)
        result.stop_reason = "invalid_output"
        return None


def run_flow(flow, delegates, payload, executor, directory, require_active):
    """Stop on the first failed step. No command or policy call is automatically replayed."""
    started = time.monotonic()
    state = {"input": payload}
    count = 0
    error = None
    last = None
    with (directory / "flow.jsonl").open("x", encoding="utf-8") as trace:
        for step in flow.steps:
            api = None
            try:
                require_active()
                if isinstance(step.call, str):
                    api = step.call
                else:
                    selected = resolve(step.call.source, state)
                    if not isinstance(selected, str) or selected not in step.call.choices:
                        raise ValueError(f"No declared route for {selected!r}")
                    api = step.call.choices[selected]
                values = arguments(step.args, state)
                name = tool_name(api) if api in delegates else api
                receipt = executor.call(name, values)
                if receipt.output is None and receipt.job_id:
                    receipt = executor.job_result(receipt.job_id, None)
                output = receipt.output
                if output is None or not output.success or output.error:
                    raise ValueError(output.error if output and output.error else f"{name} did not complete")
                last = output.extra.get("value", output.extra)
                state[step.id] = last
                count += 1
            except Exception as failure:
                error = f"{step.id}: {failure}"
            trace.write(json.dumps({"step": step.id, "api": api, "source": list(invocation_path.get()),
                                    "ok": error is None, "error": error}, ensure_ascii=False) + "\n")
            trace.flush()
            if error:
                break
    return AgentResult(success=error is None, error=error, message=json.dumps(last, ensure_ascii=False),
                       tool_calls=count, stop_reason="flow_error" if error else "completed",
                       elapsed_s=time.monotonic() - started)
