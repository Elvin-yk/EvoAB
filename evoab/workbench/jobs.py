"""Background processes started by Studio (runs and benchmark setup).

Each job runs in its own process group with stdout/stderr in a log file. A
reaper thread collects exit codes so finished jobs never linger as zombies,
and the exit code is persisted next to the job record.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_children: dict[int, tuple[subprocess.Popen, Path]] = {}
_reaper_started = False


def _reap_forever() -> None:
    while True:
        time.sleep(1.0)
        with _lock:
            items = list(_children.items())
        for pid, (process, record_path) in items:
            code = process.poll()
            if code is None:
                continue
            with _lock:
                _children.pop(pid, None)
            # A finished launch may be purged before this polling pass writes
            # its exit code. Share recycling's lock and never recreate it.
            studio = next((p for p in record_path.parents if p.name == ".studio"), None)
            if studio is not None:
                from evoab.workbench.trash import locked
                with locked(studio.parent):
                    if record_path.exists():
                        update_record(record_path, exit_code=code, finished_at=time.time())
            else:
                update_record(record_path, exit_code=code, finished_at=time.time())


def _ensure_reaper() -> None:
    global _reaper_started
    with _lock:
        if _reaper_started:
            return
        _reaper_started = True
    threading.Thread(target=_reap_forever, name="evoab-studio-reaper", daemon=True).start()


def _start_ticks(pid: int) -> str | None:
    """Kernel start time of a process, to tell it apart from a later process reusing its PID."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # Field 22 (starttime); the command name in field 2 may contain spaces, so split after ')'.
    return stat.rsplit(")", 1)[-1].split()[19]


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def update_record(path: Path, **fields: Any) -> None:
    record = read_record(path) or {}
    record.update(fields)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def start(command: list[str], record_path: Path, env: dict[str, str] | None = None, **fields: Any) -> int:
    """Start ``command`` in a new process group and write its record."""
    _ensure_reaper()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = record_path.with_suffix(".log")
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    record = {"pid": process.pid, "pid_start": _start_ticks(process.pid), "started_at": time.time(),
              "exit_code": None, **fields}
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    with _lock:
        _children[process.pid] = (process, record_path)
    return process.pid


def is_running(record: dict[str, Any]) -> bool:
    """True while the job's process exists and has not reported an exit code."""
    if record.get("exit_code") is not None:
        return False
    try:
        pid = int(record["pid"])
        os.kill(pid, 0)
    except (OSError, KeyError, TypeError, ValueError):
        return False
    # After a restart the PID may belong to an unrelated process.
    expected = record.get("pid_start")
    return expected is None or _start_ticks(pid) == expected


def interrupt(record: dict[str, Any]) -> None:
    """Ask the job's process group to stop; SIGINT lets the CLI clean up services."""
    if not is_running(record):
        raise ProcessLookupError("job is no longer running")
    os.killpg(int(record["pid"]), signal.SIGINT)


def log_tail(record_path: Path, lines: int = 40) -> str:
    try:
        text = record_path.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
