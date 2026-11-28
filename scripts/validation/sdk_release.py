"""Install a wheel with full dependencies and check SDK imports/configuration outside the checkout.

Usage: python scripts/validation/sdk_release.py WHEEL NEW_DIRECTORY [--installer uv]
An optional --wheelhouse supplies offline dependencies without skipping resolution.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--installer', choices=('pip', 'uv'), default='pip')
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--upgrade-from', help='Install this published main-package version before upgrading')
    parser.add_argument('--check-uninstall', action='store_true',
                        help='Uninstall evoab after validation and verify no module remains importable')
    args = parser.parse_args()
    wheel, directory = args.wheel.resolve(), args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('EVOAB_')
                   and key != 'ANTHROPIC_BASE_URL'}
    environment.update(PYTHONPATH='', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    target = directory / 'venv'
    venv.create(target, with_pip=True)
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    def command(parts):
        subprocess.run([str(part) for part in parts], cwd=directory, env=environment, check=True)
    install = [python, '-m', 'pip', 'install'] if args.installer == 'pip' else [
        shutil.which('uv') or 'uv', 'pip', 'install', '--link-mode', 'copy', '--python', python]
    source = ['--no-index', '--find-links', args.wheelhouse.resolve()] if args.wheelhouse else []
    if args.upgrade_from:
        command([*install, *source, f'evoab=={args.upgrade_from}'])
    command([*install, *source, str(wheel)])
    command([python, '-I', '-c', '''import pathlib,sys,evoab
for name in evoab.__all__:
    getattr(evoab, name)
assert pathlib.Path(evoab.__file__).is_relative_to(sys.prefix)
assert pathlib.Path(evoab.__file__).with_name('py.typed').is_file()
assert not any(n in sys.modules for n in ('isaacsim', 'mujoco', 'torch'))
print('Installed SDK:', evoab.__file__)
'''])
    executable = python.parent / ('evoab.exe' if os.name == 'nt' else 'evoab')
    command([executable, '--version'])
    command([executable, '--help'])
    command([python, '-m', 'evoab', '--version'])
    command([python, '-m', 'evoab', 'images', '--help'])
    # Offline method checks: the built-in agent needs no model or credential to validate a recipe.
    for integration in ('libero', 'libero_pro', 'calvin'):
        for method in ('tools', 'code', 'action'):
            command([python, '-m', 'evoab', 'methods', 'validate', method, '--benchmark', integration])
    command([python, '-I', '-c', """from pathlib import Path
from importlib.util import find_spec
import evoab
from evoab import Session, SessionConfig, ImageConfig, DeploymentConfig
from evoab.agent.context.prompt import get_prompt_builder
from evoab.envs.calvin.action_binding import create_binding as calvin_binding
from evoab.tools.hybrid.provider import HybridTools
from evoab.servers.actor.registry import backend_names
import sys
assert callable(calvin_binding)
assert set(backend_names()) == {'starvla_calvin', 'starvla_libero'}
assert not any(name in sys.modules for name in ('mujoco', 'torch', 'scipy'))
root = Path(evoab.__file__).parent
assert callable(get_prompt_builder('libero'))
for resource in ('apps/web/static/index.html', 'apps/web/static/app.js',
                 'envs/libero/container.toml', 'envs/calvin/container.toml', 'envs/libero_pro/container.toml'):
    assert (root / resource).is_file(), resource
session = Session(SessionConfig(benchmark='libero_pro',
    env_url='http://127.0.0.1:18760'))
session.close()  # Validate configuration only; do not connect or finalize an episode.
ImageConfig(benchmark="calvin").resolve()
DeploymentConfig(benchmark="libero", task="libero_spatial/0", depth=False).resolve()
try:
    ImageConfig(service="sam3")
except ValueError:
    pass  # policy and SAM3 services are run by users and reached by URL
else:
    raise AssertionError('A model-service setup was accepted')
assert not any(name in sys.modules for name in ("mujoco", "jax", "torch", "scipy"))
resolved = SessionConfig(benchmark='libero_pro', env_url='http://127.0.0.1:18760', recipe='tools').resolve()
assert resolved.recipe_id == 'tools' and resolved.act == ('tools',)
mixed = SessionConfig(benchmark='libero_pro', env_url='http://127.0.0.1:18760', recipe='tools',
                      recipe_overrides={'roles.main.act': ['tools', 'code', 'action']}).resolve()
assert mixed.act == ('tools', 'code', 'action') and mixed.direct_config.representation == 'abs_eef'
assert resolved.tool_modules == ('skills', 'scene', 'gripper', 'episode')
clean = SessionConfig(benchmark='libero_pro', env_url='http://127.0.0.1:18760', recipe='tools', recipe_overrides={
    'roles.main.style': 'compact', 'execution.return_mode': 'immediate', 'roles.main.capabilities': [
        {'id': 'gripper', 'tools': ['move', 'hold']}, {'id': 'scene', 'tools': ['capture_scene']}, 'episode']}).resolve()
assert clean.selected_tools == ('move', 'hold', 'capture_scene', 'finish')
hybrid = SessionConfig(benchmark='calvin', env_url='http://127.0.0.1:18760',
    recipe='tools', action_model_url='http://127.0.0.1:18769',
    benchmark_options={'action_model_checkpoint_sha256': 'a' * 64},
    recipe_overrides={'roles.main.capabilities': ['skills', 'scene', 'gripper',
                                                  {'id': 'hybrid', 'options': {'review': False}}, 'episode']}).resolve()
assert hybrid.tool_modules == ('skills', 'scene', 'gripper', 'hybrid', 'episode')
try:
    SessionConfig(benchmark='libero_pro', env_url='http://127.0.0.1:18760', recipe='tools',
                  recipe_overrides={'roles.main.capabilities': ['scene', 'hybrid', 'episode']}).resolve()
except ValueError as error:
    assert "no capability 'hybrid'" in str(error)
else:
    raise AssertionError('A method needing an unavailable capability was accepted')
assert {'tools', 'code', 'action'} == {
    m.id for m in __import__('evoab.recipe.methods', fromlist=['x']).list_methods()}
for resource in ('methods/tools/method.yaml', 'methods/action/CARD.md', 'resources/prompts/runtime/tools_and_code.md'):
    assert (root / resource).is_file(), resource
for name in ('robodojo', 'robotwin', 'libero_plus', 'metaworld'):
    assert find_spec(f'evoab.envs.{name}') is None, name
for name in ('robodojo_internal', 'efe', 'franka_robotiq', 'franka_sharpa', '_franka', 'dexterous_hand'):
    assert find_spec(f'evoab.envs.{name}') is None, name
    assert not (root / 'integrations' / name).exists(), name
for name in ('gripper', 'robodojo_internal', 'efe', 'franka_robotiq', 'franka_sharpa'):
    try:
        Session(SessionConfig(benchmark=name, env_url='http://127.0.0.1:18760'))
    except ValueError as error:
        assert 'Unknown benchmark' in str(error)
    else:
        raise AssertionError(f'Unavailable integration accepted: {name}')
assert find_spec('evoab.tools.dexterous_hand') is None
assert not (root / 'tools' / 'dexterous_hand').exists()
print('Offline SDK configuration, unavailable-entry errors and packaged resources: OK')
"""])
    command([python, '-m', 'pip', 'check'])
    if args.upgrade_from or args.check_uninstall:
        # Detect module ownership left behind by an upgrade or another distribution.
        command([python, '-m', 'pip', 'uninstall', '-y', 'evoab'])
        command([python, '-I', '-c', "import importlib.util; assert importlib.util.find_spec('evoab') is None"])


if __name__ == '__main__':
    main()
