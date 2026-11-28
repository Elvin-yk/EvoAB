"""Command parsing, process supervision, and exit status."""

from __future__ import annotations

import argparse
import os
import sys
import signal
from pathlib import Path

from evoab import __version__
from evoab.config import (
    _nonnegative,
    public_config,
    resolve_config,
)
from evoab.envs import apply_benchmark, load_integration, resolve_benchmark


WORKBENCH_COMMANDS = {
    "runs": "List, launch, trace and stop runs (same as Studio)",
    "methods": "The method library: list, components, validate",
    "models": "Model providers and presets",
    "benchmarks": "List benchmarks and their tasks",
    "usage": "Token usage by API and model",
    "settings": "Show or change Studio defaults",
    "studio": "Start the Studio web client",
}


def _add_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print the report as a machine-readable JSON object",
    )
    parser.add_argument(
        "--benchmark",
        metavar="NAME",
        help="Check only this benchmark and fail when it is unavailable",
    )
    parser.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Override the output-directory writability check",
    )


def _run_doctor(argv: list[str]) -> int:
    """Parse and run the offline doctor command without resolving a run configuration."""
    from evoab import doctor

    parser = argparse.ArgumentParser(
        prog="evoab doctor",
        description="Diagnose the installation and configuration offline; no model, credentials or environment required.",
        allow_abbrev=False,
    )
    _add_doctor_arguments(parser)
    args = parser.parse_args(argv)
    try:
        report = doctor.run_checks(
            integration=resolve_benchmark(args.benchmark)[0] if args.benchmark else None,
            output_dir=args.output_dir,
        )
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(doctor.render_json(report) if args.json_output else doctor.render_table(report))
    return 0 if report.ok else 1


def _probe_integration_name(argv: list[str] | None = None) -> str | None:
    """The integration behind --benchmark, so its own options can join the parser."""
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    probe.add_argument("command", nargs="?")
    probe.add_argument("--benchmark")
    name = probe.parse_known_args(argv)[0].benchmark
    return resolve_benchmark(name)[0] if name else None


