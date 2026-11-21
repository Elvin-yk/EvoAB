# Custom benchmarks

Existing installations can continue using their registered Gym environments and viewing recorded runs. Studio and the CLI no longer register or edit benchmark manifests.

## What the benchmark provides

A Python function `make(task_id, seed)` that returns an environment with:

| Method | Returns |
|---|---|
| `reset()` | `obs` or `(obs, info)` |
| `step(action)` | `(obs, reward, done, info)` or `(obs, reward, terminated, truncated, info)` |
| `close()` (optional) | — |

`obs` is a dictionary of arrays. Tasks are listed inline in the manifest
(`[[tasks]]` with `id`, `instruction`, optional `max_steps`) or by a
`module:attribute` (`tasks_from`) that is a list of such dictionaries or a
function returning one. This is the shape LIBERO, robosuite, ManiSkill, Meta-World
and most other manipulation benchmarks already expose; a thin wrapper is enough
when reset needs extra steps (see `examples/benchmarks/libero_gym.py`).

## The manifest

Existing manifests contain these sections:

- **Where it runs** — `python` (an interpreter that imports the benchmark and the
  `evoab-runtime` package), `path` (added to `sys.path`), `make`, tasks, and
  `[environment]` variables for the benchmark process.
- **`[observation]`** — which keys hold camera images (`cameras = {name = key}`,
  HxWx3 uint8 or 0–1 floats; `flip_vertical` for bottom-up renderers), the
  end-effector position and quaternion (`quat_order`), and optionally the gripper
  state with its open and closed values.
- **`[action]`** — `absolute_eef` (`[x, y, z, quaternion, gripper]`) or
  `delta_eef` (`[dx, dy, dz, rx, ry, rz, gripper]` in −1…1 with
  `position_scale` / `rotation_scale`), plus the gripper's open and closed values.
- **`[success]`** — `info_key` read from `info`, or a `method` called on the env.
- **`prompt`** — optional embodiment and frame description for Direct policies;
  timing and the action format are added from the manifest automatically.

EvoAB converts every pose target to the benchmark's action convention, serves
the episode through its standard environment service, and records frames,
state and outcome like any built-in benchmark.

## Run an existing environment

```bash
evoab benchmarks list
evoab runs launch --integration custom --profile EXISTING_PROFILE --task TASK_ID --method direct_policy --model PRESET
```
