# Local RUA resources

Ignored local resources for the `rua_upstream` method, which runs the original
`uni-robot-agent` package as an external evaluator: `rua/source/` (the package
checkout) and `rua/venv/` (its interpreter, marked ready with `.evoab-rua-ready`).
See [External evaluators](../docs/benchmarks.md#external-evaluators). The native `rua` method needs none of this.
