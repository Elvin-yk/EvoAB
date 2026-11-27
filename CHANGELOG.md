# Changelog

## Unreleased

- Direct can serve absolute EEF chunks through the actor service. On RoboDojo,
  use `agent_policy` with the `robodojo_direct_eef` binding in place of the
  `planner_direct` recipe. In Studio, selecting Direct uses the calling Actor's
  model preset and requires no separate policy service. Standalone `direct_action` still uses the action channel.

### One agent: the built-in agent over model presets

EvoAB now runs every method on its built-in agent; models come from presets
(`evoab models`). Removed, with all their code:

- The Codex, Claude Code, OpenCode, Gemini CLI and Antigravity agent runtimes,
  `--agent` and the runtime flags (`--agent-python`, `--opencode-*`,
  `--gemini-*`, `--mcp-timeout-s`, `--claude-max-budget-usd`), the
  `openai-codex` and `claude-agent-sdk` dependencies and the `antigravity` extra.
- `evoab serve`, `evoab tools` and `evoab stop` (episodes exposed over MCP).
- The Q0-Preview external evaluator (`q0_preview_upstream`).
- The provider gateway those runtimes used (`--provider-url`,
  `--provider-profile`, `--model-preset`, `--save-exchanges`, provider
  timeouts and retries) and the context window policies that needed it
  (`context.policy` sliding_window / observation_window). Recipes drop
  `context`, `brain.agent` and `execution.parallel_tool_calls`.
