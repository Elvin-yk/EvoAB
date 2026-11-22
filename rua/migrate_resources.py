"""Move a stopped evaluation checkout to physical RUA resource directories (Linux)."""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from shlex import quote as shlex_quote
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

if __name__ == "__main__":
    sys.dont_write_bytecode = True

from link_resources import RESOURCE_NAMES, check_git as check_resource_index, preflight_links


RUA_DIST = "uni-robot-agent"
COPIED = tuple(name for name in RESOURCE_NAMES if name != "venv")
PACKAGE_INVENTORY = r"""
import importlib.metadata as m, importlib.util, json, sys
from pathlib import Path
items = []
for dist in m.distributions():
    origin = json.loads(dist.read_text('direct_url.json') or '{}')
    items.append([dist.metadata['Name'], dist.version, origin,
                  str(Path(dist.locate_file('')).resolve())])
spec = importlib.util.find_spec('uni_robot_agent')
print(json.dumps({'python': sys.version.split()[0], 'base': sys._base_executable,
                  'prefix': sys.prefix, 'packages': items, 'rua': spec.origin if spec else None}))
"""


def command(args, *, env=None, timeout=45, cwd=None, private=False):
    args = list(map(str, args))
    result = subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)
    if result.returncode:
        detail = "" if private else f"\n{result.stderr.strip()[-2400:]}"
        raise ValueError(f"{Path(args[0]).name} {args[1]} exited with {result.returncode}.{detail}")
    return result.stdout


def exists(path):
    return os.path.lexists(path)


def within(path, root):
    return path == root or root in path.parents


def absolute(path):
    return Path(os.path.abspath(path))


def read_json(path):
    return json.loads(path.read_text())


