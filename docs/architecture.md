# Architecture

How EvoAB is built, and where each kind of extension goes. Development rules for
contributors are in [AGENTS.md](../AGENTS.md); running EvoAB is covered in the
[guide](guide.md).

## Idea

Robot-use agents keep a frozen general-purpose model and differ in the harness
around it. EvoAB makes the harness out of shared components and expresses every
method as a **recipe** over them:

| Axis | Choices | Examples |
|---|---|---|
| What the model sees | Multi-view RGB, proprioception, calibration; optional depth and perception | `inputs.depth: false`, SAM3 segmentation |
| How it acts | Any combination of tool calls, code and action chunks; end-effector targets with host-side IK, or joints | `act: [tools]`, `act: [code]` (Code as Policies), `act: [action]` (Astranomy Direct), `act: [tools, code, action]` |
| Policies | A VLA/WAM or other policy as one callable tool, or none | `hybrid` capability (`actor_act`) |
| Roles | One agent, or roles that delegate to each other | `delegate` capability |
| Experience | Skills or programs carried across episodes | `loop.episodes.carry: [programs]` (RAX) |
| Context | Full history, or a stateless window of frames | per role |

If a paper does not fit, the missing component is added to EvoAB rather than
special-cased in the method.

Two rules hold everywhere:

