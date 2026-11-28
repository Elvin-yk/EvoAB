"""EvoAB Studio: launch simulation runs and review their outcomes and usage.

Studio is a UI-layer launcher. It starts ordinary ``evoab run`` processes,
reads the artifacts those runs write and embeds the read-only run monitor for
replay. It never controls robots itself.
"""
