# RUA resources and physical migration

The source checkout is the entry point for Uni Robot Agent (RUA) resources.

```text
EvoAB/
├── evoab/agents/rua/         packaged adapter and isolated evaluator entry
└── rua/
    ├── README.md
    ├── .gitignore
    ├── link_resources.py    legacy layout helper
    ├── migrate_resources.py
    ├── adapter -> ../evoab/agents/rua
    ├── source/              physical upstream source
    ├── venv/                new RUA Python environment
    ├── cache/               physical cache copy
    ├── cache-reuse/         physical cache copy
    └── candidate/           physical candidate copy
```

The five directories and the local migration journal are ignored by Git. The
EvoAB host Python (for example, `../.venv311`) stays in its existing location.
WSL development may keep `.reference/rua-source/` and the local convenience
link `rua/source -> ../.reference/rua-source`. The physical migration below is
for the evaluation checkout with all five sibling resources.

## 1. Audit and prepare an offline wheelhouse

Run these commands on the evaluation host from the repository root. Use the
evaluation account and the existing EvoAB interpreter. Pick the cache from the
most recent representative run's `deployment.json` field `runtime_manifest`.
For a record at `<cache>/robodojo/<version>-<digest>-container/runtime.json`,
set `ACTIVE_CACHE` to `<cache>`. Confirm this against the Studio service's
`EVOAB_CACHE_DIR` and the CLI's `--cache-dir` when used. If the selected record
is in the home cache, that is the cache to supply. Native deployments use
`--native` for audit and migration and omit `--container` from the episode
commands below. Studio prefers Docker when both preparations are available.

```bash
git switch feat/rua-integration
git pull --ff-only origin feat/rua-integration

HOST_PY="$PWD/../.venv311/bin/python"
ACTIVE_CACHE="/absolute/path/to/the/old/active/evoab-cache"
"$HOST_PY" rua/migrate_resources.py --audit --cache-dir "$ACTIVE_CACHE"
```

The audit reports the selected record, host bind mounts, native source and
asset paths, base interpreter, installed distribution pins, link and file
references, available disk space and detectable active users. If another
runtime record is intentionally used outside the selected cache, pass its
physical path with `--manifest /absolute/path/to/runtime.json` to both audit
and migration. Apply this only to records owned by the evaluation account
and confirm that their consumers are stopped. The audit covers the five
resource trees and the selected records. Review any Studio service files,
shell startup files, cron jobs or scripts outside that scope for old paths.

Create a wheelhouse outside the five resource directories. Preserve the
exact variants and sources, especially CUDA wheels, local builds and any
direct archive provenance identified by the audit. For packages from the
ordinary configured package index, an example is:

```bash
WHEELHOUSE="$HOME/rua-migration-wheelhouse"
mkdir -p "$WHEELHOUSE"
"$HOST_PY" rua/migrate_resources.py --print-pins > "$WHEELHOUSE/requirements.txt"
../.venv-rua/bin/python -m pip download --only-binary=:all: --no-deps \
  --dest "$WHEELHOUSE" -r "$WHEELHOUSE/requirements.txt"
```

If the old Python has no pip, use a separate pip-enabled interpreter of the
same version and platform to retrieve wheels. Retrieve proprietary, custom,
direct or CUDA wheels from their original trusted artifact sources and place
them in the wheelhouse instead of substituting same-version builds. Include
the source checkout's `[build-system].requires` wheels for the isolated
editable build. In the audited RUA 0.2.0 source this is
`setuptools>=77.0.3`. The build helper runs in isolation and does not change
the target's pinned package inventory. The tool rejects external/duplicate
system-site distributions and unrelated editable or VCS installations. An
old non-editable RUA install must match the sibling source files before the
tool replaces it with an editable one.
Reconcile those dependencies in a separate, equivalent environment before
proceeding. An inventory containing no compatible wheels is a preflight
failure. The transaction itself installs from the wheelhouse without an
index.

## 2. Copy, verify and isolate the originals

Drain only the evaluation account's affected runs and stop its Studio
launcher. Keep it stopped until the old directories have been isolated. The
tool checks this user's Linux processes and local Docker bind mounts, without
signalling processes or changing containers. The operator must account for
other users and any supervisor that can launch new jobs during the maintenance
window. Leave unrelated GPU jobs running.

