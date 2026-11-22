# RUA resources and path audit

This directory is the source-checkout entry point for Uni Robot Agent (RUA)
resources. `adapter/` points to the packaged `evoab/agents/rua/` adapter. The
links to external resources are local to each checkout.

```text
rua/
├── README.md
├── .gitignore
├── link_resources.py
├── adapter -> ../evoab/agents/rua
├── source       upstream reference checkout
├── venv         existing RUA interpreter environment, when provisioned
├── cache        existing cache, when provisioned
├── cache-reuse  existing simulator cache, when provisioned
└── candidate    existing candidate source, when provisioned
```

## Local source and existing editable installs

In the WSL development checkout, `source` is a relative link to
`../.reference/rua-source`. Open `rua/source/uni_robot_agent/` to inspect the
complete upstream package. Existing `.reference/rua-source` references still
resolve to the original checkout. Keeping the source in place also preserves
any absolute path already recorded by an editable install, editor, or script.
For a new checkout that already has the legacy reference directory, create the
same link from the EvoAB root and verify both entries.

```bash
ln -sT ../.reference/rua-source rua/source
test rua/source -ef .reference/rua-source
test -f rua/source/uni_robot_agent/__init__.py
git check-ignore -v rua/source
```

If the link is already present, run the verification lines. The other local
aliases are optional and depend on which resources exist on that machine.

## Existing resources on the evaluation host

The script expects a Git checkout next to the existing resource directories.
Run it manually when the evaluation window permits. The default resource root
is the checkout's parent directory. `--resource-root` selects a different
parent when needed.

| Alias inside the checkout | Directory under the existing resource root |
| --- | --- |
| `rua/source` | `rua-source/` |
| `rua/venv` | `.venv-rua/` |
| `rua/cache` | `rua-cache/` |
| `rua/cache-reuse` | `rua-cache-reuse/` |
| `rua/candidate` | `rua-candidate-0.2.0/` |

Use an existing host Python. For the deployment layout with `.venv311` next
to the checkout, run the following from the EvoAB root.

```bash
../.venv311/bin/python rua/link_resources.py --check
../.venv311/bin/python rua/link_resources.py
git check-ignore -v rua/source rua/venv rua/cache rua/cache-reuse rua/candidate
git status --short
```

The script checks all targets and conflicting paths before creating links,
keeps existing links that resolve to the expected directory, and opens the
original RUA interpreter in isolated mode to import `uni_robot_agent`.
It also checks the interpreter through `rua/venv` after linking and reports
Git-visible untracked regular files of at least 10 MiB. It uses the Git index
and ignore rules without traversing ignored resource links. If another process
creates an alias after preflight, link creation fails for that alias. Review
the conflict and repeat the script to complete any remaining links.

The links are for browsing and operator access. Keep the running Studio's
configured interpreter and cache paths at their existing absolute locations.
Studio can continue using the resources while the aliases are created, so this
layout change needs no Studio restart or simulator setup. Keep the existing
RoboDojo manifests and the environment of the Studio process. The script does
not install packages, invoke Docker or edit configuration.

### Rollback

On WSL, check `readlink rua/source` and run `unlink rua/source` if it points to
`../.reference/rua-source`. The original checkout stays accessible under
`.reference/rua-source`.

On the evaluation host, remove only aliases reported as `Created` by the
script. Confirm each alias before unlinking it. For example, from the checkout
root on the default layout:

```bash
resource_root="$(cd .. && pwd -P)"
test -L rua/venv && test "$(readlink rua/venv)" = "$resource_root/.venv-rua" && unlink rua/venv
```

Apply the same check to any other alias you created using the mapping table.
Use `unlink` on the alias path without a trailing slash. The tracked `adapter`
link is part of the checkout. The original resources and runtime manifests
are unaffected by this rollback.

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

## Path and dependency matrix

