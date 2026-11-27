"""Run a small image and tool round trip without an environment."""

from __future__ import annotations

import base64
import io
import json
import secrets
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


_TIMEOUT_S = 90
_STATUSES = {"passed", "tool_exchange_failed", "provider_error", "timeout"}
_COLORS = ("red", "green", "blue")
_INSTRUCTIONS = "Use only the supplied verification tools. Do not invent a tool result."
_TASK = (
    "Call probe_observe once. Read the nonce from its tool result and identify the colors of the four tiles "
    "from left to right. Call probe_finish with that nonce and the four colors in order, then stop."
)
_TOOLS = (
    {"name": "probe_observe", "description": "Return a nonce and an image with four colored tiles.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "probe_finish", "description": "Submit the nonce and the tile colors from left to right.",
     "parameters": {"type": "object", "properties": {"nonce": {"type": "string"},
                     "colors": {"type": "array", "items": {"type": "string", "enum": _COLORS}}},
                     "required": ["nonce", "colors"], "additionalProperties": False}},
)


def run_model_probe(command: list[str], settings: dict, route: dict) -> dict:
    """Bound the probe lifetime and keep credentials and raw errors off the Studio API."""
    from PIL import Image

    colors = [secrets.choice(_COLORS) for _ in range(4)]
    canvas = Image.new("RGB", (256, 64), "white")
    for index, color in enumerate(colors):
        left = 64 * index + 4
        canvas.paste(color, (left, 4, left + 56, 60))
    image = io.BytesIO()
    canvas.save(image, format="PNG")
    fixture = {"instructions": _INSTRUCTIONS, "task": _TASK, "tools": _TOOLS,
               "image": base64.b64encode(image.getvalue()).decode("ascii"),
               "answer": {"nonce": secrets.token_hex(4), "colors": colors}}
    status = "runtime_unavailable"
    report = {}
    try:
        with TemporaryDirectory(prefix="evoab-model-probe-") as directory:
            process = subprocess.run(command, input=json.dumps({**settings, "fixture": fixture,
                                                               "directory": directory}).encode(),
                                     capture_output=True, timeout=_TIMEOUT_S, check=True)
            report = json.loads(process.stdout)
            if isinstance(report, dict) and isinstance(report.get("status"), str) and report["status"] in _STATUSES:
                status = report["status"]
    except subprocess.TimeoutExpired:
        status = "timeout"
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    http_status = report.get("http_status") if isinstance(report, dict) else None
    return {"status": status, "route": route,
            **({"http_status": http_status} if type(http_status) is int and 400 <= http_status <= 599 else {})}


def probe_native_preset(name: str, effort: str) -> dict:
    from evoab.provider import catalog
    from evoab.provider.contracts import PresetCompatibilityError

    config = catalog.load_config()
    from nanobot.providers.routing import resolve_automatic_model_api, resolve_provider_route

    preset = (config.model_presets or {}).get(name)
    if preset is None:
        raise PresetCompatibilityError("The model preset does not exist.")
    try:
        route = resolve_provider_route(config, preset=preset)
        api = route.model_api.preferred_api if route.model_api else resolve_automatic_model_api(config, preset=preset)[1]
        model = route.spec.request_model_name(route.model) if route.spec else route.model
    except ValueError:
        raise PresetCompatibilityError("The preset's model route could not be resolved.") from None
    route_info = {"model": model, "api": api, "adapter": route.backend}
    return run_model_probe([sys.executable, "-m", "evoab.agents.model_probe"],
                           {"preset": name, "effort": effort}, route_info)


def _probe_native(settings: dict) -> str:
    from evoab.agents.native.agent import NativeAgent
    from evoab.context.prompt import Prompt
    from evoab.execution import SerialToolExecutor
    from evoab.tools.toolkit import ImageAttachment, ToolDef, Toolkit, ToolOutput

    fixture = settings["fixture"]
    expected = fixture["answer"]
    calls = []

    def observe():
        calls.append("observe")
        return ToolOutput(extra={"nonce": expected["nonce"]},
                          images=[ImageAttachment(base64.b64decode(fixture["image"]))])

    def finish(nonce, colors):
        passed = calls == ["observe"] and {"nonce": nonce, "colors": colors} == expected
        calls.append("passed" if passed else "invalid")
        return ToolOutput(success=passed)

    toolkit = Toolkit()
    toolkit.register_tools([
        ToolDef(tool["name"], {"name": tool["name"], "description": tool["description"],
                              "input_schema": tool["parameters"]}, handler, readonly=True)
        for tool, handler in zip(fixture["tools"], (observe, finish), strict=True)
    ])
    executor = SerialToolExecutor(toolkit)
    agent = NativeAgent(model=settings["preset"], reasoning_effort=settings["effort"])
    try:
        result = agent.run(prompt=Prompt(system=fixture["instructions"], user=fixture["task"]), task=fixture["task"],
                           toolkit=toolkit, executor=executor, max_turns=3, session_dir=Path(settings["directory"]))
    finally:
        agent.close()
        executor.close()
        toolkit.close()
    if result.error:
        return "provider_error"
    return "passed" if calls == ["observe", "passed"] else "tool_exchange_failed"


if __name__ == "__main__":
    print(json.dumps({"status": _probe_native(json.load(sys.stdin))}))
