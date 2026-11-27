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
    # Offline method checks: the built-in agent needs no model or credential to validate a recipe.
    for integration in ('robodojo', 'robolab', 'robotwin', 'libero', 'libero_plus',
                        'libero_pro', 'rmbench', 'calvin', 'arx_one'):
        command([python, '-m', 'evoab', 'methods', 'validate', 'agentic', '--integration', integration])
    for integration in ('robodojo', 'robolab', 'dexjoco'):
        command([python, '-m', 'evoab', 'methods', 'validate', 'agent_policy', '--integration', integration])
    command([python, '-I', '-c', """from pathlib import Path
from importlib.util import find_spec
import evoab
from evoab import Session, SessionConfig, SetupConfig, DeploymentConfig
from evoab.context.prompt import get_prompt_builder
from evoab.integrations.robodojo.action_binding import create_binding as x5_binding
from evoab.integrations.robolab.action_binding import create_binding as droid_binding
from evoab.tools.hybrid.provider import HybridTools
from evoab.integrations.dexjoco.action_binding import create_binding as dexjoco_binding
from evoab.services.actor.backends.openpi.config import resolve_config, recipe_settings
import sys
assert all(callable(factory) for factory in (x5_binding, droid_binding, dexjoco_binding))
assert not any(name in sys.modules for name in ('isaacsim', 'mujoco', 'torch', 'scipy'))
root = Path(evoab.__file__).parent
assert callable(get_prompt_builder('robodojo'))
for resource in ('ui/web/static/index.html', 'ui/web/static/app.js',
                 'integrations/robodojo/deployment.toml',
                 'integrations/dexjoco/deployment.toml', 'integrations/dexjoco/SOURCE.md',
                 'resources/prompts/dexjoco/hybrid.md', 'resources/skills/dexjoco/index.md',
                 'services/actor/recipes/robodojo_pi05.toml',
                 'services/actor/recipes/dexjoco_pick_bucket.toml'):
    assert (root / resource).is_file(), resource
session = Session(SessionConfig(integration='libero_pro',
    env_url='http://127.0.0.1:18760'))
session.close()  # Validate configuration only; do not connect or finalize an episode.
for profile, widths in (("robodojo_pi05", (14,14,50)), ("dexjoco_pick_bucket", (23,22,30))):
    config = resolve_config(dict(profile=profile, native_root="/unused/source",
        checkpoint="/unused/checkpoint", checkpoint_sha256="a" * 64))
    assert (len(config.profile.observation_fields), len(config.profile.action_fields),
            config.profile.action_horizon) == widths
    assert recipe_settings(profile)["actor"]["model"] == "pi05"
    SetupConfig(service="actor", profile=profile).resolve()
from evoab.services.environment.device_contracts import CoordinatedDeviceStep, DeviceRequest
step = CoordinatedDeviceStep(device="robot", joints={"hand": {"finger": .1}})
assert DeviceRequest.model_validate_json(DeviceRequest(command=step,
    expected_state_version=0).model_dump_json()).command == step
SetupConfig(integration="dexjoco").resolve()
DeploymentConfig(integration="dexjoco", task="pick_bucket", depth=False).resolve()
assert not any(name in sys.modules for name in ("mujoco", "jax", "openpi", "torch", "scipy"))
resolved = SessionConfig(integration='libero_pro', env_url='http://127.0.0.1:18760', recipe='agentic').resolve()
assert resolved.recipe_id == 'agentic' and resolved.channel == 'tools'
assert resolved.tool_modules == ('skills', 'scene', 'gripper', 'episode')
clean = SessionConfig(integration='libero_pro', env_url='http://127.0.0.1:18760', recipe='clean').resolve()
assert clean.selected_tools == ('move', 'hold', 'capture_scene', 'finish')
dex = SessionConfig(integration='dexjoco', env_url='http://127.0.0.1:18788',
    action_model_url='http://127.0.0.1:18769',
    integration_options={'action_model_checkpoint_sha256': 'a' * 64},
    recipe_overrides={'roles.main.capabilities.hybrid.options': {'review': False}}).resolve()
assert dex.tool_modules == ('skills', 'scene', 'hybrid', 'episode')
try:
    SessionConfig(integration='libero_pro', env_url='http://127.0.0.1:18760', recipe='agent_policy').resolve()
except ValueError as error:
    assert "no capability 'hybrid'" in str(error)
else:
    raise AssertionError('A method needing an unavailable capability was accepted')
assert {'agentic', 'clean', 'direct_action', 'code_as_policy', 'planner_direct'} <= {
    m.id for m in __import__('evoab.composition.methods', fromlist=['x']).list_methods()}
for resource in ('methods/agentic/method.yaml', 'methods/planner_direct/CARD.md'):
    assert (root / resource).is_file(), resource
SetupConfig(integration='robodojo')
DeploymentConfig(integration='robodojo', task='stack_bowls')
rgb = DeploymentConfig(integration='robodojo', task='stack_bowls',
                       depth=False, camera_calibration=False).resolve()
assert rgb.depth is False and rgb.camera_calibration is False
try:
    DeploymentConfig(integration='robodojo', task='stack_bowls',
                     camera_calibration=False).resolve()
except ValueError as error:
    assert 'requires --no-depth' in str(error)
else:
    raise AssertionError('Uncalibrated depth configuration accepted')
from evoab.integrations.robodojo.prompt import load_task_requirements
assert load_task_requirements('make_kong') == ''
assert '2 cm' not in load_task_requirements('arrange_largest_number_random')
assert 'object itself more than 10 cm' in load_task_requirements('general_pickup')
for name in ('robodojo_internal', 'efe', 'franka_robotiq', 'franka_sharpa', '_franka', 'dexterous_hand'):
    assert find_spec(f'evoab.integrations.{name}') is None, name
    assert not (root / 'integrations' / name).exists(), name
for name in ('gripper', 'robodojo_internal', 'efe', 'franka_robotiq', 'franka_sharpa'):
    try:
        Session(SessionConfig(integration=name, env_url='http://127.0.0.1:18760'))
    except ValueError as error:
        assert 'not available' in str(error)
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
