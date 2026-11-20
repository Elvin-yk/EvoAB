"""Workbench: the operations behind Studio and the ``evoab`` resource commands.

Every Studio page and every ``evoab runs|providers|benchmarks|usage|settings``
command calls the same :class:`~evoab.workbench.service.Workbench` methods, so
anything the web client can do is also available from the command line (and,
through ``--json``, to scripts and other agents).
"""

from evoab.workbench.service import Conflict, Invalid, NotFound, Workbench, WorkbenchError, default_runs_root

__all__ = ["Conflict", "Invalid", "NotFound", "Workbench", "WorkbenchError", "default_runs_root"]
