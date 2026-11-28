"""CPU checks for recipes: validation, multi-role delegation, joint control, experience and graphs.

Run from a checkout with PYTHONPATH=. python scripts/validation/recipe_composition.py.
No robot, model or perception services are started; environments and models are fixtures.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_composition import assembled, config, surface  # noqa: E402

from evoab.recipe.compiler import compile_recipe  # noqa: E402
from evoab.recipe.graph import annotate_with_trace, recipe_graph  # noqa: E402
from evoab.recipe.methods import get_method, list_methods  # noqa: E402
from evoab.recipe.schema import Recipe, read_recipe_file, with_overrides  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "recipes"
from evoab.envs import load_integration  # noqa: E402


def recipe(**roles) -> dict:
    return {"id": "fixture", "label": "Fixture", "roles": roles, "loop": {"entry": next(iter(roles))}}


def delegation_recipe() -> Recipe:
    data = recipe(
        planner={
            "act": ["tools"],
            "capabilities": ["skills", {"id": "scene", "tools": ["view_env_state", "capture_scene"]}, "episode",
                             {"id": "delegate", "options": {"roles": ["controller"]}}],
            "prompt": {"append": "Split the task into sub-goals and hand them to the controller."},
        },
        controller={"act": ["action"], "action": {"max_calls": 6}},
    )
    data["execution"] = {"return_mode": "immediate"}
    return Recipe.model_validate(data)


class RecipeValidation(unittest.TestCase):
    def test_structure_errors(self):
        cases = {
            "not reachable": recipe(main={"capabilities": ["scene"]}, spare={}),
            "unknown role": recipe(main={"capabilities": [{"id": "delegate", "options": {"roles": ["ghost"]}}]}),
            "back to the loop entry": recipe(main={"capabilities": [{"id": "delegate", "options": {"roles": ["sub"]}}]},
                                             sub={"capabilities": [{"id": "delegate", "options": {"roles": ["main"]}}]}),
            "only through action has no tools": recipe(main={"act": ["action"], "capabilities": ["scene"]}),
            "require action in act": recipe(main={"action": {}}),
            "at most once": recipe(main={"capabilities": ["scene", "scene"]}),
        }
        for message, data in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                Recipe.model_validate(data)
        data = recipe(main={"act": ["tools"], "capabilities": ["scene"]})
        data["loop"]["episodes"] = {"carry": ["programs"]}
        with self.assertRaisesRegex(ValueError, "code in the entry role"):
            Recipe.model_validate(data)
        data["loop"]["episodes"] = {"carry": ["skills"]}
        with self.assertRaisesRegex(ValueError, "write: true"):
            Recipe.model_validate(data)

    def test_overrides_by_path(self):
        method = get_method("action").recipe
        changed = with_overrides(method, {"roles.main.action.horizon": "[2, 9]",
                                          "roles.main.action.representation": "joint_abs",
                                          "roles.main.action.history": [5, 2]})
        self.assertEqual(changed.entry.action.horizon, (2, 9))
        self.assertEqual(changed.entry.action.representation, "joint_abs")
        self.assertEqual(changed.entry.action.history, (5, 2))
        with self.assertRaisesRegex(ValueError, "Extra inputs"):
            with_overrides(method, {"horizon": "[2, 9]"})  # short parameter names no longer exist
        with self.assertRaisesRegex(ValueError, "horizon"):
            with_overrides(method, {"roles.main.action.horizon": "[9, 2]"})
        tools = get_method("tools").recipe
        narrowed = with_overrides(tools, {"roles.main.capabilities.gripper.tools": ["move"]})
        self.assertEqual(narrowed.entry.capability("gripper").tools, ["move"])

    def test_every_method_runs_on_the_built_in_agent(self):
        from types import SimpleNamespace
        from evoab.recipe.compiler import apply_to_args
        spec = load_integration("calvin").get_integration_spec()
        recipes = [get_method(method).recipe for method in ("tools", "code", "action")]
        recipes += [read_recipe_file(path) for path in sorted(EXAMPLES.glob("*.yaml"))]
        for item in recipes:
            args = SimpleNamespace(model="preset")
            apply_to_args(args, compile_recipe(item, spec))
            self.assertEqual(args.agent_backend, "native", item.id)
        with self.assertRaisesRegex(ValueError, "Extra inputs"):
            with_overrides(get_method("tools").recipe, {"roles.main.brain.agent": "codex"})

    def test_agent_settings_live_in_the_recipe(self):
        from evoab.recipe.compiler import check_inputs
        from evoab.config import SessionConfig
        base = dict(benchmark="libero", env_url="http://127.0.0.1:18760")
        rgb = SessionConfig(**base, recipe_overrides={"inputs.depth": False}).resolve()
        self.assertEqual((rgb.depth, rgb.sam3_url), (False, None))
        with self.assertRaisesRegex(ValueError, "Extra inputs"):
            SessionConfig(**base, recipe_overrides={"inputs.calibration": False}).resolve()
        segmented = {"roles.main.capabilities.scene.options": {"segmentation": True}}
        with self.assertRaisesRegex(ValueError, "--sam3-url"):
            SessionConfig(**base, recipe_overrides=segmented).resolve()
        self.assertEqual(SessionConfig(**base, recipe_overrides=segmented,
                                       sam3_url="http://127.0.0.1:18762").resolve().sam3_url,
                         "http://127.0.0.1:18762")
        # Without segmentation the SAM3 address is ignored: the recipe, not the command line, decides.
        self.assertIsNone(SessionConfig(**base, sam3_url="http://127.0.0.1:18762").resolve().sam3_url)
        policy = SessionConfig(benchmark="calvin", env_url=base["env_url"], recipe=str(EXAMPLES / "policy.yaml"),
                               action_model_url="http://127.0.0.1:18761",
                               benchmark_options={"task": "t", "action_model_checkpoint_sha256": "a" * 64},
                               recipe_overrides={"roles.main.capabilities.hybrid.options": {"review": False}}).resolve()
        self.assertEqual((policy.action_model_binding, policy.vla_review), (None, False))

        def described(depth):
            camera = SimpleNamespace(has_depth=depth)
            return SimpleNamespace(environment=SimpleNamespace(cameras=[camera]))
        inputs = get_method("tools").recipe.inputs
        check_inputs(inputs, described(True))  # None keeps whatever the environment provides
        check_inputs(inputs.model_copy(update={"depth": False}), described(False))
        with self.assertRaisesRegex(ValueError, "provides depth"):
            check_inputs(inputs.model_copy(update={"depth": False}), described(True))

    def test_studio_tool_picker_writes_the_recipe(self):
        from evoab.apps.workbench.launcher import LaunchRequest, resolve_run_recipe
        request = LaunchRequest("libero", "", "m", "medium", method="tools",
                                visible_tools=["move", "capture_scene", "finish", "wait_for_tool"])
        role = resolve_run_recipe(request, {"id": "tools"}).entry
        self.assertEqual({item.id: item.tools for item in role.capabilities},
                         {"scene": ["capture_scene"], "gripper": ["move"], "episode": ["finish"]})
        self.assertEqual(role.job_tools, ["wait_for_tool"])

    def test_run_program_tool(self):
        from evoab.agent.act.code.runner import ProgramTools
        from evoab.runtime.serial import SerialToolExecutor
        from evoab.tools.toolkit import Toolkit
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            images.mkdir()
            tools = ProgramTools(SerialToolExecutor(Toolkit()), cwd=root, env={"MARK": "ok"}, image_dir=images)
            self.assertIn("run_program", [spec["name"] for spec in tools.get_tool_schemas()])
            code = ("import os, pathlib\n"
                    "pathlib.Path(os.environ['IMG']).write_bytes(b'\\x89PNG')\nprint(os.environ['MARK'])")
            tools._env["IMG"] = str(images / "view.png")
            output = tools.call("run_program", {"code": code}).output
            self.assertTrue(output.success, output.extra)
            self.assertEqual(output.extra["stdout"].strip(), "ok")
            self.assertEqual([image.name for image in output.images], ["view.png"])
            failed = tools.call("run_program", {"code": "raise SystemExit(3)"}).output
            self.assertFalse(failed.success)
            self.assertEqual(failed.extra["exit_code"], 3)
            self.assertTrue((root / "program_002.py").is_file())

    def test_method_library(self):
        ids = {method.id for method in list_methods()}
        self.assertEqual({"tools", "code", "action"}, ids)
        self.assertNotIn("planner_direct", ids)
        for method in list_methods():
            self.assertTrue(method.card.startswith("# "), method.id)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tools"
            root.mkdir()
            (root / "method.yaml").write_text(
                "id: tools\nlabel: Replaced\nroles: {main: {capabilities: [scene]}}\n")
            with patch.dict(os.environ, {"EVOAB_METHODS_DIR": directory}):
                method = get_method("tools")
                self.assertEqual((method.recipe.label, method.source), ("Replaced", "user"))
            (root / "method.yaml").write_text("id: other\nlabel: X\nroles: {main: {}}\n")
            with patch.dict(os.environ, {"EVOAB_METHODS_DIR": directory}), \
                    self.assertRaisesRegex(ValueError, "must match its directory"):
                list_methods()

    def test_review_regressions(self):
        spec = load_integration("calvin").get_integration_spec()
        serial = with_overrides(delegation_recipe(),
                                {"execution.resource_scheduling": "global-serial"})
        compile_recipe(serial, spec)  # an action delegate steps the environment itself
        data = delegation_recipe().model_dump(mode="json")
        data["roles"]["controller"] = {"act": ["tools"], "capabilities": ["scene"]}
        data["execution"]["resource_scheduling"] = "global-serial"
        with self.assertRaisesRegex(ValueError, "global-serial"):
            compile_recipe(Recipe.model_validate(data), spec)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "tools").mkdir()
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.assertEqual(get_method("tools").source, "builtin")
            finally:
                os.chdir(cwd)
        text = with_overrides(get_method("action").recipe, {"roles.main.action.custom_prompt": '"Red" cup first'})
        self.assertEqual(text.entry.action.custom_prompt, '"Red" cup first')

    def test_graph(self):
        graph = recipe_graph(delegation_recipe())
        kinds = {(edge["from"], edge["to"], edge["kind"]) for edge in graph["edges"]}
        self.assertIn(("planner:channel", "role:controller", "delegates"), kinds)
        self.assertIn(("controller:channel", "body", "action chunks"), kinds)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "trajectory.jsonl").write_text("\n".join(json.dumps(item) for item in (
                {"type": "ToolExecution", "phase": "started", "tool_name": "capture_scene"},
                {"type": "ToolExecution", "phase": "finished", "tool_name": "capture_scene"},
                {"type": "ToolExecution", "phase": "started", "tool_name": "delegate_controller"},
            )) + "\n")
            annotated = annotate_with_trace(graph, Path(directory))
            scene = next(n for n in annotated["nodes"] if n["id"] == "planner:cap:scene")
            self.assertEqual(scene["calls"], 1)


class MultiRoleAssembly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "session"

    def tearDown(self):
        self.temp.cleanup()

    def test_planner_sees_its_tools_and_the_delegate(self):
        with patch("evoab.agent.models.catalog.presets", return_value=[{"name": "fixture"}]):
            path = self.directory.parent / "delegation.yaml"
            path.write_text(delegation_recipe().model_dump_json())
            cfg = config(recipe=str(path))
            with assembled(cfg, self.directory) as (assembly, *_):
                self.assertEqual(surface(assembly) & {"move", "grasp"}, set())
                visible = {spec["name"] for spec in assembly.entry_executor.get_tool_schemas()}
                self.assertEqual(visible, {"list_skills", "read_skill", "view_env_state", "capture_scene",
                                           "finish", "delegate_controller"})
                self.assertEqual(assembly.role_tools["controller"], frozenset())
                # Unbound dispatch reports instead of raising.
                result = assembly.entry_executor.call("delegate_controller", {"instruction": "reach"})
                self.assertFalse(result.output.success)
                calls = []
                assembly.delegation.bind(lambda role, text: calls.append((role, text)) or {"role": role, "steps": 3})
                result = assembly.entry_executor.call("delegate_controller", {"instruction": "reach the cup"})
                self.assertTrue(result.output.success)
                self.assertEqual(result.output.extra["report"]["steps"], 3)
                self.assertEqual(calls, [("controller", "reach the cup")])
                denied = assembly.entry_executor.call("move", {})
                self.assertFalse(denied.output.success)
                prompt = assembly.build_prompt("Stack the cups").flatten()
                self.assertIn("Split the task into sub-goals", prompt)
                self.assertIn("delegate_controller", (self.directory / "tools.json").read_text())
                recorded = json.loads((self.directory / "recipe.json").read_text())
                self.assertEqual(recorded["id"], "fixture")

    def test_sub_role_prompt_is_separate(self):
        data = delegation_recipe().model_dump(mode="json")
        data["roles"]["helper"] = {"description": "Moves only.",
                                   "act": ["tools"], "capabilities": [{"id": "gripper", "tools": ["move"]}]}
        data["roles"]["planner"]["capabilities"][-1]["options"]["roles"].append("helper")
        path = self.directory.parent / "custom.yaml"
        path.write_text(json.dumps(data))
        with patch("evoab.agent.models.catalog.presets", return_value=[{"name": "fixture"}]):
            with assembled(config(recipe=str(path)), self.directory) as (assembly, *_):
                self.assertEqual(assembly.role_tools["helper"], frozenset({"move"}))
                self.assertIn("move", surface(assembly))
                visible = {spec["name"] for spec in assembly.entry_executor.get_tool_schemas()}
                self.assertNotIn("move", visible)
                assembly.build_prompt("Stack the cups")
                helper = assembly.build_prompt("Move up", "helper").flatten()
                self.assertTrue((self.directory / "prompts" / "helper.md").is_file())
                self.assertNotIn("Split the task into sub-goals", helper)
                effective = (self.directory / "effective_prompt.md").read_text()
                self.assertNotIn("Move up", effective)


    def test_session_runs_delegated_roles(self):
        from evoab.agent.contracts import AgentResult
        from evoab.session import Session
        seen = {}

        def run(agent, *, prompt, task, toolkit, executor, **kwargs):
            seen["tools"] = sorted(spec["name"] for spec in executor.get_tool_schemas())
            seen["prompt"] = prompt.flatten()
            return AgentResult(success=True, message="helped")

        data = delegation_recipe().model_dump(mode="json")
        data["roles"]["helper"] = {"act": ["tools"],
                                   "capabilities": [{"id": "gripper", "tools": ["hold"]}]}
        data["roles"]["planner"]["capabilities"][-1]["options"]["roles"].append("helper")
        path = self.directory.parent / "fixture.yaml"
        path.write_text(json.dumps(data))
        from unittest import mock
        from evoab.agent.loop import NativeAgent

        def run_direct(agent, *, episode, integration, instruction, session_dir):
            seen["direct"] = (instruction, agent.direct_config.max_calls, Path(session_dir).name, agent.preset)
            return AgentResult(success=True, message="chunks executed", steps_used=4, turns_used=2)

        with assembled(config(recipe=str(path)), self.directory) as (assembly, *_), \
                mock.patch.object(NativeAgent, "run_direct", run_direct), mock.patch.object(NativeAgent, "run", run), \
                mock.patch.object(NativeAgent, "validate_config", classmethod(lambda cls, args, explicit: None)):
            args = assembly.integration_session.args
            args.agent_backend, args.model = "native", "preset-a"
            session = Session._from_resolved(args)
            session._assembly = assembly
            report = session._run_role("controller", "reach the cup")
            # The action role runs on the direct runner with the run's model preset.
            self.assertEqual(seen["direct"], ("reach the cup", 6, "001_controller", "preset-a"))
            self.assertTrue(report["success"])
            self.assertEqual(report["steps"], 4)
            self.assertTrue((self.directory / "roles" / "001_controller" / "report.json").is_file())
            report = session._run_role("helper", "hold still")
            self.assertEqual(seen["tools"], ["hold"])
            self.assertIn("hold still", seen["prompt"])
            self.assertEqual(report["message"], "helped")


class ToolViewChecks(unittest.TestCase):
    def test_view_admits_and_denies(self):
        from evoab.runtime.serial import SerialToolExecutor
        from evoab.runtime.views import ToolView
        from evoab.tools.toolkit import ToolDef, ToolOutput, Toolkit
        toolkit = Toolkit()
        for name in ("a", "b"):
            toolkit.register_tools([ToolDef(name, {"name": name, "description": name, "input_schema": {
                "type": "object", "properties": {}}}, handler=lambda: ToolOutput(extra={"ok": True}))])
        view = ToolView(SerialToolExecutor(toolkit), {"a"}, registered=toolkit.get_tool_names())
        self.assertEqual([s["name"] for s in view.get_tool_schemas()], ["a"])
        self.assertTrue(view.call("a", {}).output.success)
        result = view.call("b", {})
        self.assertFalse(result.output.success)
        self.assertTrue(result.output.extra["no_execution"])


class JointControl(unittest.TestCase):
    def description(self, commands=("pose", "joints")):
        from evoab.envs.protocol.contracts import (
            ArmControlCapabilities, ArmDescription, EnvironmentDescription)
        return EnvironmentDescription(arms=[ArmDescription(
            name="arm", control_frame="grasp", grasp_site_description="fixture", rotation_radius_m=0.1,
            joint_names=["j1", "j2"], control=ArmControlCapabilities(commands=list(commands)))],
            control_frequency_hz=10.0, max_episode_steps=8)

    def test_decode_and_clamp(self):
        from evoab.agent.act.action.actions import REPRESENTATIONS, JointRow, Limits, OutputError
        joint = REPRESENTATIONS["joint_abs"].bind(self.description())
        schema = joint.schema((1, 3), ("arm",))
        self.assertEqual(schema["function"]["parameters"]["properties"]["actions"]["items"]["minItems"], 3)
        rows = joint.decode_chunk({"actions": [[0.2, 0.0, 1.0], [0.21, 0.0, 1.0]], "note": "n"},
                                  {"arm": JointRow((0.0, 0.0), 0.0)}, (1, 3), Limits(joint_rad=0.05))
        self.assertEqual(rows[0]["arm"].joints, (0.05, 0.0))
        self.assertTrue(rows[0]["arm"].clamped)
        self.assertAlmostEqual(rows[1]["arm"].joints[0], 0.1)
        with self.assertRaises(OutputError):
            joint.decode_chunk({"actions": [[0.2, 1.0]], "note": "n"},
                               {"arm": JointRow((0.0, 0.0), 0.0)}, (1, 3), Limits())
        with self.assertRaisesRegex(ValueError, "does not accept joint commands"):
            REPRESENTATIONS["joint_abs"].bind(self.description(commands=("pose",)))

    def test_direct_runner_sends_joint_commands(self):
        from PIL import Image
        from evoab.agent.act.action.runner import DirectConfig, DirectPolicy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "front.png"
            Image.new("RGB", (8, 8)).save(image)
            sent = []

            class Env:
                def __init__(self):
                    self.step_count = 0

                def snapshot(self):
                    return {"state": {"native_steps": self.step_count, "arms": {"arm": {
                        "control_pose": {"position": [0, 0, 0], "quaternion": [1, 0, 0, 0]},
                        "joints": [0.0, 0.0], "gripper": 1.0, "gripper_command": 1.0}}},
                        "state_version": self.step_count, "captured_at": 0.0, "cameras": {}}

                def capture(self, request):
                    return self.snapshot()

                def step(self, request):
                    sent.append(request.command)
                    self.step_count += 1
                    return SimpleNamespace(execution_status="completed", feedback={})

            env = Env()
            episode = SimpleNamespace(
                env=env, description=SimpleNamespace(environment=self.description()),
                state=SimpleNamespace(ended=False, state_version=0), latest_snapshot=None,
                image_paths=lambda snapshot: {"front": str(image)}, accept_primitive=lambda reply: None)
            episode.description.environment.cameras.append(
                __import__("evoab.envs.protocol.contracts", fromlist=["CameraDescription"])
                .CameraDescription(name="front"))
            completion = SimpleNamespace(error=None, finish_reason="tool_calls", usage={}, content="",
                                         reasoning=None, started_at=0.0, latency_s=0.1, tool_calls=[{
                                             "name": "emit_joint_actions",
                                             "arguments": {"actions": [[0.01, 0.02, 1.0]], "note": "go"}}])
            model = SimpleNamespace(complete=lambda *a, **k: completion, describe=lambda: {"model": "fixture"})
            policy = DirectPolicy(episode, model, integration="fixture", instruction="reach", output_dir=root,
                                  config=DirectConfig(representation="joint_abs", min_horizon=1, max_horizon=3,
                                                      max_calls=2))
            result = policy.run()
            self.assertEqual(result.stop_reason, "call_limit")
            self.assertEqual(len(sent), 2)
            self.assertEqual(sent[0].kind, "joints")
            self.assertEqual(sent[0].arms["arm"].joints, [0.01, 0.02])
            self.assertEqual(sent[0].arms["arm"].gripper, 0.0)
            prompt = (root / "direct" / "prompt.md").read_text()
            self.assertIn("arm: [j1, j2, gripper]", prompt)


class Experience(unittest.TestCase):
    def test_programs_promoted_only_after_success(self):
        from evoab.agent.memory import experience
        method = read_recipe_file(EXAMPLES / "code_iterate.yaml")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = SimpleNamespace(experience_dir=str(root / "exp"), recipe=method)
            for index, success in enumerate((False, True, None)):
                run = root / f"run{index}"
                (run / "scratch").mkdir(parents=True)
                experience.prepare(args, run)
                programs = Path(args.programs_dir)
                (programs / f"attempt{index}.py").write_text("print(1)\n")
                experience.persist(args, run, success)
            kept = sorted(p.name for p in (root / "exp" / "programs").iterdir())
            # Run 1 started from nothing (run 0 failed) and succeeded; run 2 is not kept.
            self.assertEqual(kept, ["attempt1.py"])
            log = [json.loads(line) for line in (root / "exp" / "episodes.jsonl").read_text().splitlines()]
            self.assertEqual([entry["kept"] for entry in log], [[], ["programs"], []])

    def test_skills_are_staged_and_seeded(self):
        from evoab.agent.memory import experience
        data = get_method("tools").recipe.model_dump(mode="json")
        data["roles"]["main"]["capabilities"][0] = {"id": "skills", "options": {"write": True}}
        data["loop"]["episodes"] = {"carry": ["skills"], "keep": "always"}
        method = Recipe.model_validate(data)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = SimpleNamespace(experience_dir=str(root / "exp"), recipe=method)
            run = root / "run"
            (run / "scratch").mkdir(parents=True)
            experience.prepare(args, run)
            staged = Path(args.skills_root)
            self.assertTrue((staged / "general").is_dir())
            (staged / "libero").mkdir(exist_ok=True)
            (staged / "libero" / "learned.md").write_text("lesson\n")
            experience.persist(args, run, False)
            self.assertEqual((root / "exp" / "skills" / "libero" / "learned.md").read_text(), "lesson\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