def build_parser(integration_spec=None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evoab",
        description="evoab — an open platform for robot-use agents", allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    agent = common.add_argument_group("Agent")
    agent.add_argument(
        "--benchmark", required=True, metavar="NAME",
        help="Benchmark: libero, libero_pro, calvin or a registered custom benchmark (evoab benchmarks list)",
    )
    agent.add_argument(
        "--recipe", metavar="METHOD",
        help="Method id from the method library (evoab methods) or a recipe file; "
             "default: the benchmark's default method")
    agent.add_argument(
        "--set", action="append", default=[], metavar="PATH=VALUE",
        help="Override a recipe value by dotted path, read as YAML, e.g. "
             "--set 'roles.main.action.horizon=[16,32]' or --set inputs.depth=false (repeatable)")
    agent.add_argument(
        "--experience-dir",
        help="Directory carrying skills/programs between episodes of a method that carries experience")
    agent.add_argument("--model", help="Model preset for the entry role (evoab models; unless the recipe pins one)")
    agent.add_argument("--reasoning-effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
                       default=None, help="Reasoning effort of the entry role's model (default: the preset's)")
    services = common.add_argument_group("Services")
    # EvoAB starts the environment's container itself; connecting to an already running
    # environment service by URL is an internal and developer path, so it is not advertised.
    services.add_argument("--env-url", "--env_url", dest="env_url", help=argparse.SUPPRESS)
    services.add_argument("--sam3-url", "--sam3_url", dest="sam3_url",
                          help="Address of a running SAM3 service, for recipes that enable scene segmentation")
    services.add_argument("--action-model-url", "--action_model_url", dest="action_model_url"
    )
    services.add_argument(
        "--obs-pipeline",
        "--obs_pipeline",
        dest="obs_pipeline",
        metavar="PATH",
        help="Path to an external observation enhancement pipeline YAML/JSON (or EVOAB_OBS_PIPELINE)",
    )
    output = common.add_argument_group("Output and diagnostics")
    output.add_argument("--output-dir")
    output.add_argument(
        "--telemetry", choices=("off", "standard"), default="standard",
        help="Record traces and diagnostics in the run directory (default: standard)")
    output.add_argument(
        "--debug", action="store_true",
        help="Debug logging and raw model requests in the run record; never changes what the agent sees or does")
    output.add_argument(
        "--print-config",
        action="store_true",
        help="Print resolved non-secret configuration, without connecting",
    )
    output.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate offline, without connecting or resetting",
    )
    if integration_spec is not None:
        integration_spec.add_cli_args(common)
    from evoab.envs.docker.config import add_runtime_arguments, add_launch_arguments
    run = subparsers.add_parser(
        "run", parents=[common], allow_abbrev=False, help="Run an agent on one robot task"
    )
    run.add_argument("--verifier-url", help="Submit the completed run to this verification service")
    run.add_argument("--verifier-timeout-s", type=float, default=30, help="Verification acknowledgement timeout")
    for command, timeout in ((run, 10800),):
        command.add_argument("--instruction", help="Override the environment's task instruction")
        command.add_argument("--episode-timeout-s", type=_nonnegative, default=timeout,
                             help="Episode wall-clock budget; excludes environment startup; 0 disables")
        # Kept for scripts; without --env-url EvoAB always starts its own environment.
        command.add_argument("--deploy", choices=("local",), help=argparse.SUPPRESS)
        add_runtime_arguments(command)
        add_launch_arguments(command, managed=True)
        command.add_argument("--monitor", action="store_true", help="Serve a local live Web console")
        command.add_argument("--monitor-host", default="127.0.0.1")
        command.add_argument("--monitor-port", type=int, default=8765)
    replay = subparsers.add_parser(
        "replay", allow_abbrev=False,
        help="Replay recorded runs step by step in the browser; no GPU, model or environment needed")
    replay.add_argument("run_dir", nargs="+", type=Path, metavar="RUN",
                        help="A run directory or a directory of runs")
    replay.add_argument("--host", default="127.0.0.1")
    replay.add_argument("--port", type=int, default=8765)
    replay.add_argument("--no-browser", action="store_true", help="Print the address instead of opening it")
    doctor = subparsers.add_parser(
        "doctor",
        help="Diagnose the installation and configuration offline",
        allow_abbrev=False,
    )
    _add_doctor_arguments(doctor)
    from evoab.envs.observation.toolpackage.cli import add_ob_proxy_arguments
    ob_proxy = subparsers.add_parser(
        "ob-proxy",
        help="Run a standalone observation-enhancement proxy in front of an environment service",
        allow_abbrev=False,
    )
    add_ob_proxy_arguments(ob_proxy)
    deploy = subparsers.add_parser("deploy", help="Run a prepared environment container in the foreground",
                                   allow_abbrev=False)
    kinds = deploy.add_subparsers(dest="service", required=True)
    # Only environments are deployed; policy and SAM3 services are reached by URL.
    service = kinds.add_parser("env", allow_abbrev=False)
    service.add_argument("--benchmark", required=True, metavar="NAME")
    service.add_argument("--task", help="Task ID as evoab benchmarks tasks lists it, e.g. libero_spatial/pick_up_...")
    service.add_argument("--check", action="store_true",
                         help="Start the service, wait until it is ready, print its identity and stop it")
    service.add_argument("--run-id")
    service.add_argument("--output-dir", default="runs")
    add_runtime_arguments(service)
    add_launch_arguments(service)
    # Resource commands are dispatched before this parser runs (see main); listed here for --help.
    subparsers.add_parser("images", help="Pull, check, list and remove benchmark images", add_help=False)
    for name, text in WORKBENCH_COMMANDS.items():
        subparsers.add_parser(name, help=text, add_help=False)
    return parser


