# Direct Action

The model is the controller. Each inference is an independent request (prompt
blocks, task, a sampled window of recent frames) whose only allowed answer is one
action chunk. The chunk is returned through a forced function call
(`emit_eef_actions` and similar): the function is never executed, it only gives
the answer a schema EvoAB can validate. The chunk is step-limited, then executed
one row per control step; a malformed answer is re-requested on the unchanged
observation.

| | |
|---|---|
| Paradigm | Direct |
| Channel | `action` |
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
