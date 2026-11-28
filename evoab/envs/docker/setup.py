"""Explicit image preparation only: never invoked from deploy or run."""
from __future__ import annotations

from collections.abc import Callable
from email.parser import BytesParser
from http.client import HTTPException
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from evoab import __version__
from evoab.records.artifacts.run import write_json_atomic
from .config import Runtime, SetupConfig
from .process import OwnedProcess


class HostPreparation:
    """Resumable host-side steps of a container setup: pinned sources and downloads.

    Integrations use it from ``container_data`` for inputs that stay on the host and
    are mounted into the container (checkouts, assets, checkpoints).
    """

    def __init__(self, args, recipe, root, *, report=print):
        self.args, self.recipe, self.root = args, recipe, Path(root)
        self.report = report
        self.log = self.root / 'setup.log'
        self.deadline = time.monotonic() + args.setup_timeout_s

    def command(self, command, *, cwd=None, environment=None):
        self.root.mkdir(parents=True, exist_ok=True)
        process = OwnedProcess(command, cwd=cwd, log_path=self.log, environment=environment or {})
        process.start()
        try:
            code = process.process.wait(timeout=max(.01, self.deadline - time.monotonic()))
            if code:
                raise RuntimeError(f'Preparation command exited {code}; see {self.log}')
        finally:
            process.close()

    def step(self, name, operation):
        marker = self.root / 'steps' / (name + '.json')
        if marker.exists():
            return json.loads(marker.read_text())['result']
        if self.report is not None:
            self.report(f'Preparing {self.recipe.name}: {name}')
        value = operation()
        write_json_atomic(marker, dict(result=value))
        return value

    def git(self, url, revision, target, *, submodules=False):
        target = Path(target)
        if not (target / '.git').exists():
            self.command(['git', 'clone', '--no-checkout', url, str(target)])
        self.command(['git', '-C', str(target), 'fetch', 'origin', revision])
        self.command(['git', '-C', str(target), 'checkout', '--detach', revision])
        if submodules:
            self.command(['git', '-C', str(target), 'submodule', 'update', '--init', '--recursive'])
        return str(target)

    def snapshot(self, repo_id, revision, target, *, repo_type='dataset', patterns=None):
        if not revision or revision in ('main', 'master'):
            raise ValueError('Downloads require an immutable Hub revision')
        # Hub credentials are read by the library, never persisted in the runtime record.
        payload = json.dumps(dict(repo_id=repo_id, repo_type=repo_type, revision=revision,
                                  local_dir=str(target), allow_patterns=patterns))
        self.command([sys.executable, '-c',
            'import json,sys; from huggingface_hub import snapshot_download; snapshot_download(**json.loads(sys.argv[1]))',
            payload])
        return str(target)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_runtime_wheel(path):
    """Reject planner wheels and mismatched builds before they are installed into an image."""
    if not path.is_file() or path.suffix != '.whl':
        raise ValueError('--wheel must be an existing evoab-runtime wheel')
    with zipfile.ZipFile(path) as archive:
        entries = [name for name in archive.namelist()
                   if len(name.split('/')) == 2 and name.endswith('.dist-info/METADATA')]
        if len(entries) != 1:
            raise ValueError('Runtime wheel must have exactly one distribution metadata record')
        metadata = BytesParser().parsebytes(archive.read(entries[0]))
    if metadata['Name'] != 'evoab-runtime' or metadata['Version'] != __version__:
        raise ValueError(f'--wheel requires evoab-runtime=={__version__}; '
                         f"received {metadata['Name']}=={metadata['Version']}")


def _prepare_previews(args, adapter, runtime, report):
    download = getattr(adapter, 'prepare_previews', None)
    if download is None or getattr(args, 'skip_demo_download', False):
        return
    try:
        download(runtime)
    except (OSError, HTTPException) as error:
        message = f'Expert preview download incomplete ({error}). Rerun setup to resume.'
        if report is None:
            import logging
            logging.getLogger(__name__).warning(message)
        else:
            report(message)


def _report_empty_catalog(adapter, runtime, report):
    """Report a registered runtime with no selectable tasks instead of ready."""
    if 'tasks' not in runtime.data or runtime.data['tasks']:
        return False
    if report is not None:
        hint = getattr(adapter, 'empty_catalog_hint', None)
        report('Runtime registered without selectable tasks: setup succeeded, but no task can be '
               'launched.' + (f' {hint}' if hint else ''))
    return True


def prepare(args, adapter, *, report=print):
    """Pull or build the service's Docker image and publish its runtime record."""
    if args.wheel:
        validate_runtime_wheel(args.wheel)
    from .container import prepare as prepare_container
    runtime = prepare_container(args, adapter, report=report)
    _report_empty_catalog(adapter, runtime, report)
    _prepare_previews(args, adapter, runtime, report)
    return runtime


def setup(config: SetupConfig, *, report: Callable[[str], None] | None = None) -> Runtime:
    """Prepare a container runtime without changing host signal handlers or logging settings."""
    from .config import adapter_for
    args = config.resolve()
    return prepare(args, adapter_for(args), report=report)


def check_launch(args, adapter, runtime, *, report=print):
    """Start one task's environment service the way runs do, wait until ready, then stop it.

    Setup only proves the image builds and lists tasks; this proves the simulator
    starts on this machine (GPU, rendering, assets). The task is the adapter's
    ``CHECK_TASK`` when it names one (a seed the upstream checks admit), else the first.
    """
    import socket
    tasks = sorted((runtime.data or {}).get('tasks') or {})
    if not tasks:
        raise RuntimeError('The runtime lists no tasks to start')
    task = getattr(adapter, 'CHECK_TASK', None) or tasks[0]
    profile, _, name = task.rpartition('/')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    command = [sys.executable, '-m', 'evoab', 'deploy', 'env', '--integration', args.integration, '--task', name,
               '--port', str(port), '--check', '--output-dir', str(runtime.root / 'checks')]
    if profile:
        command += ['--runtime-profile', profile]
    elif getattr(args, 'runtime_profile', None):
        command += ['--runtime-profile', args.runtime_profile]
    if args.cache_dir:
        command += ['--cache-dir', str(args.cache_dir)]
    if report is not None:
        report(f'Starting {task} once to check the environment service')
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ['no output']
        raise RuntimeError(f'The environment service for {task} did not start: {detail[0]}')
    if report is not None:
        report(f'Environment service started for {task}; deployment verified')
