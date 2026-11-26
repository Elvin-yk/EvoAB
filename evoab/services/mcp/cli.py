"""`evoab tools list|call` and `evoab stop` against the episode `evoab serve` is serving."""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path


def _state() -> dict:
    from evoab.session import SERVE_STATE
    try:
        return json.loads(SERVE_STATE.read_text())
    except (OSError, ValueError):
        return {}


def _url(args) -> str:
    url = getattr(args, "url", None) or _state().get("url")
    if not url:
        raise SystemExit("No episode is being served. Start one with: evoab serve --integration X --task Y")
    return url


def _value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def main(args) -> int:
    if args.command == "stop":
        state = _state()
        if not state.get("pid"):
            print("No episode is being served.")
            return 0
        try:
            os.kill(int(state["pid"]), signal.SIGINT)
        except ProcessLookupError:
            from evoab.session import SERVE_STATE
            SERVE_STATE.unlink(missing_ok=True)
            print("The served episode had already ended.")
            return 0
        print(f"Stopping the served {state.get('integration')} episode (run record: {state.get('run_dir')}).")
        return 0

    from evoab.execution.code_as_policy.client import Robot, ToolCallError
    images = Path(os.environ.get("EVOAB_STATE_DIR", Path.home() / ".cache" / "evoab")) / "tool-images"
    with Robot(_url(args), image_dir=images) as robot:
        if args.tools_action == "list":
            for spec in robot.schemas:
                properties = (spec.get("inputSchema") or {}).get("properties") or {}
                required = set((spec.get("inputSchema") or {}).get("required") or ())
                arguments = ", ".join(name + ("" if name in required else "?") for name in properties)
                summary = (spec.get("description") or "").strip().split("\n")[0]
                print(f"{spec['name']}({arguments})\n    {summary}")
            return 0
        arguments = json.loads(args.arguments_json)
        if not isinstance(arguments, dict):
            raise SystemExit("--json must be a JSON object")
        for item in args.arg:
            key, sep, text = item.partition("=")
            if not sep:
                raise SystemExit(f"--arg expects KEY=VALUE, got {item!r}")
            arguments[key] = _value(text)
        try:
            result = robot.call(args.name, **arguments)
            failed = False
        except ToolCallError as error:
            result, failed = error.result, True
        print(json.dumps(result.data, indent=2, ensure_ascii=False, default=str))
        for path in result.images:
            print(f"image: {path}")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit("use `evoab tools` or `evoab stop`")
