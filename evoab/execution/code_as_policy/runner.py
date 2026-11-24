"""The code channel for agents without their own shell.

Codex and Claude Code run robot programs in their native terminal. Any other
agent gets one harness tool, ``run_program``, that writes the program into the
run's scratch directory and runs it with the session's interpreter and robot
connection. Programs use the same generated API and the same bridge, so tool
validation, scheduling, receipts and finish handling do not depend on the agent.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from evoab.execution import ExecutionResult, ToolExecutor
from evoab.tools.toolkit import ImageAttachment, ToolOutput

RUN_PROGRAM = "run_program"
MAX_OUTPUT_CHARS = 12_000
MAX_IMAGES = 6

SCHEMA = {
    "name": RUN_PROGRAM,
    "description": (
        "Run one Python program against the robot API and return its exit code, stdout and stderr. "
        "Images saved by robot calls during the program are attached to the result. Variables do not "
        "persist between programs; reconnect with Robot() in each program."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Complete Python source of the program."},
            "timeout_s": {"type": "number", "description": "Wall-clock limit in seconds (default 600).",
                          "minimum": 1, "maximum": 3600},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
}

PROMPT = (
    "Run each program by calling the run_program tool with its complete source; it returns the exit "
    "code, stdout, stderr and the images robot calls saved. Programs run in {scratch}, where files you "
    "write persist between programs. Where this API mentions your native terminal or image reader, use "
    "run_program and the images it attaches."
)


class ProgramTools(ToolExecutor):
    """``base`` plus ``run_program``; programs reach the robot through the code bridge."""

    def __init__(self, base: ToolExecutor, *, cwd: Path, env: dict[str, str], image_dir: Path):
        self._base, self._cwd, self._env, self._image_dir = base, Path(cwd), env, Path(image_dir)
        self._count = 0

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [*self._base.get_tool_schemas(), SCHEMA]

    def call(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        if name != RUN_PROGRAM:
            return self._base.call(name, arguments)
        return ExecutionResult.completed(self.run(str(arguments.get("code") or ""),
                                                  float(arguments.get("timeout_s") or 600)))

    def submit(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        return self.call(name, arguments) if name == RUN_PROGRAM else self._base.submit(name, arguments)

    def run(self, code: str, timeout_s: float) -> ToolOutput:
        if not code.strip():
            return ToolOutput(success=False, error="code is empty", extra={"no_execution": True})
        self._count += 1
        path = self._cwd / f"program_{self._count:03d}.py"
        path.write_text(code, encoding="utf-8")
        since = time.time()
        try:
            done = subprocess.run([sys.executable, path.name], cwd=self._cwd, env={**os.environ, **self._env},
                                  capture_output=True, text=True, timeout=timeout_s)
            exit_code, stdout, stderr = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired as error:
            exit_code = None
            stdout = _text(error.stdout)
            stderr = _text(error.stderr) + f"\nProgram stopped after {timeout_s:g} s; robot calls it made may have run."
        images = self._new_images(since)
        extra = {"program": str(path), "exit_code": exit_code, "stdout": _clip(stdout), "stderr": _clip(stderr)}
        if images:
            extra["images"] = [image.name for image in images]
        return ToolOutput(success=exit_code == 0, error=None if exit_code == 0 else "program failed",
                          extra=extra, images=images)

    def _new_images(self, since: float) -> list[ImageAttachment]:
        if not self._image_dir.is_dir():
            return []
        paths = sorted((p for p in self._image_dir.rglob("*") if p.is_file() and p.stat().st_mtime >= since
                        and p.suffix.lower() in {".png", ".jpg", ".jpeg"}), key=lambda p: p.stat().st_mtime)
        return [ImageAttachment(p.read_bytes(), "image/png" if p.suffix.lower() == ".png" else "image/jpeg",
                                str(p.relative_to(self._image_dir))) for p in paths[-MAX_IMAGES:]]

    def job_result(self, job_id: str, timeout: float | None = 0) -> ExecutionResult:
        return self._base.job_result(job_id, timeout)

    def cancel_job(self, job_id: str) -> bool:
        return self._base.cancel_job(job_id)

    @property
    def tool_execution_seq(self) -> int:
        return self._base.tool_execution_seq

    def add_queue_listener(self, listener) -> None:
        self._base.add_queue_listener(listener)

    def bind_event_bus(self, bus) -> None:
        self._base.bind_event_bus(bus)

    def close(self) -> None:
        """The session owns the base executor."""

    def __getattr__(self, name: str):
        return getattr(self._base, name)


def _text(value) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n… {len(text) - MAX_OUTPUT_CHARS} characters omitted …\n{text[-half:]}"
