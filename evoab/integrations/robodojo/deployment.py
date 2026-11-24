"""Prepare the pinned official runtime and resolve one frozen RoboDojo episode."""
from pathlib import Path

from evoab.integrations.simulation import materialized_asset
from evoab.services.deployment.config import LaunchPlan, Recipe
from evoab.services.deployment.runtime import check_python, query_python
from evoab.services.deployment.setup import file_hash

empty_catalog_hint = (
    "RoboDojo lists a task only when its config and the frozen layout at "
    "Assets/Eval_Layout/RoboDojo/arx_x5/0/<task>_0.json are both present. Add both, then rerun "
    "this setup command; the CLI can still run with an explicit --layout."
)


def recipe(args):
    return Recipe.load(__package__, 'deployment.toml')


def validate(args):
    from .tasks import SEEDS
    if getattr(args, 'seed', None) not in (None, *SEEDS):
        raise ValueError(f'RoboDojo evaluation seeds are {", ".join(map(str, SEEDS))}')
    if getattr(args, 'layout_id', None) is not None and (args.layout_id < 0 or args.layout):
        raise ValueError('--layout-id is a non-negative index and cannot be combined with --layout')
    if args.command != 'setup' and not args.task:
        raise ValueError('Local RoboDojo deployment requires --task')
    if getattr(args, 'camera_calibration', None) is False and getattr(args, 'depth', None) is not False:
        raise ValueError('--no-camera-calibration requires --no-depth')
    if getattr(args, 'runtime_profile', None):
        raise ValueError('RoboDojo does not use --runtime-profile')


def catalog_tasks(data):
    native = Path(data['native_root'])
    layouts = Path(data['assets_dir']) / 'Eval_Layout/RoboDojo/arx_x5/0'
    return {config.stem: {'spec': {'backend': 'RoboDojo', 'robots': ['Dual ARX X5'],
                                    'scenario': {'title': config.stem.replace('_', ' ')}}}
            for config in sorted((native / 'task/RoboDojo/config').glob('*.yml'))
            if materialized_asset(config) and materialized_asset(layouts / f'{config.stem}_0.json')}


def external_data(args):
    if not args.native_root:
        raise ValueError('An external RoboDojo runtime requires --native-root')
    native = args.native_root.expanduser().resolve()
    assets = (args.assets_dir or native / 'Assets').expanduser().resolve()
    return dict(native_root=str(native), assets_dir=str(assets))


def prepare(context):
    config = context.recipe.settings
    native = Path(context.step('source', lambda: context.git(config['repository'], config['revision'],
                                                           context.root / 'source', submodules=True)))
    if context.args.assets_dir:
        assets = context.args.assets_dir.expanduser().resolve()
    else:
        download = Path(context.step('assets', lambda: context.snapshot(config['asset_repository'],
            config['asset_revision'], context.root / 'assets', patterns=['Assets/**'])))
        assets = download / 'Assets'
    def initialize_assets():
        from .assets import asset_overlay, verify_robot_configs
        prepared = asset_overlay(assets, context.root / 'build/Assets')
        context.command([context.python, native / 'utils/update_embodiment_config_path.py'],
                        cwd=prepared.parent)
        verify_robot_configs(prepared)
        return str(prepared)
    context.data['asset_source_dir'] = str(assets)
    assets = Path(context.step('robot-configs', initialize_assets))
    link = native / 'Assets'
    if not link.exists():
        link.symlink_to(assets, target_is_directory=True)
    elif link.resolve() != assets:
        raise ValueError('Native Assets path differs from the prepared asset directory')
    context.step('torch', lambda: context.pip(*config['torch'], options=['--index-url', config['torch_index']]))
    context.step('isaacsim', lambda: context.pip('isaacsim[all,extscache]==5.1.0',
                                              options=['--extra-index-url', 'https://pypi.nvidia.com']))
    context.step('dependencies', lambda: context.pip('-r', native / 'scripts/requirements.txt', *config['packages']))
    context.step('build-tools', lambda: context.pip(*config['build_tools']))
    def install_isaaclab():
        from .build_compat import isaaclab_source
        source = isaaclab_source(native, context.root / 'build/IsaacLab')
        # Match the upstream base install: the app loads these extensions and
        # core environment imports also require their declared dependencies.
        packages = [item for name in config['isaaclab_extensions']
                    for item in ('--editable', source / 'source' / name)]
        context.pip(*packages, options=['--no-build-isolation'])
    context.step('isaaclab', install_isaaclab)
    context.data['isaaclab_build_compat'] = config['isaaclab_build_compat']
    context.step('curobo', lambda: context.pip(str(native / 'third_party/curobo') + '[cu12]',
                                            options=['--no-build-isolation']))
    context.data.update(native_root=str(native), assets_dir=str(assets))
    context.environment.update(XDG_CACHE_HOME=str(context.root / 'cache'),
                               XDG_DATA_HOME=str(context.root / 'data'),
                               XDG_CONFIG_HOME=str(context.root / 'config'))


