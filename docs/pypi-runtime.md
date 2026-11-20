# evoab runtime

Native service runtime for [evoab](https://pypi.org/project/evoab/).
For the CLI and Python SDK, install `evoab` instead.

The CLI's explicit `setup` command installs this package at the matching version
in a separate native environment, alongside simulator or model dependencies.
This distribution excludes planner SDK and terminal UI dependencies so native
runtimes can retain their own compatible versions of shared libraries.

Both packages are built from the same source and contain the same
`evoab` modules. **Never install both in the same Python environment.**
Neither package depends on the other. Simulators, assets, checkpoints and GPU
drivers are separate prerequisites.

Version 0.2.0 must be paired with the 0.2.0 CLI/planner. Upgrade and restart
environment and actor services together: scalar environment protocol 4 and actor
HTTP protocol v3 are not compatible with earlier services.

See the [documentation](https://github.com/Elvin-yk/EvoAB/tree/main/docs) for setup and usage.
