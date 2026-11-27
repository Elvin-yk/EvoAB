"""Adapt the existing executor to blocking program calls without owning it."""
from __future__ import annotations

from threading import Event, Lock

from evoab.execution import ExecutionResult, ToolExecutor
from evoab.execution.correlation import invocation_path
from evoab.services.mcp.http import HttpMcpServer
from evoab.tools.client import ToolJob


class CodePolicyBridge(ToolExecutor):
    """Serialize program calls and retain only an executed finish receipt."""

    def __init__(self, executor: ToolExecutor, *, event_bus=None):
        self._invocation_path = invocation_path.get()
        self._executor = executor
        self._closed = Event()
        self._lock = Lock()
        self._server = HttpMcpServer(self, event_bus=event_bus)
        self.finish_request: dict | None = None

    def start(self) -> str:
        return self._server.start()

    def revoke(self) -> None:
        # Never wait behind a physical action to revoke subsequent admissions.
        self._closed.set()

    def _require_active(self) -> None:
        if self._closed.is_set():
            raise RuntimeError("Code-as-policy session no longer accepts calls")

    def get_tool_schemas(self):
        return self._executor.get_tool_schemas()

    @property
    def tool_execution_seq(self):
        return self._executor.tool_execution_seq

    def call(self, name, arguments):
        with self._lock:
            self._require_active()
            token = invocation_path.set(self._invocation_path)
            try:
                # Program calls wait for a result. Foreground delegation allows nested
                # programs to call another model without occupying a pool worker.
                receipt = self._executor.call(name, arguments)
                output = receipt.output if receipt.output is not None else ToolJob(self._executor, receipt).result()
            except Exception:
                # An exception after submission does not establish zero execution.
                self.revoke()
                raise
            finally:
                invocation_path.reset(token)
            if (output.extra.get("execution_uncertain")
                    or output.extra.get("execution", {}).get("execution_uncertain")):
                self.revoke()
            if name == "finish" and output.success and not output.error and output.extra.get("submitted"):
                self.finish_request = dict(arguments)
                self.revoke()
            return ExecutionResult.completed(output)

    def close(self) -> None:
        self.revoke()
        self._server.stop()
        # Session owns executor draining and environment interruption/cleanup.
