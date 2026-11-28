# Benchmarks

Released benchmarks use published, ready-to-pull simulator images. The OpenArm
development integration builds its Gazebo image locally. Running methods on a prepared environment is in
the [guide](guide.md#run-from-the-command-line); adding a new environment is in
[architecture](architecture.md#extend-evoab).

| Integration | Kind | Image | Tools | Notes |
|---|---|---|---|---|
| `libero` | Original LIBERO | Public | Motion, policy (StarVLA) | Spatial, Object, Goal, Long, 90 |
| `libero_pro` | LIBERO-PRO suites | Public | Motion | `_env`, `_object`, `_swap`, `_task`, `_lan` variants |
| `calvin` | CALVIN Panda | Public | Motion, policy (StarVLA) | One task per episode |
| `openarm_gazebo` | OpenArm 2.0, Gazebo Classic and ROS 2 Humble | Local Docker build, CPU | Wrist motion, pinch gripper and measured status | Single-arm reach on either side |
| `custom` | Your Gym-style benchmark | Your image | Motion | See [custom benchmarks](#custom-benchmarks) |

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

GPU benchmark containers need the NVIDIA Container Toolkit. Studio: 设置 → 评测集 →
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
Run setup once after migrating from the older runtime record format. The
container now mounts the host's EvoAB code on each launch. The build requires
Docker on Linux and network access for the first setup.
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
evoab run --integration openarm_gazebo --task reach_right --recipe tools \
  --model <preset> --instruction 'Raise the right wrist by 4 cm' --monitor
evoab replay <run-directory>
```

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
