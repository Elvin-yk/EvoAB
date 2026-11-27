"""Validate recipe wiring, model handoffs and policy selection over real HTTP fixture services."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import APIRouter
import yaml

from calvin_actor import ActorFixture, NativeFixture, adapter_fixture
from evoab import AgentConfig, Session, SessionConfig
from evoab.agents.contracts import AgentResult
from evoab.artifacts.store import ArtifactStore
from evoab.composition.methods import get_method
from evoab.services.actor.routes import build_router as actor_router
from evoab.services.actor.service import ActorService
from evoab.services.environment.contracts import EpisodeInfo
from evoab.services.environment.routes import build_router as environment_router
from evoab.services.environment.service import EnvironmentService
from evoab.services.hosting import HttpServer, create_app

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/recipes/routed_policy.yaml"


def serve(stack, router):
    server = HttpServer(create_app(router))
    stack.callback(server.stop)
    server.start_background()
    return server.url


class CompositionTests(unittest.TestCase):
    def test_routed_policy(self):
        for choice in ("open", "close", "invalid"):
            with self.subTest(choice=choice), ExitStack() as stack:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                artifacts = ArtifactStore(root / "environment")
                stack.callback(artifacts.close)
                native = NativeFixture()
                adapter = adapter_fixture(native)
                identity = EpisodeInfo(episode_id=adapter.episode_id, task=adapter.instruction,
                    integration="calvin", environment=adapter.description, metadata=adapter.metadata,
                    capabilities=["pose", "native", "published_observations"])
                env = EnvironmentService(adapter, identity, artifacts)
                stack.callback(env.close)
                env.initialize()
                env_url = serve(stack, environment_router(env))
                recipe = yaml.safe_load(EXAMPLE.read_text())
                apis = recipe["apis"]
                if choice == "close":
                    apis["perceive"] = {"kind": "flow", "steps": [{
                        "id": "scene", "call": "inspect_scene",
                        "args": {"task": {"from": "input.instruction"}}}]}
                    apis["run_episode"]["steps"][0].update(
                        call="perceive", args={"instruction": {"from": "input.instruction"}})
                actors = {}
                for name in ("open", "close"):
                    actor = actors[name] = ActorFixture()
                    settings = apis["drawer_" + name]
                    actor.info = actor.info.model_copy(update={"checkpoint_sha256": settings["checkpoint_sha256"]})
                    service = ActorService(actor, max_batch_size=1)
                    stack.callback(service.close)
                    settings["url"] = serve(stack, actor_router(service))
                route = APIRouter()

                @route.post("/inspect")
                def inspect(data: dict):
                    self.assertEqual(data["task"], "open drawer")
                    return {"objects": ["drawer"]}

                apis["inspect_scene"]["url"] = serve(stack, route) + "/inspect"
                apis["choose"]["brain"] = {"model": "chooser"}
                apis["second_opinion"]["brain"] = {"model": "expert"}
                file = root / "recipe.yaml"
                file.write_text(yaml.safe_dump(recipe))
                stack.enter_context(patch("evoab.provider.catalog.presets", return_value=[
                    {"name": "chooser"}, {"name": "expert"}]))

                def infer(agent, *, prompt, task, executor, **kwargs):
                    names = {s["name"] for s in executor.get_tool_schemas()}
                    if agent.preset == "expert":
                        self.assertNotIn("drawer_open", names)
                        self.assertNotIn("delegate_second_opinion", names)
                        self.assertEqual(task, "inspect drawer")
                        return AgentResult(success=True, message="Use the drawer policy")
                    self.assertEqual(agent.preset, "chooser")
                    self.assertEqual(json.loads(task), {"objects": ["drawer"], "task": "open drawer"})
                    self.assertNotIn("inspect_scene", names)
                    self.assertNotIn("drawer_close", names)
                    self.assertIn("delegate_second_opinion", names)
                    receipt = executor.call("delegate_second_opinion", {"instruction": "inspect drawer"})
                    self.assertTrue(receipt.output.success)
                    return AgentResult(success=True, message=json.dumps({"policy": choice}))

                stack.enter_context(patch("evoab.agents.native.agent.NativeAgent.run", infer))
                cfg = SessionConfig(integration="calvin", env_url=env_url, agent=AgentConfig(),
                                    output_dir=root / "run", recipe=str(file))
                with Session(cfg) as session:
                    result = session.run_agent()
                records = [json.loads(s) for s in (result.session_dir / "flow.jsonl").read_text().splitlines()]
                self.assertEqual(len(native.actions), 0 if choice == "invalid" else 2)
                for name, actor in actors.items():
                    self.assertEqual(len(actor.observations), int(name == choice))
                self.assertEqual(records[-1]["ok"], choice != "invalid")
                runs = result.session_dir / "roles"
                parent, = runs.glob("*_choose/handoff.json")
                child, = runs.glob("*_second_opinion/handoff.json")
                if choice == "close":
                    report, = runs.glob("*_perceive/report.json")
                    self.assertEqual(json.loads(report.read_text())["value"], {"objects": ["drawer"]})
                self.assertEqual(json.loads(parent.read_text())["parent"], "entry")
                self.assertEqual(json.loads(child.read_text())["parent"], parent.parent.name)

    def test_bad_reference_fails_offline(self):
        recipe = yaml.safe_load(EXAMPLE.read_text())
        recipe["apis"]["run_episode"]["steps"][0]["args"] = {"x": {"from": "missing.y"}}
        from evoab.composition.recipe import Recipe
        with self.assertRaisesRegex(ValueError, "earlier step"):
            Recipe.model_validate(recipe)

    def test_global_and_local_prompt_files_and_direct_contract(self):
        from evoab.composition.compiler import compile_recipe
        from evoab.composition.recipe import Recipe
        from evoab.integrations import load_integration
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("global_start", "global_call", "local_start", "local_call"):
                (root / name).write_text(name)
            recipe = Recipe.model_validate({"id": "check", "label": "Check",
                "prompts": {"start": ["global_start"], "each_call": ["global_call"]},
                "apis": {"hands": {"kind": "model", "channel": "action",
                    "inputs": {"history": [1, 1], "blocks": []},
                    "output": {"representation": "delta_eef", "horizon": [1, 2]},
                    "budget": {"max_calls": 3, "repair_attempts": 1},
                    "prompts": {"start": ["local_start"], "each_call": ["local_call"]}}}})
            compiled = compile_recipe(recipe, load_integration("robodojo").get_integration_spec(), base_dir=root)
            direct = compiled.entry.direct
            self.assertEqual(compiled.entry.name, "hands")
            self.assertEqual(direct.start_prompt, "global_start\n\nlocal_start")
            self.assertEqual(direct.custom_prompt, "global_call\n\nlocal_call")
            self.assertEqual((direct.representation, direct.blocks, direct.max_calls), ("delta_eef", (), 3))

    def test_external_backend_loads_weights(self):
        import hashlib
        import sys
        from evoab.services.actor.config import resolve_actor
        from evoab.services.actor.registry import backend_names
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "fixture_weights_backend"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("from evoab.services.actor.registry import BackendDefinition\n"
                "definition = BackendDefinition('weights_example', __name__, 'Model')\n")
            (pkg / "config.py").write_text("def resolve_config(settings): return settings\n"
                                         "def service_limits(config): return {}\n")
            (pkg / "backend.py").write_text("from pathlib import Path\nimport hashlib, json\nclass Model:\n"
                "    def __init__(self, config):\n        raw = Path(config['checkpoint']).read_bytes()\n"
                "        if hashlib.sha256(raw).hexdigest() != config['checkpoint_sha256']:\n"
                "            raise ValueError('wrong weights')\n        self.weights = json.loads(raw)\n")
            dist = root / "weights_example-1.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: weights_example\nVersion: 1\n")
            (dist / "entry_points.txt").write_text(
                "[evoab.actor_backends]\nweights_example = fixture_weights_backend:definition\n")
            weights = root / "weights.json"
            weights.write_text("[2, 3]")
            params = {"backend": "weights_example", "checkpoint": str(weights),
                      "checkpoint_sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}
            with patch.object(sys, "path", [str(root), *sys.path]):
                self.assertIn("weights_example", backend_names())
                resolved = resolve_actor(params)
                self.assertEqual(resolved.definition.create(resolved.config).weights, [2, 3])
                weights.write_text("[1]")
                with self.assertRaisesRegex(ValueError, "wrong weights"):
                    resolved.definition.create(resolved.config)

    def test_fenced_result_is_validated(self):
        from evoab.composition.flow import checked_output
        result = AgentResult(success=True, message='```json\n{"ready": true}\n```')
        self.assertEqual(checked_output(result, {"type": "object"}), {"ready": True})
        result.message = result.message.removesuffix("\n```")
        self.assertEqual(checked_output(result, {"type": "object"}), {"ready": True})
        result.message += " explanatory text"
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            checked_output(result, {"type": "object"})

    def test_code_bridge_keeps_invocation_across_transport(self):
        from contextvars import Context
        from unittest.mock import Mock
        from evoab.execution import ExecutionResult
        from evoab.execution.code_as_policy.bridge import CodePolicyBridge
        from evoab.execution.correlation import invocation_path
        from evoab.tools.toolkit import ToolOutput

        executor = Mock()
        executor.call.side_effect = lambda *args: ExecutionResult.completed(
            ToolOutput(extra={"path": invocation_path.get()}))
        token = invocation_path.set(("001_writer",))
        try:
            bridge = CodePolicyBridge(executor)
        finally:
            invocation_path.reset(token)
        result = Context().run(bridge.call, "delegate_review", {})
        self.assertEqual(result.output.extra["path"], ("001_writer",))
        self.assertEqual(invocation_path.get(), ())

    def test_legacy_direct(self):
        from evoab.composition.compiler import compile_recipe
        from evoab.composition.recipe import with_overrides
        from evoab.integrations import load_integration
        recipe = get_method("direct_action").recipe
        self.assertEqual(recipe.entry.channel, "action")
        recipe = with_overrides(recipe, {f"roles.{recipe.loop.entry}.action.blocks": []})
        compiled = compile_recipe(recipe, load_integration("robodojo").get_integration_spec())
        self.assertEqual(compiled.entry.direct.blocks, ("robot", "timing"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
