"""Assembly helpers for externally prepared, Direct gripper simulations."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def build_simulation_spec(name, *, tool_profiles):
    from evoab.context.prompt import environment_for_model, state_json_for_model, Prompt, register_prompt_builder
    from evoab.integrations.gripper import build_gripper_spec, create_session, validate_gripper_cli

    def validate(args):
        validate_gripper_cli(args)
        if args.action_model_url:
            raise ValueError(f"{name} supports Direct control only")

    @register_prompt_builder(name)
    def prompt(context):
        episode = context.episode
        result = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
            "Use the registered robot tools. Do not inspect simulator source, processes, "
            "files or hidden state. Positions are world metres and internal quaternions "
            "are wxyz at the registered grasp site. Follow each tool's argument schema. "
            "Observe the effects of actions; submitting finish does not determine task success."
        ))
        if episode:
            result = result.append(system="Environment description:\n" + environment_for_model(episode.description.environment),
                                   user=None if context.tool_profile == "clean" else "Initial measured state:\n" + state_json_for_model(episode.state))
        return result

    return build_gripper_spec(name=name, create_session=create_session,
                              tool_profiles=tool_profiles, validate_cli=validate)


def materialized_asset(path: Path) -> bool:
    """Return whether a catalog input is present as data on this host."""
    if not path.is_file():
        return False
    size = path.stat().st_size
    if size > 1024:
        return True
    return size > 0 and not path.read_bytes().startswith(b"version https://git-lfs.github.com/spec/")


def source_root(path, revision):
    """Reject a different or modified native implementation before importing it."""
    root = path.expanduser().resolve(strict=True)
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if head != revision:
        raise ValueError(f"Native checkout must be at {revision}; found {head}")
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True)
    if dirty:
        raise ValueError("Native checkout has tracked modifications")
    sys.path.insert(0, str(root))
    return root


def server_parser(description, port):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--depth", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=port)
    return parser


def serve_episode(factory, args, integration):
    from evoab.artifacts.store import ArtifactStore
    from evoab.services.environment.contracts import EpisodeInfo
    from evoab.services.environment.owner import OwnedAdapter
    from evoab.services.environment.routes import build_router
    from evoab.services.environment.service import EnvironmentService
    from evoab.services.hosting import create_app, serve

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(vars(args), default=str, indent=2) + "\n")
    adapter = OwnedAdapter(factory)
    artifacts = None
    service = None
    try:
        instruction, metadata = adapter.episode_identity()
        artifacts = ArtifactStore(output / "artifacts")
        info = EpisodeInfo(episode_id=args.run_id, task=instruction, integration=integration,
                           environment=adapter.description, metadata=metadata,
                           capabilities=["pose", "published_observations"])
        service = EnvironmentService(adapter, info, artifacts)
        service.initialize()
        serve(create_app(build_router(service)), host=args.host, port=args.port)
    finally:
        if service is not None:
            service.close()
        else:
            try:
                adapter.close()
            finally:
                if artifacts is not None:
                    artifacts.close()
