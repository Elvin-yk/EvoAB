"""Link existing RUA resources into a Git checkout without changing their targets."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys


RESOURCE_NAMES = {
    "source": "rua-source",
    "venv": ".venv-rua",
    "cache": "rua-cache",
    "cache-reuse": "rua-cache-reuse",
    "candidate": "rua-candidate-0.2.0",
}
LARGE_FILE_BYTES = 10 * 1024 * 1024


def check_git(repo: Path) -> None:
    aliases = [f"rua/{name}" for name in RESOURCE_NAMES]
    tracked = subprocess.check_output(["git", "-C", str(repo), "ls-files", "--cached", "-z", "--", *aliases])
    if tracked:
        raise ValueError("RUA resource paths are in the Git index. Review the staged paths before linking.")
    for alias in aliases:
        result = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--", alias])
        if result.returncode:
            raise ValueError(f"Git did not confirm {alias} is ignored. Review rua/.gitignore before linking.")


def preflight_links(links: dict[Path, Path]) -> list[tuple[Path, Path]]:
    pending = []
    conflicts = []
    for alias, target in links.items():
        if not target.is_dir():
            conflicts.append(f"Resource directory is missing: {target}")
        elif not os.path.lexists(alias):
            pending.append((alias, target))
        elif not alias.is_symlink() or not alias.is_dir() or not alias.samefile(target):
            conflicts.append(f"Existing path {alias} does not link to {target}")
    if conflicts:
        raise ValueError("\n".join(conflicts))
    return pending


def probe_interpreter(venv: Path) -> None:
    python = venv / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(f"RUA interpreter is missing or is not executable: {python}")
    print(f"Checking RUA with {python}", flush=True)
    subprocess.run(
        [str(python), "-I", "-B", "-c", "import sys\n"
         "if sys.version_info < (3, 11): sys.exit('RUA requires Python 3.11 or newer')\n"
         "import uni_robot_agent\n"
         "print('RUA', uni_robot_agent.__version__, 'from', uni_robot_agent.__file__)"],
        check=True, timeout=30,
    )


def report_large_untracked(repo: Path) -> None:
    large = []
    names = subprocess.check_output(["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"])
    for name in filter(None, os.fsdecode(names).split("\0")):
        try:
            info = (repo / name).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_size >= LARGE_FILE_BYTES:
            large.append(name)
    if large:
        print("Review these Git-visible untracked files (10 MiB or larger) before staging:", file=sys.stderr)
        for name in large:
            print(f"  {name!r}", file=sys.stderr)
    else:
        print("No Git-visible untracked files at or above 10 MiB.")


def main() -> int:
    directory = Path(__file__).resolve().parent
    repo = directory.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, default=repo.parent,
                        help="Directory containing the existing resources (defaults to the parent of the checkout)")
    parser.add_argument("--check", action="store_true", help="Check resources and preview missing links without creating them")
    args = parser.parse_args()

    try:
        root = args.resource_root.expanduser().resolve(strict=True)
        links = {directory / alias: root / name for alias, name in RESOURCE_NAMES.items()}
        check_git(repo)
        pending = preflight_links(links)
        source = links[directory / "source"]
        if not (source / "uni_robot_agent" / "__init__.py").is_file():
            raise ValueError(f"RUA upstream Python package is missing from {source}")
        probe_interpreter(links[directory / "venv"])
        for alias, target in pending:
            if args.check:
                print(f"Would link {alias} -> {target}")
            else:
                alias.symlink_to(target, target_is_directory=True)
                print(f"Created {alias} -> {target}")
        if not pending:
            print("All resource links already point to the expected targets.")
        if not args.check or (directory / "venv").is_symlink():
            probe_interpreter(directory / "venv")
        report_large_untracked(repo)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"RUA resource check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