```bash
"$HOST_PY" rua/migrate_resources.py --migrate --quiescent \
  --cache-dir "$ACTIVE_CACHE" --wheelhouse "$WHEELHOUSE"
"$HOST_PY" rua/migrate_resources.py --verify
"$HOST_PY" rua/migrate_resources.py --isolate --quiescent
"$HOST_PY" rua/migrate_resources.py --verify
```

The tool requires temporary capacity for both copies. It uses resumable
rsync staging with checksum comparison, including for cross-filesystem copies.
It refuses mount points inside resource trees and pre-existing destination
directories unless they belong to its journal. Links into the five managed
resources are rebased to the new paths. A broken internal link or a
remaining link to an old resource blocks verification.

An external link outside the selected runtime dependencies can be copied
while its target is missing. Audit, copy and verification report
`Warning: preserving dangling external symlink` with the link and its target.
Absolute targets are retained. Relative links are adjusted when needed to
keep reaching the same external location after the copy. A historical
training dataset behind an unmounted filesystem stays unavailable until
its original target returns. Rollback restores the original link text along
with the original resource trees.

The selected runtime manifest, native root, prepared Assets and asset
links, runtime interpreter and Docker mounts are still checked. The
isolated Worker must also be able to start and import its dependencies.
Missing resources on these paths block migration or verification.

Known deployment records and generated RoboDojo robot
configuration paths are rebased. Unknown live references are reported with
filenames for manual review while the originals remain available. Native
simulator environments that are embedded in a cache, and contain path-bound
executables or extension metadata, need dedicated relocation or a stable
external runtime. Moving such an environment by renaming its venv is unsafe;
the migrator stops on unresolved references. The Docker image and its
container-internal interpreter stay unchanged.

The new RUA venv is built at its final path using the original base Python,
with the exact installed package versions and an editable install from
`rua/source`. It checks the Python prefix and versions, installed editable
metadata, dependency consistency, executable shebangs, extension references,
and runs the actual isolated EvoAB Worker import and parser check.

A runtime record inside the copied trees is updated in the copy. When a
selected external record references a moved resource, the tool saves its
original bytes and atomically updates that record. Unrelated cache entries,
images and shared assets remain in their original locations. The manifest
and Studio catalog are checked with EvoAB's real loaders. The Docker check
also verifies the local image and same-path mounts. No simulation is started
by any of the migration commands.

Isolation renames the five originals to a private sibling backup directory,
then reruns CPU verification with the old paths absent. It creates no
compatibility links at the old paths. Each operation is journaled in
`rua/.migration/state.json`. Repeat the same interrupted phase to resume.
If a prepared record cannot be safely migrated, resolve the reported
dependency or roll back before restarting the previous environment.

## 3. Run the acceptance episode and restart Studio

Use both exports printed by `--verify`. The following assumes the original
active cache was `../rua-cache-reuse`; use the printed cache path when the
original active cache was different. Launch Studio from this shell or update
its service configuration with these values before restarting it. Preserve
its other settings and runs root.

```bash
export EVOAB_RUA_PYTHON="$PWD/rua/venv/bin/python"
export EVOAB_CACHE_DIR="$PWD/rua/cache-reuse"

"$HOST_PY" -m evoab benchmarks tasks robodojo
RUA_TASK="stack_bowls"  # Use an available task from the catalog.
PRESET="deepseek-flash"
run_args=(run --integration robodojo --agent rua --rua-model-preset
  --model "$PRESET" --deploy local --container --task "$RUA_TASK"
  --tool-profile direct --no-sam3 --no-depth
  --output-dir "$PWD/runs/rua-migration-acceptance")

"$HOST_PY" -m evoab "${run_args[@]}" --validate-config
"$HOST_PY" -m evoab "${run_args[@]}"
```

An API-key preset such as `deepseek-flash` requires its existing local model
configuration. A Codex OAuth preset remains incompatible with the RUA HTTP
adapter. Check the live run's `deployment.json`, `evaluator.log`,
`supervisor.json` and `result.json`. The terminal's `Run artifacts` path
identifies the exact run. Acceptance requires the selected manifest, RUA
attachment, evaluator exit zero, and the official `task_success: true`
and `accepted: true`. GPU acceptance is separate from all CPU checks. In
Studio, confirm **Agentic Policy → RUA** and its preset list using the new
settings.

