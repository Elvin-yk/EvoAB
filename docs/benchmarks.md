# Benchmarks and robots

How to prepare each environment. Running methods on a prepared environment is in
the [guide](guide.md#run-from-the-command-line); adding a new environment is in
[architecture](architecture.md#extend-evoab).

| Integration | Kind | Image | Tools | Notes |
|---|---|---|---|---|
| `robodojo` | Dual-arm ARX X5, Isaac Sim 5.1 | Local build | Motion, policy (Pi0.5) | 42 tasks, 5 capability dimensions |
| `metaworld` | MetaWorld MT50, MuJoCo | Local build, CPU simulator | Motion, policy (SmolVLA) | Motion-only runs need no GPU |
| `libero` | Original LIBERO | Public | Motion, policy (StarVLA) | Spatial, Object, Goal, Long, 90 |
| `libero_plus` | LIBERO-Plus perturbations | Local build | Motion | |
| `libero_pro` | LIBERO-PRO suites | Public | Motion | `_env`, `_object`, `_swap`, `_task`, `_lan` variants |
| `robotwin` | RoboTwin 2.0 aloha-agilex | Local build | Motion | `demo_clean`, `demo_randomized` |
| `calvin` | CALVIN Panda | Public | Motion, policy (StarVLA) | One task per episode |
| `openarm_gazebo` | OpenArm 2.0, Gazebo Classic and ROS 2 Humble | Local Docker build, CPU | Wrist motion, pinch gripper and measured status | Single-arm reach on either side |
| `custom` | Your Gym-style benchmark | Your image | Motion | See [custom benchmarks](#custom-benchmarks) |
| `arx_one`, `piper_x` | Real dual-arm robots | Operator setup | Motion | See [real robots](#real-robots) |

## Simulation image recording

Direct and Hybrid archive one camera group at step zero and then about every
400 ms of simulation time. The environment service rounds 400 ms to a whole
number of native steps and uses that stride throughout the episode.

MetaWorld uses a stride of 32 steps, CALVIN uses 12, RoboDojo uses 10 and the
LIBERO family uses 8. RoboTwin samples every 12 native actions using a nominal
30 Hz rate. Those actions execute variable-length trajectories, so simulated
time between samples can vary. Custom integrations set their stride through
`control_hz`.

The environment artifact store saves one PNG per available camera in `images/`.
Snapshot manifests in `artifacts/` identify the archived groups by
`sampled_step` and `captured_at`. Fresh camera observations for policy inference
and tools use temporary storage. Rebuild and restart existing environment
services to use this policy.

## Preparing a runtime

Benchmark simulators run in Docker. To prepare a benchmark, `evoab setup` pulls the
upstream image (pinned by digest) or builds it once from a pinned recipe, adds
EvoAB's service package in a small local layer, and records the result under
`~/.cache/evoab` (or `--cache-dir`). Each launch then starts one container with
host networking and the run directory mounted at the same path, and removes it
afterwards. Nothing is hosted by EvoAB.

GPU benchmark containers also need the NVIDIA Container Toolkit. MetaWorld renders on the CPU. Studio: 设置 → 评测集 →
部署, or:

```bash
evoab benchmarks check                       # Docker, GPU containers, disk, model presets
evoab setup --integration robodojo --accept-license 'NVIDIA Isaac Sim EULA'
evoab run --integration robodojo --task stack_bowls …     # starts the container, runs, stops it
```

Builds pick package and model mirrors automatically when Hugging Face is
unreachable; set `PIP_INDEX_URL`, `HF_ENDPOINT` and similar variables to
override, or `EVOAB_CONTAINER_BUILD_PROXY=1` to forward the host proxy. Pinned
git sources are fetched on the host, so builds need no access to the forge. An
image archive made with `docker save` can be imported instead of building
(`--image-archive`, or Studio). 卸载 removes a Docker deployment. `--wheel`
installs a matching `evoab-runtime` wheel into the image instead of the running
package.

`evoab deploy env --integration robodojo --task stack_bowls --gpu 0` runs one
environment container in the foreground (port 18760) for runs that connect with
`--env-url`. `deploy env` takes `--no-depth` and `--no-camera-calibration`
itself; `evoab run` takes them from the recipe's `inputs`. Each environment
service serves one prepared episode.

**Policy and perception services are yours to run.** Docker holds benchmark
environments only. A policy (VLA/WAM actor) or SAM3 server runs wherever you
serve models, and EvoAB is its client: methods with the policy tool pass
`--action-model-url` and `--action-model-checkpoint-sha256`; methods that
enable scene segmentation pass `--sam3-url`. EvoAB's own servers speak these
protocols and can be started in any Python environment that has the model's
dependencies:

```bash
python -m evoab.services.actor.server --config ACTOR.toml        # backends: openpi, xpolicylab, starvla_calvin
python -m evoab.services.perception.server --checkpoint sam3.pt --port 18762
```

The `robodojo_pi05` recipe (`evoab/services/actor/recipes/`) names the pinned
OpenPI source and checkpoint for RoboDojo's Pi0.5. Services bind localhost and
have no internet-facing authentication: run them on trusted machines.

## RoboDojo

Dual-arm ARX X5 in Isaac Sim 5.1 from the pinned official checkout
([provenance](../evoab/integrations/robodojo/SOURCE.md)). The container build
needs the NVIDIA Isaac Sim EULA accepted (`--accept-license 'NVIDIA Isaac Sim EULA'`)
and about 38.5 GiB of assets.

- **Tasks.** 42 tasks in five dimensions: generalization 12, memory 6,
  precision 8, long-horizon 8, open 8. Each generalization task also has a
  `<task>_random` config with a changed initial arrangement, so 54 configs run
  (`evoab/integrations/robodojo/tasks.py`).
- **Seeds and layouts.** An episode is one frozen layout
  `Assets/Eval_Layout/RoboDojo/arx_x5/<seed>/<task>_<layout>.json`. `--seed`
  (0, 1, 2) picks the official evaluation set and `--layout-id` the scene in it;
  the official evaluation runs 25 layouts per generalization config and 50 per
  other task in each seed. Both default to 0 and are recorded in the run.
  `--layout FILE` selects any layout file directly.
- **Inputs.** Depth is captured by default; `inputs.depth: false` gives RGB with
  calibration, and `locate` then triangulates from two calibrated views.
  `inputs.task_supplements: true` appends RoboDojo's short task clarifications
  (off by default, as in the official Agent Track rules).
- **Policy tool.** Pi0.5 through the `robodojo_pi05` actor profile; its chunk
  reviewer is on by default (`hybrid` option `review: false` to disable).

## MetaWorld

MetaWorld MT50 (`metaworld==3.1.1`) through the shared motion tools. `evoab setup
--integration metaworld` builds a small CPU-only image (slim Python, MuJoCo
rendering through OSMesa) in a few minutes. Tasks are named like `reach-v3`.

The shared `actor_act` tool accepts
[`lerobot/smolvla_metaworld`](https://huggingface.co/lerobot/smolvla_metaworld).
Prepare the checkpoint directory with the published weights, configuration and
processor files. Prepare the configuration and tokenizer files for
`HuggingFaceTB/SmolVLM2-500M-Video-Instruct` separately. The policy checkpoint
supplies the model weights.

Start `python -m evoab.services.actor.server --config ACTOR.toml` in a LeRobot
runtime with SmolVLA dependencies and `num2words`. The reference checkpoint
was tested with LeRobot 0.4.4:

```toml
backend = "smolvla_metaworld"
checkpoint = "/path/to/smolvla_metaworld"
checkpoint_sha256 = "<SHA-256 of model.safetensors>"
vlm_config = "/path/to/SmolVLM2-500M-Video-Instruct"
device = "cuda"
```

Use the `agent_policy` recipe with `--integration metaworld`, the actor URL and
checkpoint SHA-256. Select `active_groups=["arm"]` and pass the policy's task
instruction. The adapter captures a 480 × 480 policy view using LeRobot's
`corner2` pose. The binding applies the training image orientation and passes
the four native Sawyer proprioceptive values. Native XYZ deltas and gripper
commands execute through the same episode as the motion tools. The binding
runs without a chunk reviewer.

## LIBERO, LIBERO-Plus, LIBERO-PRO

Three separate images; stock LIBERO and its forks never share an interpreter.

- **LIBERO**: public image; suites Spatial, Object, Goal, Long (`libero_10`) and
  `libero_90`, with official starting states selected by `--init-state-index`.
- **LIBERO-Plus**: local build of the pinned
  [LIBERO-plus](https://github.com/sylvestf/LIBERO-plus). Setup verifies the
  revision, unmodified files and `robosuite==1.4.0`. Studio groups tasks by suite
  and perturbation.
- **LIBERO-PRO**: public image of the pinned
  [LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO). The four base suites and
  their `_env`, `_object`, `_swap`, `_task`, `_lan` variants are admitted;
  initial states must exist for the exact task.

```bash
evoab run --integration libero_pro --runtime-profile libero_object \
  --task pick_up_the_cream_cheese_and_place_it_in_the_basket --recipe direct_action --model my-model
```

Stock LIBERO can use `actor_act` with RoboScope's `starvla_libero_policy.py`
and `StarVLA/Qwen2.5-VL-OFT-LIBERO-4in1`. Prepare the checkpoint with the
RoboScope LIBERO configuration and start its policy socket. Run
`python -m evoab.services.actor.server --config ACTOR.toml` with the RoboScope
SDK available. Use the same actor configuration fields as [CALVIN](#calvin)
with `backend = "starvla_libero"`.

```bash
evoab run --integration libero --env-url http://127.0.0.1:18801 \
  --recipe agent_policy --model MODEL_PRESET \
  --set 'roles.main.capabilities=[scene, hybrid, episode]' \
  --action-model-url http://127.0.0.1:8601 \
  --action-model-checkpoint-sha256 CHECKPOINT_SHA256
```

The binding uses `active_groups=["arm"]` and delivers the agent-view and wrist
images in the benchmark's training orientation. Each prediction is a chunk of
native relative OSC actions. Use the default `agent_policy` capabilities for
mixed control. Initial state index and hash are recorded by the native service.

## RoboTwin 2.0

**RoboTwin** (`aloha-agilex`, `demo_clean` / `demo_randomized`): local container
build with the pinned revision, PyTorch 2.8 / CUDA 12.8, SAPIEN 3.0.0b1, mplib
0.2.1 and CuRobo 0.7.8 compiled for SM 8.0–12.0. The upstream expert admits
each seed first; a rejected seed is a setup exclusion, not an agent failure.

## CALVIN

Public image with the pinned `calvin` and `calvin_env` revisions; Studio runs
scene D with the native scene reset and 360 steps, one task per episode.

For controlled comparisons, start the same prepared CALVIN environment with
`--initial-state CASE.npz` in place of `--scene-reset`. The service records the
reset hash and continues to score the outcome with the native task oracle.

The CALVIN StarVLA bridge was validated with RoboScope's StarVLA runtime and
the `Simplicissimus-S/StarVLA-QwenGR00T_Qwen2.5-VL-3B-Instruct-Action_calvin_D_D`
checkpoint. Start a fresh StarVLA policy socket, then run
`python -m evoab.services.actor.server --config ACTOR.toml` in a runtime that
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
  --recipe agent_policy --model MY_PRESET \
  --set 'roles.main.capabilities=[scene, hybrid, episode]' \
  --action-model-url http://127.0.0.1:8601 \
  --action-model-checkpoint-sha256 CHECKPOINT_SHA256
```

When delegating a CALVIN subtask, use `active_groups=["arm"]`. The binding
sends the original static and wrist RGB and native robot state. It executes
the policy's relative actions through CALVIN's controller, using one full
model chunk by default. Use the default `agent_policy` capabilities to enable
both `actor_act` and the motion tools. The CALVIN binding runs without a chunk
reviewer.

Each integration's `SOURCE.md` pins the sources, assets and weights it was
validated with.

## OpenArm in Gazebo

`reach_right` and `reach_left` use the OpenArm 2.0 `default_bimanual` model with
pinch grippers, Gazebo Classic 11.10.2 and ROS 2 Humble. Each episode controls
one wrist and its finger on the simulated robot. The world frame is the torso's
`arm_origin` and the wrist frame is `openarm_<side>_ee_base_link`. The fixed RGB
camera faces the selected arm. Depth, localization, base motion and the other
arm are excluded from this configuration.

`move` sends Cartesian waypoints through RuboClaw IK. The ROS 2
`openarm_joint_trajectory_controller` accepts a partial `FollowJointTrajectory`
goal for the seven arm joints and `finger_joint1`. `grasp` closes the pinch
gripper, `release` opens it, and `hold` retains the last joint and gripper
targets. The measured opening is normalized between closed (0) and open (1).
The reach score uses the final measured wrist pose against the fixed 40 mm
upward target, with 5 mm position and 0.05 rad orientation tolerances.

`get_status` reads the latest published state without sending a command,
recording a snapshot or advancing the episode. It returns joint angles in
radians, wrist position in metres and quaternion in wxyz order, finger opening,
controller action-server readiness, capture time and age. A controller rejection
or abort supplies the reported fault. Null denotes unavailable telemetry.
A sample older than five seconds, behind the current state version, or affected
by a publication error is marked stale and retains its measured values.

From an EvoAB checkout, setup downloads the pinned simulator, description and
meshes, builds the ROS 2 workspace and prepares the environment service image.
The build requires Docker on Linux and network access for the first setup.
[RuboClaw IK and Gazebo](https://github.com/Ru-hulu/RuboClaw/tree/b94399b5a7ce9d13e69a6e46bf46b2b6ac7ff42c/robot_runtime),
[OpenArm description](https://github.com/enactic/openarm_description/tree/e36b9fda795ecaad8c0ef8c71a34f2fd4dd9f532),
[ROSbot description](https://github.com/husarion/rosbot_ros/tree/1.2.1/rosbot_description).

```bash
evoab setup --integration openarm_gazebo
python examples/sdk/openarm_reach.py right
python examples/sdk/openarm_reach.py left
```

The SDK smoke runs `move`, `grasp`, `hold` and `release`, reads `get_status`
after each command, checks the measured lift and finger opening, then verifies
that the last sample becomes stale at the same step and submits `finish`.
To run the same environment with an image-capable model and review its trace:

```bash
evoab run --integration openarm_gazebo --task reach_right --recipe agentic \
  --model <preset> --instruction 'Raise the right wrist by 4 cm' --monitor
evoab replay <run-directory>
```

## Custom benchmarks

Bring your own simulator as a Docker image, Harbor-style: EvoAB adds its service
package on top and starts one container per episode. Register a TOML manifest in
`~/.config/evoab/benchmarks/NAME.toml` naming the image (local or pullable, with
a Python ≥ 3.10 that has pip), the interpreter inside it, a `make(task_id, seed)`
function returning a Gym-style environment, its tasks, and how to read
observations, write actions and detect success
([schema](../evoab/integrations/custom/manifest.py)). `path` and `mounts` name
host directories mounted at the same paths; `gpu = true` requests the GPU.

```bash
evoab setup --integration custom --runtime-profile NAME
evoab run --integration custom --runtime-profile NAME --task TASK …
```

## Real robots

Starting a robot controller is a physical action: clear the workspace first.
Real robots run as an environment service in front of a device process that
owns the hardware; the agent reaches them through the same tools as simulation.

- **ARX ONE** (two ARX X5 arms, three RealSense D405): operator commands, layout
  and the new-machine deployment (third-party binaries, site configuration,
  calibration) are in its [README](../evoab/integrations/arx_one/README.md) and
  [deployment manual](../evoab/integrations/arx_one/DEPLOYMENT.md).
- **Piper-X** (CAN arms, RealSense): see its [README](../evoab/integrations/piper_x/README.md).

A shared IK interface for adding arms is tracked in
[#18](https://github.com/Elvin-yk/EvoAB/issues/18).
