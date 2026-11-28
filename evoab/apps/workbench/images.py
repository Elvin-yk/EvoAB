"""``evoab images``: one benchmark image at a time, one operation per command.

``pull``   pull (or import, or build) the benchmark's published image, add EvoAB's
           dependency layer and record the tasks the image ships
``check``  start one task's environment from that image once and stop it
``list``   the benchmark images on this machine
``rm``     remove one benchmark's image and its record (runs stay)

EvoAB's code is never in an image: every run mounts the installed package read-only,
so editing code needs no new pull. One image serves all of a benchmark's suites.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evoab images", allow_abbrev=False,
                                     description="Pull, check, list and remove benchmark images.")
    sub = parser.add_subparsers(dest="action", required=True)
    pull = sub.add_parser("pull", allow_abbrev=False,
                          help="Pull the benchmark's published image and add EvoAB's dependency layer")
    pull.add_argument("benchmark", help="libero, libero_pro, calvin or a registered custom benchmark")
    pull.add_argument("--archive", dest="image_archive", metavar="FILENAME",
                      help="Load the image from this docker save archive in EVOAB_IMAGE_IMPORT_DIR instead of pulling")
    pull.add_argument("--accept-license", metavar="NAME", help="Accept the licence the image requires, if any")
    pull.add_argument("--wheel", type=Path,
                      help="Bake this matching evoab-runtime wheel into the image (pinned evaluations); "
                           "by default the code is mounted at run time")
    pull.add_argument("--timeout-s", dest="setup_timeout_s", type=float, default=14400)
    pull.add_argument("--cache-dir", type=Path, help="Image records directory (or EVOAB_CACHE_DIR)")
    check = sub.add_parser("check", allow_abbrev=False,
                           help="Start one task's environment from the pulled image once, then stop it")
    check.add_argument("benchmark")
    check.add_argument("--cache-dir", type=Path)
    listing = sub.add_parser("list", allow_abbrev=False, help="Benchmark images on this machine")
    listing.add_argument("--json", action="store_true")
    remove = sub.add_parser("rm", allow_abbrev=False, help="Remove one benchmark's image and its record")
    remove.add_argument("benchmark")
    return parser


def _selection(args):
    from evoab.envs import apply_benchmark
    from evoab.envs.docker.config import validate_deployment
    args.command, args.task = "images", None
    apply_benchmark(args)
    return validate_deployment(args)


def _pull(args) -> None:
    from evoab.envs.docker.process import cancellation_signals, enable_child_reaping
    from evoab.envs.docker.setup import prepare
    enable_child_reaping()
    adapter = _selection(args)
    with cancellation_signals():
        prepare(args, adapter)


def _check(args) -> None:
    from evoab.envs.docker.process import enable_child_reaping
    from evoab.envs.docker.runtime import read_runtime
    from evoab.envs.docker.setup import check_launch
    enable_child_reaping()
    adapter = _selection(args)
    runtime = read_runtime(args, adapter.recipe(args))
    check_launch(args, adapter, runtime)


def _rows() -> list[dict]:
    from evoab.apps.workbench.benchmarks import overview
    from evoab.apps.workbench.service import default_runs_root
    rows = []
    for item in overview(default_runs_root()):
        for profile in item["profiles"]:
            name = item["integration"] if item["integration"] != "custom" else profile["profile"]
            rows.append({"benchmark": name, "status": profile["status"], "image": profile.get("image"),
                         "tasks": profile.get("task_count") or 0,
                         "upstream": (item.get("container") or {}).get("image")})
    return rows


_STATUS = {"ready": "pulled", "not_prepared": "not pulled", "preparing": "pulling", "setup_failed": "pull failed"}


def _list(args) -> None:
    rows = [{**row, "status": _STATUS.get(row["status"], row["status"])} for row in _rows()]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    from evoab.apps.workbench.cli import _table
    _table([[r["benchmark"], r["status"], r["image"] or "—", r["tasks"] or "—"] for r in rows],
           ["BENCHMARK", "STATUS", "IMAGE", "TASKS"])


def _remove(args) -> None:
    from evoab.apps.workbench import Workbench
    from evoab.envs import resolve_benchmark
    integration, manifest = resolve_benchmark(args.benchmark)
    result = Workbench().uninstall(integration, manifest or "default")
    print(f"{args.benchmark}: image {result.get('image_action', 'removed')}, record removed")


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from evoab.apps.workbench import WorkbenchError
    handler = {"pull": _pull, "check": _check, "list": _list, "rm": _remove}[args.action]
    try:
        handler(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError, TimeoutError, WorkbenchError) as error:
        print(f"evoab images {args.action}: {error}", file=sys.stderr)
        return 1
    return 0
