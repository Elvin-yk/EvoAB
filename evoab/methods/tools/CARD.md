# Tools

One agent in a persistent session observes the scene, calls tools (move, grasp,
release, hold, locate, capture, a policy through `actor_act`, your `@tool`
functions) and decides from their feedback. Background tool returns let
reasoning overlap motion.

| | |
|---|---|
| Acts through | `tools` (combine with `code` and `action` in `act`) |
| Capabilities | skills, scene, gripper, episode |
| Context | Full transcript |

## Variations in one line

- RGB-only: `--set inputs.depth=false`
- With a VLA policy as a tool: add `hybrid` to the capabilities (see `examples/recipes/policy.yaml`)
- Four tools, compact results: `examples/recipes/clean.yaml`
- Tools and action chunks in one session: `--set 'roles.main.act=[tools,action]'`

## Results

No benchmark numbers recorded in this card yet. Add rows as
`| benchmark | protocol | model | success | tokens/episode | cost/episode | runs |`.
