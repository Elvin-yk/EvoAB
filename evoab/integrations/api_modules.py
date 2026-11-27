"""Adapt declared HTTP and policy endpoints to the same session-owned tool catalog."""
from functools import partial

import httpx

from evoab.composition.interfaces import HttpAPI, PolicyAPI
from evoab.execution.scheduler import ToolExecutionPolicy
from evoab.integrations.composition import ToolModule
from evoab.integrations.hybrid import PolicyToolOptions, configure_hybrid
from evoab.tools.http_api import HttpTools
from evoab.tools.selection import TOOL_GROUPS
from evoab.tools.hybrid.provider import tool_policies


def _http(session, selection, *, name, api):
    client = session.own(httpx.Client(timeout=api.timeout_s))
    return HttpTools(name, api, client)


def extend_catalog(catalog, apis):
    reserved = set(catalog) | {tool for module in catalog.values() for tool in module.tools}
    reserved.update(TOOL_GROUPS["@jobs"])
    for name, api in apis.items():
        if name in reserved or name in {"delegate", "observation", "run_program"} or name.startswith("delegate_"):
            raise ValueError(f"API name {name!r} collides with an existing module or tool")
        if isinstance(api, HttpAPI):
            catalog[name] = ToolModule(name, partial(_http, name=name, api=api),
                                       {name: ToolExecutionPolicy()}, tools=(name,))
        elif isinstance(api, PolicyAPI):
            if "hybrid" not in catalog:
                # Optional and unselected policies may coexist with other environments.
                continue
            options = PolicyToolOptions(binding=api.binding, review=api.review)
            factory = partial(configure_hybrid, endpoint=api.url, checkpoint=api.checkpoint_sha256,
                              named_options=options, tool_name=name, description=api.description, review_model=api.review_model)
            catalog[name] = ToolModule(name, factory, {name: tool_policies()["actor_act"]}, tools=(name,))
