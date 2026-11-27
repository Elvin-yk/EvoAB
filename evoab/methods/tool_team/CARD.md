# Agent team

Each Studio card sets the role, instructions and tools of one agent. The first
card receives the task. A new card initially accepts delegation from the entry
agent. Edit either the caller's delegation targets or the recipient's callers
to change the same relationship. A role can accept multiple callers and returns
each report to the caller for that invocation. All agents must be reachable from
the entry, and Studio rejects delegation cycles.

Planner cards can use skills, scene, episode and job tools. On RoboDojo, a
Planner or Actor card can select Direct and call it through `actor_act`.
Actor cards can also use the individual arm tools. The common
presets for both roles include all job controls. Applying a tool preset changes
only that card's tool selection. The component graph follows the current team.

Card requests use one-based positions in `delegate_to`. Older requests without
this field use the original entry-to-children layout. Older planner cards with
arm tools must move those tools to an actor. Previously exported recipes retain
their recorded capabilities and can still run through the CLI.
