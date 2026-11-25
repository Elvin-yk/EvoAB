# Observation ToolPackage (`evoab.observation.toolpackage`)

The Observation ToolPackage is an external visual-preprocessing layer that sits
between any EvoAB Environment Protocol v4 service and any Agent strategy. It runs
pluggable perception models (such as SAM3 + depth 3D geometry and FoundationPose
6D object pose) on camera snapshots, renders clean annotations onto the RGB
frames while preserving exact `(H, W, 3)` image dimensions, and injects structured
detection metadata into `state.metadata["visual_annotations"]`.

Because it speaks standard Environment Protocol v4 over HTTP, every built-in
method (`tools`, `code`, `direct_policy`, `planner_direct`, `tool_team`,
`agent_policy`, `rua`, `clean`) and every external evaluator (`q0_preview_upstream`,
`rua_upstream`) works without modifying the native EvoAB harness or simulator
backends.

## Architecture

```text
Simulator / Robot (Environment Protocol v4)
        │  GET /info, /snapshots/*, /records, /artifacts/*
        │  POST /captures, /steps, /finish, ...
        ▼
ObservationProxyServer (`evoab ob-proxy` or `evoab run --obs-pipeline`)
  ├── Target Prompt Resolver (task YAML -> default -> VLM / rule auto-extract)
  ├── Ordered Plugin Chain (`sam3`, `foundationpose`, or `module:ClassName`)
  ├── Non-Destructive Renderer (preserves exact HxW, rewrites RGB & manifest refs)
  └── Local ArtifactStore + Archive (`targets.json`, `annotations.jsonl`, PNGs)
        │  Environment Protocol v4 (unchanged contract)
        ▼
Any EvoAB Agent / Policy / External Evaluator
```

### Design Invariants

- **Exact `(H, W, 3)` preservation**: Rendered PNGs keep identical width and height
  so camera intrinsics (`intrinsic_K`), pixel-based localization, triangulation,
  and action models (`Q0_Preview`, `DirectPolicy`, `RUA`) remain geometrically valid.
- **Content-addressed artifact integrity**: Rewritten RGB images and snapshot
  manifests are stored in the proxy's `ArtifactStore` with valid 64-hex
  `artifact_id`, `size_bytes`, and `sha256` checksums. Unmodified artifacts
  (`depth_m`, calibration files, manuals) stream directly from upstream.
- **Zero per-step perception overhead by default**: `enhance_step_snapshots: false`
  avoids running heavy perception on every intermediate `/steps` call inside
  multi-waypoint moves, while caching enhanced snapshots by `snapshot_id` across
  `/captures`, `/snapshots/latest`, `/snapshots/{id}`, and `/records`.
- **Mask-IoU deduplication (NMS)**: `dedup_iou_threshold` (default `0.55`, `0.0`
  disables) suppresses lower-scoring overlapping detections using exact boolean
  mask IoU (falling back to 2D box IoU) right after each detection plugin, so
  downstream 6D pose estimators do not re-run on duplicate close-up wrist masks.

## Usage

### Option 1: One-flag attachment on `evoab run`

Attach any pipeline YAML/JSON directly to `evoab run` (works with `--env-url`,
`--deploy local`, built-in recipes, and external evaluators):

```bash
evoab run \
  --integration robodojo \
  --recipe q0_preview_upstream \
  --task stack_bowls \
  --env-url http://127.0.0.1:18760 \
  --sam3-url http://127.0.0.1:8001 \
  --obs-pipeline examples/observation/auto_sam3.yaml
```

You can also set `EVOAB_OBS_PIPELINE=/path/to/pipeline.yaml` or pass
`obs_pipeline=Path(...)` in `SessionConfig`.

### Option 2: Standalone external proxy (`evoab ob-proxy`)

Run the proxy as an independent service and point any agent or external script at
the proxy URL via `--env-url`:

```bash
# Terminal 1: start the observation proxy in front of the real environment service
evoab ob-proxy \
  --upstream-url http://127.0.0.1:18760 \
  --config examples/observation/sam3_foundationpose.yaml \
  --port 18765 \
  --archive-dir outputs/ob_proxy_debug

# Terminal 2: point any method at the proxy URL
evoab run \
  --integration robodojo \
  --recipe tools \
  --env-url http://127.0.0.1:18765 \
  --model ...
```

### Runtime Target Inspection & Override

While the proxy is running, you can inspect status or override target prompts
live without restarting the environment:

- `GET /ob/status` — active plugins, resolved targets, cached snapshot count.
- `GET /ob/targets` — active target list (`label`, `prompt`, `mesh_path`, `source`).
- `POST /ob/targets` — replace active targets and invalidate the snapshot cache:

```bash
curl -X POST http://127.0.0.1:18765/ob/targets \
  -H 'Content-Type: application/json' \
  -d '{"targets": [{"label": "bowl", "prompt": "white ceramic bowl"}]}'
```

## Target Prompt Resolution

Prompts and optional 3D meshes are resolved once at episode start in layered order:

1. `targets.by_task[task]` (also matches the basename after `/` for suite-prefixed tasks).
2. `targets.default` when non-empty.
3. `auto_prompt` extraction from the episode's `instruction`:
   - `strategy: hybrid` (default): calls an OpenAI-compatible VLM once (optionally
     with the first camera frame) when `vlm_base_url`, `vlm_model`, and
     `vlm_api_key_env` are configured, falling back to the deterministic offline
     noun-phrase rule extractor on error or missing credentials.
   - `strategy: rule`: purely offline noun-phrase extraction.
   - `strategy: vlm`: requires VLM credentials and raises on failure.

## Built-in & Custom Plugins

- `sam3`: calls EvoAB's SAM3 HTTP perception service (`endpoint` or `EVOAB_SAM3_URL`
  or `--sam3-url`) and computes world-frame 3D centroid and bounding box when
  calibrated depth is available.
- `foundationpose`: calls an external FoundationPose HTTP service (`POST /estimate_pose`)
  using the raw RGB, depth `.npy`, camera intrinsics, optional SAM3 mask, and
  optional `mesh_path`/`mesh_id`, transforming `T_camera_from_object` into world
  coordinates (`T_world_from_object`, `position_world_m`, `quaternion_world_wxyz`).
- Custom plugins: pass `type: "my_pkg.my_mod:MyProcessor"` implementing the
  `VisualProcessor` protocol (`name`, `process(frame, targets, existing)`, `close()`).
