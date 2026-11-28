"""CPU checks for recipe composition, visibility and owned-resource cleanup.

Run from a checkout with PYTHONPATH=. python scripts/validation/tool_composition.py.
No robot, model or perception services are started. --snapshot PATH records the
schemas and prompt text of the built-in methods for cross-revision comparisons;
--compare PATH checks the current revision against such a snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evoab import SessionConfig
from evoab.envs import load_integration
from evoab.envs.protocol.contracts import (
    ArmDescription, CameraDescription, EnvironmentDescription, EpisodeInfo,
)
from evoab.session import assemble_session
from evoab.tools.hybrid.contracts import HybridCapabilities
from evoab.tools.selection import TOOL_GROUPS
from evoab.tools.toolkit import ToolOutput

INTEGRATIONS = ("robodojo", "robotwin", "libero", "libero_plus", "libero_pro", "calvin", "metaworld")
HYBRID = ("robodojo", "calvin", "libero", "metaworld")
BASE_TOOLS = {"list_skills", "read_skill", "view_env_state", "capture_scene",
              "locate", "move", "grasp", "release", "hold", "finish"}
JOBS = set(TOOL_GROUPS["@jobs"])


class Environment:
    def __init__(self, integration):
        self.closed = False
        self.uncertain = False
        self.info = EpisodeInfo(episode_id="fixture", task="validation-task",
            integration=integration, metadata={"instruction_type": "default", "seed": 0},
            environment=EnvironmentDescription(
                arms=[ArmDescription(name="arm", control_frame="grasp",
                    grasp_site_description="Fixture grasp frame", rotation_radius_m=0.1)],
                cameras=[CameraDescription(name="front")],
                control_frequency_hz=20.0, max_episode_steps=100))
        self.initial = {"snapshot_id": "initial", "state_version": 0, "cameras": {},
            "state": {"arms": {"arm": {
                "control_pose": {"position": [0., 0., 0.], "quaternion": [1., 0., 0., 0.]},
                "joints": [], "gripper": 1., "gripper_command": 1.}},
                "state_version": 0, "native_steps": 0, "ended": False}}

    def connect(self):
        return self.info

    def attach_artifact_store(self, store):
        return SimpleNamespace()

    def record(self, step):
        return self.initial

    def capture(self, request):
        return self.initial

    def close(self):
        self.closed = True


class Resource:
    def __init__(self, **values):
        self.closed = False
        self.__dict__.update(values)

    def close(self):
        self.closed = True


EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "recipes"
PROFILE_METHODS = {"direct": "tools", "hybrid": str(EXAMPLES / "policy.yaml"), "clean": str(EXAMPLES / "clean.yaml")}
OVERRIDES = {"tool_return_mode": "execution.return_mode", "observation_profile": "execution.observation_profile"}


def config(integration="robodojo", profile="direct", *, recipe=None, overrides=None, skill_writing=False,
           **settings):
    """A tool-only session for one built-in method; profile names map to the methods that replaced them."""
    options = {"task": "validation-task"}
    recipe_overrides = dict(overrides or {})
    if profile == "hybrid":
        options.update(action_model_checkpoint_sha256="a" * 64)
        recipe_overrides["roles.main.capabilities.hybrid.options"] = {"review": False}
        settings.setdefault("action_model_url", "http://actor.invalid")
    for name, path in OVERRIDES.items():
        if name in settings:
            recipe_overrides[path] = settings.pop(name)
    if skill_writing:
        recipe_overrides["roles.main.capabilities.skills.options"] = {"write": True}
    return SessionConfig(integration=integration, env_url="http://environment.invalid", integration_options=options,
                         recipe=recipe or PROFILE_METHODS.get(profile, profile),
                         recipe_overrides=recipe_overrides, **settings)


@contextmanager
def assembled(configuration, directory, *, command="run", binding_error=False, calibrated=True):
    args = configuration.resolve()
    args.command, args._session_dir = command, str(directory)
    environment = Environment(args.integration)
    if not calibrated:
        environment.info.environment.cameras[0].has_depth = False
        environment.info.environment.cameras[0].has_calibration = False
    owned = []
    def actor(*args, **kwargs):
        resource = Resource(info=SimpleNamespace(model="fixture"))
        owned.append(resource)
        return resource
    def reviewer(**kwargs):
        resource = Resource(enabled=kwargs["enabled"])
        owned.append(resource)
        return resource
    binding = SimpleNamespace(
        capabilities=HybridCapabilities(groups=("arm",), constraint_schema={"type": "object"}),
        action_horizon=40, reviewer_supported=False, reviewer_prompt=None,
        validate_decision=lambda *a: None)
    assembly = None
    with ExitStack() as stack:
        stack.enter_context(patch("evoab.envs.prepared_episode.EnvironmentClient",
                                  return_value=environment))
        stack.enter_context(patch("evoab.tools.gripper.provider.GripperTools.hold",
                                  return_value=ToolOutput(extra={"held": True})))
        actor_mock = stack.enter_context(patch("evoab.envs.hybrid.ActorClient",
                                              side_effect=actor))
        stack.enter_context(patch("evoab.envs.hybrid.ChunkReviewer", side_effect=reviewer))
        stack.enter_context(patch("evoab.servers.perception.client.PerceptionClient",
                                 side_effect=AssertionError("Unexpected perception connection")))
        if args.integration in HYBRID:
            stack.enter_context(patch(
                f"evoab.envs.{args.integration}.action_binding.create_binding",
                side_effect=ValueError("Fixture rejects binding") if binding_error else None,
                return_value=binding))
        try:
            assembly = assemble_session(args)
            yield assembly, environment, owned, actor_mock
        finally:
            if assembly is not None:
                assembly.executor.close()
                if assembly.observation_profile is not None:
                    assembly.observation_profile.close()
                assembly.integration_session.close()
                assembly.telemetry.close()
                assembly.artifacts.close()
            assert environment.closed, "Environment leaked"
            assert all(resource.closed for resource in owned), "Model resource leaked"


def surface(assembly):
    return {item["name"] for item in assembly.executor.get_tool_schemas()}


def snapshots(directory):
    records = {}
    for integration in INTEGRATIONS:
        for profile in (("direct", "hybrid") if integration in HYBRID else ("direct",)):
            for mode in ("immediate", "background"):
                for command in ("run", "serve"):
                    key = f"{integration}/{profile}/{mode}/{command}"
                    cfg = config(integration, profile, tool_return_mode=mode)
                    with assembled(cfg, directory, command=command) as (assembly, *_):
                        prompt = assembly.build_prompt("Fixture instruction")
                        records[key] = {"schemas": assembly.executor.get_tool_schemas(),
                                        "prompt": prompt.flatten()}
    return records


def _run_paths(text: str) -> str:
    import re
    return re.sub(r"/[^\s\"']*/tmp[A-Za-z0-9_]+", "<run>", text)


def compare(previous: dict, current: dict) -> list[str]:
    differences = []
    for key in sorted(set(previous) | set(current)):
        if key not in previous or key not in current:
            differences.append(f"{key}: present in only one revision")
            continue
        old, new = previous[key], current[key]
        if old["schemas"] != new["schemas"]:
            differences.append(f"{key}: tool schemas differ")
        if _run_paths(old["prompt"]) != _run_paths(new["prompt"]):
            differences.append(f"{key}: prompt differs")
    return differences


class CompositionChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "session"

    def tearDown(self):
        self.temp.cleanup()

    def test_all_integrations_and_execution_modes(self):
        for integration in INTEGRATIONS:
            for profile in (("direct", "hybrid") if integration in HYBRID else ("direct",)):
                for mode in ("immediate", "background"):
                    for command in ("run", "serve"):
                        with self.subTest(integration=integration, profile=profile, mode=mode, command=command):
                            cfg = config(integration, profile, tool_return_mode=mode)
                            with assembled(cfg, self.directory, command=command) as (assembly, _, owned, actor):
                                expected = BASE_TOOLS | ({"actor_act"} if profile == "hybrid" else set())
                                expected |= JOBS if mode == "background" else set()
                                self.assertEqual(surface(assembly), expected)
                                self.assertEqual(actor.call_count, int(profile == "hybrid"))
                                self.assertEqual(len(owned), 2 if profile == "hybrid" else 0)
                                modules = assembly.integration_session.tool_providers
                                self.assertEqual("hybrid" in {p.provider_id for p in modules}, profile == "hybrid")
                                from evoab.agent.act.code.bridge import CodePolicyBridge
                                bridge = CodePolicyBridge(assembly.executor)
                                self.assertEqual(bridge.get_tool_schemas(), assembly.executor.get_tool_schemas())
                                bridge.close()
                                # Keep actual gripper policies and scheduling; replace only physical work.
                                result = assembly.executor.call("hold", {"steps": 1})
                                if mode == "immediate":
                                    self.assertEqual(result.status, "completed")
                                    self.assertTrue(result.output.success)
                                else:
                                    self.assertIsNotNone(result.job_id)
                                    self.assertTrue(assembly.executor.job_result(result.job_id, 5).output.success)

    def test_method_validation(self):
        for integration in INTEGRATIONS:
            self.assertEqual(config(integration).resolve().tool_modules, ("skills", "scene", "gripper", "episode"))
            with self.assertRaisesRegex(ValueError, "unknown method"):
                config(integration, recipe="missing").resolve()
            if integration not in HYBRID:
                with self.assertRaisesRegex(ValueError, "no capability 'hybrid'"):
                    config(integration, "hybrid").resolve()
        for missing in ("action_model_url", "action_model_checkpoint_sha256"):
            cfg = config(profile="hybrid")
            changes = {missing: None} if missing == "action_model_url" else {
                "integration_options": {**cfg.integration_options, missing: None}}
            with self.assertRaisesRegex(ValueError, "Hybrid requires"):
                cfg.model_copy(update=changes).resolve()
        read_only = config(overrides={"roles.main.capabilities.skills.tools": ["read_skill"]})
        with assembled(read_only, self.directory) as (assembly, *_):
            self.assertEqual(surface(assembly) & {"list_skills", "read_skill", "write_skill"}, {"read_skill"})
        with self.assertRaisesRegex(ValueError, "has no tools"):
            config(overrides={"roles.main.capabilities.gripper.tools": ["teleport"]}).resolve()
        with self.assertRaisesRegex(ValueError, "compact tool style requires execution.return_mode"):
            config(profile="clean", tool_return_mode="background").resolve()

    def test_user_tools(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        file = self.directory / "my_tools.py"
        file.write_text(
            "from evoab import tool\n"
            "@tool\n"
            "def greet(robot, name: str, times: int = 1) -> str:\n"
            "    \"\"\"Say hello.\"\"\"\n"
            "    return 'hello ' * times + name\n"
            "@tool\n"
            "def look(robot) -> str:\n"
            "    \"\"\"Read the environment state through another tool.\"\"\"\n"
            "    return str(robot.view_env_state(cameras=['missing']))\n"
            "@tool\n"
            "def settle(robot, steps: int) -> dict:\n"
            "    \"\"\"Hold the arm for some steps through the physical hold tool.\"\"\"\n"
            "    return {'held': robot.hold(steps=steps)['success']}\n")
        capabilities = [{"id": "scene"}, {"id": "gripper"}, {"id": "episode"},
                        {"id": "user_tools", "options": {"file": str(file)}}]
        cfg = config(tool_return_mode="immediate", overrides={"roles.main.capabilities": capabilities})
        with assembled(cfg, self.directory) as (assembly, *_):
            schemas = {item["name"]: item for item in assembly.executor.get_tool_schemas()}
            self.assertEqual(schemas["greet"]["description"].split("\n")[0], "Say hello.")
            self.assertEqual(schemas["greet"]["input_schema"]["required"], ["name"])
            self.assertEqual(assembly.executor.call("greet", {"name": "arm", "times": 2}).output.extra,
                             {"result": "hello hello arm"})
            self.assertFalse(assembly.executor.call("greet", {"times": 2}).output.success)  # schema checked
            nested = assembly.executor.call("look", {}).output
            self.assertFalse(nested.success)  # the inner tool's failure reaches the model as this tool's error
            self.assertIn("view_env_state failed", nested.error)
        background = config(tool_return_mode="background", overrides={"roles.main.capabilities": capabilities})
        with assembled(background, self.directory) as (assembly, *_):
            # In background mode the physical inner call is queued; the user tool waits for it.
            started = assembly.executor.call("settle", {"steps": 1})
            output = started.output or assembly.executor.job_result(started.job_id, 5).output
            self.assertEqual(output.extra, {"held": True})
        with self.assertRaisesRegex(ValueError, "first parameter must be `robot`"):
            file.write_text("from evoab import tool\n@tool\ndef bad(x: int) -> str:\n    \"\"\"Doc.\"\"\"\n")
            config(overrides={"roles.main.capabilities": capabilities}).resolve()

    def test_recipe_narrows_visibility(self):
        for mode, jobs in (("background", None), ("background", ["wait_for_tool"])):
            overrides = {"roles.main.capabilities": [{"id": "episode"}]}
            if jobs is not None:
                overrides["roles.main.job_tools"] = jobs
            with assembled(config(tool_return_mode=mode, overrides=overrides), self.directory) as (assembly, *_):
                visible = {item["name"] for item in assembly.entry_executor.get_tool_schemas()}
                self.assertEqual(visible - JOBS, {"finish"})
                if jobs is not None:
                    self.assertEqual(visible & JOBS, set(jobs))
                self.assertFalse(assembly.entry_executor.call("list_skills", {}).output.success)

    def test_cli_recipe_and_set(self):
        from evoab.cli import build_parser
        from evoab.config import resolve_settings
        for integration in INTEGRATIONS:
            spec = load_integration(integration).get_integration_spec()
            parser = build_parser(spec)
            argv = ["run", "--integration", integration, "--model", "fixture",
                    "--task", "validation-task", "--env-url", "http://fixture.invalid",
                    "--recipe", str(EXAMPLES / "clean.yaml"), "--set", "roles.main.max_turns=7"]
            args = resolve_settings(parser.parse_args(argv), spec, agent_enabled=False)
            self.assertEqual((args.recipe_id, args.tool_style, args.tool_return_mode), ("clean", "compact", "immediate"))
            self.assertEqual(args.max_turns, 7)
            self.assertEqual(args.selected_tools, ("move", "hold", "capture_scene", "finish"))
        self.assertFalse({"isaacsim", "mujoco", "torch"} & set(sys.modules))

    def test_observation_and_skill_writing_remain_optional(self):
        from evoab.tools.observation import ObservationTools
        from evoab.agent.context.prompt import PromptContribution
        hub = SimpleNamespace(capture_observation=lambda: {"observation_id": "fixture"})
        profile = SimpleNamespace(tools=ObservationTools(hub).get_tools(), policies={},
            listeners=(), prompt_contributions=(PromptContribution(system="Fixture observer guidance"),),
            close=lambda: None)
        with patch("evoab.session.load_observation_profile",
                   return_value=SimpleNamespace(create_observation_profile=lambda *a: profile)):
            cfg = config("robodojo", observation_profile="robodojo")
            with assembled(cfg, self.directory) as (assembly, *_):
                self.assertTrue(set(TOOL_GROUPS["@observation"]) <= surface(assembly))
                result = assembly.executor.call("capture_observation", {})
                self.assertTrue(result.output.success)
                self.assertIn("Fixture observer guidance", assembly.build_prompt("Observe").flatten())
        library = self.directory / "skill-library"
        library.mkdir(parents=True, exist_ok=True)
        with patch.dict("os.environ", {"EVOAB_SKILLS_DIR": str(library)}):
            with assembled(config(skill_writing=True, tool_return_mode="immediate"),
                           self.directory) as (assembly, *_):
                result = assembly.executor.call("write_skill",
                    {"path": "robodojo/learned.md", "content": "Fixture experience"})
                self.assertTrue(result.output.success)
                self.assertEqual((library / "robodojo/learned.md").read_text(), "Fixture experience")

    def test_capabilities_and_failed_assembly_cleanup(self):
        with assembled(config(), self.directory, calibrated=False) as (assembly, *_):
            self.assertNotIn("locate", surface(assembly))
        with self.assertRaisesRegex(ValueError, "Fixture rejects binding"):
            with assembled(config(profile="hybrid"), self.directory, binding_error=True):
                pass

    def test_custom_composition_omits_motion_and_its_prompt(self):
        observe = {"roles.main.capabilities": ["skills", "scene", "episode"]}
        with assembled(config(overrides=observe), self.directory) as (assembly, *_):
            self.assertFalse({"move", "grasp", "release", "hold"} & surface(assembly))
            self.assertNotIn("Gripper operation labels", assembly.build_prompt("Observe").flatten())
        from evoab.envs.composition import validate_modules
        spec = load_integration("robodojo").get_integration_spec()
        with self.assertRaisesRegex(ValueError, "Duplicate tool module"):
            validate_modules((spec.tool_modules[0], spec.tool_modules[0]))

    def test_act_combinations(self):
        """tools, code and action combine in one session: one chunk tool, both prompts, never partial chunks."""
        chunk = "emit_eef_actions"
        cases = {("tools",): (False, False, False), ("code",): (False, True, False),
                 ("tools", "code"): (False, True, True), ("tools", "action"): (True, False, False),
                 ("code", "action"): (True, True, False), ("tools", "code", "action"): (True, True, True)}
        for act, (has_chunk, code_prompt, both) in cases.items():
            with self.subTest(act=act), assembled(config(overrides={"roles.main.act": list(act)},
                                                         tool_return_mode="immediate"), self.directory) as (assembly, *_):
                self.assertEqual(chunk in surface(assembly), has_chunk)
                self.assertTrue(BASE_TOOLS <= surface(assembly))
                prompt = assembly.build_prompt("Fixture").flatten()
                self.assertEqual("generated Robot API" in prompt, code_prompt)
                self.assertEqual("You can act in both ways" in prompt, both)
                self.assertEqual("ACTION CHUNKS" in prompt, has_chunk)
                self.assertEqual("Use the structured tools" in prompt, "tools" in act or "code" not in act)
                if has_chunk:
                    rejected = assembly.executor.call(chunk, {"actions": [[0, 0, 0]] * 3, "note": "bad rows"}).output
                    self.assertFalse(rejected.success)
                    self.assertTrue(rejected.extra["no_execution"])
        with self.assertRaisesRegex(ValueError, "require action in act"):
            config(overrides={"roles.main.action": {"horizon": [1, 4]}}).resolve()

    def test_clean_method_is_the_compact_four_tool_contract(self):
        observation = patch("evoab.envs.prepared_episode.Episode.observation", return_value={})
        images = patch("evoab.tools.scene.perception.snapshot_images", return_value=[])
        metadata = patch("evoab.tools.scene.perception.snapshot_metadata", return_value={"snapshot_id": "fixture"})
        with observation, images, metadata, \
                assembled(config(profile="clean"), self.directory, calibrated=False) as (assembly, *_):
            self.assertEqual(surface(assembly), {"move", "hold", "capture_scene", "finish"})
            prompt = assembly.build_prompt("Fixture").flatten()
            self.assertIn("Clean result format", prompt)
            self.assertTrue((self.directory / "initial_observation.json").is_file())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    if args.snapshot or args.compare:
        with tempfile.TemporaryDirectory() as directory:
            records = snapshots(Path(directory))
        if args.snapshot:
            args.snapshot.write_text(json.dumps(records, indent=2) + "\n")
            print(f"Recorded {len(records)} schema/prompt combinations")
        if args.compare:
            differences = compare(json.loads(args.compare.read_text()), records)
            print("\n".join(differences) or f"Identical across {len(records)} combinations")
            raise SystemExit(1 if differences else 0)
    else:
        unittest.main(argv=["tool_composition"], verbosity=2)


if __name__ == "__main__":
    main()
