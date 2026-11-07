"""Validated server-side profiles. Loading this module performs no I/O.

The document is non-secret administrative configuration. Workflow inputs must
use ProfileSet.resolve(), not construct profiles from graph-supplied settings.
Secret references are inert until the server-side credential resolver is called.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field, replace
from urllib.parse import urlsplit, urlunsplit

from .api_contract import ContractError, ModelCapabilities, VERSION, identifier, model_id, object_fields, positive_int


@dataclass(frozen=True)
class SecretRef:
    kind: str = "none"
    name: str = ""

    def __post_init__(self):
        if self.kind not in ("none", "vault", "environment") or not isinstance(self.name, str):
            raise ContractError("Invalid credential reference.")
        if self.kind == "none" and self.name:
            raise ContractError("An anonymous profile cannot name a credential.")
        if self.kind == "vault" and not identifier(self.name):
            raise ContractError("Invalid vault record ID.")
        if self.kind == "environment" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.name):
            raise ContractError("Invalid credential environment variable name.")
        if self.kind == "environment" and self.name.upper() in ("MINIMAX_H3_VAULT_KEY", "MINIMAX_H3_API_ADMIN_TOKEN"):
            raise ContractError("Server administration secrets cannot be used as model API credentials.")


@dataclass(frozen=True)
class ProfileLimits:
    """Conservative request ceilings; context tokens are informational until tokenization."""

    output_tokens: int = 8192
    request_bytes: int = 1 << 20
    response_bytes: int = 8 << 20
    references: int = 12
    context_tokens: int | None = None

    def __post_init__(self):
        if any(not positive_int(getattr(self, name)) for name in
               ("output_tokens", "request_bytes", "response_bytes", "references")):
            raise ContractError("Invalid profile limits.")
        if self.context_tokens is not None and not positive_int(self.context_tokens):
            raise ContractError("Invalid model context limit.")
        if self.context_tokens is not None and self.output_tokens > self.context_tokens:
            raise ContractError("The output limit exceeds the model context window.")


def endpoint_url(value: str, network: str) -> str:
    """Only an administrator chooses recipients. Never resolve hosts on import.

    Loopback accepts literal addresses only, avoiding a DNS lookup for localhost.
    LAN HTTP is opt-in and limited to private literal IPs. Cloud requires HTTPS;
    an admin-managed DNS name remains subject to that administrator's DNS/proxy.
    """
    if not isinstance(value, str) or not value or network not in ("loopback", "lan", "cloud"):
        raise ContractError("Invalid API endpoint or network mode.")
    if any(ord(char) <= 32 or ord(char) >= 127 for char in value) or any(char in value for char in ("\\", "%", "?", "#")):
        raise ContractError("Invalid API endpoint.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname or ""
    except ValueError:
        raise ContractError("Invalid API endpoint.") from None
    if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
        raise ContractError("The API endpoint must be HTTP(S) without embedded credentials.")
    if port is not None and not 1 <= port <= 65535:
        raise ContractError("Invalid API endpoint port.")
    if parsed.netloc.endswith(":") or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", parsed.path):
        raise ContractError("Invalid API endpoint path.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", one)
                                  for one in host.split(".")):
            raise ContractError("Invalid API endpoint host.") from None
        if "." not in host or all(char in "0123456789." for char in host) or host.endswith((".local", ".localhost", ".internal")):
            raise ContractError("Use a public DNS name or an explicit local network mode.") from None
    if network == "loopback" and (address is None or not address.is_loopback):
        raise ContractError("A loopback profile requires a literal loopback address.")
    if network == "lan" and (address is None or not address.is_private or address.is_unspecified or address.is_link_local):
        raise ContractError("A LAN profile requires an explicit private IP address.")
    if network == "cloud" and (parsed.scheme != "https" or (address is not None and not address.is_global)):
        raise ContractError("Cloud profiles require HTTPS and a public recipient.")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class ModelSettings:
    """Explicit administrator allowlist entry; never inherit another model's vision support."""

    id: str
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    limits: ProfileLimits = field(default_factory=ProfileLimits)

    def __post_init__(self):
        if not model_id(self.id) or not isinstance(self.capabilities, ModelCapabilities) or not isinstance(self.limits, ProfileLimits):
            raise ContractError("Invalid allowed model settings.")

    def to_dict(self):
        return {"id": self.id, "capabilities": self.capabilities.to_dict(), "limits": asdict(self.limits)}

    @classmethod
    def from_dict(cls, data):
        raw = object_fields(data, cls.__dataclass_fields__, ("id",))
        return cls(raw["id"], ModelCapabilities.from_dict(raw.get("capabilities", {})),
                   ProfileLimits(**object_fields(raw.get("limits", {}), ProfileLimits.__dataclass_fields__)))


