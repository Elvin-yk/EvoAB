# Agentic Policy

The default EvoAB method: one agent in a persistent session observes the scene,
calls robot tools (move, grasp, release, hold, locate, capture) and decides from
their feedback. Background tool returns let reasoning overlap motion.

| | |
|---|---|
| Paradigm | Orchestrate |
| Channel | Tool calls |
| Capabilities | skills, scene, gripper, episode |
| Context | Full transcript (configurable) |

## Reproduction notes

- Reference setting for RoboDojo: the EvoAB paper's harness configuration.
- Astranomy's "Agentic" form (one absolute EEF target held 1–5 steps) is a different
  tool contract; see `direct_policy` for its Direct form.

## Results

No benchmark numbers recorded in this card yet. Add rows as
`| benchmark | protocol | model | success | tokens/episode | cost/episode | runs |`.