## 4. Finalize or roll back

When the acceptance episode has finished, keep the affected jobs and
Studio stopped while finalizing. Pass the exact post-isolation run directory.

```bash
"$HOST_PY" rua/migrate_resources.py --finalize --quiescent --run-dir "/path/from/Run artifacts"
git status --short
```

Finalize records the evidence and keeps the dated transaction in
`rua/.migration/` and the original resources in the printed
`.rua-backup-<id>` sibling directory. At the old locations there should be
neither resource directories nor aliases. Retain the backup until retention
and recovery requirements are met. To reclaim disk space later, archive the
backup and remove only that specific, independently verified snapshot. The
tool never deletes original data.

To roll back, stop the affected Studio/jobs and run:

```bash
"$HOST_PY" rua/migrate_resources.py --rollback --quiescent
```

Rollback restores the five originals and any prior aliases and byte-for-byte
external manifest backups. It leaves new resource copies under the private
journal. Reset the CLI or Studio's interpreter and cache settings to the
previous values before restarting. Rollback is available after finalize as
long as the transaction journal and quarantined originals remain available.

## Verified call path

1. **Studio and Workbench.** `launchRun()` in
   [`studio.js`](../evoab/ui/studio/static/studio.js) posts `/api/runs` with
   integration, profile, task, preset, reasoning effort, `method: agentic`,
   `agent: rua`, and optional turn limit. The route in
   [`server.py`](../evoab/ui/studio/server.py) calls `Workbench.launch()` in
   [`service.py`](../evoab/workbench/service.py). This constructs `LaunchRequest`.
   [`launcher.py`](../evoab/workbench/launcher.py) resolves and validates the
   selection and maps the `rua` method alias to `agentic` plus `rua`.
   `agent_launch()` in [`registry.py`](../evoab/agents/registry.py) loads
   `evoab.agents.rua:RuaAgent`, whose `AgentLaunch` descriptor declares RoboDojo,
   preset checking and allowed options. The Workbench CLI reaches the same
   service path for both method spellings.
2. **EvoAB subprocess and provider config.** `launcher._build_command()` starts
   `sys.executable -m evoab run --deploy local --agent rua --rua-model-preset`
   with the selected task and the prepared runtime's container flag when
   applicable. `jobs.start()` passes Studio's environment and inherits its
   working directory. `evoab.cli.main()` resolves settings and calls
   `RuaAgent.validate_config()`. Preset checking uses
   [`resolve_http_preset()`](../evoab/provider/http_presets.py), which resolves
   the existing catalog entry and checks API and authentication compatibility.
   `--validate-config` and `--print-config` exit here before deployment.
3. **Environment setup and handoff.** `run_evaluation()` in
   [`manager.py`](../evoab/services/deployment/manager.py) allocates the run
   directory. With managed deployment, `Deployment.start()` reads the runtime
   manifest, verifies the simulator and builds the RoboDojo launch plan. A
   container plan uses the configured image and host network with same-path
   mounts. The supervisor waits for `/info` to match the task, episode ID and
   layout hash and assigns the prepared URL. With `--env-url`, the worker
   uses that already prepared endpoint. The supervisor owns cleanup for the
   processes it starts.
4. **RUA evaluator.** `RuaAgent.evaluator_plan()` supplies `EvaluatorPlan` with
   the interpreter command and a stdin JSON payload. The payload includes the
   prepared episode URL, expected identity, task, run directory and resolved
   planner settings. The supervisor starts it as `OwnedProcess` and redirects
   output to `evaluator.log`. [`worker.py`](../evoab/agents/rua/worker.py) imports
   the installed `uni_robot_agent`, resolves its CLI configuration, checks the
   active environment handshake (protocol 4 and tool schema 8), and calls
   upstream `session.run()`. This assembles `PreparedEpisodeSession`, the
   selected robot tool profile and its executor. The worker marks the output
   `result.json` with the RUA method and selected preset where applicable.
