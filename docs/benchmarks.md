# Benchmarks

How to prepare each environment. Managed setup uses published simulator images.
The prepared episode SDK also accepts an externally started environment service. Running methods on a prepared environment is in
the [guide](guide.md#run-from-the-command-line); adding a new environment is in
[architecture](architecture.md#extend-evoab).

| Integration | Kind | Image | Tools | Notes |
|---|---|---|---|---|
| `libero` | Original LIBERO | Public | Motion, policy (StarVLA) | Spatial, Object, Goal, Long, 90 |
| `libero_pro` | LIBERO-PRO suites | Public | Motion | `_env`, `_object`, `_swap`, `_task`, `_lan` variants |
| `calvin` | CALVIN Panda | Public | Motion, policy (StarVLA) | One task per episode |
| `custom` | Your Gym-style benchmark | Your image | Motion | See [custom benchmarks](#custom-benchmarks) |
| `rmbench` | RMBench from a prepared SimpleMemVLA runtime | External service | Policy and optional motion | Replay selected policy-baseline cases |

## Simulation image recording

Direct and Hybrid archive one camera group at step zero and then about every
400 ms of simulation time. The environment service rounds 400 ms to a whole
number of native steps and uses that stride throughout the episode.

CALVIN uses a stride of 12 steps and the LIBERO family uses 8. Custom
integrations set their stride through `control_hz`.

The environment artifact store saves one PNG per available camera in `images/`.
Snapshot manifests in `artifacts/` identify the archived groups by
`sampled_step` and `captured_at`. Fresh camera observations for policy inference
and tools use temporary storage. Rebuild and restart existing environment
services to use this policy.

## Preparing a runtime

Benchmark simulators run in Docker. To prepare a benchmark, `evoab setup` pulls the
published upstream image (pinned by digest), adds a small local layer with EvoAB's Python dependencies, and records the result under
`~/.cache/evoab` (or `--cache-dir`). Each launch then starts one container with
host networking, the run directory mounted at the same path and EvoAB's own code
mounted read-only from the host, and removes it afterwards. Nothing is hosted by
EvoAB.

**Images and code are separate.** An image holds the simulator, the benchmark at
a pinned revision and EvoAB's dependencies, never EvoAB's code, and its tag is
derived from exactly those inputs (`evoab/<name>:env-<hash>`). Updating or
editing EvoAB needs no rebuild; setup rebuilds only when a benchmark's recipe or
EvoAB's dependencies change, and otherwise reuses the image. LIBERO, LIBERO-PRO
and CALVIN use AllenAI's published images
([vla-evaluation-harness](https://github.com/allenai/vla-evaluation-harness)).

Benchmark containers need the NVIDIA Container Toolkit. Studio: 设置 → 评测集 →
部署, or:

```bash
evoab benchmarks check                       # Docker, GPU containers, disk, model presets
evoab setup --integration libero --runtime-profile libero_spatial
evoab run --integration libero --runtime-profile libero_spatial --task TASK …   # starts the container, runs, stops it
```

Builds pick package and model mirrors automatically when Hugging Face is
unreachable; set `PIP_INDEX_URL`, `HF_ENDPOINT` and similar variables to
override, or `EVOAB_CONTAINER_BUILD_PROXY=1` to forward the host proxy. An
image archive made with `docker save` can be imported instead of building
(`--image-archive`, or Studio). 卸载 removes a Docker deployment. `--wheel`
bakes a matching `evoab-runtime` release into the image instead of mounting the
running package, for pinned, reproducible evaluation images.

`evoab deploy env --integration calvin --task open_drawer --gpu 0` runs one
environment container in the foreground for runs that connect with
`--env-url`. `deploy env` takes `--no-depth` itself; `evoab run` takes it from
the recipe's `inputs`. Each environment
service serves one prepared episode.

**Policy and perception services are yours to run.** Docker holds benchmark
environments only. A policy (VLA/WAM actor) or SAM3 server runs wherever you
serve models, and EvoAB is its client: methods with the policy tool pass
`--action-model-url` and `--action-model-checkpoint-sha256`; methods that
enable scene segmentation pass `--sam3-url`. EvoAB's own servers speak these
protocols and can be started in any Python environment that has the model's
dependencies:

```bash
python -m evoab.servers.actor.server --config ACTOR.toml        # backends: starvla_calvin, starvla_libero
python -m evoab.servers.perception.server --checkpoint sam3.pt --port 18762
```

Services bind localhost and have no internet-facing authentication: run them on
trusted machines.

## LIBERO, LIBERO-PRO

Two separate images; stock LIBERO and its fork never share an interpreter.

- **LIBERO**: public image; suites Spatial, Object, Goal, Long (`libero_10`) and
  `libero_90`, with official starting states selected by `--init-state-index`.
- **LIBERO-PRO**: public image of the pinned
  [LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO). The four base suites and
  their `_env`, `_object`, `_swap`, `_task`, `_lan` variants are admitted;
  initial states must exist for the exact task.

```bash
evoab run --integration libero_pro --runtime-profile libero_object \
  --task pick_up_the_cream_cheese_and_place_it_in_the_basket --recipe action --model my-model
```

Stock LIBERO can use `actor_act` with RoboScope's `starvla_libero_policy.py`
and `StarVLA/Qwen2.5-VL-OFT-LIBERO-4in1`. Prepare the checkpoint with the
RoboScope LIBERO configuration and start its policy socket. Run
`python -m evoab.servers.actor.server --config ACTOR.toml` with the RoboScope
SDK available. Use the same actor configuration fields as [CALVIN](#calvin)
with `backend = "starvla_libero"`.

```bash
evoab run --integration libero --env-url http://127.0.0.1:18801 \
  --recipe examples/recipes/policy.yaml --model MODEL_PRESET \
  --set 'roles.main.capabilities=[scene, hybrid, episode]' \
  --action-model-url http://127.0.0.1:8601 \
  --action-model-checkpoint-sha256 CHECKPOINT_SHA256
```

The binding uses `active_groups=["arm"]` and delivers the agent-view and wrist
images in the benchmark's training orientation. Each prediction is a chunk of
native relative OSC actions. Use the policy recipe's default capabilities for
mixed control. Initial state index and hash are recorded by the native service.

## CALVIN

Public image with the pinned `calvin` and `calvin_env` revisions; Studio runs
scene D with the native scene reset and 360 steps, one task per episode.

For controlled comparisons, start the same prepared CALVIN environment with
`--initial-state CASE.npz` in place of `--scene-reset`. The service records the
reset hash and continues to score the outcome with the native task oracle.

The CALVIN StarVLA bridge was validated with RoboScope's StarVLA runtime and
the `Simplicissimus-S/StarVLA-QwenGR00T_Qwen2.5-VL-3B-Instruct-Action_calvin_D_D`
checkpoint. Start a fresh StarVLA policy socket, then run
`python -m evoab.servers.actor.server --config ACTOR.toml` in a runtime that
has the RoboScope SDK on its Python path. The bridge checks the checkpoint
hash and loads the CALVIN D-to-D policy on that socket.

```toml
backend = "starvla_calvin"
socket_path = "/path/to/starvla.sock"
checkpoint = "/path/to/the/prepared/checkpoint.pt"
checkpoint_sha256 = "<checkpoint SHA-256>"
```

For an Agent trial with only `actor_act` available for motion:

```bash
evoab run --integration calvin --task open_drawer \
  --recipe examples/recipes/policy.yaml --model MY_PRESET \
  --set 'roles.main.capabilities=[scene, hybrid, episode]' \
  --action-model-url http://127.0.0.1:8601 \
  --action-model-checkpoint-sha256 CHECKPOINT_SHA256
```

When delegating a CALVIN subtask, use `active_groups=["arm"]`. The binding
sends the original static and wrist RGB and native robot state. It executes
the policy's relative actions through CALVIN's controller, using one full
model chunk by default. Use the policy recipe's default capabilities to enable
both `actor_act` and the motion tools. The CALVIN binding runs without a chunk
reviewer.

Each integration's `SOURCE.md` pins the sources, assets and weights it was
validated with.

## RMBench prepared episodes

Use the pinned SimpleMemVLA runtime and its vendored RMBench simulator.
`scripts/validation/rmbench_policy_baseline.py` records expert-admitted seeds,
instructions and initial observations in `manifest.json`. Replay one case using
`python -m evoab.envs.rmbench.server --baseline-manifest CASE_MANIFEST` with
the selected `--task`, `--seed` and vendored simulator path as `--native-root`.
Start the actor with `python -m evoab.servers.actor.server --config ACTOR.toml`.
Set `backend = "simplememvla_rmbench"`, `native_root` to the SimpleMemVLA
checkout, and set `checkpoint` and `checkpoint_sha256` to the selected weights.

Connect with `SessionConfig` using the prepared `env_url`, the actor URL and
checkpoint SHA-256, and a native planner model preset. Use
`examples/recipes/policy.yaml` with the `scene`, `hybrid` and `episode`
capabilities for policy-only motion. For mixed control, add
`--mixed-control` on the environment service and add the `gripper` capability
to the recipe. Both controllers use the same native clock and camera history.
The policy executes 16 dense targets per prediction and stops at native
termination. The actor receives the original task instruction and controls
both arm groups.

## Custom benchmarks

Bring your own simulator as a Docker image, Harbor-style: EvoAB adds its
dependencies on top, mounts its code and starts one container per episode. Register a TOML manifest in
`~/.config/evoab/benchmarks/NAME.toml` naming the image (local or pullable, with
a Python ≥ 3.10 that has pip), the interpreter inside it, a `make(task_id, seed)`
function returning a Gym-style environment, its tasks, and how to read
observations, write actions and detect success
([schema](../evoab/envs/custom/manifest.py)). `path` and `mounts` name
host directories mounted at the same paths; `gpu = true` requests the GPU.

```bash
evoab setup --integration custom --runtime-profile NAME
evoab run --integration custom --runtime-profile NAME --task TASK …
```
