"""Persistent run recycling without changing artifact paths.

An atomic index hides recycled runs while retaining their original files. This
preserves absolute artifact references and permits restoration without replay.
Only explicit permanent deletion removes files, never external artifact targets.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from uuid import uuid4

from evoab.workbench import jobs


class TrashError(ValueError):
    """A rejected recycling operation."""


def _studio(root: Path) -> Path:
    path = root / '.studio'
    if path.is_symlink():
        raise TrashError('The Studio directory must not be a symlink')
    return path


def entries(root: Path) -> list[dict]:
    """Read one complete index; corruption must never silently unhide records."""
    path = _studio(root) / 'trash.json'
    if path.is_symlink():
        raise TrashError('The recycle index must not be a symlink')
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise TrashError('Invalid recycle index')
    return data


def hidden_ids(root: Path) -> tuple[set[str], set[str]]:
    items = entries(root)
    return ({e['run']['id'] for e in items if e['run'].get('id')},
            {e['run']['launch_id'] for e in items if e['run'].get('launch_id')})


def hidden_research_ids(root: Path) -> set[str]:
    return {e['run']['research_id'] for e in entries(root)
            if e.get('category') == 'research'}


@contextmanager
def locked(root: Path):
    directory = _studio(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'trash.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _save(root: Path, items: list[dict]) -> None:
    directory = _studio(root)
    fd, name = tempfile.mkstemp(prefix='.trash-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(items, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, directory / 'trash.json')
    finally:
        Path(name).unlink(missing_ok=True)


def _name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r'[\w-][\w.\-]*', name) or name in {'.', '..'}:
        raise TrashError('Invalid run identity')
    return name


def _paths(root: Path, run: dict, category: str = 'run') -> list[Path]:
    paths = []
    if category == 'research':
        ref = run.get('research_id', '')
        if not re.fullmatch(r'research-[a-f0-9]{12}', ref):
            raise TrashError('Invalid research identity')
        paths.append(_studio(root) / 'research' / ref)
    elif run.get('id'):
        paths.append(root / _name(run['id']))
    if run.get('launch_id'):
        name = _name(run['launch_id'])
        paths.extend(_studio(root) / (name + suffix) for suffix in ('.json', '.log'))
    for path in paths:
        if (path.is_symlink() or path.resolve().parent != path.parent.resolve()
                or (category == 'research' and path.parent.is_symlink())):
            raise TrashError('Run files must not be symlinks or leave the runs directory')
    return paths


def _identity(root: Path, entry: dict, *, require_files: bool = False) -> None:
    category = entry.get('category', 'run')
    paths = _paths(root, entry['run'], category)
    for path in paths:
        expected = entry['identities'].get(str(path.relative_to(root)))
        if not path.exists():
            if require_files and expected:
                raise TrashError('Original run files are missing; the task cannot be restored')
            continue
        stat = path.stat()
        if expected is None or expected != [stat.st_dev, stat.st_ino]:
            raise TrashError('Original run files were replaced; refusing to overwrite or delete them')


def recycle(root: Path, runs: list[dict], *, category: str = 'run') -> list[dict]:
    """Validate the whole selection before publishing one atomic soft deletion."""
    items = entries(root)
    hidden_runs, hidden_launches = hidden_ids(root)
    hidden_research = hidden_research_ids(root)
    records = []
    for run in runs:
        if (category == 'research' and run.get('research_id') in hidden_research
                or run.get('id') in hidden_runs or run.get('launch_id') in hidden_launches):
            raise TrashError('The task is already in the recycle bin')
        paths = _paths(root, run, category)
        if category == 'research':
            records.append(jobs.read_record(paths[0] / 'job.json') or {})
        elif run.get('launch_id'):
            records.append(jobs.read_record(_studio(root) / (_name(run['launch_id']) + '.json')) or {})
        elif run.get('id') and not (paths[0] / 'result.json').exists():
            trial = jobs.read_record(paths[0] / 'research_trial.json') or {}
            ref = trial.get('research_id', '')
            if isinstance(ref, str) and re.fullmatch(r'research-[a-f0-9]{12}', ref):
                research_path = _paths(root, {'research_id': ref}, 'research')[0]
                records.append(jobs.read_record(research_path / 'job.json') or {})
    jobs.stop_for_recycling(records)

    # A task selected while starting can create its run directory during shutdown.
    pending = {run['launch_id'] for run in runs if run.get('launch_id') and not run.get('id')}
    late_runs = {}
    if pending:
        from evoab.workbench.runs import load_launches
        late_runs = {launch['launch_id']: launch['run_dir'] for launch in load_launches(root)
                     if launch.get('launch_id') in pending and launch.get('run_dir')}
    new = []
    for run in runs:
        if run.get('launch_id') in late_runs:
            run = {**run, 'id': late_runs[run['launch_id']]}
        if run.get('status') in {'starting', 'running'}:
            run = {**run, 'status': 'interrupted'}
        paths = _paths(root, run, category)
        identities = {}
        for path in paths:
            if path.exists():
                stat = path.stat()
                identities[str(path.relative_to(root))] = [stat.st_dev, stat.st_ino]
        new.append({'trash_id': uuid4().hex, 'deleted_at': time.time(), 'category': category,
                    'run': run, 'identities': identities})
    _save(root, items + new)
    return new


def selected(root: Path, ids: list[str]) -> list[dict]:
    by_id = {item['trash_id']: item for item in entries(root)}
    if any(key not in by_id for key in ids):
        raise TrashError('A selected recycle-bin item no longer exists; refresh the page')
    return [by_id[key] for key in ids]


def restore(root: Path, ids: list[str]) -> None:
    for entry in selected(root, ids):
        _identity(root, entry, require_files=True)
    _save(root, [e for e in entries(root) if e['trash_id'] not in ids])


def purge(root: Path, ids: list[str]) -> None:
    chosen = selected(root, ids)
    for entry in chosen:
        _identity(root, entry)
    for entry in chosen:
        for path in _paths(root, entry['run'], entry.get('category', 'run')):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    # Keep tombstones on any failure so partial removal never resurrects a run.
    _save(root, [e for e in entries(root) if e['trash_id'] not in ids])
