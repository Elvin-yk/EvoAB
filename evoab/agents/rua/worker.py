"""Entry point run by the RUA interpreter; only RUA and the standard library are required."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> None:
    try:
        from uni_robot_agent.artifacts.run import read_result_json, write_json_atomic
        from uni_robot_agent.cli import build_parser
        from uni_robot_agent.config import resolve_settings
        from uni_robot_agent.integrations import load_integration
        from uni_robot_agent.services.environment.client import EnvironmentClient
        from uni_robot_agent.session import run
        from uni_robot_agent.telemetry.logging import configure_logging
    except ImportError as error:
        raise SystemExit(f"RUA runtime import failed: {error}. Install the reference uni-robot-agent in --rua-python.") from error

    payload = json.load(sys.stdin)
    settings = payload["settings"]
    integration = settings["integration"]
    session_dir = Path(payload["session_dir"])
    spec = load_integration(integration).get_integration_spec()
    parser = build_parser(spec)
    args = parser.parse_args(["run", "--integration", integration, "--agent", "codex", "--model", settings["model"]])
    for name, value in settings.items():
        if not hasattr(args, name):
            raise RuntimeError(f"Incompatible RUA runtime: missing run setting {name}")
        setattr(args, name, value)
    # Deployment and deadlines are owned by EvoAB. RUA may use its own native
    # credentials when no explicit provider or preset was selected.
    for name in ("ACTION_MODEL_URL", "SAM3_URL", "PROVIDER_URL", "EPISODE_DEADLINE"):
        os.environ.pop(f"UNI_ROBOT_AGENT_{name}", None)
    if payload["model_preset"] is not None:
        os.environ.pop("UNI_ROBOT_AGENT_API_KEY", None)
    args._api_key = payload["api_key"]
    args.output_dir = str(session_dir.parent)
    args._session_dir = str(session_dir)
    args._deployment = payload["deployment"]
    resolve_settings(args, spec, explicit=payload["explicit"])
    configure_logging(args.log_level)

    env = EnvironmentClient(args.env_url)
    try:
        info = env.connect()
    finally:
        env.close()
    versions = {"protocol_version", "tool_schema_version"}
    if (not versions <= info.model_fields_set
            or (info.integration, info.protocol_version, info.tool_schema_version) != (integration, "4", "8")):
        raise RuntimeError("RUA requires a RoboDojo episode with environment protocol 4 and tool schema 8")
    if info.lifecycle != "active":
        raise RuntimeError(f"RUA requires an active prepared episode; service reports {info.lifecycle}")
    if args.run_id is not None and args.run_id != info.episode_id:
        raise RuntimeError("RUA environment episode ID does not match the expected run ID")
    args.run_id = info.episode_id
    args._episode_identity = info.model_dump(mode="json")
    if payload["identity"] is not None and payload["identity"]["episode_id"] != args.run_id:
        raise RuntimeError("The environment endpoint changed after EvoAB deployment")
    print(f"RUA attached to {integration} episode {args.run_id} (protocol 4, tool schema 8)", flush=True)

    try:
        run(args)
    finally:
        result_path = session_dir / "result.json"
        if result_path.is_file():
            record = read_result_json(result_path)
            record["rua_agent_backend"] = record["agent_backend"]
            record["agent_backend"] = "rua"
            record["method"] = "rua"
            if payload["model_preset"] is not None:
                record["model_preset"] = payload["model_preset"]
            write_json_atomic(result_path, record)


if __name__ == "__main__":
    main()
