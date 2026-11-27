# Environment integrations

evoab uses shared robot tools across simulator and physical environments.
Both `run` and `tui` require an explicit `--integration`; there is no default.
Choose an integration, then prepare its external services. Python extras install
harness-side dependencies; simulators, hardware runtimes, assets and model
checkpoints remain separate.

## Included integrations

| Integration | Use | External services |
|---|---|---|
| `robodojo` | Official RoboDojo simulation, task requirements and bundled gripper skills | RoboDojo environment; Hybrid adds an identified Pi0.5 model and a reviewer by default |
| `libero_plus` | Official LIBERO-Plus perturbation tasks, Direct control | [Separate pinned LIBERO-Plus runtime](libero-extensions.md) |
| `robotwin` | RoboTwin 2.0 dual-arm tasks, Direct control | [Pinned RoboTwin runtime and assets](robotwin.md) |
| `libero_pro` | Selected official LIBERO-PRO suites, Direct control | [Separate pinned LIBERO-PRO runtime](libero-extensions.md#libero-pro) |
| `rmbench` | Memory-dependent dual-arm manipulation, Direct control | [Pinned RMBench runtime and assets](rmbench.md) |
| `calvin` | Single-task Panda episodes, motion tools and `actor_act` | [Pinned CALVIN environment, annotations and StarVLA actor](calvin.md) |
| `robolab` | RoboLab DROID, Direct scalar-gripper control | Prepared RoboLab/Isaac environment; RGB cameras |
| `dexjoco` | MuJoCo Panda/Allegro manipulation through Hybrid | Pinned DexJoCo environment and an identified OpenPI actor |
| `behavior` | Experimental R1Pro wheeled mobile manipulation through Hybrid | [BEHAVIOR runtime/assets and OpenPI actor](behavior.md) |
| `isaaclab_arena` | Experimental G1 humanoid loco-manipulation through Hybrid | [Arena runtime/assets and GR00T actor](isaaclab_arena.md) |
| `navila` | Experimental Go2 quadruped navigation through Hybrid | [NaVILA-Bench runtime/scenes and NaVILA actor bridge](navila.md) |

Each integration composes its supported tools. RoboDojo and RoboLab
provide Direct and Hybrid profiles; Hybrid requires an admitted controller and
actor binding. CALVIN also exposes both motion and policy tools. LIBERO-Plus,
LIBERO-PRO, RoboTwin and RMBench provide Direct only. DexJoCo, BEHAVIOR, Arena
and NaVILA provide Hybrid with scene, skill and
episode tools, without Direct gripper tools. Actor STOP yields to the planner,
which decides when to call `finish`; native termination and metrics remain
authoritative.

The three new mobile adapters have CPU contract validation; their current native
validation results and limitations are summarized in [Hybrid](hybrid.md).
Arena uses its pinned official Isaac Sim 5.1 branch; NaVILA's pinned Isaac Sim
4.1 runtime does not support Blackwell. Consult the individual guides before
preparing runtimes. RoboLab has no automatic
setup/deploy recipe. Use SAM3 only where supported; otherwise pass `--no-sam3`.
Depth localization requires paired depth and calibration; RGB triangulation
requires at least two calibrated views.

Studio lists RoboDojo tasks with a native task configuration and a frozen layout at index zero.

RoboDojo has 42 tasks in five capability dimensions (generalization 12, memory 6,
precision 8, long-horizon 8, open 8); each generalization task also has a
`<task>_random` config with a changed initial arrangement, so 54 configs run.
`evoab/integrations/robodojo/tasks.py` records this catalog, and Studio groups
tasks by dimension with a standard/random switch. An episode is one frozen layout
`Assets/Eval_Layout/RoboDojo/arx_x5/<seed>/<task>_<layout>.json`: `--seed`
(0, 1 or 2) picks the official evaluation set and `--layout-id` the scene within
it (25 per generalization config, 50 otherwise). Both default to 0 and are
recorded in the episode metadata. To register an existing prepared checkout, run `evoab setup --integration robodojo --runtime-python /path/to/native/bin/python --native-root /path/to/RoboDojo` on the Studio host. Rerun the original setup command to refresh the catalog after adding layouts or upgrading an older installation. This also refreshes an existing isolated RoboDojo runtime without reinstalling it.

A registered RoboDojo runtime whose configs have no frozen layout at
`Assets/Eval_Layout/RoboDojo/arx_x5/0/<task>_0.json` is marked in Studio as
registered with no selectable tasks, with the recovery steps for that asset
layout; setup reports the same condition. The CLI can still run such a task when
`--layout` names a layout explicitly. Other integrations show the same notice
with a generic recovery step instead of RoboDojo's layout instructions.

## Real-robot release status

`arx_one` supports a dual-arm ARX ONE with its own Body runtime, on the shared gripper
tools. See [ARX ONE](../evoab/integrations/arx_one/README.md); a new machine
supplies third-party binaries, meshes and calibration as described in its
[deployment guide](../evoab/integrations/arx_one/DEPLOYMENT.md).
Public support for **Franka** is **Coming soon**.

`piper_x` provides a separate Piper-X Body runtime and environment adapter using
the same Direct tools and `wholepath` contract. Recipes without the gripper capability give an observation-only session, and explicit per-station identity/calibration manifests.
See [Piper-X](../evoab/integrations/piper_x/README.md) for preparation,
official CLI commands, migration details and hardware validation status.

## Inspect integration options

```bash
evoab run --integration robodojo --help
```

See [getting started](getting-started.md) for installation and a first offline
configuration check. The CLI is `evoab`; the Python package is `evoab`.

## Simulation image recording

Direct and Hybrid archive one camera group at step zero and then about every
400 ms of simulation time. The environment service rounds 400 ms to a whole
number of native steps and uses that stride throughout the episode.

MetaWorld uses 32 steps, DexJoCo, IsaacLab Arena and NaVILA use 20, CALVIN and
BEHAVIOR use 12, RoboDojo uses 10, the LIBERO family uses 8, and the standard
RoboLab DROID setup uses 6. RoboTwin and RMBench sample every 12 native actions
using a nominal 30 Hz rate. Those actions execute variable-length trajectories,
so simulated time between samples can vary. Custom integrations set their
stride through `control_hz`.

The environment artifact store saves one PNG per available camera in `images/`.
Snapshot manifests in `artifacts/` identify the archived groups by
`sampled_step` and `captured_at`. Fresh camera observations for policy inference
and tools use temporary storage. Rebuild and restart existing environment
services to use this policy.

## Prepared episodes and shared tools

`--env-url` points to an already prepared episode, including its full allocated
path when using a pool. The harness checks optional expected identity fields;
it does not use them to create or reset a simulator. The episode's metadata
provides its task instruction, available arms/devices, cameras, calibration and
action binding. Profiles need not expose scalar grippers.
`--instruction` overrides the instruction for the agent and actor.

| Tool family | Interface |
|---|---|
| Scene | `view_env_state` reads recorded RGB/state; `capture_scene` freezes RGB/state and available calibration and paired depth; `locate` queries the immutable snapshot using the advertised capabilities |
| Direct motion | `move(targets, profile="standard")`, `grasp`, `release`, `hold` |
| Actor | `actor_act` runs bounded chunks with fresh observations and optional reviewer decisions |
| Guidance | `list_skills()` lists shared general/ and current-environment Markdown paths recursively; `read_skill(path=...)` reads a file's content and SHA256; `--set roles.main.capabilities.skills.options.write=true` adds `write_skill` within the episode's namespace and requires an explicit external skills root |
| Completion | `finish` submits termination; the official task outcome stays outside model-visible feedback |

Tool definitions live in [`tools/gripper/`](../evoab/tools/gripper/),
[`tools/hybrid/`](../evoab/tools/hybrid/),
[`tools/scene/`](../evoab/tools/scene/), [`tools/skills.py`](../evoab/tools/skills.py)
and [`tools/episode.py`](../evoab/tools/episode.py).

An environment adjusts these tools through its description's `metadata`; an
environment that declares nothing gets the defaults, with unchanged schemas.

| Key | Default | Effect |
|---|---|---|
| `finish_statuses` | `["success"]` | Statuses `finish` offers, from `success`, `failure` and `give_up`; they are recorded as `completion_claimed`, `task_failed` and `task_abandoned` |
| `motion_profiles` | Shared `standard`/`approach`/`precision` | Replaces whole named profiles (`max_step_m`, `position_tolerance_m`, `orientation_tolerance_rad`, all positive) with arrival criteria matched to the arm; the `move` description states the effective values |
| `physical_hardware` | false | A physical environment's results never count as a confirmed stop by themselves; its receipts must confirm the stop for the [replanning execution mode](architecture.md#execution-modes) (see below) |
| `motion_execution` | `stepwise` | `stepwise`: `move` is the waypoint loop that checks tracking after every step, and gripper actions repeat a pose hold. `wholepath`: `move` sends one `PathCommand` that the environment preflights and runs without intermediate stops, arrival is checked once and a truncated path reports `reachable_prefix`; `grasp`, `release` and `hold` hold every joint at its measured angle in one command lasting `steps` control periods. Schemas are the same in both modes; descriptions follow the mode. `wholepath` requires the backend's `step_path` |
| `normalized_opening_tolerance` | None | For arms without `gripper_opening_models`, checks an explicit 0-1 opening against the measured 0-1 opening within this tolerance, in `(0, 0.5]`. Only readings whose arm metadata sets `gripper_opening_measured: true` are judged; others stay unknown |

A physical environment reports what a refused or interrupted command did in three typed
`StepReceipt` fields, which the gripper tools turn into the action's outcome:
`execution_outcome` (`none`, `partial` or `unknown`), `stop_confirmed` (the actuators are
confirmed stationary) and `requires_operator_recovery`. It reports facts, not recovery steps: the
[replanning execution mode](architecture.md#execution-modes) derives the only model-facing
recovery report (`replan`) from them. A refusal that ran nothing and needs no operator is an
ordinary failed result. Unknown execution, an unconfirmed stop or a required operator leaves
recovery to the operator. Keep native stop evidence in `diagnostics` for people.

## RoboDojo

`--integration robodojo` adds the RoboDojo prompt and optional task-requirements
loader to the shared prepared-episode lifecycle. `--task` and `--run-id` identify
the expected episode. `--include-extra-task-requirements` loads the repository's
task requirements; it is off by default (official Agent Track rules) and loads the simplified
`resources/prompts/robodojo/instruction_supplements.json` catalog. These supplements
clarify the task instruction, including the required sequence.
The full `task_requirements.json` remains a separate reference resource.

### Realistic observation settings

The paper's **Realistic** principle excludes simulator-provided depth and
task-verifier feedback from agent/tool interaction. RGB images, proprioception,
camera calibration, robot descriptions and task instructions remain available.
Static task requirements describe the task; they are separate from a verifier's
live assessment of progress or success. `finish` acknowledges submission without
returning the official outcome to the agent. Outcomes are saved for evaluation.

RoboDojo captures depth by default. For the paper's RGB-only input setting,
pass `--no-depth` when starting the environment through `run --deploy local`,
`deploy env` or the native environment server. This retains RGB and camera
calibration. With at least two calibrated RGB-only cameras, `locate` supports
multi-view triangulation. When connecting with `--env-url`, configure these
capabilities on the existing environment service before starting the agent;
the agent connection does not disable service-side depth acquisition.

The harness also supports optional depth localization and physical RGB-D sensors.
These capabilities do not establish that a run uses the paper's RGB-only setting.
See [runtime defaults](running.md#single-episode-cli) for capability selection.

<a id="minimal-robot-tools"></a>

### Clean robot tools

All seven gripper integrations support `--recipe clean`: RoboDojo,
RoboLab, RoboTwin, LIBERO-Plus, LIBERO-PRO, RMBench and CALVIN.
The former `--recipe clean` remains a compatibility alias; CLI and Python
configuration resolve it to `clean`, including saved run metadata.
It exposes `move`, `hold`, `capture_scene` and `finish`, reusing the same schemas, controllers,
execution receipts and outcome storage as Direct. `move` already accepts a
gripper opening, so separate grasp/release tools are unnecessary.

Clean selects immediate tool returns and omits skill, localization, Hybrid, observer,
workspace and background-job tools. Explicit background returns, tool debugging,
skill writing, observation profiles and Code as Policy are rejected. Planner
SDK native computation/file tools retain their existing configuration. Direct
remains the default; Direct/Hybrid tool visibility and scheduling are unchanged.

Start the environment with depth disabled (for example RoboDojo `--no-depth`).
An external `--env-url` must already describe a depth-disabled service. Clean
supports calibrated RGB and cameras that explicitly declare no calibration;
it does not expose a depth setting or silently change the connected service.
Initial input includes RGB images, measured robot state and per-image calibration.
Codex, Claude Code, Gemini CLI, OpenCode and Antigravity accept these images as
multimodal user input. The shared artifact store retains images and snapshot
manifests; `initial_observation.json` records the initial image/state references.
`capture_scene` requests all or selected camera views without advancing control
steps. It does not initialize SAM or expose `locate` or `view_env_state`.
Initial input and image-bearing clean results include `observation.artifact_ref`,
the readable JSON of the immutable snapshot, with original same-frame calibration.
Model-facing `observation.cameras` contains rounded camera matrices and each view's
absolute `image_path`; image attachments remain unchanged. Snapshot artifact IDs
are opaque references, not local image filenames. Other profiles retain
`observation_path` and `camera_calibration`.

All seven integrations publish robot reference files independently of the tool
profile. Initial input lists their exact local paths, media types and SHA-256
hashes in `robot_resources`; the list is saved as `robot_resources.json` and
returned by scene tools. Native agent file tools may read those exact paths.
Existing `robot_calibration_path`, hash and `robot_manual` scene fields remain.
Static robot references do not replace per-observation camera matrices.

Reference detail follows the native integration, not a fabricated common URDF:
Dojo/Lab retain their existing geometry and gripper calibration; RoboTwin/RMBench
and CALVIN publish their actual URDF kinematic trees and control mappings;
LIBERO-Plus/PRO publish robot-only compiled bodies,
joints, sites and geometry parameters. Each resource states its format and limitations. No task objects,
goal state, grader inputs or whole simulator scene files are published.

Completed gripper actions in every profile reuse their final snapshot when it
contains current images and the camera's declared calibration. Otherwise they
capture once after the model-level action, not after every primitive. Returned
`camera_calibration` includes `intrinsic_K` and `T_world_from_optical` for the
attached image grids, together with `image_state_version` and `snapshot_id`.
The latter transform maps optical coordinates into world metres. This may add
capture latency on RoboDojo, whose ordinary tiled snapshots lack calibration.
Missing declared calibration or a stale capture produces an `artifact_error`
while preserving the acknowledged action feedback; it never replays motion.
Cameras declaring calibration unavailable, including distorted LIBERO-Plus
agent views, retain null matrices instead of fabricated geometry.

### Camera-calibration ablation

For RoboDojo, add `--no-camera-calibration` at environment startup to disable
camera intrinsics and extrinsics as well; this option requires `--no-depth`.
Use `run --deploy local`, `deploy env`, or the native RoboDojo server. The environment
declares `has_calibration=false`. RoboLab does not support this switch;
explicit calibration overrides are rejected for that integration and for actor
or SAM3 deployment. In `run --env-url` mode, configure the external service at
startup instead; the agent connection cannot change its camera capabilities.
Captures and action records contain no camera matrices or depth data; image paths,
sizes, robot proprioception and robot geometry remain available. `locate` exposes
only 2D SAM segmentation, or is absent when SAM is unavailable. Triangulation,
pixel-to-world localization and plane fitting are not exposed.
Use matching agent and service versions; older clients may reject the added
camera capability field.

### Hybrid control

External actors require `--action-model-url` and an expected
`--action-model-checkpoint-sha256`. The Pi0.5 reviewer is enabled by default and uses
`--vla-review-base-url` or the inherited `--provider-url`. Reviewer model, adapter
and credentials are independently configurable; see
[reviewer configuration](running.md#vla-reviewer). `--no-vla-review` leaves the
[actor execution budgets](running.md#actor-execution-budgets) unchanged. See [RoboDojo Direct policy](hybrid.md#robodojo-direct-policy) for the in-process model-preset binding.

### Coordinated arm targets

`move`, `grasp` and `release` accept one or two distinct arm targets.
RoboDojo applies them together in each native control step, including in serial
execution mode. Unlisted arms hold; an arm reaching tolerance holds its achieved
pose while the other continues. A fault on either arm stops the call.

List order does not set execution order. Separate tool calls remain separate
actions even when emitted together. This coordinates target tracking, without
planning collision-free paths or maintaining a rigid relative pose between hands.

`move` requires absolute world [x,y,z,roll,pitch,yaw] in metres and fixed-axis
XYZ radians (R=Rz(yaw)@Ry(pitch)@Rx(roll)). State, primitive poses and Hybrid
previews keep wxyz quaternions at the same site. Feedback adds six-value
`target_pose`/`actual_pose` alongside existing position/quaternion fields.
Retain measured XYZ for an in-place turn. Translation and rotation interpolate
together; dependent stages need separate calls and completed feedback checks. Each integration
registers `control_frame`, `grasp_site_description` and `rotation_radius_m` on its
arm descriptors; shared tools interpolate poses without loading a robot model.
RoboDojo merges a fixed-site URDF extension into the native X5 chain for FK/DLS,
and converts site targets once for native link6 IK.
The site is a geometric convention, not object attachment evidence.

Environment protocol 4 and tool schema 8 define this interface. Upgrade services
and clients together. When replaying saved commands, verify that their recorded
coordinate conventions and schema match the target environment.

### Active gripper guidance

The [RoboDojo index](../evoab/resources/skills/robodojo/index.md)
links to the [shared skill index](../evoab/resources/skills/general/index.md)
for gripper control, handover, pose targeting, contact, placement, verification
and recovery. It adds X5 and environment-specific supplements. The agent selects
references from the observed situation and available capabilities.

Start with [the Direct example](../examples/README.md#robodojo). For actor-backed
control, use [the Hybrid example](../examples/robodojo_hybrid.sh) and
[environment service scripts](../scripts/deploy/services/robodojo/).
[RoboDojo SOURCE.md](../evoab/integrations/robodojo/SOURCE.md) records pinned
upstream revisions and retained licenses. The official environment server requires
`--native-root` pointing to a clean pinned official checkout, including its submodules.
External runtime trees are not packaged.

## RoboLab

`--integration robolab` composes the shared scene, gripper, skill and finish providers
for a prepared single-DROID episode. See [RoboLab setup](robolab.md) for runtime
requirements, coordinate frames and supported features. The integration uses RGB
and calibrated multi-view triangulation, with the optional `robolab_droid_joint`
actor binding and no SAM3 binding.

## DexJoCo

`--integration dexjoco` connects a prepared native device episode to the shared
Hybrid tools. The first admitted task is `pick_bucket`, using the public
single-arm Pi0.5 checkpoint. The binding maps measured TCP/finger state and
absolute TCP/finger actions to one coordinated native control step. The native
OSC and Allegro controllers retain their original physics and task budget.

The integration requires Hybrid and exposes actor execution with optional
student/stop review. Direct motion and geometric action corrections are not
provided. Official task success is recorded only at finalization. See
[DexJoCo setup and validation](dexjoco.md) for exact support and baseline limits.

## Model bindings and backends

All Hybrid integrations share `ActorClient`, `ActorService`, `HybridTools` and
one execution loop. The integration selects its binding through a factory;
`integrations/hybrid.py` owns actor/reviewer connections.

| Environment | Binding | Actor semantics |
|---|---|---|
| RoboDojo | `robodojo_pi05` | Explicit legacy Pi0.5 config, images, RNG and clipping |
| RoboDojo X5 | `robodojo_x5_joint` | Absolute X5 joints and continuous normalized openings at 25 Hz |
| RoboDojo X5 | `robodojo_direct_eef` | In-process Direct policy with absolute grasp-site targets and normalized openings at 25 Hz |
| RoboLab DROID | `robolab_droid_joint` | Seven measured joints plus normalized finger angle; joint and physical-gap targets |
| DexJoCo | `dexjoco_tcp_hand` | Absolute world TCP pose plus named Allegro finger positions in one native step |
| BEHAVIOR R1Pro | `behavior_r1pro_joint` | Torso, arm and gripper joint targets with local base velocity at 30 Hz |
| Arena G1 | `arena_g1_locomanipulation` | Arm/hand targets, heading-frame velocity and pelvis height through the native whole-body controller at 50 Hz |
| NaVILA Go2 | `navila_go2` | Front-camera history to discrete movement; bounded native locomotion segments or yield to the planner |

For external actors aside from the pinned legacy Pi0.5 path, bindings validate semantics without
requiring a particular backend runtime. XPolicyLab can serve any of these
contracts when its configured model actually implements them. A compatible
checkpoint change is deployment configuration. A new representation needs an
explicit binding conversion or reusable capability, not another actor loop.
See [Hybrid extension and migration](hybrid.md), the
[XPolicyLab bridge](services.md#xpolicylab-bridge), and
[service profiles](services.md#openpi-deployment-profiles).

## Integration contract

Expose `get_integration_spec()` from an integration module. The
[`IntegrationSpec` and `IntegrationSession` contracts](../evoab/integrations/base.py)
keep the runtime independent of a simulator or transport:

1. **Configuration:** declare CLI arguments, validation, typed `config_model`
   options and tools available in task runs. Declare `deployment_module` only
   when automatic preparation/deployment is supported.
2. **Lifecycle:** return a session from `create_session(IntegrationContext)`
   implementing `start`, `finalize` and `close`.
3. **Tools:** provide `tool_providers`, `episode`, `task_succeeded()` and
   `action_model_name`. Return structured `ToolOutput` values.
4. **Scheduling:** declare resource policies for physical operations and
   lifecycle barriers; transport order does not establish exclusion.
5. **Outcomes:** return `EpisodeOutcome` with acceptance and failure
   classification. Retryable infrastructure failures are not accepted evaluation
   results. Set `allow_automatic_retry=False` when replay is unsuitable.

Register environment prompt builders explicitly; otherwise the generic builder
is used. Keep heavy imports in service processes or behind startup so CLI help
and offline validation work without native runtimes.

## Integration layout

```text
integrations/<name>/
├── __init__.py             # integration registration
├── prompt.py               # environment-specific context
├── action_binding.py       # model observation/action conversion, when needed
└── environment/
    ├── adapter.py          # EnvironmentBackend implementation
    ├── native_compat.py    # pinned runtime compatibility checks
    ├── server.py           # prepare episode and mount shared routes
    └── ...                 # native geometry, controllers and kinematics
```

RoboDojo keeps its native adapter in `environment/adapter.py`.
Shared tools and HTTP protocols stay in `tools/` and
`services/`.

### Prepared episode composition

Publish the tool modules the integration supports on `IntegrationSpec.tool_modules` and reuse
`PreparedEpisodeSession(context, binding_factory=...)` for URL-backed episodes.
The modules the recipe selected (`context.tool_modules`, each with its selection) create providers against `session.episode`
and register auxiliary clients with `session.own()`. The session closes them even if later
setup fails, then releases its environment client. The outer session owns
`IntegrationContext.artifacts`; integrations use that store without closing it
or creating their own image copies.

Observation providers return `ObservationData.image_paths` pointing to ready
files in the shared store. Publish image bytes through the store at the provider
boundary, then pass `local_path(artifact_id)` to `ObservationHub`. Runtime paths are absolute;
persisted observation references are relative to the session directory.

RoboDojo uses `integrations.gripper.create_session`; RoboDojo also
supplies a task-requirements loader. Another tool family can use a different callback
without adding action-name dispatch to the runtime or duplicating episode state.

See [architecture](architecture.md) for module ownership and
[services](services.md#add-an-actor-backend) for backend extensions.
