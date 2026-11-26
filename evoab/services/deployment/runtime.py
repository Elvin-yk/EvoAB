"""Versioned container runtime records; deployment never repairs an installation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time

from evoab import __version__
from evoab.artifacts.run import write_json_atomic
from .config import Runtime


def cache_root(args=None):
    path = getattr(args, 'cache_dir', None) or os.environ.get('EVOAB_CACHE_DIR')
    return Path(path or Path.home() / '.cache' / 'evoab').expanduser().absolute()


def runtime_root(args, recipe):
    # The suffix predates container-only runtimes; keeping it keeps prepared images usable.
    return cache_root(args) / recipe.name / f'{__version__}-{recipe.digest[:16]}-container'


@contextmanager
def runtime_lock(root, timeout=14400):
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with (root / '.prepare.lock').open('a') as lock:
        # One producer publishes this immutable recipe/version; processes may read the old record.
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out waiting for another setup process')
                time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_runtime(args, recipe, root=None):
    root = root or runtime_root(args, recipe)
    path = root / 'runtime.json'
    if not path.is_file():
        raise ValueError(f'Runtime {recipe.name} is not prepared. Run the matching evoab setup command '
                         '(it pulls or builds the Docker image).')
    data = json.loads(path.read_text())
    if data.pop('schema_version', None) != 1:
        raise ValueError('Unsupported runtime record version; rerun setup')
    runtime = Runtime(**{**data, 'root': Path(data['root'])})
    if runtime.recipe_digest != recipe.digest or runtime.package_version != __version__:
        raise ValueError('Runtime version mismatch; rerun setup')
    if runtime.data.get('container', {}).get('imported_image') and not getattr(args, 'check', False):
        validation_path = root / 'validation.json'
        validation = json.loads(validation_path.read_text()) if validation_path.is_file() else {}
        if not validation.get('launch_verified'):
            raise ValueError('Imported runtime has not passed its environment launch check')
    from .container import is_container
    if not is_container(runtime.python):
        raise ValueError('This runtime was prepared for a native interpreter, which EvoAB no longer runs; '
                         'rerun setup to build its Docker image')
    return runtime


def runtime_for(args, recipe, adapter):
    runtime = read_runtime(args, recipe)
    if getattr(args, 'assets_dir', None):
        raise ValueError('--assets-dir is a setup input; the prepared runtime records it')
    return runtime


def check_python(python, imports=(), *, environment=None, timeout=600):
    script = ('import importlib,importlib.metadata,json,sys; '
              'from evoab import __version__; '
              'names=json.loads(sys.argv[1]); '
              '[importlib.import_module(n) for n in names]; '
              'print(json.dumps(dict(python=sys.version.split()[0],package_version=__version__)))')
    from .container import python_command
    command = python_command(python, ['-c', script, json.dumps(['evoab.services.hosting', *imports])],
                             environment=environment)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd='/')
    if result.returncode:
        raise RuntimeError(f'Runtime import check failed: {result.stderr[-3000:]}')
    info = json.loads(result.stdout.strip().splitlines()[-1])
    if info['package_version'] != __version__:
        raise ValueError(f'Runtime needs evoab-runtime=={__version__}, found {info["package_version"]}')
    return info


def publish_runtime(runtime, validation):
    write_json_atomic(runtime.root / 'validation.json', validation)
    write_json_atomic(runtime.root / 'runtime.json', runtime.record())


def query_python(python, code, *arguments, environment=None, timeout=600):
    """Run ``code`` with the runtime's interpreter inside its image; parse the last stdout line."""
    from .container import query
    return query(python, code, *map(str, arguments), environment=environment, timeout=max(timeout, 600))
