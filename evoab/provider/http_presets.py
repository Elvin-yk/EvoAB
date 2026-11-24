"""Resolve catalog presets for agents that own an HTTP model transport."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from evoab.provider import catalog
from evoab.provider.contracts import PresetCompatibilityError
from evoab.provider.registry import validate_provider_profile


@dataclass(frozen=True)
class HttpPresetConnection:
    model: str
    base_url: str
    profile: str
    api_key: str = field(repr=False)


def validate_http_provider_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
    except ValueError:
        valid = False
    if not valid:
        raise PresetCompatibilityError("The model provider needs an absolute HTTP(S) base URL.")
    if parsed.username is not None or parsed.query or parsed.fragment:
        raise PresetCompatibilityError(
            "The provider URL must omit user information, query parameters and fragments. Configure credentials as an API key."
        )


def resolve_http_preset(name: str, *, client_protocol: str, profiles: tuple[str, ...]) -> HttpPresetConnection:
    """Select an explicit wire profile using the catalog's resolved route and API."""
    config = catalog.load_config()
    from nanobot.providers.routing import resolve_automatic_model_api, resolve_provider_route

    preset = (config.model_presets or {}).get(name)
    if preset is None:
        raise PresetCompatibilityError("The model preset does not exist.")
    try:
        route = resolve_provider_route(config, preset=preset)
    except ValueError:
        # Dependency diagnostics can contain untrusted configuration values.
        raise PresetCompatibilityError(
            "The provider route is invalid. Check the model, API key and request API in model settings."
        ) from None

    if route.backend == "openai_codex":
        raise PresetCompatibilityError(
            "This Codex OAuth preset uses the Codex service. Public Responses needs separate app authorization "
            "through Sign in with ChatGPT or an API-key preset."
        )
    if route.spec and route.spec.is_oauth:
        raise PresetCompatibilityError("This provider uses OAuth. The agent's HTTP adapter requires an API-key connection.")
    if route.backend != "openai_compat" or route.provider_config is None:
        raise PresetCompatibilityError(
            "This provider backend needs a dedicated adapter. The available transport uses OpenAI-compatible HTTP."
        )

    provider = route.provider_config
    if provider.use_proxy:
        raise PresetCompatibilityError("The agent's HTTP transport cannot apply the preset's catalog proxy setting.")
    if (provider.extra_headers or provider.extra_query or provider.extra_body
            or (route.spec and route.spec.default_extra_headers)):
        raise PresetCompatibilityError(
            "The agent's HTTP transport cannot apply this provider's additional request headers, query parameters or body overrides."
        )
    if not provider.api_key or any(not 33 <= ord(char) <= 126 for char in provider.api_key):
        raise PresetCompatibilityError("Configure a valid API key for this model provider.")

    try:
        api = route.model_api.preferred_api if route.model_api else resolve_automatic_model_api(config, preset=preset)[1]
        base_url = config.get_api_base(route.model, preset=preset)
    except ValueError:
        raise PresetCompatibilityError("The provider's request API or base URL could not be resolved.") from None
    if not base_url:
        raise PresetCompatibilityError("Configure an HTTP(S) base URL for this model provider.")
    validate_http_provider_url(base_url)

    for profile_name in profiles:
        profile = validate_provider_profile(profile_name, client_protocol=client_protocol)
        if profile.upstream_protocol == api:
            model = route.spec.request_model_name(route.model) if route.spec else route.model
            return HttpPresetConnection(model, base_url, profile.name, provider.api_key)
    raise PresetCompatibilityError(f"This agent has no wire profile for the preset's {api} API.")


def preset_for_protocol(name: str, client_protocol: str) -> HttpPresetConnection:
    """Resolve a Studio model preset for an agent speaking ``client_protocol`` (responses, messages)."""
    from evoab.provider.registry import list_provider_profiles
    return resolve_http_preset(name, client_protocol=client_protocol,
                               profiles=list_provider_profiles(client_protocol=client_protocol))
