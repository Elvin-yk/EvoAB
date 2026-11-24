# Direct Policy

The model is the controller. Each inference is an independent request (prompt
blocks, task, a sampled window of recent frames) that must return exactly one
action-chunk tool call. The chunk is validated and step-limited completely, then
executed one row per control step; malformed output is re-requested on the
unchanged observation.

| | |
|---|---|
| Paradigm | Direct |
| Channel | Action (`emit_eef_actions`, `emit_delta_eef_actions` or `emit_joint_actions`) |
| Context | Stateless; frames t−14, t−7, t by default |

## Reproduction target: Astranomy (Direct form)

Paper setting: GPT-6 Astra, medium reasoning, three cameras at 384×216, 15 Hz,
simulation paused during inference, 3–15 absolute EEF targets per call. Reported
RoboLab-120 success 66.3% at about $1.96 per episode.

Known differences: EvoAB's bundled prompt packs are rewritten, not copied; image
size is 384 px on the long side.

## Results

| benchmark | protocol | model | success | tokens/episode | cost/episode | runs |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |
