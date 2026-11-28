"""Container runtimes: every benchmark environment runs in a Docker image.

EvoAB has no other environment runtime. An environment is a simulator image
plus a thin local layer with EvoAB's Python dependencies; the framework starts
one container per launch, like Harbor. EvoAB's own code is not in the image:
every container mounts the running ``evoab`` package read-only, so changing
EvoAB never requires rebuilding an image. Images change only when the simulator
recipe, the dependencies or the ``prepare`` commands change, and their tags are
derived from exactly those inputs. Policy (actor) and SAM3 services are not
deployed here: users run them and pass their URLs. An integration declares
``container.toml`` next to its deployment module (a custom benchmark's manifest
names the user's own image instead):

``image``        upstream image reference, pinned by digest, pulled as-is (a
                 local image is used without pulling); or
``dockerfile``   a build recipe (path relative to the declaring package) for
                 images nobody publishes; the user's machine builds it once
``python``       interpreter inside the image that imports the simulator
``native_root``  upstream checkout inside the image (passed as ``--native-root``)
``gpu``          whether the service needs the GPU (EGL rendering, Isaac Sim)
``prepare``      shell commands run while building EvoAB's local layer
``build``        "native": build the Dockerfile at the root of the native checkout
                 that the integration prepares on the host (``container_data``)
``root``         run as root inside the image (files are handed back afterwards)
``constraints``  pins applied when installing the runtime wheel into the image
``build_args``   host variables forwarded to the upstream build (e.g. mirrors)
``license``      a licence the user must accept (``--accept-license``) to build
``[[sources]]``  git sources (``path``, ``url``, ``ref``) a ``dockerfile`` build
                 copies in; the host fetches them with its own network settings
                 (proxies, credentials), so builds need no access to the forge
``[environment]`` variables for every container process

Deployment modules may also define ``container_spec(args)`` to supply the spec
themselves, ``container_data(args, root, report)``: host-side preparation
(sources, assets) returning runtime data plus ``mounts``, and
``container_finalize(python, data, report)`` for steps that need the built image.
Without ``container_data``, setup calls the module's ``image_data(args)`` inside
the image to record its facts (task catalogs, checkout paths).

Setup builds a small local image ``FROM`` the upstream one that adds EvoAB's
runtime dependencies and the ``prepare`` commands (or, with ``--wheel``, a pinned
``evoab-runtime`` release baked in instead of the mounted code). Nothing is pushed
anywhere. A prepared container runtime records its interpreter as
``docker://<local image>#<inner python>[|code]``; queries and launches run there
with host networking, the host user's identity, the run directory mounted at
the same path and, with ``|code``, the host's ``evoab`` package mounted at
``/opt/evoab-runtime``, so service URLs, artifact paths and code are the host's.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

PREFIX = "docker://"
#: Where the host's ``evoab`` package is mounted read-only in every container (``|code``).
CODE_ROOT = "/opt/evoab-runtime"
#: Host variables forwarded into the local layer's build (package and model mirrors).
BUILD_VARIABLES = ("PIP_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "HF_ENDPOINT", "UBUNTU_MIRROR")
PROXY_VARIABLES = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


@dataclass(frozen=True)
class ContainerSpec:
    #: Name of the runtime image (an integration, ``actor-…`` or ``sam3``); also its label.
    integration: str
    python: str
    #: Package the ``dockerfile`` path is relative to.
    package: str = ""
    native_root: str | None = None
    image: str | None = None
    image_id: str | None = None
    dockerfile: str | None = None
    gpu: bool = True
    prepare: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    build: str | None = None
    root: bool = False
    constraints: tuple[str, ...] = ()
    build_args: tuple[str, ...] = ()
    license: str | None = None
    sources: tuple[dict[str, str], ...] = ()
    source: str = ""

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.source.encode()).hexdigest()


def spec_for(integration: str) -> ContainerSpec | None:
    """The integration's container recipe, or None when it has none."""
    package = f"evoab.envs.{integration}"
    try:
        text = resources.files(package).joinpath("container.toml").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    data = tomllib.loads(text)
    # Identity is the recipe's content, so editing a comment never forces a rebuild.
    return spec_from(integration, package, data, json.dumps(data, sort_keys=True))


def adapter_spec(args, adapter) -> ContainerSpec:
    """The container recipe a deployment module prepares; every service has one."""
    custom = getattr(adapter, "container_spec", None)
    spec = custom(args) if custom is not None else spec_for(args.integration)
    if spec is None:
        raise ValueError(f"{args.integration} has no container recipe")
    return spec


def spec_from(integration: str, package: str, data: dict, source: str) -> ContainerSpec:
    """Validate one container recipe table; ``source`` is the text its identity hashes."""
    if sum(bool(data.get(key)) for key in ("image", "dockerfile", "build")) != 1:
        raise ValueError(f"{integration} container recipe must declare exactly one of image, dockerfile or build")
    if data.get("build") not in (None, "native"):
        raise ValueError(f"{integration} container recipe: build must be 'native'")
    return ContainerSpec(integration=integration, package=package, python=data["python"], native_root=data.get("native_root"),
                         image=data.get("image"), image_id=data.get("image_id"), dockerfile=data.get("dockerfile"), gpu=bool(data.get("gpu", True)),
                         prepare=tuple(data.get("prepare", ())),
                         environment={k: str(v) for k, v in data.get("environment", {}).items()},
                         build=data.get("build"), root=bool(data.get("root", False)),
                         constraints=tuple(data.get("constraints", ())), build_args=tuple(data.get("build_args", ())),
                         license=data.get("license"),
                         sources=tuple({k: str(v[k]) for k in ("path", "url", "ref")} for v in data.get("sources", ())),
                         source=source)


def is_container(python: str) -> bool:
    return str(python).startswith(PREFIX)


def runtime_python(image: str, inner: str, *, root: bool = False, mounts=(), code: bool = False) -> str:
    """Encode a container interpreter: ``docker://IMAGE#PYTHON[|root][|code][|/mount...]``."""
    return (f"{PREFIX}{image}#{inner}" + ("|root" if root else "") + ("|code" if code else "")
            + "".join(f"|{m}" for m in mounts))


def parse(python: str) -> tuple[str, str]:
    image, _, rest = str(python)[len(PREFIX):].partition("#")
    return image, rest.split("|")[0]


def options(python: str) -> tuple[bool, list[str]]:
    """Whether a container interpreter runs as root, and its host paths to mount."""
    parts = str(python)[len(PREFIX):].partition("#")[2].split("|")[1:]
    return "root" in parts, [part for part in parts if part.startswith("/")]


def mounts_code(python: str) -> bool:
    """Whether a container interpreter runs the host's mounted ``evoab`` package."""
    return "code" in str(python)[len(PREFIX):].partition("#")[2].split("|")[1:]


def code_mounts() -> list[tuple[str, str]]:
    """(host, container) pairs that make the running ``evoab`` importable at ``CODE_ROOT``.

    The package directory and its distribution metadata are mounted, so the
    container imports exactly the host's code and reports the host's version.
    """
    from importlib import metadata
    import evoab
    pairs = [(str(Path(evoab.__file__).resolve().parent), f"{CODE_ROOT}/evoab")]
    for name in ("evoab", "evoab-runtime"):
        try:
            info = Path(str(getattr(metadata.distribution(name), "_path", "")))
        except metadata.PackageNotFoundError:
            continue
        if info.name.endswith(".dist-info") and info.is_dir():
            pairs.append((str(info.resolve()), f"{CODE_ROOT}/{info.name}"))
        break
    return pairs


def docker() -> str:
    path = shutil.which("docker")
    if path is None:
        raise RuntimeError("Docker is not installed or not on PATH")
    return path


