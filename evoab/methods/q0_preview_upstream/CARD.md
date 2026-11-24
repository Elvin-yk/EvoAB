# Q0-Preview (upstream reference)

Qoo Tech's Q0-Preview uses Claude Opus 5.5, a Python tool sandbox and a GPU
CuRobo controller on the simulated dual-arm RoboDojo ARX X5. The upstream
Agent handles planning and visual checkpoint review. EvoAB owns one episode,
passes three calibrated RGB images and robot state to the upstream policy,
and executes one complete dual-arm joint command per acknowledged 25 Hz step.

[Reference implementation and Apache-2.0 license](https://github.com/XPolicyLab/XPolicyLab/tree/4a9e3afd73b86a48c5db076f5cf9dbfbef1a180e/policy/Q0_Preview)
· [Upstream installation](https://github.com/XPolicyLab/XPolicyLab/blob/4a9e3afd73b86a48c5db076f5cf9dbfbef1a180e/policy/Q0_Preview/INSTALLATION.md)

## Protocol and evidence

The 2026-11-24 RoboDojo simulation Agent leaderboard lists the upstream
submission with an asterisk, score 73.20 and success rate 67.34% averaged
over the four non-Open dimensions. The reference harness includes additional
per-task evaluation conditions in the planning context and makes the task
reference file accessible in its sandbox. This recipe preserves that historical
behavior and records the protocol as `upstream_reference`. EvoAB obtains the
official episode verdict from the environment after the last control step.

[Agent leaderboard](https://robodojo-benchmark.com/leaderboard?bench=sim)
· [Evaluation protocol](https://robodojo-benchmark.com/leaderboard/protocol?lang=en)

EvoAB uses calibrated snapshot render products, converts the principal point
to RoboDojo's pixel convention and optical axes to USD camera axes, recovers
native flange poses from EvoAB grasp-site poses, and supplies the applied
gripper command. Images, renderer settings, the benchmark revision, layout
and API model version can affect comparability.
Measure these on the target installation before comparing numbers.

| Benchmark | Protocol | Model | EvoAB task success | Tokens/episode | Cost/episode | Scored runs |
|---|---|---|---|---|---|---|
| RoboDojo Sim | Upstream reference | Claude Opus 5.5 | — | — | — | 0 |
| RoboDojo Sim, `stack_bowls` | Q0 with a validation-only Codex CLI transport | GPT-6.2 Astra | 1/1 | 728,010 | — | 1 |

The live RTX trial used frozen layout 0 and seed 0. RoboDojo terminated the
episode successfully at step 483 of 800 with a native score of 1.0. All 483
dual-arm actions had distinct completed receipts. The upstream agent ran two
Python tool calls, submitted eleven trajectories and requested six visual reviews.
A loopback adapter passed Q0's conversation, images and tool schemas to
Codex CLI 0.167.0 and returned its tool selections to Q0. The CLI used
`gpt-6.2-astra` at `high` for planning and `low` for review. The token count
sums CLI-reported input and output across the 19 completed model calls.

An offline CLI integration trial used the pinned Q0 service, Docker Python
sandbox and a local Messages response fixture. It exercised two observed
control steps and native-result handling on an HTTP simulation fixture.
Separate checks projected a known 3D point through all three cameras at two
fixture poses using the upstream helper. The pinned CuRobo planner produced
a 21-waypoint joint-space path from the default X5 posture on an A100.

On an RTX 4090 D, the upstream two-group smoke test on `stack_bowls`
(seed 0, frozen layout 0) completed four motion groups and exercised both
checkpoint decisions. EvoAB recorded 800 unique completed steps through the
native episode limit. A same-state comparison at steps 0 and 6 found identical
joint positions and applied gripper commands on both arms. The three 640×480
camera matrices matched RoboDojo's native getters within 0.000002 pixels for
intrinsics and 0.00000006 per matrix entry for extrinsics. The largest
mean absolute RGB difference across the six image pairs was 0.42 on a 0–255 scale.

## Running

Prepare the Linux Python 3.11 GPU policy environment, Agent Python prefix
and tool sandbox following the upstream installation guide. Place the pinned
XPolicyLab checkout in a policy workspace beside the official `env_cfg/`.
Install the `xpolicylab` and `robodojo` extras in the EvoAB harness.
The simulation may run on a separate prepared environment service.

```bash
export EVOAB_Q0_ROOT=/path/to/benchmark-workspace/XPolicyLab
export EVOAB_Q0_PYTHON=/path/to/q0-policy/bin/python
export Q0_PREVIEW_AGENT_PREFIX=/path/to/q0-agent
export Q0_PREVIEW_API_BASE_URL=https://api.anthropic.com
# Supply Q0_PREVIEW_API_KEY through the process environment.
evoab run --integration robodojo --recipe q0_preview_upstream \
  --task stack_bowls --env-url "$EPISODE_URL" --run-id "$EPISODE_ID" \
  --q0-policy-gpu 0 --no-sam3
```

For local simulation, use `--deploy local --no-depth` with a prepared
RoboDojo runtime and frozen layout. For the upstream GPT example, select its
Responses configuration and model together.

```bash
export Q0_PREVIEW_API_BASE_URL=https://api.openai.com/v1
export Q0_PREVIEW_AGENT_CONFIG="$EVOAB_Q0_ROOT/policy/Q0_Preview/config/agent.gpt6astra.example.json"
evoab run --integration robodojo --recipe q0_preview_upstream \
  --task stack_bowls --env-url "$EPISODE_URL" --run-id "$EPISODE_ID" \
  --set roles.main.brain.model=gpt-6-astra --no-sam3
```

`--q0-agent-config` also accepts the upstream agent JSON directly. Its
`api_key_env` names the credential environment variable. Studio uses the
configuration selected on its host and requires a preset matching its model
and wire protocol. Q0 owns the planning loop, including its Python sandbox,
and requests `xhigh` for planning and `low` for checkpoint review.
Every run launches its own policy service and archives
its sessions under `q0/`. The policy receives fresh observations and an empty
trial-end notification. The run directory contains the recipe, action receipts,
native outcome on completed episodes and final evaluation status.
