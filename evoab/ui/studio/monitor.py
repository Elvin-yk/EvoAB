"""Replay monitor that picks up runs created after it started.

The standard monitor discovers run directories once. Studio launches new runs
continuously, so this collection rediscovers the runs root (throttled) before
lookups and reuses the monitor's readers, routes and static page unchanged.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import uvicorn

from evoab.ui.web.reader import RunReader
from evoab.ui.web.server import Collection, create_monitor_app, discover

RESCAN_INTERVAL_S = 3.0


def monitor_id(run_dir: Path) -> str:
    """The id the monitor uses for a run directory (its ``#run/<id>`` route)."""
    return hashlib.sha256(str(Path(run_dir).resolve()).encode()).hexdigest()[:16]


class RescanningCollection(Collection):
    def __init__(self, runs_root: Path):
        self.runs_root = Path(runs_root)
        super().__init__([], archive_scopes=[self.runs_root])
        self._lock = threading.Lock()
        self._scanned_at = 0.0

    def rescan(self, force: bool = False) -> None:
        with self._lock:
            if not force and time.time() - self._scanned_at < RESCAN_INTERVAL_S:
                return
            self._scanned_at = time.time()
            for root in discover([self.runs_root]):
                run_id = monitor_id(root)
                if run_id not in self.readers:
                    self.readers[run_id] = RunReader(root, [self.runs_root])

    def reader(self, run_id):
        if run_id not in self.readers:
            self.rescan(force=True)
        return super().reader(run_id)

    def overview(self):
        self.rescan()
        return super().overview()


def start_monitor(runs_root: Path, host: str, port: int) -> None:
    """Serve the rescanning monitor in a daemon thread."""
    app = create_monitor_app(RescanningCollection(runs_root))
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, name="evoab-studio-monitor", daemon=True).start()