1. **An agent acts in exactly three ways, in any combination** (a role's `act`):
   `tools` (it calls tools), `code` (its programs call the same tools through
   `run_program`), and `action` (the model itself emits action chunks). Acting
   only through `action` is the stateless Direct loop; next to `tools` or `code`,
   the chunk is one more call in the agent's session. There is no fourth way.
2. **Everything else is a tool.** Motion primitives (`move`, `grasp`), perception
   (`locate`) and **policies** are tools reached through `tools` or `code`; motion
   tools are not "direct control". A policy (a VLA, a world-action
   model, a scripted or learned controller) maps observations and an instruction
   to actions behind one actor-service interface (`servers/actor/`), and agents
   call it as a tool. Adding a policy to an environment means a binding and a
   checkpoint, never environment-specific tool code. Users can add their own tools
   the same way.

How the agent works is configured only in the recipe (inputs, ways of acting, tools and
their options, context, budgets, loop); the command line selects the run (task,
services, model, outputs). New capabilities land in the CLI, recipes and SDK
first; Studio follows later.

## Layers

The layers follow the System 2 / 1 / 0 split of agentic robotics: the agent
(model plus harness) reasons, tools and policies are its skills, and the
environment is the body.

```
evoab/
├── cli.py config.py session.py __init__.py   entry: CLI, settings, one run, SDK exports
├── recipe/      recipe schema, compiler, method library, graphs
├── methods/     tools, code, action: one method.yaml + CARD.md each
├── agent/       System 2: the built-in agent
│   ├── loop.py      the model loop over a role's calls
│   ├── act/         the three ways of acting: code/ (run_program, bridge), action/ (chunks, Direct loop)
│   ├── context/     prompt assembly
│   ├── models/      model presets and their client
│   ├── memory/      skills and programs carried across episodes
│   └── team/        roles and delegate_<role>
├── tools/       System 1: model-visible tools (gripper, scene, skills, episode, hybrid policy, @tool)
├── runtime/     scheduling, resource exclusion, background jobs, replanning
├── envs/        System 0: one adapter per benchmark, plus
│   ├── protocol/    the environment HTTP protocol (snapshots, receipts)
│   ├── observation/ observation pipeline and ToolPackage proxy
│   └── docker/      setup and start of benchmark containers
├── servers/     model services you run yourself: actor (VLA/WAM), perception (SAM3)
├── records/     artifacts, telemetry and events of a run
├── apps/        console, Studio, replay and the runs/benchmarks commands (workbench/)
└── resources/   prompts and skills the robot agent reads at run time
```

`session.py` assembles one run: it compiles the recipe, prepares the episode,
builds each role's tools and prompt, runs the entry role and finalizes.
`resources/` holds the prompts and skills the robot agent reads at run time;
editing them changes method behavior.

## One run

1. Configuration loads the method, applies `--set` overrides and compiles every
   role against the integration's tool modules (`recipe/compiler.py`).
   Errors stop the run here, before any service starts.
2. The environment is started in its Docker container (`--deploy local`) or
   connected (`--env-url`), and its description supplies arms, cameras,
   calibration, limits and instruction.
3. All roles share one executor; each role sees its own tools through a view.
   `delegate_<role>` tools run another role against the same episode.
4. The entry role runs: an agent session over its calls (tools, `run_program`
   for `code`, the chunk tool for `action`), or, when it acts only through
   `action`, the stateless Direct loop.
5. The run drains outstanding work, finalizes the episode, records the official
   outcome (never shown to the agent) and writes the run directory.

## Components

`evoab methods components --integration X` prints the catalog that recipes are
checked against:

- **Ways of acting** (`act`): `tools`, `code`, `action`, in any combination ([guide](guide.md#methods-and-recipes)).
- **Capabilities** (tool modules published by integrations):

  | Module | Tools | Options |
  |---|---|---|
  | `gripper` | `move`, `grasp`, `release`, `hold` | |
  | `scene` | `view_env_state`, `capture_scene`, `locate` | `segmentation` (SAM3) |
  | `skills` | `list_skills`, `read_skill`, optional `write_skill` | `write` |
  | `episode` | `finish` | |
  | `hybrid` | `actor_act`: run a policy (VLA/WAM or Direct) from the actor service | `binding`, `review` |
  | `user_tools` | your `@tool` functions (every environment) | `file` |
  | `delegate` | `delegate_<role>` (harness) | `roles` |
  | `observation` | observation sidecar profile (harness) | |

- **Action representations** (`act: [... action]`): `abs_eef`, `delta_eef` (through
  the environment's IK), `joint_abs`.
- **Context**: agent sessions keep their transcript (the built-in agent
  consolidates long histories); a role acting only through `action` sees a
  stateless window of frames.
- **Loops**: single episode, or an episode sequence that carries skills or programs.

Perception: `locate` uses SAM3 when the recipe enables `segmentation` (the run
passes `--sam3-url`), and depth or
calibrated multi-view triangulation for 3D. The Observation ToolPackage
(`envs/observation/toolpackage/`, `--obs-pipeline` or `evoab ob-proxy`) runs SAM3,
FoundationPose and mask-IoU NMS as a proxy in front of the environment and adds
annotated images and detections without changing image size; making it a recipe
capability is tracked in [#12](https://github.com/Elvin-yk/EvoAB/issues/12).

## Episodes and the environment protocol

Environments are HTTP services (`envs/protocol/`, protocol v4). The
environment owns native state, immutable snapshots, one primitive step at a time
and execution receipts. Tools compose those steps into higher-level actions and
check stopping conditions; there are no server-side `move` or `grasp` routes.

- **Snapshots** bind images, calibration and robot state; `locate` reads a
  snapshot, so it can run while the arm moves.
- **Receipts** settle every command. A lost acknowledgement is reconciled, never
  replayed. Model-request retries never authorize physical retries.
- **Metadata** in the environment description adjusts shared tools without new
  schemas: `finish_statuses` (default `["success"]`; also `failure`, `give_up`),
  `motion_profiles`, `physical_hardware`, `motion_execution` (`stepwise` or
  `wholepath`), `normalized_opening_tolerance`.
- **Outcomes**: tool success, agent completion and task success are separate.
  `finish` returns an acknowledgement; the official outcome is recorded for
  evaluation only.

## Session verification

The environment service owns the sampled-image index. Its `GET /samples?start=N`
endpoint returns references to durable manifests and PNGs. The Session emits
one `observation_sampled` event per camera group, including groups collected
inside a multi-step command.

After the trajectory writer closes and the native outcome is available, a
configured Session writes its initial result with a pending verification
request ID. It sends `POST /verification/jobs` with `request_id`,
`native_outcome_path`, `trajectory_path` and `result_path`. The verifier
serializes jobs through its FIFO worker. All three files belong to the same
session within its shared runs root. The worker atomically updates only the
`verification` field and replies with the request ID and completed status.
Repeating the request with the same ID acknowledges the existing result.
The stub records the four output fields and labels its response with its backend.
The client uses a bounded wait and leaves result-file ownership with the worker
after submission.

## Scheduling

Each tool declares a policy: return mode (`immediate` or `background`) and
placement (`exclusive` on a resource, or `parallel`). Physical actions,
`capture_scene` and `finish` share the exclusive `episode` resource and run in
submission order; background calls return job handles (`wait_for_tool`,
`get_tool_result`, `cancel_tool`, `flush_queue`). Recipe settings:

| Setting | Values |
|---|---|
| `execution.return_mode` | `immediate`, `background` |
| `execution.resource_scheduling` | `per-resource`, `global-serial` |
| `execution.mode` | `parallel`, or `replanning`: after a failed world-changing action, queued actions are cancelled and new ones are refused until the result is read, the stop is confirmed and a fresh `capture_scene` is taken |

Tools report what an action did in `ToolOutput.action` (`executed` none/some/unknown,
`stopped`, `operator_required`); the executor never reads tool-specific fields.

## Extend EvoAB

| To add | Do this |
|---|---|
| **A method** | A recipe file; a directory in `evoab/methods/` only for a new way of acting's reference method. Paper reproductions are recipes over the three ways plus capabilities ([guide](guide.md#methods-and-recipes)). Never branch on a method id in code. |
| **A tool for your own methods** | A `@tool` function in a file selected with `user_tools` ([guide](guide.md#methods-and-recipes)); no EvoAB code changes. |
| **A capability** | A `ToolModule` (provider factory `create(session, selection)`, policies, selectable `tools`, typed `options`, label) whose schemas and handlers live in `tools/<family>/`; integrations that support it publish it in `tool_modules`. |
| **An action representation** | A `Representation` in `agent/act/action/actions.py`. There is no fourth way of acting. |
| **An environment** | An integration package with `get_integration_spec()`: native execution, observations and outcome in the adapter, an accurate description (arms, frames, units, limits, cameras, calibration), the tool modules it supports, a deployment module with a `container.toml` (a pinned upstream image or a `Dockerfile`; see `envs/docker/container.py`), and a section in [benchmarks](benchmarks.md). Environments run only in containers. It must not define its own model tools. |
| **An action model** | An `ActorBackend` registered in `servers/actor/registry.py`; environment-specific conversion stays in the integration's binding. |
| **A model vendor** | Add a provider and preset with `evoab models` (or Studio, 设置 → 模型); no code changes. |

## Validation

```bash
python scripts/validation/recipe_composition.py     # recipes, roles, experience, run_program
python scripts/validation/tool_composition.py       # tool schemas, visibility, prompts, act combinations
python scripts/validation/calvin_actor.py           # CALVIN Hybrid execution against fixture services
python scripts/validation/libero_actor.py           # LIBERO policy observation and native action binding
python scripts/validation/metaworld_actor.py        # MetaWorld policy observation and native/pose action binding
python scripts/validation/observation_toolpackage.py
evoab methods validate METHOD --integration X
```

For a behavior-preserving change to shared tools or prompts, compare
`tool_composition.py --compare SNAPSHOT` against a snapshot taken before the change.
CPU checks are not evidence of simulator or hardware success; state hardware
validation separately.

## Packaging

Two distributions share one version: `evoab` (CLI, SDK, Studio) and
`evoab-runtime` (installed into every service image). Build both with
`python scripts/release/prepare.py --output-dir DIR` and check them with
`scripts/validation/release_contents.py` and `scripts/validation/sdk_release.py`;
`evoab setup --wheel` accepts only the matching runtime wheel.