@dataclass(frozen=True)
class ApiProfile:
    id: str
    name: str
    base_url: str
    model: str
    network: str
    protocol: str = "chat_completions"
    secret: SecretRef = field(default_factory=SecretRef)
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    limits: ProfileLimits = field(default_factory=ProfileLimits)
    stream: bool = True
    models: tuple[ModelSettings, ...] = ()

    def __post_init__(self):
        if not identifier(self.id) or not isinstance(self.name, str) or not self.name.strip():
            raise ContractError("Invalid profile ID or name.")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 256 or any(ord(char) < 32 for char in self.model):
            raise ContractError("Invalid model ID.")
        if self.protocol != "chat_completions":
            raise ContractError("Unsupported API protocol.")
        if type(self.stream) is not bool:
            raise ContractError("Invalid API streaming setting.")
        if not isinstance(self.secret, SecretRef) or not isinstance(self.capabilities, ModelCapabilities) or not isinstance(self.limits, ProfileLimits):
            raise ContractError("Invalid profile settings.")
        if (not isinstance(self.models, (list, tuple)) or len(self.models) > 63
                or any(not isinstance(one, ModelSettings) for one in self.models)):
            raise ContractError("Invalid allowed model list.")
        object.__setattr__(self, "models", tuple(self.models))
        if self.models and not model_id(self.model):
            raise ContractError("Multiple models require a public default model ID or server alias, not a local path.")
        if len({self.model, *(one.id for one in self.models)}) != len(self.models) + 1:
            raise ContractError("Duplicate allowed model IDs, including the profile default.")
        object.__setattr__(self, "base_url", endpoint_url(self.base_url, self.network))

    @property
    def completions_url(self) -> str:
        return self.base_url + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url + "/models"

    @property
    def health_url(self) -> str:
        """A diagnostic endpoint on the same recipient and optional proxy prefix."""
        return self.base_url.removesuffix("/v1") + "/health"

    @property
    def allowed_models(self) -> tuple[str, ...]:
        return (self.model,) + tuple(one.id for one in self.models)

    def select_model(self, selected="") -> ApiProfile:
        if selected == "" or selected == self.model:
            return self
        found = next((one for one in self.models if one.id == selected), None)
        if found is None:
            raise ContractError("The selected model is not allowed by this API profile. Configure its models locally; no fallback was used.")
        return replace(self, model=found.id, capabilities=found.capabilities, limits=found.limits, models=())

    def to_dict(self) -> dict:
        raw = dict(asdict(self), capabilities=self.capabilities.to_dict())
        raw.pop("models")
        if self.models:
            raw["models"] = [one.to_dict() for one in self.models]
        return raw

    @classmethod
    def from_dict(cls, data):
        raw = dict(object_fields(data, cls.__dataclass_fields__, ("id", "name", "base_url", "model", "network")))
        raw["secret"] = SecretRef(**object_fields(raw.get("secret", {}), SecretRef.__dataclass_fields__))
        raw["capabilities"] = ModelCapabilities.from_dict(raw.get("capabilities", {}))
        raw["limits"] = ProfileLimits(**object_fields(raw.get("limits", {}), ProfileLimits.__dataclass_fields__))
        entries = raw.get("models", [])
        if not isinstance(entries, list):
            raise ContractError("Invalid allowed model list.")
        raw["models"] = tuple(ModelSettings.from_dict(one) for one in entries)
        return cls(**raw)


@dataclass(frozen=True)
class ProfileSet:
    profiles: tuple[ApiProfile, ...]

    def __post_init__(self):
        if not isinstance(self.profiles, (list, tuple)) or any(not isinstance(one, ApiProfile) for one in self.profiles):
            raise ContractError("Invalid profile list.")
        object.__setattr__(self, "profiles", tuple(self.profiles))
        if len({one.id for one in self.profiles}) != len(self.profiles):
            raise ContractError("Duplicate profile IDs.")

    def resolve(self, profile_id: str) -> ApiProfile:
        if not identifier(profile_id):
            raise ContractError("Invalid profile ID.")
        found = next((one for one in self.profiles if one.id == profile_id), None)
        if found is None:
            raise ContractError("Unknown API profile. Configure it on the server first.")
        return found

    def to_dict(self) -> dict:
        return {"version": VERSION, "profiles": [one.to_dict() for one in self.profiles]}

    @classmethod
    def from_dict(cls, data):
        raw = object_fields(data, ("version", "profiles"), ("version", "profiles"))
        if type(raw["version"]) is not int or raw["version"] != VERSION or not isinstance(raw["profiles"], list):
            raise ContractError("Unsupported profile document.")
        return cls(tuple(ApiProfile.from_dict(one) for one in raw["profiles"]))


def local_runtime_profiles() -> tuple[ApiProfile, ...]:
    """Optional text-only templates for independently managed local servers."""
    parameters = frozenset({"max_tokens", "temperature", "seed"})
    return (
        ApiProfile("ollama-local", "Local Ollama (including WSL)", "http://127.0.0.1:11434/v1",
                   "local-model:tag", "loopback", capabilities=ModelCapabilities(parameters=parameters)),
        ApiProfile("vllm-local", "Local vLLM", "http://127.0.0.1:8000/v1",
                   "served-model", "loopback", capabilities=ModelCapabilities(parameters=parameters)),
    )


def example_profiles(*, include_local_runtimes=False) -> ProfileSet:
    """Templates only: no probing, installation, credential discovery or saving.

    Set local-model to the alias of the already running llama-server. Its actual
    modalities depend on its model/projector; this template declares text only.
    DeepSeek metadata was checked against its official docs on 2026-11-05.
    Images use the API writer's inline transport after capability validation.
    """
    if type(include_local_runtimes) is not bool:
        raise ContractError("Invalid local runtime template option.")
    defaults = (
        ApiProfile("llama-server-local", "Local llama-server", "http://127.0.0.1:8080/v1", "local-model", "loopback",
                   capabilities=ModelCapabilities(formats=frozenset({"text", "json_object", "json_schema"}),
                                                  parameters=frozenset({"max_tokens", "temperature", "seed"}))),
        ApiProfile("deepseek", "DeepSeek API", "https://api.deepseek.com", "deepseek-flash", "cloud",
                   secret=SecretRef("vault", "deepseek"),
                   capabilities=ModelCapabilities(modalities=frozenset({"text", "image"}),
                                                  formats=frozenset({"text", "json_object"})),
                   limits=ProfileLimits(context_tokens=1048576)),
    )
    return ProfileSet(defaults + (local_runtime_profiles() if include_local_runtimes else ()))
