# Changelog

## Unreleased

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
  `evoab` package Studio runs. Studio shows 一键部署（Docker） per benchmark, a
  部署条件 check (Docker, GPU containers, disk, model presets; also
  `evoab benchmarks check`), a 构建镜像源 setting (Tsinghua + hf-mirror) forwarded
  to every build, marks container runtimes and launches runs in them. Native
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
  type-check. See [tested versions and limits](docs/external-cli-backends.md).
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
  receipts to prevent replay. See [migration](docs/hybrid.md).

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
  validation limits. See [integrations](docs/integrations.md).
- Include SDK type information, prompts, skills and local monitoring resources,
  plus a standalone installation/configuration validator.
- Use `evoab` for the distribution/CLI and `evoab` for imports.
- Use `EVOAB_*` configuration and consistent names for actor protocols,
  RoboUse workers, tool metadata and default storage paths. Update agent and
  service runtimes together; select existing pose libraries with `--store` or
  `--hand-pose-store`. New provider records use trace format 5.