5. **Model requests.** In upstream `session._run_agent()`, an explicit
   provider URL causes the worker to start its loopback `ProviderGateway`.
   `CodexAgent.run()` configures the Codex SDK to send Responses requests to
   that Gateway. The Gateway applies the chosen wire profile and binds the
   response to the SDK's tool-call IDs. The worker's transport guard filters
   credential echoes before archive and delivery. Codex uses its native model
   path when no provider URL is configured. The Gateway and MCP servers are
   threads in the RUA evaluator process. The SDK starts its own Codex binary.
6. **Robot tools and results.** The Gateway transports model requests and
   replies. In a separate leg, Codex calls the worker's `HttpMcpServer`, whose
   `tools/call` dispatch reaches the RUA executor and the selected tools. For
   `move`, the gripper provider and `MotionExecutor` produce typed primitive
   commands. RUA's `EnvironmentClient` sends requests such as `/captures`,
   `/steps` and `/finish` to the same EvoAB environment. The
   [environment service](../evoab/services/environment/service.py) checks
   request and state identity, dispatches to the RoboDojo backend, and returns
   feedback, receipts and the official task outcome. RUA returns tool results
   through MCP, and the supervisor closes the evaluator and any owned
   environment. Model/tool completion and official task success are distinct.

## Path and dependency audit

| Original → new | Reference | Migration and risk | Verification |
| --- | --- | --- | --- |
| `../rua-source` → `rua/source` | RUA upstream, editable finder/`.pth`, `.egg-link` and `direct_url.json` | Checksum-copy and install editable at the new path. A candidate installed in place of the expected source needs review | Isolated Worker reports its imported source, version and Codex SDK |
| `../.venv-rua` → `rua/venv` | Studio startup environment, `--rua-python`, `pyvenv.cfg`, shebangs, installed packages, compiled extensions | Rebuild with the original base Python, pinned dependencies and offline wheels. Override precedence is CLI, environment, verified checkout venv, then host Python | Compare the inventory, check dependency consistency, metadata, legacy paths and Worker/CLI |
| `../rua-cache` and `../rua-cache-reuse` → `rua/cache` and `rua/cache-reuse` | `--cache-dir`, `EVOAB_CACHE_DIR`, internal symlinks, runtime records | Checksum-copy physical data. Rebase known references without changing which cache is selected | Verify symlinks, selected manifest, same-path mounts and frozen task layout |
| `../rua-candidate-0.2.0` → `rua/candidate` | Candidate source and related cache links | Checksum-copy and adjust references inside managed resource trees | Verify reachable links and integrity before switching |
| Selected `runtime.json` → corresponding path reached by the new cache setting | Supervisor `runtime_for()`, Workbench `prepared_runtimes()`, and historical `deployment.json` | Rebase the copied record or atomically update the explicitly selected external record with backup. Historical artifacts remain as evidence | Use actual EvoAB loaders and check the record path in the acceptance run |
| RoboDojo `native_root`, asset source, generated asset overlay and XDG paths | Manifest data/environment and Docker mount list encoded in `python` | Rebase affected host paths only. A mount with a stale path loses access inside the container. External assets unrelated to the five directories stay in place | Check mount metadata, host files, `native_root/Assets` and representative launch plan |
| Container-internal `/root/miniconda3/envs/RoboDojo/bin/python` → same path | RoboDojo container recipe and `docker://…#…` | Retain the image and interpreter. Rebase only the host mounts encoded after it | Check local image and validate live episode separately |
| `../.venv311` → same path | EvoAB CLI, Studio and Workbench `sys.executable` | Preserve the host environment; Studio jobs inherit its environment and working directory | Launch the actual EvoAB CLI with the migrated evaluator |
| Adapter `evoab/agents/rua/worker.py` → same path | Adapter `Path(__file__)`, worker `-I` | Preserve the separate interpreter. `-I` ignores `PYTHONPATH` and the user site; editable metadata and venv site-packages determine the import | Launch the existing Worker with its CPU check |
| Gateway and MCP loopback, model preset files and run workdir → same locations | Worker threads, Codex SDK binary, Provider catalog, current working directory | No provider or authentication changes. Relative settings still follow the launch working directory | CLI settings validation or separate live model/tool test |