def container_data(args, root, report):
    """Host side of a container runtime: pinned checkout (the build context) and prepared assets.

    The checkout and assets stay on the host and are mounted into the container
    at the same paths, so task, layout and asset checks read the files the
    container service uses.
    """
    from evoab.services.deployment.setup import Preparation
    from .assets import asset_overlay
    settings = recipe(args).settings
    context = Preparation(args, recipe(args), root, report=report)
    native = Path(context.step('source', lambda: context.git(settings['repository'], settings['revision'],
                                                           root / 'source', submodules=True)))
    if args.assets_dir:
        source = args.assets_dir.expanduser().resolve()
    else:
        source = Path(context.step('assets', lambda: context.snapshot(settings['asset_repository'],
            settings['asset_revision'], root / 'assets', patterns=['Assets/**']))) / 'Assets'
    prepared = asset_overlay(source, root / 'build/Assets')
    link = native / 'Assets'
    if link.is_symlink() and link.resolve() != prepared.resolve():
        link.unlink()
    if not link.exists():
        link.symlink_to(prepared, target_is_directory=True)
    # Isaac Sim's shader and extension caches persist across runs (containers are removed).
    xdg = root / 'xdg'
    for name in ('cache', 'data', 'config'):
        (xdg / name).mkdir(parents=True, exist_ok=True)
    return dict(native_root=str(native), assets_dir=str(prepared), asset_source_dir=str(source),
                mounts=[str(native), str(root / 'build'), str(source), str(xdg)],
                environment={'XDG_CACHE_HOME': str(xdg / 'cache'), 'XDG_DATA_HOME': str(xdg / 'data'),
                             'XDG_CONFIG_HOME': str(xdg / 'config')})


def container_finalize(python, data, report):
    """Initialize the robot configurations with the image's interpreter, as native setup does."""
    import subprocess
    from evoab.services.deployment.container import options, parse, run_prefix
    from .assets import verify_robot_configs
    image, inner = parse(python)
    root, mounts = options(python)
    assets = Path(data['assets_dir'])
    command = run_prefix(image, gpu=False, environment={}, mounts=mounts, root=root, workdir=str(assets.parent)) \
        + [inner, str(Path(data['native_root']) / 'utils/update_embodiment_config_path.py')]
    if report is not None:
        report('Initializing RoboDojo robot configurations in the image')
    subprocess.run(command, check=True, capture_output=True, text=True)
    verify_robot_configs(assets)


def verify(runtime):
    from .assets import verify_robot_configs
    info = check_python(runtime.python, ['scipy', 'trimesh', 'sklearn', 'h5py', 'omegaconf', 'hydra'],
                        environment=runtime.environment)
    native, assets = Path(runtime.data['native_root']), Path(runtime.data['assets_dir'])
    if (native / 'Assets').resolve() != assets.resolve():
        raise ValueError('The official checkout must resolve Assets to the prepared assets')
    for directory in ('Robots', 'Object', 'Material', 'Eval_Layout'):
        if not (assets / directory).is_dir():
            raise ValueError(f'Missing RoboDojo asset directory: {assets / directory}')
    verify_robot_configs(assets)
    code = ('import hashlib,json,sys,importlib.util; from pathlib import Path; '
            'from evoab.integrations.robodojo.environment.native_compat import resolve_native_root; '
            'from evoab.integrations.robodojo.environment.gripper_geometry import AUDITED_ASSETS; '
            'root=resolve_native_root(Path(sys.argv[1])); '
            'robot=root/"Assets/Robots/x5"; '
            'invalid=[name for name,values in AUDITED_ASSETS.items() '
            'if hashlib.sha256((robot/name).read_bytes()).hexdigest() not in values]; '
            'assert not invalid,("Unaudited X5 assets",invalid); '
            'names=("isaacsim","isaaclab","curobo"); '
            'missing=[n for n in names if importlib.util.find_spec(n) is None]; '
            'assert not missing,missing; '
            'lab=Path(importlib.util.find_spec("isaaclab").origin).parent.parent; '
            'assets=Path(importlib.util.find_spec("isaaclab_assets").origin).parent.parent; '
            'required=(lab/"config/extension.toml",assets/"config/extension.toml",'
            'lab.parent.parent/"apps/isaaclab.python.kit"); '
            'assert all(p.is_file() for p in required),required; '
            'print(json.dumps(dict(native_root=str(root),isaaclab_source=str(lab))))')
    info.update(query_python(runtime.python, code, native, environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    native, assets = Path(runtime.data['native_root']), Path(runtime.data['assets_dir'])
    task = args.task
    if not task or Path(task).name != task or task in ('.', '..'):
        raise ValueError('RoboDojo task must be a native task name')
    if not (native / 'task/RoboDojo/config' / (task + '.yml')).is_file():
        raise ValueError(f'Unknown RoboDojo task: {task}')
    from .tasks import layout_path
    seed, layout_id = getattr(args, 'seed', None) or 0, getattr(args, 'layout_id', None) or 0
    layout = (args.layout or layout_path(assets, task, seed, layout_id)).resolve()
    if not layout.is_file():
        raise ValueError(f'Frozen layout not found: {layout} (seed {seed}, layout {layout_id}); '
                         'prepare assets or pass --layout')
    digest = file_hash(layout)
    if args.layout_sha256 and digest != args.layout_sha256:
        raise ValueError('Layout SHA256 mismatch')
    port = args.port or 18760
    command = [runtime.python, '-m', 'evoab.integrations.robodojo.environment.server',
        '--native-root', str(native), '--task', task, '--layout', str(layout), '--layout-sha256', digest,
        '--output-dir', str(output / 'environment'), '--run-id', run_id, '--host', args.host, '--port', str(port)]
    # Older prepared runtimes report seed 0 but do not accept a --seed argument.
    if seed != 0:
        command.extend(('--seed', str(seed)))
    if args.depth is False:
        command.append('--no-depth')
    if getattr(args, 'camera_calibration', None) is False:
        command.append('--no-camera-calibration')
    connect_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    return LaunchPlan(tuple(command), f'http://{connect_host}:{port}',
                      {'integration': 'robodojo', 'episode_id': run_id, 'task': task,
                       'environment.metadata.layout_sha256': digest, 'metadata.seed': seed},
                      runtime.environment, output)
