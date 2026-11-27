"""A named JSON-over-HTTP tool with an input and output contract."""
import os

import httpx
from jsonschema import Draft202012Validator

from evoab.tools.toolkit import ToolOutput, ToolProvider, ToolSpec, bind_tools


class HttpTools(ToolProvider):
    def __init__(self, name, api, client):
        self.name, self.api, self.client = name, api, client

    def call(self, **values):
        headers = {}
        if self.api.bearer_env:
            token = os.environ.get(self.api.bearer_env)
            if not token:
                return ToolOutput(success=False, error=f"Missing credential environment variable {self.api.bearer_env}")
            headers["Authorization"] = f"Bearer {token}"
        try:
            payload = {"params": values} if self.api.method == "GET" else {"json": values}
            response = self.client.request(self.api.method, self.api.url, headers=headers, **payload)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return ToolOutput(success=False, error=f"{self.name}: {type(error).__name__}")
        problem = next(Draft202012Validator(self.api.output_schema).iter_errors(value), None)
        if problem:
            return ToolOutput(success=False, error=f"{self.name}: response violates {problem.validator} at {problem.json_path}")
        return ToolOutput(extra={"value": value})

    def get_tools(self):
        spec = ToolSpec(self.name, self.api.description or self.name, self.api.input_schema)
        return bind_tools([spec], provider_id=self.name, handlers={self.name: self.call})
