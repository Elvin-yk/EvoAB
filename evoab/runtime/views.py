"""Restricted views of one shared executor.

Several roles of one recipe share the session executor, so resource exclusion,
job records and receipts stay in one place. Each role sees only its own tools
through a :class:`ToolView`; scheduling is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from evoab.runtime import ExecutionResult, ToolExecutor
from evoab.runtime.correlation import execution_role
from evoab.tools.toolkit import ToolOutput


def waits_for_output(job_tools: list[str] | None) -> bool:
    """A role needs a way to read the output before a receipt alone is useful."""
    return job_tools is not None and not {"get_tool_result", "wait_for_tool", "wait_for_tools"}.intersection(job_tools)


class ToolView(ToolExecutor):
    """Expose a subset of an executor's tools; every admitted call runs on the shared executor."""

    def __init__(self, executor: ToolExecutor, names: Iterable[str], *, registered: Iterable[str] | None = None,
                 denial: str | None = None, job_tools: list[str] | None = None, role: str = ""):
        """Inherit job controls unless the role explicitly selects its own."""
        self._executor = executor
        allowed = set(names)
        if job_tools is not None:
            allowed.update(job_tools)
        elif registered is not None:
            allowed |= {spec["name"] for spec in executor.get_tool_schemas()} - set(registered)
        self._names = frozenset(allowed)
        self._denial = denial
        self._wait_for_result = waits_for_output(job_tools)
        self._role = role

    @property
    def names(self) -> frozenset[str]:
        return self._names

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [spec for spec in self._executor.get_tool_schemas() if spec["name"] in self._names]

    def _admit(self, name: str) -> ExecutionResult | None:
        if name in self._names:
            return None
        message = self._denial or f"Tool {name!r} is not available to this role"
        return ExecutionResult.completed(ToolOutput(success=False, error=message, extra={"no_execution": True}))

    def call(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        with execution_role(self._role):
            result = self._admit(name) or self._executor.call(name, arguments)
        if self._wait_for_result and result.job_id and result.output is None:
            return self._executor.job_result(result.job_id, timeout=None)
        return result

    def submit(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        with execution_role(self._role):
            return self._admit(name) or self._executor.submit(name, arguments)

    def job_result(self, job_id: str, timeout: float | None = 0) -> ExecutionResult:
        return self._executor.job_result(job_id, timeout)

    def cancel_job(self, job_id: str) -> bool:
        return self._executor.cancel_job(job_id)

    @property
    def tool_execution_seq(self) -> int:
        return self._executor.tool_execution_seq

    def add_queue_listener(self, listener) -> None:
        self._executor.add_queue_listener(listener)

    def bind_event_bus(self, bus) -> None:
        self._executor.bind_event_bus(bus)

    def close(self) -> None:
        """The session owns and closes the shared executor."""

    def __getattr__(self, name: str):
        return getattr(self._executor, name)
