"""Start the depth worker in a model environment without the agent's dependencies."""
import runpy
import sys
from types import ModuleType

import evoab

# These package initializers import agent modules the worker never uses.
for name in ("integrations", "integrations.arx_one"):
    module = ModuleType(f"evoab.{name}")
    module.__path__ = [f"{evoab.__path__[0]}/{name.replace('.', '/')}"]
    sys.modules[module.__name__] = module

runpy.run_module("evoab.integrations.arx_one.perception.completion.worker", run_name="__main__")