| Reference | Existing path or setting | Use | Effect of the new layout |
| --- | --- | --- | --- |
| Developer/editor | `.reference/rua-source/` | Full reference source | Also accessible at `rua/source/`, with the original directory in place |
| Agent registry | `evoab.agents.rua:RuaAgent` | Runtime Python adapter | `rua/adapter` is a browsing link to the same package |
| Adapter `resolve_defaults()` and `evaluator_plan()` | `--rua-python`, then `EVOAB_RUA_PYTHON`, then `sys.executable` | Host RUA interpreter | The absolute interpreter path is used without dereferencing the venv's `bin/python` symlink. Relative CLI paths are anchored to the working directory |
| Evaluator startup | `Path(__file__).with_name("worker.py").resolve()` and `<python> -I -u` | Real worker script and isolated imports | `rua/source` is outside the import contract. `-I` ignores `PYTHONPATH` and user site directories. Editable installs can still reference an absolute source path through the selected venv's site-packages |
| Upstream `_run_agent()` and `CodexAgent.run()` | Gateway and MCP loopback URLs, SDK cwd `.` for this mode | Model requests and tool callbacks | Both servers run inside the isolated RUA process. The SDK's relative working directory follows the evaluator's inherited cwd |
| Upstream `context.prompt.get_prompts_dir()` and `tools.skills` | `Path(__file__).resolve()` under the imported `uni_robot_agent` package | Prompts, RoboDojo supplements and packaged skills | The chosen interpreter's installed package continues to supply these files |
| Workbench `jobs.start()` and supervisor | Studio `sys.executable`, inherited cwd and environment | Parent launches `python -m evoab`, then starts the selected RUA interpreter | Existing Studio interpreter and cwd still determine imports and relative path resolution |
| `config.resolve_settings()` | `--env-url` / `EVOAB_ENV_URL`, `--provider-url` / `EVOAB_PROVIDER_URL`, `--rua-model-preset` | Environment and model endpoints | Managed deployment injects its environment URL. The model preset and explicit provider still use their existing configuration paths |
| `catalog.config_path()` | `EVOAB_AGENT_HOME` or `~/.config/evoab/agent` | Model presets, provider configuration and usage data | Resource links do not supply or alter model credentials |
| `runtime.cache_root()` and `read_runtime()` | `--cache-dir`, `EVOAB_CACHE_DIR` or `~/.cache/evoab`, plus recipe, package version and digest | `runtime.json` lookup | Keep the active cache setting. The two cache aliases do not select a manifest |
| RoboDojo `deployment.plan()` and `container.wrap_plan()` | Manifest `python`, `root`, `data.native_root`, `data.assets_dir` and container mount declarations | Simulator image, absolute mounts, task layout and service interpreter | Keep manifest paths and original resources. `/root/miniconda3/envs/RoboDojo/bin/python` in `container.toml` is an image-internal path. RoboDojo prepends the native root and its XPolicyLab directory to `sys.path` inside the simulator |
| Deployment and RUA session | `--output-dir` or Studio `--runs-root` / `EVOAB_RUNS_ROOT` | Run directory, logs, artifact store and run-local `.codex` when using the Gateway | The supervisor resolves the run directory to an absolute path. `deployment.json` records the selected `runtime_manifest` |
| Packaging and CI | `evoab*` package discovery, explicit `MANIFEST.in` entries, `scripts/validation/release_contents.py` | Wheel, source archive and native distribution staging | The source manifest and release allowlist include only this guide, helper and ignore file from `rua/`. Release validation rejects the local links and reference checkout |

The branch audit found no checkout-relative RUA source or cache path in the
planner's startup contract. The ARX ONE body and Piper resource files also
contain `rua` names, including hardware deployment paths under `/opt` and
`/etc`. They belong to separate hardware integrations. The existing RoboDojo
shell examples and evaluator scripts call EvoAB entry points and consume their
configured paths. To identify the cache actually used on an evaluation host,
read that run's `deployment.json` for the selected manifest.