- The VLA chunk reviewer now uses a model preset: `--vla-review-model PRESET`
  (default: the run's model) replaces `--vla-review-base-url`,
  `--vla-review-adapter` and `--vla-review-api-key`.

### Ideas from dimos: your own tools, MCP serving, replay

- `@tool` (`from evoab import tool`): a decorated Python function is an agent
  tool; the docstring is its description, type hints its arguments, and `robot`
  calls the episode's other tools. Select a file with the `user_tools`
  capability (every environment). Example: `examples/tools/lift.py` (#43).
- `evoab serve` starts an episode and serves the recipe's tools over MCP until
  `evoab stop`; `evoab tools list|call` call them by hand and any MCP client
  (Claude Code, Cursor) can be the agent (#44). It replaces `evoab tui`, which is
  removed with the Textual dependency and its file tools.
- `evoab replay RUN…` replaces `evoab monitor --run-dir` (#45).

### Plain recipes and a clearer command line

- `evoab run` starts the benchmark's environment itself by default (`--deploy
  local` is implied). `--env-url` still connects to a running environment but is
  an internal and developer path and is no longer shown in help or the guide.
- NaVILA (Go2 navigation) is removed: the integration, its actor backend and
  profile, prompts, skills and examples.

- `direct_policy` is renamed `direct_action` ("policy" means a VLA-style tool).
  Use `--recipe direct_action`.
- Recipe `parameters` (named shortcuts such as `--set horizon=3:10`) are removed.
  Every built-in `method.yaml` now writes out its settings with a comment on
  each; override any value by dotted path, read as YAML:
  `--set 'roles.main.action.horizon=[16,32]'`. `evoab runs launch --set` and the
  SDK's `recipe_overrides` take the same paths.
- `evoab methods show` and `evoab methods graph` are removed; `evoab methods list`
  prints where each `method.yaml` lives.
- `evoab benchmarks list` shows benchmarks only (no real robots) with
  integration, title and status (ready or not prepared).
- `evoab benchmarks tasks` shows each task's capability dimension, its real
  instruction and step limit read from the native task source (RoboDojo), and
  which seeds and layouts can be chosen.

### How the agent works is configured only in the recipe

The recipe says how the agent works; the command line only selects the run
(task and scene, where services run, which model, outputs).

- New recipe section `inputs`: `depth`, `calibration` (applied by
  `--deploy local`, checked against a connected environment's description) and
  `task_supplements` (environments that publish them; RoboDojo).
- New capability options: `scene` `segmentation` (SAM3 for `locate`) and
  `hybrid` `binding` and `review` (the policy tool's environment binding and
  chunk reviewer).
- Studio's tool picker writes the narrowed recipe for the run.
- Removed from `evoab run` and the SDK, with their replacements:

  | Removed | Use |
  |---|---|
  | `--no-depth`, `--no-camera-calibration`, `--cameras` | `--set inputs.depth=false`, `--set inputs.calibration=false` (`deploy env` keeps the flags) |
  | `--include-extra-task-requirements` | `--set inputs.task_supplements=true` |
  | `--no-sam3` | nothing: SAM3 is off unless the recipe sets `scene` `segmentation: true` (then pass `--sam3-url`) |
  | `--no-vla-review`, `--action-model-binding X` | `--set roles.<role>.capabilities.hybrid.options.review=false`, `….binding=X` |
  | `--max-turns N` | `--set roles.<role>.max_turns=N` |
  | `--visible-tools …` | narrow `roles.<role>.capabilities.<id>.tools` |
  | `--model-parallel-tool-calls`, `--mcp-parallel-dispatch` | `execution.parallel_tool_calls` in the recipe; Codex always delivers calls one at a time to EvoAB's executor |
  | `--codex-base-prompt`, `--codex-web-search` | nothing: every agent gets the assembled robot prompt and only the recipe's tools (Codex web search is off) |
  | `--claude-effort` | `--reasoning-effort` (Claude Code accepts none, low, medium, high, max) |
  | `--log-level`, `--telemetry debug` | `--debug` (`--telemetry off\|standard` stays) |
  | `--tool-debug`, `--tools` | narrow capabilities in the recipe; the declared-but-unavailable tool catalog is removed |
  | SDK `no_sam3`, `visible_tools`, `AgentConfig.max_turns`, `integration_options` `include_extra_task_requirements`, `vla_review`, `action_model_binding`, `tool_debug`, `tools`, `model_parallel_tool_calls`, `codex_*`, `mcp_parallel_dispatch`, `claude_effort`; `telemetry="debug"` | `recipe_overrides` with the paths above; `debug=True` |

### RUA folded into the core

- RUA (arXiv 2611.01560) is EvoAB's own harness, so it is no longer a separate
  method or agent. The `rua` method was identical to `agentic` and is removed. Removed the `rua_upstream` external
  evaluator, the `rua` agent adapter (`evoab/agents/rua/`), the `rua/` directory
  and `AgentLaunch.tool_groups`, which only that adapter used. Use
  `--recipe agentic` where `--recipe rua` was used; to run the original
  uni-robot-agent package, install it on its own.

### Documentation

- Documentation is now four files: `README.md`, `docs/guide.md` (install, models,
  Studio, running, methods and recipes, results, SDK), `docs/architecture.md`
  (layers, components, protocol, scheduling, extension points, validation) and
  `docs/benchmarks.md` (preparing every environment and robot). The 36 former
  pages, `CONTRIBUTING.md` and `examples/README.md` were merged into them or
  removed where obsolete (platform design notes, release procedure details).
  Upstream `SOURCE.md` provenance files, method cards and the real-robot
  manuals stay beside their code. Both wheels use `README.md` as their readme.

- Add the Q0-Preview reproduction for simulated RoboDojo X5 episodes, with calibrated RGB observations, single-step receipts and native outcome recording. On an RTX 4090 D, GPT-6.2 Astra through Codex CLI completed the frozen `stack_bowls` task in 483 steps.

- **Recipes replace interaction modes and tool profiles (breaking).** A run's
  method is a recipe (`evoab/composition/`): roles with a brain, a channel
  (`tools`, `code`, `action`), capabilities and a context policy, connected by a
  loop. `--recipe METHOD` and `--set PATH=VALUE` replace `--interaction-mode`,
  `--tool-profile`/`--action-profile`, `--direct-*`, `--skill-writing`,
  `--tool-return-mode`, `--resource-scheduling`, `--execution-mode`,
  `--observation-profile` and `--context-*`; `SessionConfig(recipe=...,
  recipe_overrides=...)` replaces the matching SDK fields. Migration:
  `--tool-profile direct` → `--recipe agentic`, `hybrid` → `agent_policy`,
  `clean` → `clean`; `--interaction-mode code_as_policy|direct_policy` →
  `--recipe code_as_policy|direct_policy`; `--direct-horizon 3:15` →
  `--set horizon=3:15`; execution flags → `--set execution.<name>=...`.
  Single-role recipes produce the former tool schemas and prompts byte for byte.
- `IntegrationSpec.tool_profiles`/`default_tool_profile` become `tool_modules`
  (a catalog) and `default_recipe`; `ToolModule.create(session, selection)`
  receives the role's tool subset, typed options and style.
- Method library (`evoab/methods/`, `EVOAB_METHODS_DIR`) with reproduction
  cards; `evoab methods list|show|graph|components|validate`.
- Multi-role recipes: `delegate_<role>` tools run another role (its own brain,
  channel and tool view) against the same episode. New `planner_direct` method.
- Experience across episodes: `loop.episodes.repeat/carry/keep` with staged,
  success-gated promotion of skills and programs. New `code_iterate` method.
- Direct control in joint space: `joint_abs` representation (`JointCommand`).
- RUA becomes native: the `rua` method is the paper setting (arXiv 2611.01560)
  as a recipe; the external evaluator is the `rua_upstream` method. The one-off
  resource migration tooling (`rua/migrate_resources.py`, `rua/link_resources.py`)
  and its test are removed.
- `--model-preset` lets HTTP agents use a Studio preset; Codex and Claude Code
  can be launched from Studio, including code methods.
- Managed runs (`--deploy local`) pass the recipe to the agent process as data
  and recompile it there.
- Delegation: tool/code delegations no longer hold an exclusive queue (and are
  rejected under `global-serial`); `Session.cancel()` and the episode deadline
  reach delegated roles; action delegations report executed steps for replanning.
- Removed unused code: `AgentLaunch.modes`, Studio's legacy method aliases,
  several unused helpers, three unreferenced validation scripts and two orphaned docs.
- Studio: method step from the library with component graphs and generic
  parameter forms; run pages show the recipe graph annotated with call counts.

- Studio can uninstall one prepared Docker deployment (卸载 beside 部署/已部署):
  `DELETE /api/benchmarks/<integration>/<profile>/setup` removes that
  deployment's records and local runtime image while keeping runs and
  trajectories, import archives, shared upstream images and build caches, and
  host assets. It refuses while the integration has an active setup, an active
  Studio run or any container (stopped included) using the image, never stops
  containers, prunes images or forces removal, keeps images another recorded
  deployment references, retains the setup log but clears its failed status, and
  unregisters only after Docker positively reports an already-missing image.
  Native installations are not uninstalled. See `docs/containers.md`.

- Studio can import single-image Docker archives from a server directory it shows,
  reuse container deployment checks, and require a successful environment launch
  before offering imported runtimes. That directory defaults to the cache's
  `image-imports` and can be edited, saved or reset in Studio; the saved setting
  and `EVOAB_IMAGE_IMPORT_DIR` both feed the archive listing and the setup child.

- RUA uses the shared HTTP preset resolver and its declared wire profiles.
  Studio shows incompatible presets with provider-specific reasons. The existing
  Codex OAuth preset requires separate authorization for public Responses.
  The RUA evaluator also rejects model replies that echo request credentials.

- Studio groups registered Agent implementations under Agentic Policy.
  RoboDojo offers the built-in Agent and RUA, with model presets and settings
  supplied by each adapter. `evoab runs launch --method agentic --agent rua`
  and its legacy `--method rua` alias start the same evaluator. RUA runs on the
  host and connects to EvoAB's prepared Docker episode. See
  RUA on RoboDojo for configuration and verification.

- Studio serves the nanobot settings UI as versioned, cacheable assets. Benchmark
  registration and manifest management have been removed from Studio, the HTTP
  API and the CLI. Existing profiles and historical runs remain readable.

- Container runtimes (`docs/containers.md`): `evoab setup --container` /
  `evoab benchmarks prepare --container` pull a public upstream image pinned by
  digest, or build a recipe shipped with the integration, then add the
  `evoab-runtime` wheel in a local layer; `evoab run --container` launches the
  environment service in it (host network, host user, GPU, run directory
  mounted at the same path) and removes the container afterwards. LIBERO-PRO
  uses allenai/vla-evaluation-harness's public image plus the official
  perturbation suites (200 tasks); LIBERO-Plus builds locally from a pinned
  recipe; RoboDojo builds its pinned upstream Isaac Sim 5.1 Dockerfile
  (`--accept-license "NVIDIA Isaac Sim EULA"`) with sources and assets prepared
  on the host and mounted. Without a runtime wheel, the local layer installs the
  `evoab` package Studio runs. CALVIN uses allenai's public image (revisions
  match EvoAB's pins); RoboTwin builds its pinned revision locally with
  PyTorch 2.8 / CUDA 12.8 and CuRobo compiled for SM 8.0–12.0. Studio lists only
  benchmarks it can deploy in one step, each with one status and one button
  (部署, or 已部署 once ready; the LIBERO catalog placeholder is gone). Studio
  deployments end with `evoab setup --check-launch`, which starts one task's
  environment service once (`evoab deploy env --check`); a failure shows 部署失败,
  and runtimes from an older container recipe show 未部署. RoboDojo's container
  points Vulkan at the ICD the NVIDIA Container Toolkit mounts. Plus a
  部署条件 check (Docker, GPU containers, disk, model presets; also
  `evoab benchmarks check`), picks build download sources itself (Tsinghua +
  hf-mirror when huggingface.co is unreachable; Studio has no mirror or runtime
  wheel settings), marks container runtimes and launches runs in them. Native
  source checks accept a recorded `.evoab-revision` in images without `.git`.

- Rerunning setup for a recorded LIBERO-Plus or LIBERO-PRO installation may
  publish an empty task catalog: removing every eligible asset now disables the
  stale tasks in Studio, the task API and launch validation instead of failing
  before `runtime.json` is updated. Other installations and failed scans keep
  the previous record, and restored assets return on the next setup. LIBERO-Plus
  setup also rejects a native runtime whose `evoab` version differs and runs the
  pinned-revision and `robosuite==1.4.0` checks through that version check.

- Studio marks a registered runtime with no selectable tasks instead of showing
  it as ready, with recovery guidance for RoboDojo layouts and LIBERO assets;
  setup reports the same condition, and the CLI still accepts an explicit
  `--layout` for RoboDojo.

- CALVIN resolves the Panda URDF from its native asset directory regardless of
  the working directory used to start the service.

- The episode step budget is evaluation-side for every method: the environment
  description in system prompts drops `max_episode_steps`, model-facing state
  drops `remaining_steps` / `max_episode_steps` metadata, robot tool results and
  the code-as-policy `CodeResult` no longer report `remaining_steps`, and tool
  guidance, runtime prompts and the general skill no longer refer to it
  (`evoab/context/prompt.py`: `environment_for_model`, `state_json_for_model`).
  Per-call limits such as `move`'s `max_steps` are unchanged.

- Direct policies (`--interaction-mode direct_policy`, `evoab/direct/`): the
  model emits action chunks (`emit_eef_actions`, absolute or delta EEF) that the
  benchmark controller executes one row per control step, with per-benchmark
  prompt packs, environment-derived timing/gripper/camera geometry blocks,
  complete validation and bounded retries on the same observation. Exhausted
  retries end the run with an error and consume zero control steps. Historical
  runs retain any measured-pose holds they actually executed. Records in
  `direct/` (policy, prompt, per-step frames, per-inference calls and requests).
  This replaces "Direct" meaning tool calls to `move`. The episode's step
  budget and remaining steps are never shown to the model.

- Built-in agent `--agent native` (`pip install 'evoab[agent]'`): model presets,
  providers and usage records in `~/.config/evoab/agent/`; tool calling with only
  the robot tools and transcript consolidation (run-local storage). Both Studio
  methods (Direct Policy, Agentic Policy) use it. `evoab models` manages presets
  and providers; `evoab usage` reports recorded calls. The earlier provider store
  (`evoab providers`, `~/.config/evoab/providers.json`) and model probing are
  removed.

- Custom benchmarks (`--integration custom`, `docs/custom-benchmarks.md`): a
  Gym-style `make(task_id, seed)` plus a TOML manifest mapping observations,
  actions and success; registered with `evoab benchmarks add` or in Studio, and
  checked in the benchmark's own interpreter. Example: `examples/benchmarks/`.

- Studio: settings are 概览 (usage), 模型 (presets and providers) and 评测集
  (benchmarks, runtime wheel, custom benchmarks); the separate 评测集 page,
  provider list, model probing, usage and general pages are gone. Direct runs
  open in a step-by-step player: synchronized cameras, a control-step track
  segmented by inference (optionally including real inference waits), play /
  replay call / previous-next call / speed, CALL·STEP·ACTION telemetry, the
  action chunk with measured tracking error, model note, repair / clamp / hold
  alerts and per-inference requests and replies. The single-column Chinese
  review shows the task and playback first, with expandable configuration,
  ending details, trajectories and inference history. Task instructions, model notes and prompts are
  displayed as recorded. Each launch picks a free environment port.

- Studio's operations move to `evoab/workbench/` and are exposed as
  `evoab runs | providers | benchmarks | usage | settings | studio` commands
  with `--json`; Studio's HTTP routes call the same methods. `evoab studio`
  replaces `python -m evoab.ui.studio` (which still works).

- Codex robot agents no longer receive `AGENTS.md` project docs or Codex's
  bundled skills (`project_doc_max_bytes=0`, bundled skills and skill
  instructions disabled), and start in the run's `scratch` directory instead of
  the launcher's working directory. Earlier runs launched from a checkout carried
  that checkout's developer instructions in the agent's context.

- Studio run review rebuilt around a reconstructed trace (`/api/trace/<run>`):
  fixed harness stages, one round per model request (trigger, new input, full
  context on demand, latency, retries, tokens, output) and every tool execution
  with arguments, timing, environment effect, result and the round that used it.
  Handles Codex code mode (`exec` scripts calling robot tools) and context
  compaction, and states why each run ended. Cameras play side by side over one
  shared time axis. The old embedded replay view is removed.

- Removed the RoboUse integration, CLI and Studio launch entries, native workers and setup recipes. LIBERO-PRO and LIBERO-Plus use their own Panda controller.

- Rename the package, CLI and environment variables to `evoab`, `evoab` and
  `EVOAB_*`; the runtime distribution is `evoab-runtime`.

- Share precise local-computation cancellation guidance across Clean, Direct,
  Hybrid and Code as Policy instead of limiting it to Code as Policy.

- Restore Piper-X quarantine from every unknown operation after restart and
  enforce device-scoped credentials for stop, receipt, lease and stream access.

- Move Piper-X near-bound entry targets 0.03 rad inside native limits and retain
  strict measured entry verification. Apply the margin to other boundary joints
  on the recovering arm while retaining unaffected arms. Report stationary
  feedback separately from failed hold requests and unknown operations; these
  observations never clear quarantine or replay motion.

- Add the Piper-X physical integration with its native Body runtime, pinned
  installation/calibration configuration and dual-arm whole-path control. It
  reuses the existing Direct tools and replanning executor; an explicit
  `readonly` profile exposes observation, skills and completion only. Physical
  task success remains externally assessed. See the integration README for
  migration and validation limits.

## 0.2.0 — 2026-11-11

Upgrade the CLI/planner and native environment/actor services together, then
restart those services. This release uses scalar environment protocol 4,
scalar tool schema 8 and actor HTTP protocol v3; old services are not compatible.
The main and runtime distributions must be version-matched and installed in
separate environments. `minimal` remains an alias for `clean`.

- Let environments declare tool settings through their description metadata:
  `finish_statuses` (success, failure, give_up), whole `motion_profiles`, and
  `normalized_opening_tolerance` for measured 0-1 gripper openings.
  Environments that declare nothing keep the previous tools.
- `--execution-mode` returns with the values `parallel` and `replanning`;
  the former `serial` value is not accepted. Unset, a run uses its
  integration's mode, `parallel` unless the integration declares otherwise.
  `replanning` keeps parallel scheduling and adds result reads before the next
  world-mutating call and a replan after a failed action. Results of an
  environment that declares `physical_hardware` count as stopped only when its
  receipt confirms the stop. Job controls now list each cancelled job, and a
  cancelled result carries `no_execution`, `execution_status` and `reason`.
- Add the `wholepath` motion mode (environment metadata `motion_execution`):
  `move` sends one preflighted `PathCommand` through an optional backend
  `step_path`, and gripper-only actions hold every joint for `steps` control
  periods. `stepwise` stays the default and its tools are unchanged.
- Add the ARX ONE real-robot integration (two ARX X5 arms, three RealSense D405
  cameras) with its Body device process, offline calibration and deployment
  templates. It runs in the `replanning` execution mode.
- Report recovery through one model-facing field. `StepReceipt` gains typed
  `execution_outcome`, `stop_confirmed` and `requires_operator_recovery` facts,
  and `replanning` derives the `replan` report from them; its unrecoverable
  phase is now `operator_required` (was `stop_unconfirmed`). A failed action
  that ran nothing no longer requires a new observation. ARX ONE drops its own
  `control_status` recovery state: a control lease that expires while the model
  thinks is taken again by the next command after a readiness check.

- Rename the minimal tool profile to `clean`; retain `minimal` as a CLI/Python
  configuration alias without changing tools, observations or context behavior.

- Compact clean-mode result JSON while retaining readable image/snapshot paths
  and full stored observations. Add opt-in `observation_window` history selection
  for recent observation images and calibration; action history and reasoning remain.
- Honor `reasoning_effort="none"` in the Qwen Chat adapter by disabling thinking.

- Add `capture_scene` to clean mode for observations without action steps,
  alongside readable snapshot JSON and initial robot reference resources across
  gripper integrations. Preserve existing scene fields and ordinary tool profiles.

- Clarify clean-mode gripper sequencing, action budgets, recovery and
  evidence required before submitting task completion.

- Generalize Hybrid predictions to numeric chunks and symbolic navigation, with
  source-clock camera history and coordinated joint/base/height commands. Keep
  `actor_act` and existing numeric bindings on the shared execution path.
- Add experimental BEHAVIOR R1Pro/Pi0.5, IsaacLab Arena G1/GR00T and NaVILA-Bench
  Go2/NaVILA integrations. CPU contract checks do not establish native rollouts;
  pinned runtime and GPU limits are documented in each integration guide.
- Centralize lazy actor backend registration and delegate configuration and
  preparation to each backend. OpenPI recipes reuse one inference implementation.
- Upgrade actor HTTP protocol to v3: clients and services must upgrade and restart
  together. New mobile adapters use device protocol/tool schema `devices-2`;
  existing scalar and `devices-1` environments retain their contracts.

- Add an offline `evoab doctor` command that checks the installed
  package split, required and optional dependencies, agent CLI binaries,
  provider profiles, integration imports, skills root, output-directory
  writability and environment-variable presence without connecting to services,
  probing credentials or creating directories.
- Add explicit integration tool compositions selected with `--tool-profile`.
  All eight integrations declare their supported profiles. Direct no longer
  constructs an inactive Hybrid provider; legacy `--action-profile`, debug
  visibility and foreground/background job controls retain their behavior.

- Add an optional, pinned Antigravity Python SDK backend for Qwen tool calling,
  with explicit MCP call identity, native lifecycle records, a dedicated
  GenerateContent dialect adapter and installed-package smoke validation.

- Add Direct RMBench memory-task episodes with cached four-view observations.
- Add Direct CALVIN single-task episodes with native task predicates, explicit
  reset provenance and calibrated RGB; five-task chain evaluation is not included.

- Add typed Code as Policy feedback, generated return-field documentation and
  explicit receipt summaries, preserving existing tool payloads and images.

- Add experimental OpenCode and Gemini CLI backends with Qwen protocol bridges,
  explicit tool-call identity, response delivery gates and isolated subprocess
  state in `tool_calling` interaction mode. Gemini requires a separately built
  ACP compatibility patch and manifest covering its split bundle and assets.
- Add offline configuration and package checks plus an inert Qwen image/tool
  smoke runner. OpenCode `1.19.5` and the patched Gemini CLI
  `0.69.0-nightly.20261104.g89e9d25d6` passed live image/tool smoke runs with
  `qwen3.8-flash`; the pinned Gemini source also passed its full build and
  type-check. See tested versions and limits.
- Reject incomplete Chat tool generations and emit complete Responses text events.
  Preserve Codex's Qwen thinking default while disabling it for the new adapters.
- Add a Direct RoboTwin 2.0 adapter with dual-arm TCP control, measured gripper
  state, native seed admission and calibrated cached observations.

- Add a separate Direct LIBERO-PRO entry point for pinned base and perturbation
  suites, reusing the LIBERO-family controller and native observation path.

- Add a Direct LIBERO-Plus environment adapter with official task/initial-state
  selection, native corrupted images and explicit evaluation budgets.
- Extract Hybrid into an independent tool family with one receipt-aware execution
  loop, capability-derived schemas and integration-owned semantic bindings.
- Generalize XPolicyLab wire mappings; actor v2 separates observation/action
  semantics and timing. Upgrade actor clients/services and migrate bridge configs.
- Add RoboLab DROID joint-policy binding and native nominal FK preview, plus
  RoboUse LIBERO full-pose and RoboCasa translation-only bindings. Checkpoints
  still require explicit compatibility validation.
- Upgrade scalar environment protocol to 4 and tool schema to 8. Hybrid uses
  `active_groups` and capability-specific `constraints`; retain unknown device
  receipts to prevent replay. See migration.

- Add opt-in `code_as_policy` interaction for Codex and Claude Code: run ordinary
  Python programs against the registered robot tools, with completed feedback,
  local image paths, and the existing session lifecycle.

- Add an optional XPolicyLab WebSocket bridge for explicitly validated RoboDojo
  X5 joint policies, with declared camera selection and action horizon.
- Add `--action-model-binding robodojo_x5_joint` and its SDK option to reuse
  hybrid review and correction with compatible actors; retain the Pi0.5 default.
- Record actor identity/profile in proposal archives and reviewer context;
  preserve full output chunks while bounding FK preview and execution prefixes.

- Add the `tencent.responses` provider for Tencent TokenHub, including HY4,
  preserving native Responses fields and losslessly encoding inline PNG images.
- Document provider configuration and validate Tencent support in installed SDKs.

## 0.1.1 — 2026-11-04

- Generate native CuRobo robot configs during setup in a private asset overlay;
  shared downloaded assets remain unchanged. Detect missing configs before launch.
- Accept the audited X5 URDF's byte-equivalent CRLF snapshot alongside its LF
  form, retaining exact mesh checks and checking these assets before GPU startup.

- Install a matching `evoab-runtime` in native service environments,
  excluding planner SDK and UI dependencies while preserving the main CLI/SDK.
- Align RoboDojo and RoboUse Factory FastAPI/Uvicorn constraints with Isaac Sim
  5.1; retain strict dependency checks.
- Build both distributions from one source tree and verify independent sdists,
  resource parity, native HTTP hosting, and upgrades from 0.1.0.
- Build the pinned IsaacLab fork with a documented, checksum-guarded Starlette
  metadata correction and a local version suffix, preserving native module bytes.
- Install all IsaacLab base extensions and verify their required imports before
  marking the simulator runtime ready.
- `setup --wheel` now requires the runtime wheel at the CLI version.

Known issue: on containers that do not reap zombie descendants, SDK shutdown
can wait about 35 extra seconds and report forced cleanup after the simulator
has exited. The validated runs left no live simulator or GPU allocation behind.

## 0.1.0 — 2026-11-03

Initial open-source release of evoab, a robot agent built on Codex and
Claude Code.

### Agent and robot tools

- Default RoboDojo task supplements to the simplified instruction_supplements
  catalog; retain the full task_requirements catalog as a reference. Keep its
  CLI switch and Python configuration field exclusive to RoboDojo.
- Support pure RGB RoboDojo observations with depth and camera calibration disabled;
  expose only 2D segmentation and retain robot proprioception and robot geometry.
- Wait for calibrated camera data alongside RGB when capturing RoboDojo scenes,
  including with depth disabled. Capture errors report no physical execution
  and allow subsequent actions instead of leaving the client locked.
- Guide RoboDojo planners to reposition a free arm for a clearer wrist-camera
  view, then recapture calibrated observations and use the updated extrinsics.
- Observe scenes, plan actions and execute manipulation tasks through shared
  perception, motion, skill and completion tools.
- Use Direct motion or RoboDojo Hybrid control with Pi0.5 proposals and an optional
  reviewer. Preserve execution receipts and report partial execution explicitly.
- Read shared and environment skills; write environment-local experience only
  when an external skill library and skill writing are explicitly enabled.
- Connect model providers through protocol adapters while preserving tool identity,
  call/result pairing and available model traces.

### CLI, SDK and monitoring

- Reject camera-calibration overrides outside RoboDojo environment deployment,
  including connections to an existing service, instead of silently ignoring them.
- Document episode timeout start points separately for external services and
  managed local deployment.

- Simplify the Hybrid example to use normal budgets and task supplements;
  exclude default run artifacts from Git.
- Add minimal RoboDojo, RoboLab and RoboUse episode scripts with preparation
  instructions, explicit model selection and offline configuration checks.
- Select the Codex calculation interpreter with `--agent-python`, independently
  of the agent runtime and service interpreters.
- Prepare isolated runtimes and resources with `setup`; start environment, actor
  and SAM3 services separately with `deploy`; use `run --deploy local` to run the
  agent on one task and clean up owned processes.
- Expose typed Python configuration, direct tools and queued jobs, built-in agent
  execution, events, saved results, and explicit setup/deployment APIs.
- Monitor live runs and replay result directories locally, including synchronized
  camera views, tool batches, resource queues and native outcome/step metrics.
  Multiple saved directories open a card overview; replay does not launch tasks.
- Preserve completed Codex counters and finalize model records on interruption
  before propagating cancellation to the caller.
- Keep tool success, agent completion and native task success distinct. Record
  explicit false outcomes, zero counters and storage failures without replacing
  the original execution outcome.

### Distribution

- Remove the unimplemented physical-home entry point and obsolete RoboUse test
  claims; document the worker interpreter's required installed package.
- Make source launchers prefer their own checkout over inherited Python paths.
- Supply RoboDojo observation guidance from its integration, keeping shared
  observation code independent of environment names.

- Remove experiment-specific provider bridging and obsolete diagnostic probes
  from the public distribution.
- Prepare checked release candidates and SHA-256 manifests without uploading;
  run Linux/macOS installation checks and explicitly dispatch tagged releases
  to PyPI or TestPyPI through Trusted Publishing.
- Document package-index installation for the CLI and SDK, matching-version
  runtime setup, and source/wheel installation before the first publication.

- Include official RoboDojo and RoboUse runtime recipes; LIBERO tasks use RoboUse.
  Other included adapters retain their existing connection workflows and stated
  validation limits. See integrations.
- Include SDK type information, prompts, skills and local monitoring resources,
  plus a standalone installation/configuration validator.
- Use `evoab` for the distribution/CLI and `evoab` for imports.
- Use `EVOAB_*` configuration and consistent names for actor protocols,
  RoboUse workers, tool metadata and default storage paths. Update agent and
  service runtimes together; select existing pose libraries with `--store` or
  `--hand-pose-store`. New provider records use trace format 5.
