"""Replay data for Studio, served by the run monitor's routes under ``/replay``.

The standard monitor discovers run directories once. Studio launches new runs
continuously, so this collection rediscovers the runs root (throttled) before
lookups and reuses the monitor's readers and routes unchanged. Studio draws
the replay itself from ``/replay/api/runs/<id>`` and its media URLs.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from evoab.apps.web.reader import RunReader
from evoab.apps.web.server import Collection, discover

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


def live_progress(collection: RescanningCollection, monitor_run_id: str) -> dict:
    """Step budget, call count and latest frame of a run for the running page."""
    try:
        state = collection.state(monitor_run_id)
    except Exception:  # noqa: BLE001 - the run may not have written records yet
        return {}
    frame = state["frames"][-1] if state.get("frames") else None
    return {
        "steps": state.get("steps"),
        "max_steps": state.get("max_steps"),
        "total_calls": state.get("total_calls"),
        "phase": state.get("status"),
        "frame_url": f"/replay{frame['url']}?width=320" if frame else None,
    }