def atomic_write(path, data, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def rename(source, target):
    """Use Linux no-replace rename for both publication and backup."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno), f"{source} -> {target}")


def regular(path):
    return path.is_file() and not path.is_symlink()


def check_git(repo):
    check_resource_index(repo)
    command(["git", "-C", repo, "check-ignore", "-q", "--", "rua/.migration"])
    if command(["git", "-C", repo, "ls-files", "--cached", "--", "rua/.migration"]):
        raise ValueError("Migration journal files must be ignored by Git")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root):
    """Walk physical directories only, including extensionless setup files."""
    with os.scandir(root) as directory:
        for item in directory:
            path = Path(item.path)
            info = item.stat(follow_symlinks=False)
            yield path, info
            if stat.S_ISDIR(info.st_mode):
                yield from files(path)


def tree_stamp(root):
    stamp = hashlib.sha256()
    for path, info in sorted(files(root), key=lambda item: item[0]):
        stamp.update(os.fsencode(path.relative_to(root)))
        stamp.update(f"\0{info.st_mode}:{info.st_size}:{info.st_mtime_ns}\0".encode())
        if stat.S_ISLNK(info.st_mode):
            stamp.update(os.fsencode(path.readlink()) + b"\0")
    return stamp.hexdigest()


def mounted_below(roots):
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        point = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), line.split()[4]))
        if any(within(point, root) for root in roots):
            raise ValueError(f"Resource directory contains a mount point: {point}")


def package_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def inventory(python):
    return json.loads(command([python, "-I", "-B", "-c", PACKAGE_INVENTORY], private=True))


def package_versions(info, editable_source=None):
    versions = {}
    for name, version, _, location in info["packages"]:
        name = package_name(name)
        in_venv = within(Path(location), Path(info["prefix"]).resolve())
        legacy_editable = name == RUA_DIST and editable_source and within(Path(location), editable_source)
        if (not in_venv and not legacy_editable) or name in versions:
            raise ValueError(f"External or duplicate Python distribution: {name}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not re.fullmatch(r"[a-zA-Z0-9.!+_-]+", version):
            raise ValueError("Invalid installed package name or version")
        versions[name] = version
    return versions


def build_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PIP_", "PYTHON"))}
    return {**env, "PIP_CONFIG_FILE": os.devnull, "PYTHONDONTWRITEBYTECODE": "1"}


class Migration:
    def __init__(self, repo, args):
        self.repo = repo
        self.directory = repo / "rua"
        self.args = args
        self.old = {name: repo.parent / old for name, old in RESOURCE_NAMES.items()}
        self.new = {name: self.directory / name for name in self.old}
        self.paths = [(str(self.old[name]), str(self.new[name])) for name in self.old]
        self.pattern = re.compile(rb"(?:(?<![\w./-])|(?<=file://))(?:"
                                  + b"|".join(re.escape(os.fsencode(source)) for source, _ in self.paths)
                                  + rb")(?=$|[/\\\s:|;,\x00\"'\]\)}?#])")
        self.replacements = {os.fsencode(old): os.fsencode(new) for old, new in self.paths}
        self.control = self.directory / ".migration"
        self.state_file = self.control / "state.json"
        if self.control.is_symlink() or (exists(self.control) and self.control.stat().st_uid != os.getuid()):
            raise ValueError(f"Migration journal is not owned by this account: {self.control}")
        if self.state_file.is_symlink():
            raise ValueError("Migration state must be a regular file")
        self.state = read_json(self.state_file) if self.state_file.is_file() else {}
        if self.state and self.state["repo"] != str(repo):
            raise ValueError("Migration state belongs to another checkout path")

    @property
    def work(self):
        return self.control / self.state["id"]

    @property
    def backup(self):
        return self.repo.parent / f".rua-backup-{self.state['id']}"

    def save(self, event, **details):
        row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        self.state["events"].append(row)
        atomic_write(self.state_file, (json.dumps(self.state, indent=2) + "\n").encode())
        print(event, *details.values(), flush=True)

    def rebase(self, data):
        return self.pattern.sub(lambda match: self.replacements[match.group()], data)

    def mapped(self, path):
        for name, old in self.old.items():
            if within(path, old):
                return self.new[name] / path.relative_to(old)
        return path

    def managed(self, path):
        return any(within(path, root) for root in (*self.old.values(), *self.new.values()))

    def active_manifest(self, cache_dir):
        from types import SimpleNamespace
        from evoab.integrations.robodojo.deployment import recipe
        from evoab.services.deployment.container import container_root
        from evoab.services.deployment.runtime import runtime_root

        args = SimpleNamespace(cache_dir=cache_dir)
        root = runtime_root(args, recipe(args)) if self.args.native else container_root(args, recipe(args))
        return root / "runtime.json"

    def load_old_environment(self, *, report=True):
        info = inventory(self.old["venv"] / "bin/python")
        if Path(info["prefix"]).resolve() != self.old["venv"]:
            raise ValueError("Old RUA interpreter is not using the expected virtual environment")
        base = Path(info["base"]).resolve(strict=True)
        if self.mapped(base) != base or not base.is_file():
            raise ValueError("Old RUA virtual environment needs a base Python outside the five resources")
        if tuple(map(int, info["python"].split(".")[:2])) < (3, 11):
            raise ValueError("RUA requires Python 3.11 or later")
        versions = package_versions(info, self.old["source"])
        if RUA_DIST not in versions:
            raise ValueError("Old RUA environment has no uni-robot-agent distribution")
        if not info["rua"]:
            raise ValueError("Old RUA distribution cannot be imported")
        imported = Path(info["rua"]).resolve(strict=True)
        if not within(imported, self.old["source"]):
            if not within(imported, self.old["venv"]):
                raise ValueError(f"Old RUA imports from an unmanaged location: {imported}")
            self.check_installed_source(imported.parent)
        for raw_name, version, origin, _ in sorted(info["packages"]):
            name = package_name(raw_name)
            if not origin:
                if report:
                    print(f"  {name}=={version} (package index or wheel)")
                continue
            url = urlsplit(origin.get("url", ""))
            if name == RUA_DIST and url.scheme == "file" and url.netloc in ("", "localhost"):
                source = Path(unquote(url.path)).resolve()
                if source == self.old["source"]:
                    if report:
                        print(f"  {name}=={version} (local RUA source)")
                    continue
                raise ValueError(f"{name} was installed from a different source tree; reconcile it before migrating")
            if "dir_info" in origin or "vcs_info" in origin:
                raise ValueError(f"{name} has a local directory or VCS origin; supply a reviewed pinned wheel and reinstall"
                                 " it in a separate equivalent source environment before migrating")
            if report:
                print(f"  {name}=={version} (direct archive; supply its original wheel)")
        return info["python"], str(base), versions

    def check_installed_source(self, installed):
        source = self.old["source"] / "uni_robot_agent"
        for path, info in files(installed):
            if stat.S_ISREG(info.st_mode) and path.suffix != ".pyc":
                original = source / path.relative_to(installed)
                if not original.is_file() or file_digest(path) != file_digest(original):
                    raise ValueError(f"Installed RUA differs from sibling source: {path}")

    def cache_plan(self):
        from evoab.services.deployment.runtime import cache_root

        active = cache_root(self.args).resolve()
        new_cache = self.mapped(active)
        selected = self.active_manifest(active)
        manifests = {selected, *(path.expanduser().absolute() for path in self.args.manifest)}
        for manifest in sorted(manifests):
            if not regular(manifest):
                raise ValueError(f"Prepared runtime record is missing or is a symlink: {manifest}")
            if manifest.parent.resolve() != manifest.parent:
                raise ValueError(f"Use the physical path of the manifest: {manifest}")
            record = read_json(manifest)
            print(f"Runtime manifest {manifest} -> {self.mapped(manifest)}")
            if manifest == selected:
                self.runtime_directories(record["data"])
            for key in ("native_root", "assets_dir", "asset_source_dir"):
                if value := record.get("data", {}).get(key):
                    print(f"  {key}: {value} -> {self.mapped(Path(value))}")
            runtime_python = record["python"]
            if runtime_python.startswith("docker://"):
                from evoab.services.deployment.container import options, parse

                _, mounts = options(runtime_python)
                _, inner = parse(runtime_python)
                print(f"  Docker interpreter: {inner}")
                for mount in mounts:
                    if manifest == selected and not Path(mount).exists():
                        raise ValueError(f"Required RoboDojo Docker host mount is missing: {mount}")
                    print(f"  Docker host mount: {mount} -> {self.mapped(Path(mount))}")
            else:
                if manifest == selected and not Path(runtime_python).is_file():
                    raise ValueError(f"Required RoboDojo native interpreter is missing: {runtime_python}")
                print(f"  Native interpreter: {runtime_python} -> {self.mapped(Path(runtime_python))}")
        return active, new_cache, selected, sorted(manifests)

    def audit(self):
        check_git(self.repo)
        if self.state and self.state["phase"] != "rolled-back":
            print(f"Migration phase: {self.state['phase']}, selected manifest: {self.state['selected']}")
            print(f"Journal: {self.state_file}")
            return
        self.plan()
        for name in COPIED:
            self.inspect_paths(name)
        self.check_idle()

    def plan(self):
        for name, old in self.old.items():
            if not old.is_dir() or old.is_symlink():
                raise ValueError(f"Original resource must be a physical directory: {old}")
            print(f"{old} -> {self.new[name]} (filesystem {old.stat().st_dev} -> {self.directory.stat().st_dev})")
        mounted_below(self.old.values())
        preflight_links({self.new[name]: old for name, old in self.old.items()})
        if not (self.old["source"] / "uni_robot_agent" / "__init__.py").is_file():
            raise ValueError("Original RUA source package is missing")
        for program in ("rsync", "git"):
            if not shutil.which(program):
                raise ValueError(f"Install {program} before migrating")
        required = 0
        for old in self.old.values():
            seen = set()
            for path, info in files(old):
                if stat.S_ISREG(info.st_mode):
                    key = (info.st_dev, info.st_ino)
                    if key not in seen:
                        # The venv is rebuilt from wheels. Its old logical file total is an estimate.
                        required += info.st_size if old == self.old["venv"] else info.st_blocks * 512
                        seen.add(key)
                elif not (stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError(f"Cannot safely copy special file: {path}")
        free = shutil.disk_usage(self.directory).free
        print(f"Copy and rebuild estimate: {required:,} bytes, available: {free:,} bytes")
        if free < required:
            raise ValueError("Insufficient disk space to keep original resources and build the new layout")
        version, base, packages = self.load_old_environment()
        print(f"RUA Python {version}, base: {base}")
        for path, info in files(self.old["venv"]):
            if stat.S_ISREG(info.st_mode) and self.venv_metadata(path, self.old["venv"]):
                if self.contains_legacy_path(path):
                    print(f"  Legacy path in old Python metadata or executable: {path}")
        active, new_cache, selected, manifests = self.cache_plan()
        print(f"Deployment cache: {active} -> {new_cache}")
        for name in ("EVOAB_RUA_PYTHON", "EVOAB_CACHE_DIR"):
            if value := os.environ.get(name):
                print(f"{name} currently resolves to {Path(value).expanduser().absolute()}")
        return {
            "repo": str(self.repo), "id": uuid4().hex, "phase": "copying", "events": [],
            "python": version, "base": base, "packages": packages,
            "links": {name: str(path.readlink()) if path.is_symlink() else None for name, path in self.new.items()},
            "ready": [], "published": [], "external": {}, "originals": {},
            "old_cache": str(active), "cache": str(new_cache), "selected": str(self.mapped(selected)),
            "manifests": list(map(str, manifests)), "native": self.args.native,
        }

    def check_idle(self):
        """Inspect this user's processes and bind mounts in the local Docker daemon."""
        roots = [*self.old.values(), *self.new.values()]
        if self.state:
            roots.extend((self.backup, Path(self.state["old_cache"]) / "robodojo",
                          Path(self.state["cache"]) / "robodojo"))
        affected = lambda value: value.startswith("/") and any(within(Path(value), root) for root in roots)
        # The invoking shells can legitimately refer to the migration's paths.
        ancestors = {os.getpid(), os.getppid()}
        busy = []
        for entry in Path("/proc").iterdir():
            argv = []
            try:
                if not entry.name.isdecimal() or int(entry.name) in ancestors or entry.stat().st_uid != os.getuid():
                    continue
                argv = os.fsdecode((entry / "cmdline").read_bytes()).split("\0")
                cwd = (entry / "cwd").readlink()
                locations = [str(cwd), str((entry / "exe").readlink())]
                locations += [argument.partition("=")[-1] for argument in argv]
                with (entry / "maps").open() as stream:
                    locations += [line.split(maxsplit=5)[-1].strip().removesuffix(" (deleted)") for line in stream]
                for descriptor in (entry / "fd").iterdir():
                    try:
                        locations.append(str(descriptor.readlink()).removesuffix(" (deleted)"))
                    except FileNotFoundError:
                        continue
                launch = any(token in {"studio", "uni_robot_agent", "evoab.integrations.robodojo.environment.server"}
                             or token.endswith("/agents/rua/worker.py") for token in argv)
                launch |= "robodojo" in argv and ("run" in argv or "deploy" in argv or "setup" in argv)
                if launch:
                    for item in os.fsdecode((entry / "environ").read_bytes()).split("\0"):
                        key, _, value = item.partition("=")
                        if key in {"VIRTUAL_ENV", "EVOAB_RUA_PYTHON", "EVOAB_CACHE_DIR", "PYTHONPATH"}:
                            locations.extend(value.split(":"))
                if any(affected(value) for value in locations) or (launch and within(cwd, self.repo)):
                    busy.append(f"PID {entry.name}")
            except (ProcessLookupError, FileNotFoundError):
                continue
            except PermissionError:
                if argv and any(affected(value) for value in argv):
                    busy.append(f"restricted PID {entry.name}")
        if shutil.which("docker"):
            host = os.environ.get("DOCKER_HOST") or command(
                ["docker", "context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}']
            ).strip()
            if not host.startswith("unix://"):
                raise ValueError("Select the local Unix-socket Docker context before migrating")
            containers = command(["docker", "ps", "-q"]).split()
            for container in containers:
                mounts = command(["docker", "inspect", "--format", "{{json .Mounts}}", container])
                for mount in json.loads(mounts):
                    if mount.get("Type") != "bind":
                        continue
                    path = Path(mount["Source"])
                    if any(within(path, root) or within(root, path) for root in roots):
                        busy.append(f"Docker container {container}")
                        break
        elif not (self.state.get("native") if self.state else self.args.native):
            raise ValueError("Docker CLI is required to check active container bind mounts")
        if busy:
            raise ValueError("Stop only your RUA/RoboDojo jobs and Studio before migrating: " + ", ".join(busy))

    def link_targets(self, path, link):
        """Resolve a link in its live layout, tolerating only missing external targets."""
        referent = path.parent / link
        target = absolute(referent)
        try:
            return target, referent.resolve(strict=True)
        except FileNotFoundError as error:
            # A link outside the tree can itself lead back to a missing managed resource.
            unresolved = referent.resolve(strict=False)
            if self.managed(target) or self.managed(unresolved):
                raise ValueError(f"Dangling internal resource symlink: {path} -> {link}") from error
            print(f"Warning: preserving dangling external symlink: {path} -> {link}", file=sys.stderr)
            return target, None

    def rebase_link(self, path, original, destination, *, audit=False):
        link = path.readlink()
        target, resolved = self.link_targets(original, link)
        # Preserve internal chains and external targets. Redirect external aliases into
        # the managed trees so a migrated link cannot still route through an old tree.
        mapped = self.mapped(target)
        if not self.managed(target) and resolved is not None and self.managed(resolved):
            mapped = self.mapped(resolved)
        if absolute(destination.parent / link) == mapped:
            return
        if link.is_absolute():
            replacement = mapped
        else:
            replacement = Path(os.path.relpath(mapped, destination.parent))
        if audit:
            print(f"  Cache/source symlink will be rebased: {original} -> {replacement}")
        else:
            path.unlink()
            path.symlink_to(replacement)

    def contains_legacy_path(self, path):
        overlap = max(map(len, self.replacements)) + 4
        with path.open("rb") as stream:
            data = stream.read(2**20)
            while data:
                more = stream.read(2**20)
                limit = len(data) - overlap if more else len(data)
                if any(old in data for old in self.replacements) and any(
                    match.start() < limit for match in self.pattern.finditer(data)
                ):
                    return True
                data = data[-overlap:] + more if more else b""
        return False

    def rebase_manifest(self, data):
        def visit(value):
            if isinstance(value, str):
                if value.startswith("docker://"):
                    from evoab.services.deployment.container import options, parse, runtime_python

                    image, inner = parse(value)
                    root, mounts = options(value)
                    if self.pattern.search(os.fsencode(inner)):
                        raise ValueError("Docker interpreter references a host path; inspect the prepared runtime")
                    return runtime_python(image, inner, root=root,
                                          mounts=[os.fsdecode(self.rebase(os.fsencode(item))) for item in mounts])
                return os.fsdecode(self.rebase(os.fsencode(value)))
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {key: visit(val) for key, val in value.items()}
            return value

        return (json.dumps(visit(json.loads(data)), indent=2) + "\n").encode()

    def inspect_paths(self, name, stage=None):
        root = stage or self.old[name]
        issues = []
        for path, info in files(root):
            relative = path.relative_to(root)
            if stat.S_ISLNK(info.st_mode):
                self.rebase_link(path, self.old[name] / relative, self.new[name] / relative, audit=stage is None)
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if path.suffix in {".pyc", ".log"} or (
                ".git" in relative.parts and path.name not in {".git", "config", "alternates", "gitdir", "commondir"}
            ):
                continue
            if not self.contains_legacy_path(path):
                continue
            if name in {"cache", "cache-reuse"} and self.historical_robodojo_record(relative):
                print(f"  Preserving historical RoboDojo record: {path}")
                continue
            if path.name in {"runtime.json", "validation.json", "prepare-request.json", "direct_url.json"} or (
                path.suffix == ".json" and relative.parent.name == "steps"
            ):
                data = self.rebase_manifest(path.read_bytes())
            elif self.relocatable_text(path, relative, root):
                data = self.rebase(path.read_bytes())
            else:
                issues.append(relative)
                continue
            if stage:
                atomic_write(path, data, stat.S_IMODE(info.st_mode))
            else:
                print(f"  Cache/source record will be rebased: {path}")
        if issues:
            raise ValueError(f"Unrecognized legacy path references in {self.old[name]}: "
                             + ", ".join(map(str, issues[:12])))

    @staticmethod
    def historical_robodojo_record(relative: Path) -> bool:
        parts = relative.parts
        if len(parts) < 3 or parts[0] != "robodojo" or not re.fullmatch(r".+-[0-9a-f]{16}", parts[1]):
            return False
        record = parts[2:]
        if record in {("setup_failure.json",), ("installed.txt",)}:
            return True
        # pip names each self-check record by the SHA-224 of the interpreter prefix.
        return (len(record) == 4 and record[:3] == ("cache", "pip", "selfcheck")
                and re.fullmatch(r"[0-9a-f]{56}", record[-1]) is not None)

    @staticmethod
    def venv_metadata(path, root):
        return path.name in {"pyvenv.cfg", "direct_url.json"} or path.suffix in {".pth", ".egg-link"} or (
            path.parent == root / "bin" or path.name.endswith(".so") or path.name.startswith("__editable__")
        )

    @staticmethod
    def relocatable_text(path, relative, root):
        if path.suffix in {".yml", ".yaml"} and "Robots" in relative.parts and "Assets" in relative.parts:
            return "build" in relative.parts
        if path.name in {"pyvenv.cfg", ".git", "gitdir", "commondir", "alternates"} or path.suffix in {".pth", ".egg-link"}:
            return True
        if path.name.startswith("__editable__") and path.name.endswith("_finder.py"):
            return True
        if path.parent.name == "bin" and (path.parent.parent / "pyvenv.cfg").is_file():
            with path.open("rb") as stream:
                return stream.read(2) == b"#!" or path.name in {"activate", "activate.csh", "activate.fish", "Activate.ps1"}
        return path.name == "config" and ".git" in path.relative_to(root).parts

    def copy_resource(self, name):
        stage = self.work / "stage" / name
        old = self.old[name]
        if name not in self.state["ready"]:
            if stage.is_symlink():
                raise ValueError(f"Staging directory is a symlink: {stage}")
            stage.mkdir(exist_ok=True)
            flags = ["rsync", "-aHAXSx", "--checksum", "--delete"]
            paths = [f"{old}/", f"{stage}/"]
            original = tree_stamp(old)
            print(f"Copying and verifying {old}", flush=True)
            command([*flags, *paths], timeout=None)
            if command([*flags, "--dry-run", "--itemize-changes", *paths], timeout=None).strip():
                raise ValueError(f"Checksum comparison changed while copying {old}")
            self.inspect_paths(name, stage)
            if tree_stamp(old) != original:
                raise ValueError(f"Original data changed during copy: {old}")
            self.state["originals"][name] = original
            self.state["ready"].append(name)
            self.save("Staged", name=name)
        if name not in self.state["published"]:
            # A missing stage after a journaled copy means rename already succeeded.
            if stage.is_dir():
                self.remove_alias(name)
                rename(stage, self.new[name])
            elif not self.new[name].is_dir() or self.new[name].is_symlink():
                raise ValueError(f"Could not find staged resource {name}")
            self.state["published"].append(name)
            self.save("Published", path=str(self.new[name]))

    def remove_alias(self, name):
        path = self.new[name]
        preflight_links({path: self.old[name]})
        if path.is_symlink():
            path.unlink()

    def rebuild_environment(self):
        name = "venv"
        if name in self.state["published"]:
            return
        target = self.new[name]
        if not self.state.get("building_venv"):
            preflight_links({target: self.old[name]})
            self.state["building_venv"] = True
            self.save("Building", path=str(target))
        if target.is_symlink():
            self.remove_alias(name)
        elif exists(target):
            rename(target, self.work / f"partial-venv-{uuid4().hex}")
        wheels = self.args.wheelhouse
        env = build_env()
        python = target / "bin/python"
        command([self.state["base"], "-I", "-m", "venv", "--without-pip", target], env=env, timeout=120)
        pins = self.work / "requirements.txt"
        installer = [sys.executable, "-B", "-m", "pip", "--python", python, "--disable-pip-version-check",
                     "--no-cache-dir", "install", "--no-index", "--find-links", wheels, "--no-deps"]
        command([*installer, "--only-binary", ":all:", "-r", pins], env=env, timeout=None, private=True)
        command([*installer, "-e", self.new["source"]], env=env, timeout=None, private=True)
        self.verify_python()
        atomic_write(target / ".evoab-rua-ready", b"RUA migration interpreter verified\n")
        self.state["originals"][name] = tree_stamp(self.old[name])
        self.state["published"].append(name)
        self.save("Rebuilt", path=str(target))

    def check_wheels(self):
        pins = self.work / "requirements.txt"
        packages = self.state["packages"]
        atomic_write(pins, "".join(f"{name}=={version}\n" for name, version in sorted(packages.items())
                                  if name != RUA_DIST).encode())
        try:
            command([sys.executable, "-B", "-m", "pip", "--python", self.old["venv"] / "bin/python",
                     "--disable-pip-version-check", "--no-cache-dir", "install", "--no-index",
                     "--find-links", self.args.wheelhouse, "--no-deps", "--only-binary", ":all:",
                     "--dry-run", "--ignore-installed", "-r", pins], env=build_env(), timeout=None, private=True)
        except ValueError as error:
            raise ValueError(f"Wheelhouse cannot satisfy every saved package pin: {pins}") from error

    def verify_python(self):
        python = self.new["venv"] / "bin/python"
        if not python.is_file():
            raise ValueError(f"Required RUA interpreter is missing: {python}")
        info = inventory(python)
        if Path(info["prefix"]).resolve() != self.new["venv"] or info["python"] != self.state["python"]:
            raise ValueError("New RUA interpreter does not match the original Python version and expected prefix")
        if package_versions(info) != self.state["packages"]:
            raise ValueError("New environment package versions differ from the saved inventory")
        for name, _, origin, _ in info["packages"]:
            if package_name(name) == RUA_DIST:
                url = urlsplit(origin.get("url", ""))
                if not origin.get("dir_info", {}).get("editable") or url.scheme != "file" or (
                    Path(unquote(url.path)).resolve() != self.new["source"]
                ):
                    raise ValueError("RUA editable metadata does not point to the new source")
        worker = self.repo / "evoab/agents/rua/worker.py"
        if not worker.is_file():
            raise ValueError(f"Required RUA Worker is missing: {worker}")
        report = json.loads(command([python, "-I", "-B", "-u", worker, "--check-runtime"], private=True))
        if not within(Path(report["rua"]), self.new["source"]):
            raise ValueError("Worker imported RUA from outside the migrated source")
        command([sys.executable, "-B", "-m", "pip", "--python", python, "check"], env=build_env(), private=True)
        for path, info in files(self.new["venv"]):
            if not stat.S_ISREG(info.st_mode) or path.suffix == ".pyc":
                continue
            if self.venv_metadata(path, self.new["venv"]):
                if self.contains_legacy_path(path):
                    raise ValueError(f"Legacy reference in new virtual environment: {path}")
        print(f"Worker imports {report['rua']} (RUA {report['version']}, Codex SDK {report['sdk']})")

    def update_external_manifests(self):
        for index, name in enumerate(self.state["manifests"]):
            path = Path(name)
            if self.mapped(path) != path:
                continue
            if not regular(path) or path.parent.resolve() != path.parent:
                raise ValueError(f"Runtime record changed location: {path}")
            data = path.read_bytes()
            if name not in self.state["external"] and self.pattern.search(data):
                if path.stat().st_uid != os.getuid():
                    raise ValueError(f"External manifest is not owned by this account: {path}")
                original = self.work / f"manifest-{index}.json"
                atomic_write(original, data)
                self.state["external"][name] = {"backup": str(original), "mode": stat.S_IMODE(path.stat().st_mode),
                                                "before": digest(data), "after": digest(self.rebase_manifest(data))}
                self.save("Backed up external manifest", path=name)
            record = self.state["external"].get(name)
            if record and digest(data) != record["after"]:
                if digest(data) != record["before"]:
                    raise ValueError(f"External manifest changed during migration: {path}")
                atomic_write(path, self.rebase_manifest(data), record["mode"])
                self.save("Updated external manifest", path=name)

    @staticmethod
    def runtime_directories(data):
        for key in ("native_root", "assets_dir", "asset_source_dir"):
            if key == "asset_source_dir" and key not in data:
                continue
            if not Path(data[key]).is_dir():
                raise ValueError(f"Required RoboDojo {key} is missing: {data[key]}")
        native, assets = (Path(data[key]) for key in ("native_root", "assets_dir"))
        overlay = native / "Assets"
        if not overlay.is_dir() or overlay.resolve(strict=True) != assets.resolve(strict=True):
            raise ValueError(f"RoboDojo native source does not resolve to the prepared Assets: {overlay}")
        for directory in ("Robots", "Object", "Material", "Eval_Layout"):
            group = assets / directory
            if not group.is_dir():
                raise ValueError(f"Missing RoboDojo assets: {group}")
            for path, info in files(group):
                if stat.S_ISLNK(info.st_mode):
                    try:
                        path.resolve(strict=True)
                    except FileNotFoundError as error:
                        raise ValueError(f"Required RoboDojo asset symlink is dangling: {path} -> {path.readlink()}") from error
        return native, assets

    def verify_runtime(self):
        from types import SimpleNamespace
        from evoab.integrations.robodojo import deployment
        from evoab.integrations.robodojo.assets import verify_robot_configs
        from evoab.services.deployment.container import options, parse
        from evoab.services.deployment.runtime import runtime_for
        from evoab.workbench import benchmarks

        args = SimpleNamespace(cache_dir=Path(self.state["cache"]), container=not self.state["native"],
                               runtime_python=None, runtime_env=[])
        selected = Path(self.state["selected"])
        for name in self.state["manifests"]:
            path = self.mapped(Path(name))
            if not regular(path) or self.pattern.search(path.read_bytes()):
                raise ValueError(f"{path} is missing or still references a legacy resource")
        recipe = deployment.recipe(args)
        runtime = runtime_for(args, recipe, deployment)
        if runtime.root / "runtime.json" != selected:
            raise ValueError(f"Supervisor loads {selected} but the record root is {runtime.root}")
        native, assets = self.runtime_directories(runtime.data)
        catalogs = benchmarks.prepared_runtimes(Path(self.state["cache"]))
        robodojo = [item for item in catalogs if item.integration == "robodojo" and item.profile == "default"]
        preferred = next((item for item in robodojo if item.container), robodojo[0] if robodojo else None)
        if preferred is None or preferred.root != runtime.root:
            raise ValueError(f"Studio does not recognize the prepared RoboDojo runtime {runtime.root}")
        verify_robot_configs(assets)
        for config in (assets / "Robots").rglob("*.yml"):
            if self.contains_legacy_path(config):
                raise ValueError(f"RoboDojo asset configuration still uses a legacy path: {config}")
        tasks = deployment.catalog_tasks(runtime.data)
        if not tasks:
            raise ValueError("Prepared RoboDojo runtime has no readable frozen task layouts")
        task = next(iter(tasks))
        plan_args = SimpleNamespace(task=task, layout=None, layout_sha256=None, port=None,
                                    host="127.0.0.1", depth=False, camera_calibration=None)
        deployment.plan(plan_args, runtime, self.repo, "rua-migration-check")
        if args.container:
            image, inner = parse(runtime.python)
            docker_root, mounts = options(runtime.python)
            recorded = runtime.data["container"]
            if (image, docker_root, mounts) != (recorded["image"], recorded["root"], recorded["mounts"]):
                raise ValueError("Docker host mounts differ between manifest fields")
            if not inner.startswith("/") or not all(Path(path).exists() for path in mounts):
                raise ValueError("Docker record contains missing host mounts or an invalid image interpreter")
            for path in (native, assets):
                if not any(within(path.resolve(), Path(mount).resolve()) for mount in mounts):
                    raise ValueError(f"Docker is missing a same-path mount for {path}")
            command(["docker", "image", "inspect", "--format", "{{.Id}}", image], private=True)
        print(f"Supervisor and Studio recognize {selected}; frozen task checked: {task}")
        return task

    def verify(self):
        if self.state.get("phase") not in {"ready", "isolating", "isolated", "complete"}:
            raise ValueError("Complete --migrate before verification")
        if self.state["phase"] in {"isolated", "complete"}:
            self.check_isolated()
        check_git(self.repo)
        for root in self.new.values():
            if not root.is_dir() or root.is_symlink():
                raise ValueError(f"Expected a physical resource directory: {root}")
            for path, info in files(root):
                if stat.S_ISLNK(info.st_mode):
                    target, resolved = self.link_targets(path, path.readlink())
                    if self.mapped(target) != target or (resolved is not None and self.mapped(resolved) != resolved):
                        raise ValueError(f"Resource symlink still uses a legacy path: {path}")
        self.verify_python()
        task = self.verify_runtime()
        env = {**os.environ, "EVOAB_RUA_PYTHON": str(self.new["venv"] / "bin/python"),
               "EVOAB_CACHE_DIR": self.state["cache"]}
        flags = ["--container"] if not self.state["native"] else []
        command([sys.executable, "-B", "-m", "evoab", "run", "--integration", "robodojo", "--agent", "rua",
                 "--deploy", "local", *flags, "--task", task, "--tool-profile", "direct", "--no-sam3",
                 "--model", "rua-migration-check", "--provider-url", "http://127.0.0.1:18080/v1",
                 "--validate-config"], cwd=self.repo, env=env, private=True)
        print(f"export EVOAB_RUA_PYTHON={shlex_quote(env['EVOAB_RUA_PYTHON'])}")
        print(f"export EVOAB_CACHE_DIR={shlex_quote(env['EVOAB_CACHE_DIR'])}")

    def migrate(self):
        if self.state.get("phase") in {"ready", "isolating", "isolated", "complete"}:
            self.verify()
            return
        wheels = self.args.wheelhouse
        if not wheels or not wheels.is_dir():
            raise ValueError("--migrate requires a directory of trusted offline wheels (--wheelhouse)")
        self.args.wheelhouse = wheels.resolve()
        if any(within(self.args.wheelhouse, path) for path in (*self.old.values(), *self.new.values())):
            raise ValueError("Keep the wheelhouse outside the migrated directories")
        if not self.state or self.state["phase"] == "rolled-back":
            self.state = self.plan()
            self.work.mkdir(mode=0o700)
            (self.work / "stage").mkdir(mode=0o700)
            self.save("Began migration", journal=str(self.work))
        self.check_idle()
        mounted_below((*self.old.values(), *self.new.values()))
        if "venv" not in self.state["published"]:
            self.check_wheels()
        for name in COPIED:
            self.copy_resource(name)
        self.rebuild_environment()
        self.update_external_manifests()
        self.state["phase"] = "ready"
        self.save("Physical directories published")
        self.verify()

    def isolate(self):
        self.verify()
        if self.state["phase"] in {"isolated", "complete"}:
            return
        if self.state["phase"] == "ready":
            for name, old in self.old.items():
                if not old.is_dir() or old.is_symlink() or tree_stamp(old) != self.state["originals"][name]:
                    raise ValueError(f"Original changed after copying; roll back and migrate again: {old}")
            self.backup.mkdir(mode=0o700, exist_ok=True)
            self.check_backup()
            if any(self.backup.iterdir()):
                raise ValueError(f"Backup destination already contains files: {self.backup}")
            self.state["phase"] = "isolating"
            self.save("Isolating", backup=str(self.backup))
        self.check_backup()
        mounted_below(self.old.values())
        for name, old in self.old.items():
            target = self.backup / old.name
            if exists(old):
                if not old.is_dir() or old.is_symlink():
                    raise ValueError(f"Original resource changed: {old}")
                rename(old, target)
                self.save("Isolated", source=str(old), backup=str(target))
            elif not target.is_dir() or target.is_symlink():
                raise ValueError(f"Missing original resource and backup: {old}")
        self.check_isolated()
        self.verify()
        self.state["phase"] = "isolated"
        self.state["isolated_at"] = time.time()
        self.save("Verified with legacy paths absent", backup=str(self.backup))

    def check_backup(self):
        if self.backup.is_symlink() or not self.backup.is_dir() or self.backup.stat().st_uid != os.getuid():
            raise ValueError(f"Expected a physical backup directory owned by this account: {self.backup}")

    def check_isolated(self):
        retained = exists(self.backup) or self.state["phase"] != "complete"
        if retained:
            self.check_backup()
        for old in self.old.values():
            backup = self.backup / old.name
            if exists(old) or (retained and (not backup.is_dir() or backup.is_symlink())):
                raise ValueError(f"Legacy path must be absent and its backup present: {old}")

    def finalize(self):
        if self.state.get("phase") not in {"isolated", "complete"}:
            raise ValueError("Run --isolate and complete the RoboDojo acceptance episode before finalizing")
        self.verify()
        if self.state["phase"] == "complete":
            print(f"Migration already finalized; see {self.state_file}")
            return
        if not self.args.run_dir:
            raise ValueError("--finalize requires --run-dir pointing to a successful post-isolation RUA episode")
        run = self.args.run_dir.expanduser().resolve()
        records = [run / name for name in ("deployment.json", "supervisor.json", "result.json", "evaluator.log")]
        for path in records:
            if not regular(path) or path.stat().st_mtime < self.state["isolated_at"]:
                raise ValueError(f"Missing evidence from after isolation: {path}")
        deployment, supervisor, result = map(read_json, records[:3])
        if deployment["state"] != "ready" or Path(deployment["runtime_manifest"]).resolve() != Path(self.state["selected"]):
            raise ValueError("Episode has no ready RoboDojo deployment with the selected runtime manifest")
        attach = f"RUA attached to robodojo episode {deployment['run_id']} (protocol 4, tool schema 8)"
        if (supervisor.get("episode_exit_code") != 0 or result.get("agent_backend") != "rua"
                or result.get("task_success") is not True or result.get("accepted") is not True
                or attach not in records[3].read_text(errors="replace")):
            raise ValueError("RoboDojo acceptance requires RUA attachment, exit 0 and an officially successful task")
        self.state["phase"] = "complete"
        self.save("Finalized", run=str(run), retained_backup=str(self.backup))

    def rollback(self):
        if not self.state or self.state["phase"] == "rolled-back":
            print("No migration to roll back")
            return
        recovery = self.work / "rollback"
        recovery.mkdir(mode=0o700, exist_ok=True)
        if exists(self.backup):
            self.check_backup()
        for name, record in self.state["external"].items():
            path = Path(name)
            if (not regular(path) or path.parent.resolve() != path.parent
                    or digest(path.read_bytes()) not in {record["after"], record["before"]}
                    or digest(Path(record["backup"]).read_bytes()) != record["before"]):
                raise ValueError(f"Manifest or backup changed since migration, review before restoring: {path}")
        self.state["phase"] = "rolling-back"
        self.save("Rolling back", recovery=str(recovery))
        for name, old in self.old.items():
            saved = self.backup / old.name
            if exists(saved):
                if not saved.is_dir() or saved.is_symlink():
                    raise ValueError(f"Invalid quarantined backup: {saved}")
                rename(saved, old)
                self.save("Restored original", path=str(old))
            if not old.is_dir() or old.is_symlink():
                raise ValueError(f"Missing original resource: {old}")
            path = self.new[name]
            previous = self.state["links"][name]
            if exists(path) and not path.is_symlink():
                owned = name in self.state["ready"] or (name == "venv" and self.state.get("building_venv"))
                if not owned or not path.is_dir():
                    raise ValueError(f"Migration does not own {path}")
                rename(path, recovery / name)
                self.save("Preserved migrated copy", path=str(recovery / name))
            if path.is_symlink() and (previous is None or str(path.readlink()) != previous):
                raise ValueError(f"Unexpected symlink during rollback: {path}")
            if previous is not None and not exists(path):
                path.symlink_to(previous, target_is_directory=True)
                self.save("Restored original symlink", path=str(path))
        for name, record in self.state["external"].items():
            path = Path(name)
            if digest(path.read_bytes()) != record["before"]:
                atomic_write(path, Path(record["backup"]).read_bytes(), record["mode"])
                self.save("Restored manifest", path=name)
        self.state["phase"] = "rolled-back"
        self.save("Rolled back", retained_copies=str(recovery))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_mutually_exclusive_group()
    for name, help_text in {
        "audit": "Inspect old resources, Python pins, free space and the selected manifest (default)",
        "migrate": "Copy resource data and build the new independent virtual environment",
        "verify": "Validate the new Python, CLI, Studio catalog and prepared RoboDojo paths without GPU",
        "isolate": "Rename original resources into a backup and verify with every old path absent",
        "finalize": "Record successful live acceptance and retain the isolated backup",
        "rollback": "Restore the original paths, aliases and external manifest records",
    }.items():
        phases.add_argument(f"--{name}", dest="phase", action="store_const", const=name, help=help_text)
    parser.set_defaults(phase="audit")
    parser.add_argument("--cache-dir", type=Path, help="Original active EvoAB cache, defaults to EVOAB_CACHE_DIR")
    parser.add_argument("--manifest", type=Path, action="append", default=[],
                        help="Additional owned external runtime.json to check and rebase (repeatable)")
    parser.add_argument("--native", action="store_true", help="Select the native manifest instead of Docker")
    parser.add_argument("--wheelhouse", type=Path, help="Trusted local wheels for exact-version offline installation")
    parser.add_argument("--print-pins", action="store_true", help="Print exact old distribution pins, excluding RUA")
    parser.add_argument("--run-dir", type=Path, help="Successful RoboDojo artifact directory for --finalize")
    parser.add_argument("--quiescent", action="store_true", help="Confirm Studio and associated runs are stopped")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if os.getuid() == 0:
        parser.error("Run as the evaluation account")
    try:
        check_git(repo)
        migration = Migration(repo, args)
        if args.print_pins:
            if args.phase != "audit":
                parser.error("--print-pins applies to --audit")
            packages = migration.state.get("packages") or migration.load_old_environment(report=False)[2]
            for name, version in sorted(packages.items()):
                if name != RUA_DIST:
                    print(f"{name}=={version}")
            return 0
        if args.phase in {"audit", "verify"}:
            getattr(migration, args.phase)()
            return 0
        if not args.quiescent:
            parser.error(f"--{args.phase} requires --quiescent")
        migration.control.mkdir(mode=0o700, exist_ok=True)
        if migration.control.stat().st_mode & 0o077:
            raise ValueError(f"Migration journal must be private: {migration.control}")
        lock = os.open(migration.control / "lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            migration = Migration(repo, args)
            if migration.state.get("phase") == "rolling-back" and args.phase != "rollback":
                raise ValueError("Finish --rollback before another operation")
            migration.check_idle()
            getattr(migration, args.phase)()
        return 0
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"RUA physical migration stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
