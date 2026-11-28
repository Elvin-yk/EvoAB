"""Lightweight contracts for preparing and owning containerized service processes."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module, resources
import hashlib
import argparse
import math
from pathlib import Path
import tomllib
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

#: Runtime record format. 2: EvoAB's code is mounted into the image (``|code``), not baked in.
RECORD_SCHEMA = 2


@dataclass(frozen=True)
class Recipe:
    name: str
    package: str
    resource: str
    settings: dict
    digest: str

    @classmethod
    def load(cls, package, resource):
        content = resources.files(package).joinpath(resource).read_bytes()
        settings = tomllib.loads(content.decode())
        if base := settings.get('base'):
            inherited = resources.files(package).joinpath(base).read_bytes()
            defaults = tomllib.loads(inherited.decode())
            settings = {**defaults, **settings, 'packages': defaults.get('packages', []) + settings.get('packages', [])}
            content = inherited + b'\0' + content
        return cls(settings['name'], package, resource, settings, hashlib.sha256(content).hexdigest())


@dataclass(frozen=True)
class Runtime:
    name: str
    python: str
    recipe_digest: str
    package_version: str
    root: Path
    data: dict = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    def record(self):
        return dict(schema_version=RECORD_SCHEMA, name=self.name, python=self.python,
                    recipe_digest=self.recipe_digest, package_version=self.package_version,
                    root=str(self.root), data=self.data, environment=self.environment)


@dataclass(frozen=True)
class LaunchPlan:
    command: tuple[str, ...]
    url: str
    expected: dict
    environment: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None


def adapter_for(args):
    """Resolve the environment's deployment module without importing simulator runtimes.

    Only environments are deployed. Policy (actor) and SAM3 services are run by
    users and reached by URL (``--action-model-url``, ``--sam3-url``).
    """
    from evoab.envs import load_integration
    if not args.integration:
        raise ValueError('Environment deployment requires --benchmark')
    module = load_integration(args.integration).get_integration_spec().deployment_module
    if not module:
        raise ValueError(f'{args.integration} has no local deployment recipe; use --env-url')
    return import_module(module)


def positive(value):
    number = float(value)
    if number <= 0 or not math.isfinite(number):
        raise ValueError('must be finite and positive')
    return number


def validate_deployment(args):
    if getattr(args, 'deploy_gpu', 0) < 0:
        raise ValueError('GPU index must be nonnegative')
    adapter = adapter_for(args)
    adapter.validate(args)
    if getattr(args, 'port', None) is not None and not 1 <= args.port <= 65535:
        raise ValueError('--port must be between 1 and 65535')
    for field_name in ('startup_timeout_s', 'shutdown_timeout_s', 'setup_timeout_s'):
        if hasattr(args, field_name):
            positive(getattr(args, field_name))
    return adapter


def add_runtime_arguments(parser):
    parser.add_argument('--cache-dir', type=Path, help='Persistent runtime cache (or EVOAB_CACHE_DIR)')


def add_launch_arguments(parser, *, managed=False):
    parser.add_argument('--env-gpu' if managed else '--gpu', dest='deploy_gpu', type=int, default=0,
                        help='Index within currently visible GPUs')
    parser.add_argument('--env-port' if managed else '--port', dest='port', type=int)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--startup-timeout-s', type=float, default=900)
    parser.add_argument('--shutdown-timeout-s', type=float, default=30)
    if '--seed' not in parser._option_string_actions:
        parser.add_argument('--seed', type=int)
    if managed:
        return  # evoab run takes what the agent observes from the recipe's inputs
    parser.add_argument('--cameras', nargs='+')
    parser.add_argument('--depth', action=argparse.BooleanOptionalAction, default=None)


class RuntimeConfig(BaseModel):
    """Selection shared by image preparation and deployment."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    benchmark: str = Field(min_length=1)
    cache_dir: Path | None = None

    def resolve(self):
        from evoab.envs import apply_benchmark
        args = SimpleNamespace(**self.model_dump())
        args.command = "images" if isinstance(self, ImageConfig) else "deploy"
        if isinstance(self, ImageConfig):
            args.setup_timeout_s = self.timeout_s
        apply_benchmark(args)
        validate_deployment(args)
        return args


class ImageConfig(RuntimeConfig):
    """Pull (or import, or build) one benchmark's image; never run implicitly by a Session."""

    wheel: Path | None = None
    image_archive: str | None = None
    accept_license: str | None = None
    timeout_s: float = Field(default=14400, gt=0, allow_inf_nan=False)


class DeploymentConfig(RuntimeConfig):
    """Launch one prepared service container, with an explicit owned process handle."""

    task: str | None = None
    run_id: str | None = None
    output_dir: Path = Path("runs")
    deploy_gpu: int = Field(default=0, ge=0)
    port: int | None = Field(default=None, ge=1, le=65535)
    host: str = "127.0.0.1"
    startup_timeout_s: float = Field(default=900, gt=0, allow_inf_nan=False)
    shutdown_timeout_s: float = Field(default=30, gt=0, allow_inf_nan=False)
    seed: int | None = None
    cameras: list[str] | None = None
    depth: bool | None = None
