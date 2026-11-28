"""Local Docker archives, imported through the same runtime verification as builds."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tarfile
from pathlib import Path

from .runtime import cache_root


SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
#: Selected directory for the CLI setup child; Studio resolves its saved setting into this.
DIRECTORY_ENV = "EVOAB_IMAGE_IMPORT_DIR"


def default_directory(args=None) -> Path:
    """The cache's import directory: ``<EVOAB_CACHE_DIR or ~/.cache/evoab>/image-imports``."""
    return cache_root(args) / "image-imports"


def _checked_directory(directory, args=None) -> Path:
    """Expand and create one configured directory, rejecting relative paths and files."""
    if directory is None:
        path = default_directory(args)
    else:
        if not isinstance(directory, (str, os.PathLike)):
            raise ValueError("Image import directory must be a path")
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise ValueError(f"Image import directory must be absolute: {directory}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"Cannot create image import directory {path}: {error.strerror or error}") from error
    if not path.is_dir():
        raise ValueError(f"Image import directory is not a directory: {path}")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError(f"Image import directory is not readable and writable: {path}")
    return path


def import_directory(args=None, directory=None) -> Path:
    """Explicit directory, else ``EVOAB_IMAGE_IMPORT_DIR``, else the cache default."""
    if directory is None:
        directory = os.environ.get(DIRECTORY_ENV) or None
    return _checked_directory(directory, args)


def normalize_directory(value) -> str | None:
    """Validate a Studio setting; ``None`` or empty text restores the default."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Image import directory must be a path string")
    value = value.strip()
    if not value:
        return None
    if not (value.startswith("/") or value.startswith("~/")):
        raise ValueError("Image import directory must start with / or ~/")
    import_directory(directory=value)
    return value


def archive_path(name: str, args=None, directory=None) -> Path:
    """Resolve one regular archive immediately inside the server's import directory."""
    directory = import_directory(args, directory)
    if not name or Path(name).name != name or not name.endswith(SUFFIXES):
        raise ValueError("Select an image archive from the import directory")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("Image archive must be a regular file in the import directory")
    return path


def available_archives(directory=None) -> dict:
    directory = import_directory(directory=directory)
    files = []
    for path in sorted(directory.iterdir()):
        try:
            if path.name.endswith(SUFFIXES) and not path.is_symlink() and path.is_file():
                files.append({"name": path.name, "size": path.stat().st_size})
        except FileNotFoundError:
            continue
    return {"directory": str(directory), "files": files}


def load_archive(name: str, spec, args, report) -> str:
    """Load a single-image docker-save archive and return its immutable image ID.

    Native revision, dependency, task and launch verification happens in container.prepare.
    Names and tags are never used as proof of image compatibility.
    """
    from .container import _stream, docker

    path = archive_path(name, args)
    with path.open("rb") as source:
        before = path.stat()
        try:
            with tarfile.open(fileobj=source, mode="r:*") as archive:
                def read_json(member_name):
                    member = archive.getmember(member_name)
                    if not member.isfile() or member.size > 1024 * 1024:
                        raise ValueError("Invalid image archive metadata")
                    content = archive.extractfile(member).read()
                    return json.loads(content), content

                manifest, _ = read_json("manifest.json")
                if not isinstance(manifest, list) or len(manifest) != 1:
                    raise ValueError("Export exactly one image with docker save")
                config, content = read_json(manifest[0]["Config"])
                if not isinstance(config, dict) or not isinstance(config.get("config", {}), dict):
                    raise ValueError("Invalid image configuration")
                machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
                if config.get("os") != "linux" or config.get("architecture") != machine:
                    raise ValueError(f"Image must target linux/{machine}")
                labels = config.get("config", {}).get("Labels") or {}
                if not isinstance(labels, dict):
                    raise ValueError("Invalid image labels")
                if labels.get("org.evoab.integration", spec.integration) != spec.integration:
                    raise ValueError("Image belongs to a different EvoAB integration")
                image_id = "sha256:" + hashlib.sha256(content).hexdigest()
        except (tarfile.TarError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid docker save archive") from error
        tags = manifest[0].get("RepoTags") or []
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("Invalid image tags")
        for tag in tags:
            existing = subprocess.run([docker(), "image", "inspect", tag, "--format", "{{.Id}}"],
                                      capture_output=True, text=True)
            if existing.returncode == 0 and existing.stdout.strip() != image_id:
                raise ValueError(f"Image tag {tag} already refers to another image; export with a distinct tag")
        source.seek(0)
        _stream([docker(), "load"], report, stdin=source)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError("Image archive changed during import; finish copying and retry")
    result = subprocess.run([docker(), "image", "inspect", image_id, "--format", "{{.Id}}"],
                            capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != image_id:
        raise ValueError(f"Cannot verify loaded image {image_id}: {result.stderr.strip() or result.stdout.strip()}")
    if report:
        report(f"Loaded {path.name} as {image_id}; verifying simulator compatibility")
    return image_id