def run_prefix(image: str, *, gpu: bool | int, environment: dict[str, str], mounts=(), name: str | None = None,
               interactive: bool = False, root: bool = False, workdir: str | None = None,
               code: bool = False) -> list[str]:
    """``docker run`` for one EvoAB process: host network, host user unless root, same-path mounts.

    ``code`` mounts the host's ``evoab`` package read-only at ``CODE_ROOT``.
    """
    command = [docker(), "run", "--rm", "--init", "--network", "host", "--ipc", "host"]
    if not root:
        command += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
        # The host uid has no passwd entry in the image; getpass.getuser() (torch caches) reads USER first.
        command += ["-e", f"USER={os.environ.get('USER') or 'evoab'}"]
        # Image checkouts are root-owned; simulators that inspect their own git state
        # (CALVIN records its diff) would otherwise fail git's ownership check.
        command += ["-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory", "-e", "GIT_CONFIG_VALUE_0=*"]
    if workdir:
        command += ["-w", workdir]
    if gpu is not False:
        command += ["--runtime=nvidia", "--gpus", "all" if gpu is True else f"device={gpu}"]
    if name:
        command += ["--name", name]
    if interactive:
        command.append("-i")
    for path in mounts:
        resolved = str(Path(path).resolve())
        command += ["-v", f"{resolved}:{resolved}"]
    if code:
        for host, inside in code_mounts():
            command += ["-v", f"{host}:{inside}:ro"]
    for key, value in environment.items():
        if key in ("CUDA_VISIBLE_DEVICES", "PYTHONNOUSERSITE"):
            continue  # the container sees only its own GPU; the image owns its site-packages
        command += ["-e", f"{key}={value}"]
    return command + [image]


def python_command(python: str, arguments, *, environment=None, gpu: bool = False) -> list[str]:
    """A command running ``<python> <arguments>`` inside a container runtime."""
    if not is_container(python):
        raise ValueError("Runtimes run only in Docker containers; rerun setup to build this runtime's image")
    image, inner = parse(python)
    root, mounts = options(python)
    return run_prefix(image, gpu=gpu, environment=environment or {}, mounts=mounts, root=root,
                      code=mounts_code(python)) + [inner, *map(str, arguments)]


def wrap_plan(plan, runtime, *, gpu_index: int, name: str):
    """Run a launch plan (written for the image's interpreter) inside the runtime's image."""
    from .config import LaunchPlan
    image, inner = parse(runtime.python)
    if plan.command[0] != runtime.python:
        raise ValueError("A container launch plan must start with the runtime interpreter")
    gpu = gpu_index if runtime.data.get("container", {}).get("gpu", True) else False
    root, extra = options(runtime.python)
    mounts = ([plan.cwd] if plan.cwd else []) + extra
    environment = {**runtime.environment, **plan.environment}
    command = run_prefix(image, gpu=gpu, environment=environment, mounts=mounts, name=name, root=root,
                         code=mounts_code(runtime.python)) + [inner, *plan.command[1:]]
    # The docker client itself needs the host proxy for nothing; keep the host environment minimal.
    return LaunchPlan(tuple(command), plan.url, plan.expected, {}, plan.cwd)


def hand_back(python: str, path) -> None:
    """Give files a root container wrote under ``path`` back to the host user."""
    root, _ = options(python)
    if not root or not shutil.which("docker"):
        return
    image, _ = parse(python)
    resolved = str(Path(path).resolve())
    subprocess.run([docker(), "run", "--rm", "-v", f"{resolved}:{resolved}", "--entrypoint", "chown", image,
                    "-R", f"{os.getuid()}:{os.getgid()}", resolved], capture_output=True, timeout=600)


def remove(name: str) -> None:
    """Best-effort removal of a launch's container if its client exited without stopping it."""
    if shutil.which("docker"):
        subprocess.run([docker(), "rm", "-f", name], capture_output=True, timeout=60)


