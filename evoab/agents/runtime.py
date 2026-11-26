"""Model services supplied by an agent to the shared session owner."""
from dataclasses import dataclass
from typing import Callable
from evoab.provider.contracts import ProviderAdapter
from evoab.provider.contracts import ModelBinding, ModelProtocol

@dataclass(frozen=True)
class ModelRuntime:
    trace_directory: str
    adapter: ProviderAdapter
    protocol: ModelProtocol
    binding: ModelBinding | None
    finalize_records: Callable


def add_model_arguments(parser):
    """Shared transport options, independent of installed agent SDKs."""
    from evoab.config import _nonnegative, _positive
    from evoab.provider.registry import list_provider_profiles
    options = parser.add_argument_group("Model transport and context")
    options.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default=None,
        help="Reasoning effort (default: max for Codex, none for Claude)",
    )
    options.add_argument("--provider-url")
    options.add_argument(
        "--model-preset", action="store_true",
        help="Treat --model as a Studio model preset and take its model ID, base URL, wire profile and "
             "API key from model settings (agents with an HTTP model transport)")
    options.add_argument(
        "--provider-profile",
        choices=list_provider_profiles(),
        default=None,
        help="Provider service and protocol (default: passthrough for the selected agent protocol)",
    )
    options.add_argument(
        "--provider-timeout-s",
        type=_positive,
        default=600,
        help="Main agent HTTP request timeout in seconds",
    )
    options.add_argument(
        "--provider-retries",
        type=_nonnegative,
        default=2,
        help="Additional attempts for a main agent HTTP request",
    )
    options.add_argument("--provider-retry-backoff-s", type=_nonnegative, default=5)

