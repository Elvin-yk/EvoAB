"""Explicit installation only: never invoked from deploy or run."""
from __future__ import annotations

from dataclasses import replace

from collections.abc import Callable
from email.parser import BytesParser
from http.client import HTTPException
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from evoab import __version__
from evoab.artifacts.run import write_json_atomic
from .config import Runtime, SetupConfig, environment_options
from .process import OwnedProcess
from .runtime import publish_runtime, read_runtime, runtime_lock, runtime_root


class Preparation:
    def __init__(self, args, recipe, root, *, report=print):
        self.args, self.recipe, self.root = args, recipe, root
        self.report = report
        self.python = root / 'venv/bin/python'
        self.environment = {'PYTHONNOUSERSITE': '1', 'PYTHONPATH': '',
                            **environment_options(args.runtime_env)}
        self.log = root / 'setup.log'
        self.data = {}
        self.wheel = args.wheel
        self.constraints = root / 'constraints.txt'
        constraints = recipe.settings.get('constraints', [])
        if constraints:
            self.constraints.write_text('\n'.join(constraints) + '\n')
        self.deadline = time.monotonic() + args.setup_timeout_s

    def command(self, command, *, cwd=None, environment=None):
        process = OwnedProcess(command, cwd=cwd, log_path=self.log,
                               environment={**self.environment, **(environment or {})})
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

    def create_python(self):
        def create():
            base = str(self.args.python or self.recipe.settings.get('python', sys.executable))
            if not shutil.which(base) and not Path(base).is_file():
                raise ValueError(f'Required interpreter {base} is unavailable; supply --python')
            self.command([base, '-m', 'venv', str(self.root / 'venv')])
            return str(self.python)
        self.step('venv', create)

    def pip(self, *requirements, options=()):
        constraints = ['--constraint', str(self.constraints)] if self.constraints.is_file() else []
        self.command([str(self.python), '-m', 'pip', 'install', *constraints, *options, *map(str, requirements)])

    def check_dependencies(self):
        env = {**os.environ, **self.environment}
        result = subprocess.run([str(self.python), '-m', 'pip', 'check'], capture_output=True,
                                text=True, cwd='/', env=env,
                                timeout=max(.01, self.deadline-time.monotonic()))
        if not result.returncode:
            return
        reported = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        allowed = self.recipe.settings.get('dependency_exceptions', [])
        if not reported or result.stderr or any(line not in allowed for line in reported):
            raise RuntimeError('Runtime dependencies are inconsistent: ' + result.stdout + result.stderr)
        self.data['dependency_exceptions'] = reported

    def install_harness(self):
        wheel = self.wheel
        if wheel is None:
            self.install_source_harness()
            return
        if not wheel.is_file() or wheel.suffix != '.whl':
            raise ValueError('--wheel must be an existing wheel')
        self.pip(str(wheel.expanduser().absolute()))

    def install_source_harness(self):
        """Without a wheel, install the running ``evoab`` package (as containers do).

        The package is copied into the runtime root and added to the venv with a
        ``.pth`` file; an ``evoab-runtime`` metadata record keeps version checks working.
        """
        from .container import _installed_package
        source, requirements, _ = _installed_package()
        if requirements:
            self.pip(*requirements)
        target = self.root / 'runtime-source'
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target / 'evoab', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        site = Path(subprocess.check_output([str(self.python), '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
                                            text=True).strip())
        (site / 'evoab-runtime.pth').write_text(str(target) + '\n')
        record = site / f'evoab_runtime-{__version__}.dist-info'
        record.mkdir(exist_ok=True)
        (record / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: evoab-runtime\nVersion: {__version__}\n')
        (record / 'INSTALLER').write_text('evoab\n')

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
    """Reject planner wheels and mismatched builds before any native installation."""
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


def _with_task_catalog(runtime, adapter):
    catalog = getattr(adapter, 'catalog_tasks', None)
    if catalog is None:
        return runtime
    return replace(runtime, data={**runtime.data, 'tasks': catalog(runtime.data)})


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
    if args.wheel:
        validate_runtime_wheel(args.wheel)
    if getattr(args, 'container', False):
        from .container import prepare as prepare_container
        runtime = prepare_container(args, adapter, report=report)
        _prepare_previews(args, adapter, runtime, report)
        return runtime
    recipe = adapter.recipe(args)
    root = runtime_root(args, recipe)
    with runtime_lock(root, args.setup_timeout_s):
        request = {name: str(getattr(args, name)) if getattr(args, name, None) is not None else None
                   for name in ('runtime_python', 'native_root', 'assets_dir', 'checkpoint', 'worker_python', 'python')}
        if getattr(args, 'runtime_profile', None) is not None:
            request['runtime_profile'] = args.runtime_profile
        request['environment'] = environment_options(args.runtime_env)
        request['wheel_sha256'] = file_hash(args.wheel) if args.wheel else None
        request_file = root / 'prepare-request.json'
        if request_file.exists() and json.loads(request_file.read_text()) != request:
            raise ValueError('This cache entry was prepared with different inputs. Select another --cache-dir.')
        write_json_atomic(request_file, request)
        if (root / 'runtime.json').exists():
            runtime = read_runtime(args, recipe)
            expected = args.checkpoint_sha256
            if expected and expected != runtime.data.get('checkpoint_sha256'):
                raise ValueError('Checkpoint SHA256 differs from the prepared runtime')
            refreshed = runtime
            if args.runtime_python:
                # The validated record above identifies the installation being rescanned.
                refresh = getattr(adapter, 'refresh_external_data', None)
                data = refresh(args, runtime) if refresh else adapter.external_data(args)
                refreshed = replace(runtime, data=data)
            refreshed = _with_task_catalog(refreshed, adapter)
            validation = adapter.verify(refreshed)
            if refreshed.data != runtime.data:
                previous = json.loads((root / "validation.json").read_text())
                publish_runtime(refreshed, {**previous, **validation})
            runtime = refreshed
            _prepare_previews(args, adapter, runtime, report)
            if not _report_empty_catalog(adapter, runtime, report) and report is not None:
                report(f'Runtime ready (reused): {root}')
            return runtime
        context = Preparation(args, recipe, root, report=report)
        if args.wheel:
            if args.wheel.suffix != '.whl':
                raise ValueError('--wheel must be an existing wheel')
            staged = root / 'inputs' / request['wheel_sha256'] / args.wheel.name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.wheel, staged)
            if file_hash(staged) != request['wheel_sha256']:
                raise ValueError('Input wheel changed during preparation')
            context.wheel = staged
        started = time.time()
        try:
            if args.runtime_python:
                # Adoption records runtime metadata in the cache without installing packages.
                runtime = Runtime(recipe.name, str(args.runtime_python.expanduser().absolute()),
                    recipe.digest, __version__, root, adapter.external_data(args), context.environment)
            else:
                context.create_python()
                adapter.prepare(context)
                context.step('harness', lambda: context.install_harness())
                runtime = Runtime(recipe.name, str(context.python), recipe.digest,
                                  __version__, root, context.data, context.environment)
                context.check_dependencies()
                frozen = subprocess.check_output([str(context.python), '-m', 'pip', 'freeze', '--all'], text=True)
                (root / 'installed.txt').write_text(frozen)
            runtime = _with_task_catalog(runtime, adapter)
            validation = adapter.verify(runtime)
            validation.update(preparation='external' if args.runtime_python else 'isolated',
                              elapsed_s=time.time()-started, gpu_verified=False)
            publish_runtime(runtime, validation)
        except BaseException as error:
            write_json_atomic(root / 'setup_failure.json', dict(error=str(error), type=type(error).__name__,
                                                              elapsed_s=time.time()-started))
            raise
        _prepare_previews(args, adapter, runtime, report)
        if not _report_empty_catalog(adapter, runtime, report) and report is not None:
            report(f'Runtime ready: {root}')
        return runtime


def setup(config: SetupConfig, *, report: Callable[[str], None] | None = None) -> Runtime:
    """Prepare a runtime without changing host signal handlers or logging settings."""
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
    if getattr(args, 'container', False):
        command.append('--container')
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
