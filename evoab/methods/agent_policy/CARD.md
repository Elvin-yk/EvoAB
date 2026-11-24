# Agent + Policy (Hybrid)

The agent calls `actor_act` with a language instruction; the actor proposes
numeric or symbolic actions, an optional reviewer selects a bounded prefix, and
the integration binding executes it. Direct gripper motion is added where the
environment offers it.

Requires an actor service (`--action-model-url`) and a compatible binding.
