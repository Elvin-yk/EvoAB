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


def research_dependency(root: Path, entry: dict) -> str | None:
    """Identify retained campaign evidence, including interrupted setup records."""
    if entry.get('category', 'run') != 'run' or not entry['run'].get('id'):
        return None
    trial = jobs.read_record(root / _name(entry['run']['id']) / 'research_trial.json') or {}
    ref = trial.get('research_id', '')
    if re.fullmatch(r'research-[a-f0-9]{12}', ref) and (_studio(root) / 'research' / ref).exists():
        return ref
    return None


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


def _inactive(root: Path, run: dict, category: str = 'run') -> None:
    from evoab.workbench.runs import load_launches, summarize_run
    if category == 'research':
        job = jobs.read_record(_paths(root, run, category)[0] / 'job.json') or {}
        if jobs.is_running(job):
            raise TrashError('A running research campaign cannot be deleted or restored; stop it first')
        return
    launches = load_launches(root)
    if run.get('id'):
        trial = jobs.read_record(root / run['id'] / 'research_trial.json') or {}
        research_id = trial.get('research_id', '')
        if re.fullmatch(r'research-[a-f0-9]{12}', research_id):
            job = jobs.read_record(_studio(root) / 'research' / research_id / 'job.json') or {}
            if jobs.is_running(job):
                raise TrashError('This episode belongs to an active research campaign; stop the campaign first')
    matching = [l for l in launches if
                (run.get('launch_id') and l.get('launch_id') == run['launch_id']) or
                (run.get('id') and l.get('run_dir') == run['id'])]
    if any(jobs.is_running(l) for l in matching):
        raise TrashError('A running task cannot be deleted or restored; stop it and wait for it to finish')
    if run.get('id') and (root / run['id']).is_dir():
        current = summarize_run(root / run['id'], launch=matching[0] if matching else None)
        if current and current['status'] in {'running', 'starting'}:
            raise TrashError('A running task cannot be deleted or restored; wait for it to finish')


def _identity(root: Path, entry: dict, *, require_files: bool = False) -> None:
    category = entry.get('category', 'run')
    paths = _paths(root, entry['run'], category)
    _inactive(root, entry['run'], category)
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
    new = []
    for run in runs:
        if (category == 'research' and run.get('research_id') in hidden_research
                or run.get('id') in hidden_runs or run.get('launch_id') in hidden_launches):
            raise TrashError('The task is already in the recycle bin')
        paths = _paths(root, run, category)
        _inactive(root, run, category)
        identities = {str(p.relative_to(root)): [p.stat().st_dev, p.stat().st_ino]
                      for p in paths if p.exists()}
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
    purging_research = {e['run']['research_id'] for e in chosen if e.get('category') == 'research'}
    for entry in chosen:
        _identity(root, entry)
    blocked = [(e['run'].get('short_id') or e['run'].get('id'), research_dependency(root, e))
               for e in chosen if research_dependency(root, e) not in (None, *purging_research)]
    if blocked:
        parents = ', '.join(sorted({ref for _, ref in blocked}))
        raise TrashError(f'本次未删除任何记录：所选记录中有 {len(blocked)} 条仍是研究证据。'
                         f'关联研究：{parents}。请先在“自我改进”删除这些研究，再到回收站“全部”中将研究和关联回合一起彻底删除；也可取消选择受保护的回合')
    for entry in chosen:
        for path in _paths(root, entry['run'], entry.get('category', 'run')):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    # Keep tombstones on any failure so partial removal never resurrects a run.
    _save(root, [e for e in entries(root) if e['trash_id'] not in ids])
