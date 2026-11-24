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

from evoab.composition.compiler import compile_recipe  # noqa: E402
from evoab.composition.graph import annotate_with_trace, recipe_graph  # noqa: E402
from evoab.composition.methods import get_method, list_methods  # noqa: E402
from evoab.composition.recipe import Recipe, with_overrides  # noqa: E402
from evoab.integrations import load_integration  # noqa: E402


def recipe(**roles) -> dict:
    return {"id": "fixture", "label": "Fixture", "roles": roles, "loop": {"entry": next(iter(roles))}}


class RecipeValidation(unittest.TestCase):
    def test_structure_errors(self):
        cases = {
            "not reachable": recipe(main={"capabilities": ["scene"]}, spare={}),
            "unknown role": recipe(main={"capabilities": [{"id": "delegate", "options": {"roles": ["ghost"]}}]}),
            "back to the loop entry": recipe(main={"capabilities": [{"id": "delegate", "options": {"roles": ["sub"]}}]},
                                             sub={"capabilities": [{"id": "delegate", "options": {"roles": ["main"]}}]}),
            "emits action chunks only": recipe(main={"channel": "action", "capabilities": ["scene"]}),
            "require channel: action": recipe(main={"action": {}}),
            "at most once": recipe(main={"capabilities": ["scene", "scene"]}),
        }
        for message, data in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                Recipe.model_validate(data)
        data = recipe(main={"channel": "tools", "capabilities": ["scene"]})
        data["loop"]["episodes"] = {"carry": ["programs"]}
        with self.assertRaisesRegex(ValueError, "code entry role"):
            Recipe.model_validate(data)
        data["loop"]["episodes"] = {"carry": ["skills"]}
        with self.assertRaisesRegex(ValueError, "write: true"):
            Recipe.model_validate(data)

    def test_overrides_by_parameter_and_path(self):
        method = get_method("direct_policy").recipe
        changed = with_overrides(method, {"horizon": "2:9", "representation": "joint_abs",
                                          "roles.main.action.history": [5, 2]})
        self.assertEqual(changed.entry.action.horizon, (2, 9))
        self.assertEqual(changed.entry.action.representation, "joint_abs")
        self.assertEqual(changed.entry.action.history, (5, 2))
        with self.assertRaisesRegex(ValueError, "must be one of"):
            with_overrides(method, {"representation": "teleport"})
        with self.assertRaisesRegex(ValueError, "horizon"):
            with_overrides(method, {"horizon": "9:2"})
        agentic = get_method("agentic").recipe
        narrowed = with_overrides(agentic, {"roles.main.capabilities.gripper.tools": ["move"]})
        self.assertEqual(narrowed.entry.capability("gripper").tools, ["move"])

    def test_methods_are_agent_independent(self):
        from types import SimpleNamespace
        from evoab.composition.compiler import apply_to_args
        spec = load_integration("robolab").get_integration_spec()
        for method in ("agentic", "code_as_policy", "direct_policy", "planner_direct", "code_iterate", "rua"):
            recipe = get_method(method).recipe
            self.assertTrue(all(role.brain.agent is None for role in recipe.roles.values()), method)
            for agent in ("native", "codex", "claude_code", "opencode"):
                pinned = with_overrides(recipe, {f"roles.{recipe.loop.entry}.brain.agent": agent})
                compile_recipe(pinned, spec)
        args = SimpleNamespace(agent_backend="codex", model="preset")
        apply_to_args(args, compile_recipe(get_method("direct_policy").recipe, spec), explicit={"--agent"})
        self.assertEqual(args.agent_backend, "native")  # the action channel has no agent loop
        with self.assertRaisesRegex(ValueError, "external evaluator"):
            apply_to_args(SimpleNamespace(agent_backend="rua", model="m"),
                          compile_recipe(get_method("agentic").recipe, spec), explicit={"--agent"})

    def test_run_program_tool(self):
        from evoab.execution.code_as_policy.runner import ProgramTools
        from evoab.execution.serial import SerialToolExecutor
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
        self.assertTrue({"agentic", "clean", "direct_policy", "code_as_policy", "agent_policy",
                         "planner_direct", "code_iterate", "rua"} <= ids)
        for method in list_methods():
            self.assertTrue(method.card.startswith("# "), method.id)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "agentic"
            root.mkdir()
            (root / "method.yaml").write_text(
                "id: agentic\nlabel: Replaced\nroles: {main: {capabilities: [scene]}}\n")
            with patch.dict(os.environ, {"EVOAB_METHODS_DIR": directory}):
                method = get_method("agentic")
                self.assertEqual((method.recipe.label, method.source), ("Replaced", "user"))
            (root / "method.yaml").write_text("id: other\nlabel: X\nroles: {main: {}}\n")
            with patch.dict(os.environ, {"EVOAB_METHODS_DIR": directory}), \
                    self.assertRaisesRegex(ValueError, "must match its directory"):
                list_methods()

    def test_review_regressions(self):
        spec = load_integration("robolab").get_integration_spec()
        serial = with_overrides(get_method("planner_direct").recipe,
                                {"execution.resource_scheduling": "global-serial"})
        compile_recipe(serial, spec)  # an action delegate steps the environment itself
        data = get_method("planner_direct").recipe.model_dump(mode="json")
        data["roles"]["controller"] = {"brain": {"agent": "native"}, "channel": "tools", "capabilities": ["scene"]}
        data["execution"]["resource_scheduling"] = "global-serial"
        data["parameters"] = []
        with self.assertRaisesRegex(ValueError, "global-serial"):
            compile_recipe(Recipe.model_validate(data), spec)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "rua").mkdir()
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                self.assertEqual(get_method("rua").source, "builtin")
            finally:
                os.chdir(cwd)
        text = with_overrides(get_method("direct_policy").recipe, {"custom_prompt": '"Red" cup first'})
        self.assertEqual(text.entry.action.custom_prompt, '"Red" cup first')

    def test_graph(self):
        graph = recipe_graph(get_method("planner_direct").recipe)
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
        with patch("evoab.provider.catalog.presets", return_value=[{"name": "fixture"}]):
            cfg = config(recipe="planner_direct")
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
                self.assertIn("You cannot move the robot directly", prompt)
                self.assertIn("delegate_controller", (self.directory / "tools.json").read_text())
                recorded = json.loads((self.directory / "recipe.json").read_text())
                self.assertEqual(recorded["id"], "planner_direct")

    def test_sub_role_prompt_is_separate(self):
        data = get_method("planner_direct").recipe.model_dump(mode="json")
        data["roles"]["helper"] = {"description": "Moves only.", "brain": {"agent": "native"},
                                   "channel": "tools", "capabilities": [{"id": "gripper", "tools": ["move"]}]}
        data["roles"]["planner"]["capabilities"][-1]["options"]["roles"].append("helper")
        path = self.directory.parent / "custom.yaml"
        path.write_text(json.dumps(data))
        with patch("evoab.provider.catalog.presets", return_value=[{"name": "fixture"}]):
            with assembled(config(recipe=str(path)), self.directory) as (assembly, *_):
                self.assertEqual(assembly.role_tools["helper"], frozenset({"move"}))
                self.assertIn("move", surface(assembly))
                visible = {spec["name"] for spec in assembly.entry_executor.get_tool_schemas()}
                self.assertNotIn("move", visible)
                assembly.build_prompt("Stack the cups")
                helper = assembly.build_prompt("Move up", "helper").flatten()
                self.assertTrue((self.directory / "prompts" / "helper.md").is_file())
                self.assertNotIn("You cannot move the robot directly", helper)
                effective = (self.directory / "effective_prompt.md").read_text()
                self.assertNotIn("Move up", effective)


    def test_session_runs_delegated_roles(self):
        from evoab.agents.contracts import AgentResult
        from evoab.agents.registry import register_agent
        from evoab.session import Session
        seen = {}

        @register_agent("fixture_agent")
        class FixtureAgent:
            model_protocol, owns_provider = "native", True

            @classmethod
            def add_cli_arguments(cls, parser): ...

            @classmethod
            def validate_config(cls, args, explicit): ...

            @classmethod
            def resolve_defaults(cls, args): ...

            @classmethod
            def config_options(cls, args):
                return {"direct": args.direct_config}

            def __init__(self, *, model, timeout_s=None, direct=None):
                self.model, self.direct = model, direct

            def run(self, *, prompt, task, toolkit, executor, **kwargs):
                seen["tools"] = sorted(spec["name"] for spec in executor.get_tool_schemas())
                seen["prompt"] = prompt.flatten()
                return AgentResult(success=True, message="helped")

            def finalize_without_provider(self, result, session_dir): ...

            def cancel(self, reason): ...

            def close(self): ...

        data = get_method("planner_direct").recipe.model_dump(mode="json")
        for role in data["roles"].values():
            role["brain"]["agent"] = "fixture_agent"
        data["roles"]["helper"] = {"brain": {"agent": "fixture_agent"}, "channel": "tools",
                                   "capabilities": [{"id": "gripper", "tools": ["hold"]}]}
        data["roles"]["planner"]["capabilities"][-1]["options"]["roles"].append("helper")
        path = self.directory.parent / "fixture.yaml"
        path.write_text(json.dumps(data))
        from unittest import mock
        from evoab.agents.native.agent import NativeAgent

        def run_direct(agent, *, episode, integration, instruction, session_dir):
            seen["direct"] = (instruction, agent.direct_config.max_calls, Path(session_dir).name, agent.preset)
            return AgentResult(success=True, message="chunks executed", steps_used=4, turns_used=2)

        with assembled(config(recipe=str(path)), self.directory) as (assembly, *_), \
                mock.patch.object(NativeAgent, "run_direct", run_direct):
            args = assembly.integration_session.args
            args.agent_backend, args.model, args.model_preset_name = "fixture_agent", "fixture-model", "preset-a"
            session = Session._from_resolved(args)
            session._assembly = assembly
            report = session._run_role("controller", "reach the cup")
            # The action role runs on the direct runner with the run's model preset, whatever the agent.
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
        from evoab.execution.serial import SerialToolExecutor
        from evoab.execution.views import ToolView
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
        from evoab.services.environment.contracts import (
            ArmControlCapabilities, ArmDescription, EnvironmentDescription)
        return EnvironmentDescription(arms=[ArmDescription(
            name="arm", control_frame="grasp", grasp_site_description="fixture", rotation_radius_m=0.1,
            joint_names=["j1", "j2"], control=ArmControlCapabilities(commands=list(commands)))],
            control_frequency_hz=10.0, max_episode_steps=8)

    def test_decode_and_clamp(self):
        from evoab.direct.actions import REPRESENTATIONS, JointRow, Limits, OutputError
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
        from evoab.direct.runner import DirectConfig, DirectPolicy
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
                __import__("evoab.services.environment.contracts", fromlist=["CameraDescription"])
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
        from evoab.composition import experience
        method = get_method("code_iterate").recipe
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
        from evoab.composition import experience
        data = get_method("agentic").recipe.model_dump(mode="json")
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
            (staged / "robolab").mkdir(exist_ok=True)
            (staged / "robolab" / "learned.md").write_text("lesson\n")
            experience.persist(args, run, False)
            self.assertEqual((root / "exp" / "skills" / "robolab" / "learned.md").read_text(), "lesson\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
