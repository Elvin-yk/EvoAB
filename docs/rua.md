# RUA on RoboDojo

In Studio, select RoboDojo, then choose **Agentic Policy → RUA**. From a shell,
use `evoab runs launch --method agentic --agent rua` or `evoab run --agent rua`.
The former `evoab runs launch --method rua` remains an alias. The adapter uses the reference
`uni-robot-agent` source package (tested against the supplied 0.2.0 checkout)
and its default Codex planner with the `direct` tool profile. The method is
available for single-episode RoboDojo runs. Existing methods need no migration.

See the [RUA resource directory](../rua/README.md) for source links, the
evaluation-host helper, and the audited call and path dependencies.

## Episode ownership

RUA's own `--deploy local` creates an environment, and its minimal RoboDojo
example sets that flag. For this method, EvoAB's deployment supervisor starts
one Docker environment, waits for `/info` and gives its URL and episode identity
to the host RUA process. Alternatively, an operator can supply an already
prepared `--env-url` and optional `--task` and `--run-id` assertions. Use one
RUA evaluator per active endpoint. The RUA process checks the integration,
environment protocol 4 and tool schema 8 before assembling its session.

RUA's model emits calls to its robot tools. Scene tools read state and capture
camera images through the environment service. Motion tools compile grasp-site
poses or joint targets and gripper commands into typed steps. The environment
receives commands with request IDs, sequence numbers and expected state
versions. RUA reconciles uncertain receipts through the existing client, and
the environment verifier determines the official task outcome. EvoAB supervises
the evaluator and removes containers it started.

## Prepare the evaluation host

Run from the EvoAB checkout on the integration branch with the adapter changes
present. Use Python 3.11 or later on the host. `RUA_PYTHON` can be the Python
executable of the existing RUA environment. To prepare a host-only RUA
interpreter from the provided checkout, run the following on the evaluation host.

```bash
cd /path/to/EvoAB
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[agent]'

python3 -m venv ../.venv-rua-evaluator
RUA_PYTHON="$PWD/../.venv-rua-evaluator/bin/python"
"$RUA_PYTHON" -m pip install -e /path/to/rua-source
```

Check the RUA install and look up the tasks in EvoAB's prepared catalog.

```bash
"$RUA_PYTHON" -I -m uni_robot_agent --version
"$RUA_PYTHON" -I -m uni_robot_agent run --integration robodojo --help
.venv/bin/evoab benchmarks tasks robodojo
```

If the EvoAB Docker runtime is already prepared, reuse its recorded image and
assets. Otherwise, set `ROBODOJO_ASSETS` to the existing absolute `Assets`
directory containing `Robots`, `Object`, `Material` and `Eval_Layout`. Accept
the NVIDIA Isaac Sim EULA before running this setup command. `--check-launch`
starts and stops a separate readiness-check episode during setup.

```bash
.venv/bin/evoab setup --integration robodojo --container \
  --accept-license 'NVIDIA Isaac Sim EULA' \
  --assets-dir "$ROBODOJO_ASSETS" --check-launch
.venv/bin/evoab benchmarks tasks robodojo
```

## Run a Docker episode

Set `RUA_MODEL` to the provider's model ID and `RUA_PROVIDER_URL` to its API
base URL. Export `EVOAB_API_KEY` in the shell if the endpoint requires it. This
example uses an OpenAI Responses endpoint. For another provider, choose its
compatible RUA Codex profile from the `uni_robot_agent run` help. Set `RUA_TASK`
to a task ID from `evoab benchmarks tasks robodojo`, for example `stack_bowls`
when its frozen layout is available.

```bash
.venv/bin/evoab run --integration robodojo --agent rua \
  --rua-python "$RUA_PYTHON" --model "$RUA_MODEL" \
  --provider-url "$RUA_PROVIDER_URL" --provider-profile passthrough.responses \
  --deploy local --container --task "$RUA_TASK" --tool-profile direct \
  --no-sam3 --no-depth --output-dir ./runs/rua --monitor --monitor-port 0
```

The command selects RGB with calibration through `--no-depth`. Add
`--validate-config` to check the command without starting a service. For
an already prepared episode, replace `--deploy local --container --no-depth`
with `--env-url "$EPISODE_URL"`. Configure depth in the existing service.

For Studio or `evoab runs launch --integration robodojo --method agentic
--agent rua --task "$RUA_TASK" --model "$PRESET"`, export `EVOAB_RUA_PYTHON="$RUA_PYTHON"` before
starting the launcher. Studio uses the selected preset's model ID, URL and
key, plus the reasoning effort selected at launch. The optional turn limit
maps to `--max-turns`. The shared HTTP preset resolver uses the provider
catalog's resolved model, request API and authentication type, and checks them
against RUA's declared wire profiles. API-key Responses presets such as
`deepseek-flash` use `passthrough.responses`. Chat Completions presets use the
existing Responses-to-Chat profile. RUA continues to own its Responses gateway,
stream handling and MCP tool delivery. A response that echoes a request
credential is rejected before the gateway can save it or deliver a tool call.

Studio displays disabled presets with their reason. The existing `openai_codex`
OAuth backend uses Nanobot's Codex login flow and the Codex service on
`chatgpt.com`. For public Responses with ChatGPT plan usage, OpenAI documents
[separate app authorization](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
and a [specific Responses contract](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
RUA cannot reuse the existing Codex credential through its HTTP adapter.
An API-key Responses preset is usable when its endpoint grants access to the
selected model. The resolver also reports catalog proxies, request overrides
and credential-bearing base URLs as incompatible with this transport.

For an explicitly configured RUA provider profile, use the single-episode CLI
above. After updating EvoAB on a
prepared host, restart Studio with its existing runs directory and RUA Python
setting. The prepared Docker runtime, assets and RUA installation can be reused.

## Verify the run

The supervisor prints `Run artifacts` with the exact run directory.

| File | Evidence |
| --- | --- |
| `deployment.json` | Environment `ready`, matching task and episode identity, container image and cleanup fields |
| `service.log` | Docker environment startup, Isaac Sim, asset and native runtime diagnostics |
| `evaluator.log` | `RUA attached to robodojo episode …` with the same identity, then planner and provider logs |
| `trajectory.jsonl`, `native_outcome.json`, `result.json` | Tool and native records, official verdict, and `agent_backend: rua` in `result.json` |
| `supervisor.json` | Final state and evaluator exit code |

A successful task has `task_success: true` and `accepted: true` in
`result.json`, and the supervisor records `episode_exit_code: 0`. The official
verdict is independent of the agent's `agent_finished` field. Exit `75` is a
retryable infrastructure outcome, `124` is the episode deadline, and `130`
is interruption. A rejected or uncertain run needs its existing receipts and
logs inspected before any new attempt.

- Before environment readiness, inspect `deployment.json` and `service.log`
  for Docker GPU access, Vulkan ICD, the pinned RoboDojo revision, asset and
  layout paths, and port availability.
- Inspect `evaluator.log` for interpreter imports, endpoint access, protocol,
  and task or episode identity mismatches. A finished endpoint needs a new
  prepared episode before another run.
- After attachment, inspect provider errors in `evaluator.log` and archived
  `codex_responses/` exchanges when saved. Check provider endpoint, model ID,
  profile, credentials and support for vision and tool calls.
- For tool or task failures, compare `trajectory.jsonl` with
  `native_outcome.json`. An acknowledged tool invocation is separate from
  environment task success.
