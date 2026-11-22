"""Entry point run by the RUA interpreter; only RUA and the standard library are required."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys


_CREDENTIAL_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "api-key", "cookie", "set-cookie"}


@contextmanager
def protect_model_transport():
    """Keep authentication echoes out of the reference gateway's traces and SDK replies."""
    import httpx
    from uni_robot_agent.provider import client
    from uni_robot_agent.services.errors import HttpCallError

    transport = client.ModelTransport

    class GuardedTransport(transport):
        def request(self, method, url, *, content, headers, check=True):
            credentials: set[bytes] = set()
            for name, value in headers.items():
                name = name.lower()
                if name in {"authorization", "proxy-authorization"}:
                    value = value.partition(" ")[2] or value
                if name in _CREDENTIAL_HEADERS and value:
                    credentials.update((value.encode(), json.dumps(value)[1:-1].encode()))
            try:
                response = super().request(method, url, content=content, headers=headers, check=False)
            except Exception as error:
                diagnostic = str(error).encode(errors="replace")
                if any(secret in diagnostic for secret in credentials):
                    raise RuntimeError("Model transport returned a credential in its diagnostic.") from None
                raise

            response_headers = response.headers.raw
            if any(secret in response.content or any(secret in name or secret in value for name, value in response_headers)
                   for secret in credentials):
                # Reject the whole response so a redacted tool argument cannot execute.
                status = response.status_code if response.is_error else 502
                response = httpx.Response(status, json={"error": {
                    "code": "credential_in_response", "message": "The model provider returned authentication material.",
                }})
            for name in _CREDENTIAL_HEADERS:
                response.headers.pop(name, None)
            if check and not response.is_success:
                raise HttpCallError(response)
            return response

    # The supplied RUA version constructs this transport inside its ModelClient.
    # Scope the override to this evaluator run and leave the gateway's retry and binding intact.
    client.ModelTransport = GuardedTransport
    try:
        yield
    finally:
        client.ModelTransport = transport


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

    if sys.argv[1:] == ["--check-runtime"]:
        import openai_codex
        import uni_robot_agent

        spec = load_integration("robodojo").get_integration_spec()
        flags = ["--integration", "robodojo", "--agent", "codex", "--model", "runtime-check",
                 "--env-url", "http://127.0.0.1:18080", "--no-sam3", "--validate-config"]
        args = build_parser(spec).parse_args(["run", *flags])
        resolve_settings(args, spec, explicit={flag for flag in flags if flag.startswith("--")})
        print(json.dumps({"rua": str(Path(uni_robot_agent.__file__).resolve()),
                          "version": uni_robot_agent.__version__, "sdk": openai_codex.__version__}))
        return

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
        with protect_model_transport() if args.provider_url else nullcontext():
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
