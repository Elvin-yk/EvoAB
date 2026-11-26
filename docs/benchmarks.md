# Benchmarks and robots

How to prepare each environment. Running methods on a prepared environment is in
the [guide](guide.md#run-from-the-command-line); adding a new environment is in
[architecture](architecture.md#extend-evoab).

| Integration | Kind | Prepare with | Tools | Notes |
|---|---|---|---|---|
| `robodojo` | Dual-arm ARX X5, Isaac Sim 5.1 | Container or native | Motion, policy (Pi0.5) | 42 tasks, 5 capability dimensions |
| `metaworld` | MetaWorld MT50, MuJoCo | Native (`evoab setup`) | Motion | CPU only; fastest smoke test |
| `libero` | Original LIBERO | Container | Motion | Spatial, Object, Goal, Long, 90 |
| `libero_plus` | LIBERO-Plus perturbations | Container or native | Motion | |
| `libero_pro` | LIBERO-PRO suites | Container or native | Motion | `_env`, `_object`, `_swap`, `_task`, `_lan` variants |
| `robotwin` | RoboTwin 2.0 aloha-agilex | Container or native | Motion | `demo_clean`, `demo_randomized` |
| `rmbench` | Memory-dependent dual-arm | Native | Motion | |
| `calvin` | CALVIN Panda | Container or native | Motion | One task per episode |
| `robolab` | RoboLab DROID, Isaac Sim | Native service | Motion, policy | No automatic setup |
| `dexjoco` | Panda/Allegro, MuJoCo | Native + actor | Policy only | `pick_bucket` |
| `behavior` | R1Pro mobile manipulation | Adopt native | Policy only | Experimental |
| `isaaclab_arena` | G1 humanoid | Adopt native | Policy only | Experimental |
| `arx_one`, `piper_x` | Real dual-arm robots | Operator setup | Motion | See [real robots](#real-robots) |

## Preparing a runtime

The CLI never imports simulators. Each environment runs as its own service, in
its own interpreter or container, prepared once and reused.

**Container (recommended).** Studio: 设置 → 评测集 → 部署, or:

```bash
evoab benchmarks check                       # Docker, GPU containers, disk, model presets
evoab benchmarks prepare robodojo --container
evoab run --integration robodojo --deploy local --container --task stack_bowls …
```

Public upstream images are pulled by digest; others are built locally from a
pinned recipe. Nothing is hosted by EvoAB. Builds pick package and model mirrors
automatically when Hugging Face is unreachable; set `PIP_INDEX_URL`,
`HF_ENDPOINT` and similar variables to override, or
`EVOAB_CONTAINER_BUILD_PROXY=1` to forward the host proxy. An image archive made
with `docker save` can be imported from Studio instead of building. 卸载 removes
a Docker deployment.

**Native.** `evoab setup --integration X` creates an isolated runtime under
`~/.cache/evoab` (or `--cache-dir`) with the pinned source, assets and a
matching `evoab-runtime`. `--python` selects the base interpreter; RoboDojo
requires Python 3.11.

**Adopt an existing installation** without modifying it:

```bash
evoab setup --integration robodojo --runtime-python /path/to/venv/bin/python --native-root /path/to/RoboDojo
```

**Services** for `--env-url` runs, policies and perception:

```bash
evoab deploy env --integration robodojo --task stack_bowls --gpu 0   # :18760
evoab setup --service actor --profile robodojo_pi05 && evoab deploy actor --profile robodojo_pi05 --gpu 1   # :18761
evoab setup --service sam3 && evoab deploy sam3 --gpu 2              # :18762
```

Methods with the policy tool pass `--action-model-url` and
`--action-model-checkpoint-sha256`; methods that enable scene segmentation pass
`--sam3-url`. `deploy env` takes `--no-depth` and `--no-camera-calibration`
itself; `run --deploy local` takes them from the recipe's `inputs`. Each
environment service serves one prepared episode. Services bind localhost and have no internet-facing
authentication: run them on trusted machines.

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

MetaWorld MT50 (`metaworld==3.1.1`) through the shared motion tools. Prepare with
`evoab setup --integration metaworld`; tasks are named like `reach-v3`. It needs
no GPU or Docker, so it is the quickest check that a method runs end to end.

## LIBERO, LIBERO-Plus, LIBERO-PRO

Three separate runtimes; never install stock LIBERO and its forks in one interpreter.

- **LIBERO**: public image; suites Spatial, Object, Goal, Long (`libero_10`) and
  `libero_90`, with official starting states selected by `--init-state-index`.
- **LIBERO-Plus**: local container build, or adopt a native checkout of the pinned
  [LIBERO-plus](https://github.com/sylvestf/LIBERO-plus) with
  `--runtime-env MUJOCO_GL=egl`. Setup verifies the revision, unmodified files and
  `robosuite==1.4.0`. Studio groups tasks by suite and perturbation.
- **LIBERO-PRO**: public image, or the pinned
  [LIBERO-PRO](https://github.com/Zxy-MLlab/LIBERO-PRO) natively. The four base
  suites and their `_env`, `_object`, `_swap`, `_task`, `_lan` variants are
  admitted; initial states must exist for the exact task.

```bash
evoab run --integration libero_pro --deploy local --container --runtime-profile libero_object \
  --task pick_up_the_cream_cheese_and_place_it_in_the_basket --recipe direct_action --model my-model
```

Rerunning setup rescans an installation and replaces its task catalog.

## RoboTwin 2.0 and RMBench

**RoboTwin** (`aloha-agilex`, `demo_clean` / `demo_randomized`): local container
build with the pinned revision, PyTorch 2.8 / CUDA 12.8, SAPIEN 3.0.0b1, mplib
0.2.1 and CuRobo 0.7.8 compiled for SM 8.0–12.0. Natively, run
`python -m evoab.integrations.robotwin.server --native-root … --task … --seed …`
from a runtime with the official assets. The upstream expert admits each seed
first; a rejected seed is a setup exclusion, not an agent failure.

**RMBench** (memory-dependent tasks): native only, from the pinned
[RMBench](https://github.com/RoboTwin-Platform/RMBench) checkout with its own
Hugging Face assets; start `python -m evoab.integrations.rmbench.server …` and
connect with `--env-url`.

## CALVIN

Public image with the pinned `calvin` and `calvin_env` revisions; Studio runs
scene D with the native scene reset and 360 steps, one task per episode. Natively,
`python -m evoab.integrations.calvin.server --native-root … --benchmark-root …
--scene D --scene-reset --task open_drawer`.

## RoboLab

Single-DROID RoboLab 0.3.1 on Isaac Sim 5.1 / Isaac Lab 2.3.2 (motion tools and a
joint-space policy tool). There is no automatic setup: prepare RoboLab with its assets
(materialize Git LFS), install `.[robolab]` into the simulator's Python, start
`scripts/deploy/services/robolab/start_environment.sh --task … --seed …
--output-dir NEW_DIR`, and connect with `--env-url`. Each service owns one episode.

## Policy-only environments

These expose scene, skill, `actor_act` (the policy tool) and finish, but no motion
tools, and default to `agent_policy`.

- **DexJoCo**: MuJoCo Panda/Allegro, `pick_bucket` with the public DexJoCo-Pi05
  weights. `evoab setup --integration dexjoco` and
  `evoab setup --service actor --profile dexjoco_pick_bucket`.
- **BEHAVIOR** (experimental): R1Pro on BEHAVIOR-1K v3.9.4 / Isaac Sim 5.1;
  adopt a prepared installation, `turning_on_radio` with the official Pi0.5 example.
- **IsaacLab Arena** (experimental): G1 `galileo_g1_locomanip_pick_and_place` with
  a GR00T N1.6 actor (`arena_g1_gr00t`); simulator and actor use separate runtimes.

Each integration's `SOURCE.md` pins the sources, assets and weights it was
validated with.

## External evaluators

Some methods run another project's complete agent against an EvoAB episode; the
recipe cannot change their roles or tools.

- **`q0_preview_upstream`**: Qoo Tech's Q0-Preview on RoboDojo. Setup, credentials
  and validation evidence are in its
  [card](../evoab/methods/q0_preview_upstream/CARD.md).

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
