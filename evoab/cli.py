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
from evoab.agents import get_agent_backend, list_agent_backends
from evoab.integrations import load_integration


WORKBENCH_COMMANDS = {
    "runs": "List, launch, trace and stop runs (same as Studio)",
    "methods": "The method library: list, show, graph, components, validate",
    "models": "Model providers and presets",
    "benchmarks": "List benchmarks and tasks; prepare runtimes",
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
        "--integration",
        metavar="NAME",
        help="Check only this integration and fail when it is unavailable",
    )
    parser.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Override the output-directory writability check",
    )
    parser.add_argument(
        "--check-binaries",
        action="store_true",
        help="Run --version on opencode/gemini with a short timeout; disabled by default",
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
            integration=args.integration,
            output_dir=args.output_dir,
            check_binaries=args.check_binaries,
        )
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(doctor.render_json(report) if args.json_output else doctor.render_table(report))
    return 0 if report.ok else 1


def _probe_integration_name(argv: list[str] | None = None) -> str | None:
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    probe.add_argument("command", nargs="?")
    probe.add_argument("--integration", dest="integration")
    return probe.parse_known_args(argv)[0].integration


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
        "--integration",
        dest="integration",
        required=True,
        help="Required environment integration, such as robodojo or libero_pro",
    )
    agent.add_argument(
        "--recipe", metavar="METHOD",
        help="Method id from the method library (evoab methods) or a recipe file; "
             "default: the integration's default method")
    agent.add_argument(
        "--set", action="append", default=[], metavar="PATH=VALUE",
        help="Override a recipe value by dotted path, read as YAML, e.g. "
             "--set 'roles.main.action.horizon=[16,32]' or --set inputs.depth=false (repeatable)")
    agent.add_argument(
        "--experience-dir",
        help="Directory carrying skills/programs between episodes of a method that carries experience")
    agent.add_argument("--agent",
        dest="agent_backend",
        choices=list_agent_backends(),
        default=None,
        help="Override the entry role's agent runtime (default: the recipe's)",
    )
    agent.add_argument("--model", help="Model ID or preset for the entry role (unless the recipe pins one)")
    services = common.add_argument_group("Services")
    for name in ("env-url", "sam3-url"):
        services.add_argument("--" + name, "--" + name.replace("-", "_"))
    services.add_argument("--action-model-url", "--action_model_url", dest="action_model_url"
    )
    services.add_argument(
        "--obs-pipeline",
        "--obs_pipeline",
        dest="obs_pipeline",
        metavar="PATH",
        help="Path to an external observation enhancement pipeline YAML/JSON (or EVOAB_OBS_PIPELINE)",
    )
    from evoab.agents.runtime import add_model_arguments
    add_model_arguments(common)
    parser.agent_options = {}
    for backend in list_agent_backends():
        before = set(common._option_string_actions)
        get_agent_backend(backend).add_cli_arguments(common)
        parser.agent_options[backend] = set(common._option_string_actions) - before
    output = common.add_argument_group("Output and diagnostics")
    output.add_argument("--output-dir")
    output.add_argument(
        "--save-exchanges", action=argparse.BooleanOptionalAction, default=True,
        help="Archive model requests/responses; disabling releases temporary inputs after successful training export, retaining images and diagnostics",
    )
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
    run = subparsers.add_parser(
        "run", parents=[common], allow_abbrev=False, help="Run the agent on one robot task"
    )
    run.add_argument(
        "--instruction", help="Override the environment's task instruction"
    )
    run.add_argument(
        "--episode-timeout-s",
        type=_nonnegative,
        default=10800,
        help="Agent episode wall clock budget; excludes managed environment startup; 0 disables",
    )
    from evoab.services.deployment.config import add_runtime_arguments, add_launch_arguments
    run.add_argument("--deploy", choices=("local",))
    add_runtime_arguments(run)
    add_launch_arguments(run, managed=True)
    run.add_argument("--monitor", action="store_true", help="Serve a local live Web console")
    run.add_argument("--monitor-host", default="127.0.0.1")
    run.add_argument("--monitor-port", type=int, default=8765)
    monitor = subparsers.add_parser("monitor", help="View existing run directories; no model or environment required", allow_abbrev=False)
    monitor.add_argument("--run-dir", type=Path, nargs="+", action="extend", required=True,
                         help="One or more runs or parent directories containing runs")
    monitor.add_argument("--host", default="127.0.0.1")
    monitor.add_argument("--port", type=int, default=8765)
    doctor = subparsers.add_parser(
        "doctor",
        help="Diagnose the installation and configuration offline",
        allow_abbrev=False,
    )
    _add_doctor_arguments(doctor)
    from evoab.observation.toolpackage.cli import add_ob_proxy_arguments
    ob_proxy = subparsers.add_parser(
        "ob-proxy",
        help="Run a standalone observation-enhancement proxy in front of an environment service",
        allow_abbrev=False,
    )
    add_ob_proxy_arguments(ob_proxy)
    setup = subparsers.add_parser("setup", help="Prepare an isolated runtime and resources", allow_abbrev=False)
    target = setup.add_mutually_exclusive_group(required=True)
    target.add_argument("--integration")
    target.add_argument("--service", choices=("actor", "sam3"))
    setup.add_argument("--profile")
    setup.add_argument("--python", help="Base interpreter for the isolated runtime")
    setup.add_argument("--wheel", type=Path, help="Install a matching evoab-runtime wheel in the native environment")
    setup.add_argument("--setup-timeout-s", type=float, default=14400)
    setup.add_argument("--skip-demo-download", action="store_true", help="Skip optional expert demonstration videos")
    setup.add_argument("--image-archive", metavar="FILENAME",
                       help="Import one docker save archive from EVOAB_IMAGE_IMPORT_DIR "
                            "(default: the runtime cache's image-imports directory)")
    setup.add_argument("--check-launch", action="store_true",
                       help="After setup, start one task's environment service once and fail if it does not become ready")
    setup.add_argument("--accept-license", metavar="NAME",
                       help="Accept the named licence a container recipe requires (for example the NVIDIA Isaac Sim EULA)")
    add_runtime_arguments(setup)
    deploy = subparsers.add_parser("deploy", help="Run a prepared service in the foreground", allow_abbrev=False)
    kinds = deploy.add_subparsers(dest="service", required=True)
    for kind in ("env", "actor", "sam3"):
        service = kinds.add_parser(kind, allow_abbrev=False)
        if kind == "env":
            service.add_argument("--integration", required=True)
            service.add_argument("--task")
            service.add_argument("--check", action="store_true",
                                 help="Start the service, wait until it is ready, print its identity and stop it")
        else:
            service.set_defaults(integration=None, task=None)
        service.add_argument("--profile")
        service.add_argument("--run-id")
        service.add_argument("--output-dir", default="runs")
        add_runtime_arguments(service)
        add_launch_arguments(service)
    tui = subparsers.add_parser(
        "tui", parents=[common], allow_abbrev=False, help="Interactive TUI"
    )
    tui.set_defaults(episode_timeout_s=0, instruction=None)
    # Resource commands are dispatched before this parser runs (see main); listed here for --help.
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
        from evoab.observation.toolpackage.cli import main as ob_proxy_main
        raise SystemExit(ob_proxy_main(argv[1:]))
    # Same operations as Studio; they need no integration or run configuration.
    if argv and argv[0] in WORKBENCH_COMMANDS:
        from evoab.workbench.cli import main as workbench_main
        raise SystemExit(workbench_main(argv))
    integration_name = _probe_integration_name(argv)
    try:
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
    if args.command == "monitor":
        from evoab.ui.web.runtime import serve_directories
        try:
            if not 0 <= args.port <= 65535:
                raise ValueError("--port must be between 0 and 65535")
            serve_directories(args)
        except (ValueError, OSError, RuntimeError) as error:
            parser.exit(1, f"{error}\n")
        return
    if args.command == "run" and not 0 <= args.monitor_port <= 65535:
        parser.error("--monitor-port must be between 0 and 65535")
    if args.command in ("setup", "deploy"):
        from evoab.services.deployment.config import validate_deployment
        try:
            from evoab.services.deployment.process import enable_child_reaping
            enable_child_reaping()
            adapter = validate_deployment(args)
            if args.command == "setup":
                from evoab.services.deployment.setup import prepare
                from evoab.services.deployment.process import cancellation_signals
                with cancellation_signals():
                    runtime = prepare(args, adapter)
                    if getattr(args, "check_launch", False) and not getattr(args, "image_archive", None):
                        from evoab.services.deployment.setup import check_launch
                        check_launch(args, adapter, runtime)
            else:
                from evoab.services.deployment.manager import deploy
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
        from evoab.composition.episodes import run_sequence
        raise SystemExit(run_sequence(argv, args))
    if args.command == "run" and (
        args.deploy == "local" or hasattr(get_agent_backend(args.agent_backend), "evaluator_plan")
    ):
        from evoab.services.deployment.manager import run_evaluation
        from evoab.services.deployment.process import enable_child_reaping
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
    from evoab.telemetry.logging import configure_logging

    configure_logging(args.log_level)
    if args.command == "run":
        from evoab.session import run
    else:
        from evoab.session import run_tui as run
    run(args)


if __name__ == "__main__":
    main()
