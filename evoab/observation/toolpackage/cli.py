"""Standalone CLI for running the external observation enhancement proxy."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from evoab.services.hosting import HttpServer
from evoab.telemetry.logging import configure_logging

from .config import load_pipeline_config
from .proxy import ObservationProxyService, build_proxy_router
from evoab.services.hosting import create_app
from .pipeline import ObservationPipeline


def add_ob_proxy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--upstream-url",
        default=os.environ.get("EVOAB_ENV_URL"),
        help="Upstream EvoAB Environment Protocol v4 HTTP URL (or EVOAB_ENV_URL)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to an observation enhancement pipeline YAML or JSON file",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=18765, help="Bind port (0 selects a free port)")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Optional directory to archive raw/annotated images, targets.json and annotations.jsonl",
    )
    parser.add_argument("--sam3-url", default=None, help="Optional SAM3 endpoint override for the sam3 plugin")
    parser.add_argument(
        "--foundationpose-url",
        default=None,
        help="Optional FoundationPose endpoint override for the foundationpose plugin",
    )
    parser.add_argument("--timeout-s", type=float, default=120.0, help="Upstream HTTP timeout in seconds")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print the validated pipeline configuration JSON and exit without starting the server",
    )


def build_proxy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evoab ob-proxy",
        description=(
            "Run an external Environment Protocol v4 observation-enhancement proxy "
            "that preprocesses camera frames (e.g. SAM3, FoundationPose) before any agent sees them."
        ),
        allow_abbrev=False,
    )
    add_ob_proxy_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_proxy_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = load_pipeline_config(args.config)
    except ValueError as error:
        parser.exit(1, f"{error}\n")

    if args.print_config:
        print(json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if not args.upstream_url:
        parser.error("--upstream-url or EVOAB_ENV_URL is required")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    pipeline = ObservationPipeline(
        config,
        archive_dir=args.archive_dir,
        default_sam3_url=args.sam3_url,
        default_foundationpose_url=args.foundationpose_url,
    )
    service = ObservationProxyService(
        args.upstream_url,
        pipeline,
        archive_dir=args.archive_dir,
        timeout_s=args.timeout_s,
    )
    app = create_app(build_proxy_router(service))
    server = HttpServer(app, host=args.host, port=args.port)

    previous: dict[int, object] = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: server.request_stop())

    print(
        json.dumps(
            {
                "url": server.url,
                "upstream_url": service.upstream_url,
                "plugins": list(pipeline.plugin_names),
                "archive_dir": str(pipeline.archive_dir) if pipeline.archive_dir else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.stop()
        finally:
            service.close()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
