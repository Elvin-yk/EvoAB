"""Content-based version evidence also works in a wheel without Git metadata."""
import hashlib
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path


def fingerprint():
    root = Path(__file__).resolve().parents[4]
    sha = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        sha.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    package, release = "evoab", "source-checkout"
    for candidate in ("evoab-runtime", "evoab"):
        try:
            release = version(candidate)
            package = candidate
            break
        except PackageNotFoundError:
            continue
    runtimes = {}
    for dependency in ("pyAgxArm", "pyrealsense2", "python-can", "numpy", "scipy"):
        try:
            runtimes[dependency] = version(dependency)
        except PackageNotFoundError:
            continue
    return {"package": package, "version": release, "source_sha256": sha.hexdigest(), "runtimes": runtimes}
