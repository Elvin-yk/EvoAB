"""CPU validation suite for the external Observation ToolPackage.

Run from a checkout with:
    PYTHONPATH=. python scripts/validation/observation_toolpackage.py

Exercises config validation, hybrid prompt resolution, SAM3 + FoundationPose
processors, non-destructive rendering, full Environment Protocol v4 HTTP proxy
forwarding (including RemoteArtifactMirror),
runtime /ob/targets override, and offline CLI/SessionConfig validation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
from fastapi import APIRouter
from PIL import Image

from evoab import SessionConfig
from evoab.records.artifacts.mirror import RemoteArtifactMirror
from evoab.records.artifacts.store import ArtifactStore
from evoab.envs.observation.toolpackage import (
    AutoPromptConfig,
    DetectionAnnotation,
    FrameInput,
    ObservationPipelineConfig,
    ObservationProxyServer,
    PluginConfig,
    RenderConfig,
    TargetConfig,
    TargetsConfig,
    TargetSpec,
    load_pipeline_config,
)
from evoab.envs.observation.toolpackage.pipeline import ObservationPipeline, annotation_iou, deduplicate_annotations
from evoab.envs.observation.toolpackage.plugins import create_processor
from evoab.envs.observation.toolpackage.plugins.foundationpose import FoundationPoseProcessor
from evoab.envs.observation.toolpackage.plugins.sam3 import Sam3Processor
from evoab.envs.observation.toolpackage.prompts import extract_prompts_by_rule, resolve_targets
from evoab.envs.observation.toolpackage.renderer import render_annotations
from evoab.envs.protocol.client import EnvironmentClient
from evoab.envs.protocol.contracts import (
    ArmDescription,
    ArmState,
    CameraDescription,
    CaptureRequest,
    EnvironmentDescription,
    EnvironmentState,
    EpisodeInfo,
    FinishRequest,
    JointCommand,
    JointTarget,
    Pose,
    StepReceipt,
    StepRequest,
)
from evoab.envs.protocol.routes import build_router
from evoab.envs.protocol.service import EnvironmentService
from evoab.envs.protocol.snapshots import CameraFrame, CapturedObservation
from evoab.servers.hosting import HttpServer, create_app
from evoab.servers.perception.backend import SegmentationResult

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_rgb_png(height: int = 64, width: int = 80, color: tuple[int, int, int] = (40, 60, 80)) -> tuple[np.ndarray, bytes]:
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:, :] = color
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    return rgb, buf.getvalue()


def _make_camera_frame(height: int = 64, width: int = 80) -> CameraFrame:
    rgb, _ = _make_rgb_png(height, width)
    depth = np.full((height, width), 0.75, dtype=np.float32)
    intrinsic = np.array([
        [100.0, 0.0, width / 2.0],
        [0.0, 100.0, height / 2.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    extrinsic = np.eye(4, dtype=np.float64)
    extrinsic[:3, 3] = [0.2, 0.0, 0.9]
    return CameraFrame(rgb=rgb, depth_m=depth, intrinsic_K=intrinsic, T_world_from_optical=extrinsic)

#: Fixture cameras of a three-view environment.
X5_CAMERAS = ("head", "left_wrist", "right_wrist")


class _DummyCustomProcessor:
    name = "dummy_custom"

    def __init__(self, tag: str = "custom"):
        self.tag = tag
        self.calls = 0

    def process(self, frame, targets, existing):
        del existing
        self.calls += 1
        if not targets:
            return []
        target = targets[0]
        h, w = frame.rgb.shape[:2]
        mask = np.zeros((h, w), dtype=bool)
        mask[12:32, 16:40] = True
        return [
            DetectionAnnotation(
                label=target.label,
                prompt=target.prompt,
                source=f"{self.name}:{self.tag}",
                score=0.93,
                box_xyxy=(16.0, 12.0, 40.0, 32.0),
                mask=mask,
                centroid_world_xyz=[0.21, -0.05, 0.15],
                geometry_valid=True,
                color=target.color,
            )
        ]

    def close(self) -> None:
        return None


class _FakePerceptionClient:
    def __init__(self):
        self.queries: list[str] = []

    def segment(self, image_bytes: bytes, *, query, min_score: float = 0.0) -> SegmentationResult:
        del image_bytes, min_score
        self.queries.append(query.text)
        mask = np.zeros((64, 80), dtype=bool)
        mask[20:44, 24:56] = True
        return SegmentationResult(
            status="matched",
            score=0.91,
            mask=mask,
            box_xyxy=(24.0, 20.0, 56.0, 44.0),
        )

    def close(self) -> None:
        return None


class _FakeX5Backend:
    """Minimal EnvironmentBackend exposing calibrated X5 cameras and dual arms."""

    def __init__(self, episode_id: str, instruction: str):
        self.episode_id = episode_id
        self.instruction = instruction
        self.state_version = 0
        self.native_steps = 0
        self.finished = False
        self.description = EnvironmentDescription(
            arms=[
                ArmDescription(
                    name=side,
                    control_frame="grasp",
                    grasp_site_description=f"{side} grasp frame",
                    rotation_radius_m=0.1,
                    joint_names=[f"{side}_j{i}" for i in range(6)],
                )
                for side in ("left", "right")
            ],
            cameras=[CameraDescription(name=cam) for cam in X5_CAMERAS],
            control_frequency_hz=25.0,
            max_episode_steps=100,
            metadata={"instruction": instruction},
        )

    def _arms_state(self) -> dict[str, ArmState]:
        return {
            side: ArmState(
                control_pose=Pose(position=(0.15, -0.2 if side == "right" else 0.2, 0.35), quaternion=(1.0, 0.0, 0.0, 0.0)),
                joints=[0.0, 0.1, -0.1, 0.0, 0.2, 0.0],
                gripper=0.8,
                gripper_command=0.8,
                metadata={"gripper_applied_command": 0.8},
            )
            for side in ("left", "right")
        }

    def read_state(self) -> EnvironmentState:
        return EnvironmentState(
            arms=self._arms_state(),
            state_version=self.state_version,
            native_steps=self.native_steps,
            ended=self.finished,
            metadata={"instruction": self.instruction},
        )

    def step_joints(self, command: JointCommand) -> StepReceipt:
        del command
        self.state_version += 1
        self.native_steps += 1
        return StepReceipt(
            accepted=True,
            state=self.read_state(),
        )

    def step_pose(self, command):
        raise NotImplementedError

    def step_native(self, command):
        raise NotImplementedError

    def validate_commands(self, commands, mode: str) -> None:
        del commands, mode

    def preview_joints(self, commands):
        del commands
        return {"valid": True}

    def capture(self, cameras: list[str] | None = None, *, high_resolution: bool = False) -> CapturedObservation:
        del high_resolution
        selected = cameras or list(X5_CAMERAS)
        frames = {cam: _make_camera_frame(64, 80) for cam in selected}
        return CapturedObservation(
            episode_id=self.episode_id,
            state_version=self.state_version,
            captured_at=time.time(),
            state=self.read_state().model_dump(mode="json"),
            cameras=frames,
            terminated=self.finished,
            truncated=False,
        )

    def finish(self, reason: str, status: str | None = None) -> dict:
        self.finished = True
        return {"success": True, "reason": reason, "status": status or "completed"}

    def close(self) -> None:
        return None


class ObservationToolPackageTests(unittest.TestCase):
    def test_config_loader_and_examples(self):
        auto_cfg = load_pipeline_config(REPO_ROOT / "examples/observation/auto_sam3.yaml")
        self.assertEqual(auto_cfg.schema_version, 1)
        self.assertFalse(auto_cfg.enhance_step_snapshots)
        self.assertEqual([p.type for p in auto_cfg.plugins], ["sam3"])

        fp_cfg = load_pipeline_config(REPO_ROOT / "examples/observation/sam3_foundationpose.yaml")
        self.assertEqual([p.type for p in fp_cfg.plugins], ["sam3", "foundationpose"])
        self.assertIn("stack_bowls", fp_cfg.targets.by_task)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_file = tmp_path / "pipeline.json"
            cfg_file.write_text(json.dumps({
                "schema_version": 1,
                "targets": {"default": [{"label": "mug", "mesh_path": "meshes/mug.obj"}]},
                "plugins": [{"type": "sam3"}],
            }), encoding="utf-8")
            loaded = load_pipeline_config(cfg_file)
            self.assertEqual(
                loaded.targets.default[0].mesh_path,
                str((tmp_path / "meshes/mug.obj").resolve()),
            )

            bad_file = tmp_path / "bad.yaml"
            bad_file.write_text("unknown_top_level_key: true\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_pipeline_config(bad_file)

    def test_hybrid_prompt_resolution(self):
        targets_cfg = TargetsConfig(
            default=[TargetConfig(label="generic_obj", prompt="object")],
            by_task={"stack_bowls": [TargetConfig(label="bowl", prompt="ceramic bowl")]},
        )
        # Task exact match and suite-prefixed match
        exact = resolve_targets(
            task="stack_bowls",
            instruction="Stack the bowls neatly",
            targets_config=targets_cfg,
            auto_prompt=AutoPromptConfig(),
        )
        self.assertEqual([t.prompt for t in exact], ["ceramic bowl"])
        self.assertEqual(exact[0].source, "task_config:stack_bowls")

        suite_match = resolve_targets(
            task="libero/stack_bowls",
            instruction="Stack the bowls neatly",
            targets_config=targets_cfg,
            auto_prompt=AutoPromptConfig(),
        )
        self.assertEqual([t.prompt for t in suite_match], ["ceramic bowl"])

        # Default fallback
        default_hit = resolve_targets(
            task="unlisted_task",
            instruction="Pick up the red mug",
            targets_config=targets_cfg,
            auto_prompt=AutoPromptConfig(),
        )
        self.assertEqual([t.label for t in default_hit], ["generic_obj"])

        # Rule-based extraction
        rule_targets = extract_prompts_by_rule(
            "Pick up the red bowl and place it onto the white plate next to the wooden spoon."
        )
        prompts = [t.prompt for t in rule_targets]
        self.assertIn("red bowl", prompts)
        self.assertIn("white plate", prompts)
        self.assertIn("wooden spoon", prompts)

        # Hybrid VLM extraction with mocked httpx.post + fallback on error
        fake_response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "targets": [{"label": "bowl", "prompt": "blue bowl"}, {"label": "cup", "prompt": "white cup"}]
            })}}]},
            request=httpx.Request("POST", "https://vlm.invalid/v1/chat/completions"),
        )
        auto_cfg = AutoPromptConfig(
            strategy="hybrid",
            vlm_base_url="https://vlm.invalid/v1",
            vlm_model="test-vlm",
            vlm_api_key_env="TEST_OB_VLM_KEY",
        )
        with patch.dict(os.environ, {"TEST_OB_VLM_KEY": "secret-token"}):
            with patch("evoab.envs.observation.toolpackage.prompts.httpx.post", return_value=fake_response):
                vlm_targets = resolve_targets(
                    task="custom",
                    instruction="Stack the bowl on the cup",
                    targets_config=TargetsConfig(),
                    auto_prompt=auto_cfg,
                )
            self.assertEqual([t.prompt for t in vlm_targets], ["blue bowl", "white cup"])
            self.assertEqual(vlm_targets[0].source, "auto_vlm")

            # Fallback to rule extractor when VLM fails in hybrid mode
            with patch("evoab.envs.observation.toolpackage.prompts.httpx.post", side_effect=RuntimeError("boom")):
                fallback_targets = resolve_targets(
                    task="custom",
                    instruction="Place the green apple into the yellow basket",
                    targets_config=TargetsConfig(),
                    auto_prompt=auto_cfg,
                )
            self.assertTrue(any("green apple" in t.prompt for t in fallback_targets))
            self.assertEqual(fallback_targets[0].source, "auto_rule")

    def test_sam3_foundationpose_and_renderer(self):
        rgb, rgb_png = _make_rgb_png(64, 80)
        cam = _make_camera_frame(64, 80)
        frame = FrameInput(
            camera="head",
            snapshot_id="a" * 64,
            state_version=0,
            rgb=rgb,
            rgb_png=rgb_png,
            depth_m=cam.depth_m,
            intrinsic_K=cam.intrinsic_K,
            T_world_from_optical=cam.T_world_from_optical,
            task="stack_bowls",
            instruction="Stack the bowls",
        )
        targets = (TargetSpec(label="bowl", prompt="bowl", mesh_id="bowl_mesh"),)

        # 1. SAM3 processor with depth geometry
        fake_sam3 = _FakePerceptionClient()
        sam3_proc = Sam3Processor(client=fake_sam3, compute_depth_geometry=True)
        anns = sam3_proc.process(frame, targets, [])
        self.assertEqual(len(anns), 1)
        self.assertEqual(anns[0].label, "bowl")
        self.assertTrue(anns[0].geometry_valid)
        self.assertIsNotNone(anns[0].centroid_world_xyz)
        self.assertAlmostEqual(anns[0].centroid_world_xyz[2], 0.9 + 0.75, places=2)

        # 2. FoundationPose processor against a live local mock HTTP endpoint
        received_requests: list[dict] = []
        fp_router = APIRouter()

        @fp_router.post("/estimate_pose")
        def estimate_pose(payload: dict):
            received_requests.append(payload)
            t_cam_obj = np.eye(4, dtype=np.float64)
            t_cam_obj[:3, 3] = [0.05, -0.02, 0.72]
            return {
                "found": True,
                "T_camera_from_object": t_cam_obj.tolist(),
                "bbox_3d_extents_m": [0.12, 0.12, 0.08],
                "confidence": 0.96,
            }

        fp_server = HttpServer(create_app(fp_router), host="127.0.0.1", port=0)
        fp_url = fp_server.start_background()
        try:
            fp_proc = FoundationPoseProcessor(endpoint=fp_url, require_mask=True)
            try:
                extra = fp_proc.process(frame, targets, anns)
                self.assertEqual(extra, [])  # enriched existing SAM3 annotation in-place
                self.assertEqual(len(received_requests), 1)
                self.assertIsNotNone(received_requests[0]["mask_png_base64"])
                self.assertEqual(received_requests[0]["mesh_id"], "bowl_mesh")
                self.assertIsNotNone(anns[0].pose_6d)
                self.assertEqual(anns[0].pose_6d.position_world_m, [0.25, -0.02, 1.62])
                self.assertEqual(anns[0].pose_6d.quaternion_world_wxyz, [1.0, 0.0, 0.0, 0.0])
            finally:
                fp_proc.close()
        finally:
            fp_server.stop()

        # 3. Renderer preserves exact (H, W, 3) and modifies pixels when annotations exist
        rendered = render_annotations(rgb, anns, RenderConfig(), intrinsic_K=cam.intrinsic_K)
        self.assertEqual(rendered.shape, rgb.shape)
        self.assertEqual(rendered.dtype, np.uint8)
        self.assertFalse(np.array_equal(rendered, rgb))

        unmodified = render_annotations(rgb, anns, RenderConfig(enabled=False), intrinsic_K=cam.intrinsic_K)
        self.assertTrue(np.array_equal(unmodified, rgb))

        # 4. Dynamic plugin instantiation via module:ClassName
        custom = create_processor(PluginConfig(
            type=f"{__name__}:_DummyCustomProcessor",
            options={"tag": "unit"},
        ))
        self.assertEqual(custom.tag, "unit")

    def test_mask_iou_deduplication_and_pipeline_pruning(self):
        h, w = 64, 80
        m1 = np.zeros((h, w), dtype=bool)
        m1[10:40, 10:40] = True
        m2 = np.zeros((h, w), dtype=bool)
        m2[12:40, 10:40] = True  # high overlap with m1 (IoU > 0.9)
        m3 = np.zeros((h, w), dtype=bool)
        m3[10:40, 45:75] = True  # disjoint from m1/m2

        d_low = DetectionAnnotation(label="dup_low", prompt="a", source="sam3", score=0.45, box_xyxy=(10, 12, 40, 40), mask=m2)
        d_high = DetectionAnnotation(label="dup_high", prompt="b", source="sam3", score=0.92, box_xyxy=(10, 10, 40, 40), mask=m1)
        d_sep = DetectionAnnotation(label="separate", prompt="c", source="sam3", score=0.70, box_xyxy=(45, 10, 75, 40), mask=m3)

        self.assertGreater(annotation_iou(d_low, d_high), 0.9)
        self.assertEqual(annotation_iou(d_high, d_sep), 0.0)

        kept = deduplicate_annotations([d_low, d_high, d_sep], iou_threshold=0.55)
        self.assertEqual([d.label for d in kept], ["dup_high", "separate"])

        # Box-IoU fallback when masks are absent
        b1 = DetectionAnnotation(label="b1", prompt="a", source="sam3", score=0.8, box_xyxy=(0, 0, 20, 20))
        b2 = DetectionAnnotation(label="b2", prompt="b", source="sam3", score=0.6, box_xyxy=(1, 1, 20, 20))
        kept_boxes = deduplicate_annotations([b1, b2], iou_threshold=0.55)
        self.assertEqual([d.label for d in kept_boxes], ["b1"])

        # Disabled when threshold == 0.0
        self.assertEqual(len(deduplicate_annotations([d_low, d_high], iou_threshold=0.0)), 2)

        # Pipeline runs deduplication BEFORE downstream plugin so FoundationPose never runs twice on same object.
        seen_by_downstream: list[list[str]] = []

        class _DupProducer:
            name = "dup_sam3"
            def process(self, frame, targets, existing):
                return [d_low, d_high, d_sep]
            def close(self):
                return None

        class _DownstreamObserver:
            name = "mock_fp"
            def process(self, frame, targets, existing):
                seen_by_downstream.append([item.label for item in existing])
                return []
            def close(self):
                return None

        rgb, rgb_png = _make_rgb_png(h, w)
        frame = FrameInput(camera="right_wrist", snapshot_id="b" * 64, state_version=0, rgb=rgb, rgb_png=rgb_png)
        pipeline = ObservationPipeline(
            ObservationPipelineConfig(dedup_iou_threshold=0.55, render=RenderConfig(enabled=False)),
            processors=[_DupProducer(), _DownstreamObserver()],
        )
        res = pipeline.process_frame(frame, targets=(TargetSpec(label="bowl", prompt="bowl"),))
        self.assertEqual(seen_by_downstream, [["dup_high", "separate"]])
        self.assertEqual([d.label for d in res.annotations], ["dup_high", "separate"])

    def test_end_to_end_http_v4_proxy_with_environment_service(self):
        episode_id = "ep-ob-toolpackage-001"
        instruction = "Pick up the red bowl and place it onto the white plate"
        backend = _FakeX5Backend(episode_id=episode_id, instruction=instruction)
        info = EpisodeInfo(
            episode_id=episode_id,
            task="stack_bowls",
            integration="libero",
            environment=backend.description,
            metadata={"instruction": instruction},
        )
        env_artifacts = ArtifactStore(root=None)
        env_service = EnvironmentService(backend, info, env_artifacts)
        initial_raw = env_service.initialize()
        env_server = HttpServer(create_app(build_router(env_service)), host="127.0.0.1", port=0)
        env_url = env_server.start_background()

        processor = _DummyCustomProcessor(tag="proxy")
        config = ObservationPipelineConfig(
            auto_prompt=AutoPromptConfig(enabled=True, strategy="rule"),
            render=RenderConfig(enabled=True, draw_hud=True),
            inject_metadata=True,
            enhance_step_snapshots=False,
            archive_images=True,
        )

        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp) / "obs_toolpackage"
            local_store = ArtifactStore(Path(tmp) / "client_store")
            try:
                with ObservationProxyServer(
                    upstream_url=env_url,
                    config=config,
                    archive_dir=archive_dir,
                    processors=[processor],
                ) as proxy:
                    client = EnvironmentClient(proxy.url)
                    try:
                        # 1. /info passthrough
                        connected = client.connect()
                        self.assertEqual(connected.episode_id, episode_id)
                        self.assertEqual(connected.task, "stack_bowls")

                        # 2. /snapshots/latest enhanced & checksum-valid through RemoteArtifactMirror
                        mirror = RemoteArtifactMirror(client, local_store)
                        snap = client.latest_snapshot()
                        self.assertEqual(snap["snapshot_id"], initial_raw["snapshot_id"])
                        self.assertIn("visual_annotations", snap["state"]["metadata"])
                        va = snap["state"]["metadata"]["visual_annotations"]
                        self.assertTrue(len(va["targets"]) >= 2)
                        self.assertEqual(set(va["cameras"].keys()), set(X5_CAMERAS))

                        # Verify manifest and enhanced RGB pass strict SHA256 + size checks
                        mirrored_manifest = mirror.mirror_ref(snap["manifest"])
                        self.assertEqual(len(mirrored_manifest["artifact_id"]), 64)
                        for cam in X5_CAMERAS:
                            raw_ref = initial_raw["cameras"][cam]["rgb"]
                            enh_ref = snap["cameras"][cam]["rgb"]
                            self.assertNotEqual(enh_ref["sha256"], raw_ref["sha256"])
                            mirrored_rgb = mirror.mirror_ref(enh_ref)
                            enh_bytes = local_store.read_bytes(mirrored_rgb["artifact_id"])
                            self.assertEqual(hashlib.sha256(enh_bytes).hexdigest(), enh_ref["sha256"])

                        # 4. Caching: re-reading same snapshot_id or /records does not re-run processor
                        calls_after_first = processor.calls
                        self.assertEqual(calls_after_first, len(X5_CAMERAS))
                        snap_again = client.snapshot(snap["snapshot_id"])
                        self.assertEqual(snap_again["manifest"]["sha256"], snap["manifest"]["sha256"])
                        rec = client.record(0)
                        self.assertEqual(rec["record_index"], 0)
                        self.assertEqual(processor.calls, calls_after_first)

                        # 5. POST /steps passes through without running perception (enhance_step_snapshots=False)
                        step_reply = client.step(
                            StepRequest(
                                command=JointCommand(
                                    arms={
                                        "left": JointTarget(joints=[0.0] * 6, gripper=0.5),
                                        "right": JointTarget(joints=[0.0] * 6, gripper=0.5),
                                    }
                                ),
                                expected_state_version=0,
                            )
                        )
                        self.assertEqual(step_reply.state_after.state_version, 1)
                        self.assertEqual(processor.calls, calls_after_first)

                        # 6. POST /captures triggers enhancement for the new snapshot
                        cap_reply = client.capture(CaptureRequest(cameras=list(X5_CAMERAS)))
                        self.assertIn("visual_annotations", cap_reply["state"]["metadata"])
                        self.assertEqual(processor.calls, calls_after_first + len(X5_CAMERAS))

                        # 7. Runtime target override via POST /ob/targets invalidates cache and updates prompts
                        status = httpx.get(f"{proxy.url}/ob/status", trust_env=False).json()
                        self.assertGreaterEqual(status["cached_snapshots"], 2)
                        resp = httpx.post(
                            f"{proxy.url}/ob/targets",
                            json={"targets": [{"label": "ceramic_bowl", "prompt": "small ceramic bowl"}]},
                            trust_env=False,
                        )
                        self.assertEqual(resp.status_code, 200)
                        updated_targets = httpx.get(f"{proxy.url}/ob/targets", trust_env=False).json()["targets"]
                        self.assertEqual([t["label"] for t in updated_targets], ["ceramic_bowl"])

                        re_snap = client.latest_snapshot()
                        self.assertEqual(
                            re_snap["state"]["metadata"]["visual_annotations"]["cameras"]["head"][0]["label"],
                            "ceramic_bowl",
                        )

                        # 8. POST /finish and GET /outcome pass through cleanly
                        finish_reply = client.finish(FinishRequest(reason="done", status="success"))
                        self.assertTrue(finish_reply["outcome"]["success"])
                        outcome = client.outcome()
                        self.assertTrue(outcome["outcome"]["success"])

                        # 9. Archive files created on disk
                        self.assertTrue((archive_dir / "targets.json").is_file())
                        self.assertTrue((archive_dir / "annotations.jsonl").is_file())
                        self.assertTrue(any((archive_dir / "raw_images").glob("*.png")))
                        self.assertTrue(any((archive_dir / "annotated_images").glob("*.png")))
                    finally:
                        client.close()
            finally:
                local_store.close()
                env_server.stop()
                env_artifacts.close()

    def test_cli_and_session_config_offline_validation(self):
        pipeline_path = REPO_ROOT / "examples/observation/auto_sam3.yaml"
        cfg = SessionConfig(
            benchmark="libero",
            env_url="http://127.0.0.1:18760",
            obs_pipeline=pipeline_path,
            benchmark_options={"task": "stack_bowls"},
        )
        resolved = cfg.resolve()
        self.assertEqual(resolved.obs_pipeline, str(pipeline_path.resolve()))

        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
        from unittest import mock
        from evoab.cli import build_parser
        from evoab.config import resolve_settings
        from evoab.envs import load_integration
        spec = load_integration("libero").get_integration_spec()
        argv = ["run", "--benchmark", "libero", "--recipe", "tools", "--model", "fixture",
                "--task", "stack_bowls", "--env-url", "http://127.0.0.1:18760",
                "--obs-pipeline", str(pipeline_path)]
        with mock.patch("evoab.agent.models.catalog.presets", return_value=[{"name": "fixture"}]):
            checked = resolve_settings(build_parser(spec).parse_args(argv), spec)
        self.assertEqual(checked.obs_pipeline, str(pipeline_path.resolve()))

        proxy_check = subprocess.run(
            [
                sys.executable, "-m", "evoab.cli", "ob-proxy",
                "--config", str(REPO_ROOT / "examples/observation/sam3_foundationpose.yaml"),
                "--print-config",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proxy_check.returncode, 0, msg=proxy_check.stderr)
        printed = json.loads(proxy_check.stdout)
        self.assertEqual(printed["schema_version"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