def main(argv=None, *, _supervised=False) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Doctor must run before integration probing and configuration resolution:
    # it needs no integration, model or credential and must not import runtimes.
    if argv and argv[0] == "doctor":
        raise SystemExit(_run_doctor(argv[1:]))
    if argv and argv[0] == "ob-proxy":
        from evoab.envs.observation.toolpackage.cli import main as ob_proxy_main
        raise SystemExit(ob_proxy_main(argv[1:]))
    # Same operations as Studio; they need no integration or run configuration.
    if argv and argv[0] == "images":
        from evoab.apps.workbench.images import main as images_main
        raise SystemExit(images_main(argv[1:]))
    if argv and argv[0] in WORKBENCH_COMMANDS:
        from evoab.apps.workbench.cli import main as workbench_main
        raise SystemExit(workbench_main(argv))
    try:
        integration_name = _probe_integration_name(argv)
        integration_spec = (
            load_integration(integration_name).get_integration_spec()
            if integration_name
            else None
        )
    except ValueError as error:
        build_parser().error(str(error))
    parser = build_parser(integration_spec)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        raise SystemExit(1)
    if getattr(args, "benchmark", None):
        try:
            apply_benchmark(args)
        except ValueError as error:
            parser.error(str(error))
    if args.command == "replay":
        from evoab.apps.web.runtime import serve_directories
        try:
            if not 0 <= args.port <= 65535:
                raise ValueError("--port must be between 0 and 65535")
            serve_directories(args)
        except (ValueError, OSError, RuntimeError) as error:
            parser.exit(1, f"{error}\n")
        return
    if args.command == "run" and not 0 <= args.monitor_port <= 65535:
        parser.error("--monitor-port must be between 0 and 65535")
    if args.command == "run" and args.deploy is None and not (args.env_url or os.environ.get("EVOAB_ENV_URL")):
        args.deploy = "local"  # the default: start the prepared environment, run, and stop it
    if args.command == "deploy":
        from evoab.envs.docker.config import validate_deployment
        try:
            from evoab.envs.docker.process import enable_child_reaping
            enable_child_reaping()
            adapter = validate_deployment(args)
            from evoab.envs.docker.manager import deploy
            raise SystemExit(deploy(args, adapter))
        except KeyboardInterrupt:
            raise SystemExit(130)
        except (ValueError, OSError, RuntimeError, TimeoutError) as error:
            parser.exit(1, f"{error}\n")
        return
    resolve_config(args, parser, integration_spec, argv)
    if args.print_config or args.validate_config:
        import json

        print(
            json.dumps(public_config(args), ensure_ascii=False, indent=2)
            if args.print_config
            else "Configuration valid"
        )
        return
    if args.command == "run" and args.recipe.loop.episodes.repeat > 1:
        from evoab.recipe.episodes import run_sequence
        raise SystemExit(run_sequence(argv, args))
    if args.command == "run" and args.deploy == "local":
        from evoab.envs.docker.manager import run_evaluation
        from evoab.envs.docker.process import enable_child_reaping
        enable_child_reaping()
        raise SystemExit(run_evaluation(args))
    if args.command == "run" and args.episode_timeout_s and not _supervised:
        import shutil

        timeout = shutil.which("timeout")
        command = [
            sys.executable,
            "-c",
            "from evoab.cli import main; main(_supervised=True)",
            *argv,
        ]
        if timeout is not None:
            # GNU timeout supervises the process group: INT first, then KILL after 30s.
            # Mark the child through a function argument, not a public flag or inherited variable.
            os.execv(timeout, [timeout, "--signal=INT", "--kill-after=30s",
                               str(args.episode_timeout_s), *command])
        # macOS images do not ship GNU timeout. Keep the same signal/exit contract
        # with Python's process supervisor instead of rejecting an otherwise valid run.
        import subprocess
        child = subprocess.Popen(command)
        try:
            raise_code = child.wait(timeout=args.episode_timeout_s)
        except subprocess.TimeoutExpired:
            child.send_signal(getattr(signal, "SIGINT", 2))
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            raise SystemExit(124)
        raise SystemExit(raise_code)
    from evoab.records.telemetry.logging import configure_logging

    configure_logging(args.log_level)
    from evoab.session import run
    run(args)


if __name__ == "__main__":
    main()