_PROGRESS = re.compile(r"\x1b\[|\d+%\s*$|#{3,}|(Downloading|Extracting|Waiting|Verifying Checksum)\s*$")


def _stream(command, report, *, cwd=None, environment=None, stdin=None) -> None:
    if report is not None:
        report("$ " + " ".join(shlex.quote(str(part)) for part in command[:6]) + (" …" if len(command) > 6 else ""))
    process = subprocess.Popen([str(c) for c in command], cwd=cwd, env={**os.environ, **(environment or {})},
                               stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last = None
    for line in process.stdout:
        line = line.rstrip()
        # Layer and package progress bars repeat endlessly; report completed steps only.
        if report is not None and line and line != last and not _PROGRESS.search(line):
            report(line[-300:])
        last = line
    if process.wait():
        raise RuntimeError(f"{Path(str(command[0])).name} {command[1]} failed with exit code {process.returncode}")


def _build_args() -> list[str]:
    """Builds use the host network; a proxy is forwarded only when EVOAB_CONTAINER_BUILD_PROXY=1."""
    arguments = ["--network", "host"]
    if os.environ.get("EVOAB_CONTAINER_BUILD_PROXY") == "1":
        for key in PROXY_VARIABLES:
            if os.environ.get(key):
                arguments += ["--build-arg", f"{key}={os.environ[key]}"]
    for key in BUILD_VARIABLES:
        if os.environ.get(key):
            arguments += ["--build-arg", f"{key}={os.environ[key]}"]
    return arguments


def _fetch_sources(spec: ContainerSpec, report) -> Path:
    """Shallow-fetch the recipe's pinned git sources on the host; return the build context.

    Sources are cached per integration and reused while their pinned ref is unchanged;
    ``.git`` is dropped so the context holds only the pinned tree.
    """
    from .runtime import cache_root
    context = cache_root() / "container-sources" / spec.integration
    for source in spec.sources:
        target = context / source["path"]
        stamp = context / f".{source['path'].replace('/', '_')}.ref"
        if target.is_dir() and stamp.is_file() and stamp.read_text().strip() == source["ref"]:
            continue
        if report is not None:
            report(f"Fetching {source['url']} at {source['ref']}")
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        for command in (["git", "init", "-q"], ["git", "remote", "add", "origin", source["url"]],
                        ["git", "fetch", "-q", "--depth", "1", "origin", source["ref"]],
                        ["git", "checkout", "-q", "FETCH_HEAD"]):
            _stream(command, report, cwd=target)
        shutil.rmtree(target / ".git")
        stamp.write_text(source["ref"] + "\n")
    return context


def build_runtime_image(spec: ContainerSpec, wheel: Path | None, version: str, report, *,
                        context: Path | None = None, constraints=(), local_image: str | None = None) -> str:
    """Pull or build the upstream image, then build EvoAB's local layer on it; return its tag."""
    if local_image:
        # Docker builders resolve FROM by name; give the verified content ID a local tag.
        base = f"evoab/imported:{local_image.removeprefix('sha256:')}"
        _stream([docker(), "tag", local_image, base], report)
    elif spec.image:
        present = subprocess.run([docker(), "image", "inspect", spec.image], capture_output=True).returncode == 0
        if not present:
            _stream([docker(), "pull", spec.image], report)
        base = spec.image
    elif spec.build == "native":
        if context is None or not (Path(context) / "Dockerfile").is_file():
            raise ValueError("The native checkout has no Dockerfile to build")
        revision = subprocess.run(["git", "-C", str(context), "rev-parse", "HEAD"], capture_output=True,
                                  text=True).stdout.strip()[:12] or spec.digest[:12]
        base = f"evoab/{spec.integration.replace('_', '-')}-base:{revision}"
        forwarded = [arg for key in spec.build_args if os.environ.get(key)
                     for arg in ("--build-arg", f"{key}={os.environ[key]}")]
        _stream([docker(), "build", "-t", base, *_build_args(), *forwarded, str(context)], report)
    else:
        recipe = Path(str(resources.files(spec.package or f"evoab.envs.{spec.integration}")
                          .joinpath(spec.dockerfile)))
        base = f"evoab/{spec.integration.replace('_', '-')}-base:{spec.digest[:12]}"
        build_context = _fetch_sources(spec, report) if spec.sources else recipe.parent
        _stream([docker(), "build", "-t", base, "-f", recipe, *_build_args(), build_context], report)
    requirements = None if wheel else runtime_requirements()
    # The tag names exactly what the layer contains: the recipe, its base, the runtime
    # dependencies and pins. EvoAB's code is mounted, so code changes reuse the image.
    contents = (hashlib.sha256(wheel.read_bytes()).hexdigest() if wheel
                else "requirements:" + "\n".join(requirements))
    identity = spec.digest + contents + base + "\n".join(spec.constraints or constraints)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    tag = f"evoab/{spec.integration.replace('_', '-')}:{f'{version}-' if wheel else 'env-'}{digest}"
    if not wheel and subprocess.run([docker(), "image", "inspect", tag], capture_output=True).returncode == 0:
        if report is not None:
            report(f"Reusing {tag}: recipe and dependencies are unchanged")
        return tag
    with tempfile.TemporaryDirectory(prefix="evoab-image-") as context:
        context_path = Path(context)
        python = shlex.quote(spec.python)
        place = []
        if wheel:
            shutil.copyfile(wheel, context_path / wheel.name)
            install = f"/tmp/evoab-wheel/{wheel.name}"
            copy = f"COPY {wheel.name} /tmp/evoab-wheel/\n"
        else:
            # No runtime wheel: install only EvoAB's runtime dependencies. The code and its
            # metadata are mounted at CODE_ROOT on every run (code_mounts), found through a .pth.
            (context_path / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
            install = "-r /tmp/evoab-wheel/requirements.txt"
            copy = "COPY requirements.txt /tmp/evoab-wheel/\n"
            place = [f"RUN {python} -c \"import site, pathlib; pathlib.Path(site.getsitepackages()[0], "
                     f"'evoab-runtime.pth').write_text('{CODE_ROOT}\\n')\""]
        uv_pins = pip_pins = ""
        constraints = tuple(spec.constraints or constraints)
        if constraints:
            (context_path / "constraints.txt").write_text("\n".join(constraints) + "\n", encoding="utf-8")
            copy += "COPY constraints.txt /tmp/evoab-wheel/\n"
            uv_pins, pip_pins = " --constraint /tmp/evoab-wheel/constraints.txt", " -c /tmp/evoab-wheel/constraints.txt"
        lines = [f"FROM {base}", *[f"ARG {key}" for key in BUILD_VARIABLES], copy.rstrip(),
                 f"RUN (command -v uv >/dev/null && uv pip install --python {python} --no-cache{uv_pins} {install}) "
                 f"|| {python} -m pip install --no-cache-dir{pip_pins} {install}",
                 *place,
                 "RUN rm -rf /tmp/evoab-wheel",
                 *[f"RUN {command}" for command in (() if local_image and local_image != spec.image_id else spec.prepare)],
                 "ENTRYPOINT []", "CMD []", f'LABEL org.evoab.integration="{spec.integration}" '
                 + (f'org.evoab.version="{version}"' if wheel else 'org.evoab.code="mounted"')]
        (context_path / "Dockerfile").write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")
        _stream([docker(), "build", "-t", tag, *_build_args(), context_path], report)
    return tag


#: Planner-only dependencies the service runtime never needs (pyproject [tool.evoab.runtime]).
RUNTIME_EXCLUDED: tuple[str, ...] = ()


def runtime_requirements() -> list[str]:
    """The running ``evoab`` distribution's runtime requirements (no extras), sorted."""
    from importlib import metadata
    requirements = []
    for requirement in metadata.requires("evoab") or []:
        name = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower()
        if "extra ==" in requirement or name in RUNTIME_EXCLUDED:
            continue
        requirements.append(requirement)
    return sorted(requirements)


def query(python: str, code: str, *arguments, environment=None, timeout=600):
    """``query_python`` inside a container runtime: run code, parse the last stdout line as JSON."""
    command = python_command(python, ["-c", code, *arguments], environment=environment)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd="/")
    if result.returncode:
        raise RuntimeError(f"Runtime query failed: {result.stderr[-3000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


_IMAGE_DATA = (
    "import json, sys; from importlib import import_module; from pathlib import Path; "
    "from types import SimpleNamespace; "
    "args = SimpleNamespace(**json.loads(sys.argv[2])); "
    "args.native_root = Path(args.native_root) if args.native_root else None; "
    "print(json.dumps(import_module(sys.argv[1]).image_data(args)))"
)


def prepare(args, adapter, *, report=print):
    """Prepare the container runtime ``adapter`` deploys and publish its record."""
    import time
    from dataclasses import replace

    from evoab import __version__
    from evoab.records.artifacts.run import write_json_atomic
    from .config import Runtime
    from .runtime import publish_runtime, runtime_lock, runtime_root

    spec = adapter_spec(args, adapter)
    if spec.license and getattr(args, "accept_license", None) != spec.license:
        raise ValueError(f"Building {spec.integration} requires accepting {spec.license}: "
                         f"rerun with --accept-license '{spec.license}'")
    recipe = adapter.recipe(args)
    root = runtime_root(args, recipe)
    started = time.time()
    imported = getattr(args, "image_archive", None)
    with runtime_lock(root, args.setup_timeout_s):
        previous = {}
        published = False
        try:
            if imported:
                for name in ("validation.json", "runtime.json"):
                    path = root / name
                    previous[name] = (path.stat(), path.read_bytes()) if path.is_file() else None
            local_image = None
            if imported:
                from .image_import import load_archive
                local_image = load_archive(imported, spec, args, report)
            host = getattr(adapter, "container_data", None)
            host_data = host(args, root, report) if host else None
            mounts = list((host_data or {}).pop("mounts", []))
            environment = {**spec.environment, **(host_data or {}).pop("environment", {})}
            # The runtime wheel must not move pins the simulator stack depends on.
            tag = build_runtime_image(spec, args.wheel, __version__, report,
                                      context=Path(host_data["native_root"]) if spec.build == "native" else None,
                                      constraints=recipe.settings.get("constraints", ()), local_image=local_image)
            if imported:
                result = subprocess.run([docker(), "image", "inspect", tag, "--format", "{{.Id}}"],
                                        capture_output=True, text=True)
                if result.returncode or not result.stdout.strip().startswith("sha256:"):
                    raise RuntimeError(f"Cannot inspect imported runtime image: {result.stderr.strip()}")
                tag = result.stdout.strip()
            python = runtime_python(tag, spec.python, root=spec.root, mounts=mounts, code=args.wheel is None)
            if host_data is not None:
                data = host_data
                finalize = getattr(adapter, "container_finalize", None)
                if finalize is not None:
                    finalize(python, data, report)
                for path in mounts:
                    hand_back(python, path)
            else:
                inputs = {"native_root": spec.native_root, "runtime_profile": getattr(args, "runtime_profile", None),
                          "command": "setup"}
                data = query(python, _IMAGE_DATA, adapter.__name__, json.dumps(inputs),
                             environment=environment)
            runtime = Runtime(recipe.name, python, recipe.digest, __version__, root,
                              {**data, "container": {"image": tag, "upstream": spec.image or spec.dockerfile or "native",
                                                     "gpu": spec.gpu, "root": spec.root, "mounts": mounts,
                                                     "recipe_digest": spec.digest, "imported_image": local_image, "archive": imported}},
                              environment)
            catalog = getattr(adapter, "catalog_tasks", None)
            if catalog is not None:
                runtime = replace(runtime, data={**runtime.data, "tasks": catalog(runtime.data)})
            validation = adapter.verify(runtime)
            validation.update(preparation="container", image=tag, upstream=spec.image or spec.dockerfile or "native",
                              elapsed_s=time.time() - started, gpu_verified=False)
            publish_runtime(runtime, validation)
            published = True
            if imported:
                from .setup import check_launch
                check_launch(args, adapter, runtime, report=report)
                validation["launch_verified"] = True
                publish_runtime(runtime, validation)
        except BaseException as error:
            if imported and published:
                for name, record in previous.items():
                    if record is None:
                        (root / name).unlink(missing_ok=True)
                    else:
                        stat, content = record
                        with tempfile.NamedTemporaryFile(dir=root, delete=False) as restored:
                            restored.write(content)
                        os.utime(restored.name, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                        os.replace(restored.name, root / name)
            write_json_atomic(root / "setup_failure.json", dict(error=str(error), type=type(error).__name__,
                                                               elapsed_s=time.time() - started))
            raise
    if report is not None:
        report(f"Container runtime ready: {tag} ({len(runtime.data.get('tasks') or {})} tasks)")
    return runtime


# -- uninstall -----------------------------------------------------------------

#: Records that make a container runtime prepared; everything else in its root stays
#: (host assets, launch checks, previews and the setup lock's inode belong to the host).
DEPLOYMENT_RECORDS = ("runtime.json", "validation.json", "setup_failure.json")
#: Label every local EvoAB runtime image carries; see ``build_runtime_image``.
EVOAB_LABEL = "org.evoab.integration"
_IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}")
_ABSENT_IMAGE = re.compile(r"no such (?:image|object)", re.IGNORECASE)


class ContainerError(RuntimeError):
    """Docker could not finish the operation; every recorded deployment stays as it was."""


class RuntimeInUse(ContainerError):
    """Containers still reference the runtime image, so EvoAB will not remove it."""

    def __init__(self, image: str, containers: list[dict[str, str]]):
        names = ", ".join(item["name"] for item in containers[:3])
        more = f" and {len(containers) - 3} more" if len(containers) > 3 else ""
        super().__init__(f"{image} is used by container {names}{more}; stop or remove it first")
        self.image = image
        self.containers = containers


def _docker_run(*arguments: str, timeout: float = 60):
    """Run one Docker CLI command; a missing client is a failure, not an absent image.

    The call is bounded: a hung daemon must fail this request instead of blocking the
    Studio admission lock that also serializes launches and deploys.
    """
    try:
        command = docker()
    except RuntimeError as error:
        raise ContainerError(str(error)) from error
    try:
        return subprocess.run([command, *arguments], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ContainerError(f"Docker did not answer within {timeout:.0f}s: {arguments[0]} {arguments[1]}") from error


def _detail(result) -> str:
    text = ((result.stderr or "") + (result.stdout or "")).strip()
    return text.splitlines()[-1] if text else "no output"


def image_record(reference: str) -> dict | None:
    """A local image's content ID and labels, or None when Docker reports it absent.

    Only Docker's own "no such image/object" answer counts as absence; a daemon or
    client error raises instead, so cleanup can never mistake one for a removed image.
    """
    result = _docker_run("image", "inspect", reference, "--format", "{{.Id}} {{json .Config.Labels}}")
    if result.returncode:
        if _ABSENT_IMAGE.search(result.stderr or ""):
            return None
        raise ContainerError(f"Docker cannot inspect {reference}: {_detail(result)}")
    identifier, _, raw = (result.stdout or "").strip().partition(" ")
    if not identifier.startswith("sha256:"):
        raise ContainerError(f"Docker returned no content ID for {reference}")
    try:
        labels = json.loads(raw or "{}")
    except ValueError as error:
        raise ContainerError(f"Docker returned unreadable labels for {reference}") from error
    return {"id": identifier, "labels": labels if isinstance(labels, dict) else {}}


def image_containers(identifier: str) -> list[dict[str, str]]:
    """Every container that references one image, including stopped and created ones."""
    result = _docker_run("ps", "--all", "--filter", f"ancestor={identifier}",
                         "--format", "{{.ID}}\t{{.Names}}\t{{.State}}")
    if result.returncode:
        raise ContainerError(f"Docker cannot list containers: {_detail(result)}")
    rows = []
    for line in (result.stdout or "").splitlines():
        fields = (line.split("\t") + ["", "", ""])[:3]
        if fields[0]:
            rows.append({"id": fields[0], "name": fields[1], "state": fields[2]})
    return rows


def remove_image(reference: str) -> str:
    """Remove one image reference without ``--force``; "removed", "missing", or raise."""
    result = _docker_run("image", "rm", reference)
    if result.returncode:
        if _ABSENT_IMAGE.search(result.stderr or ""):
            return "missing"
        raise ContainerError(f"Docker could not remove {reference}: {_detail(result)}")
    return "removed"


def forget_runtime(root: Path) -> list[str]:
    """Unpublish one deployment's records; assets, checks, previews and the lock stay."""
    removed = []
    for name in DEPLOYMENT_RECORDS:
        path = Path(root) / name
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(name)
    return removed


def _recorded_image(root: Path) -> str:
    """The image one runtime record publishes, cross-checked against its interpreter."""
    data = json.loads((Path(root) / "runtime.json").read_text(encoding="utf-8"))
    payload = data.get("data") if isinstance(data, dict) else None
    container = (payload or {}).get("container") if isinstance(payload, dict) else None
    image = container.get("image") if isinstance(container, dict) else None
    python = str(data.get("python") or "") if isinstance(data, dict) else ""
    recorded = parse(python)[0] if is_container(python) else None
    if image and recorded and image != recorded:
        raise ContainerError("Runtime record and interpreter name different images; rerun setup")
    reference = image or recorded
    if not isinstance(reference, str) or _IMAGE_REFERENCE.fullmatch(reference) is None:
        raise ContainerError("Runtime record has no removable image reference")
    return reference


def uninstall_runtime(root: Path, integration: str, *, others=(), timeout: float = 5.0) -> dict:
    """Remove one prepared deployment's image and records, keeping the runtime directory.

    ``root`` comes from EvoAB's own cache scan and ``others`` are (deployment, image)
    pairs of other records that must keep working, so a shared image is left alone.
    Setup exclusion reuses the runtime lock. Containers are never stopped: an image
    still referenced by one is refused, and Docker's non-force removal refuses images
    a concurrently started container takes after that check.
    """
    from .runtime import runtime_lock

    root = Path(root)
    if not (root / "runtime.json").is_file():
        raise ValueError("This deployment is no longer recorded")
    with runtime_lock(root, timeout):
        reference = _recorded_image(root)
        image = image_record(reference)
        shared: list[str] = []
        if image is None:
            action = "missing"
        else:
            used = image_containers(image["id"])
            if used:
                raise RuntimeInUse(reference, used)
            if image["labels"].get(EVOAB_LABEL) != integration:
                raise ContainerError(f"{reference} is not {integration}'s EvoAB runtime image; refusing to remove it")
            shared = sorted({name for name, other in others
                             if isinstance(other, str) and _IMAGE_REFERENCE.fullmatch(other)
                             and (image_record(other) or {}).get("id") == image["id"]})
            action = "shared" if shared else remove_image(reference)
        return {"image": reference, "image_id": image["id"] if image else None, "image_action": action,
                "shared_with": shared, "records": forget_runtime(root)}
