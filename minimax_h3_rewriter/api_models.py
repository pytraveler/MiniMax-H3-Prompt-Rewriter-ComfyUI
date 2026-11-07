"""Allowlisted model discovery. No I/O until an explicit refresh is requested.

Only a configured connection can be queried. Remote metadata can narrow admin
capabilities, never grant new ones. Unknown IDs and provider-specific router
fields (including local paths/process arguments) never reach the browser.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, replace

from .api_client import ApiError, _exchange, credential
from .api_contract import ContractError, model_id, positive_int
from .api_profiles import ApiProfile

TTL = 60.0
COOLDOWN = 3.0
MAX_CACHED = 128
_CACHE = OrderedDict()
_ATTEMPTS = OrderedDict()
_BUSY = set()


@dataclass(frozen=True)
class ModelInfo:
    id: str
    modalities: frozenset[str] | None = None
    context_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ModelList:
    entries: tuple[ModelInfo, ...]
    at: float


def _bounded_put(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > MAX_CACHED:
        cache.popitem(last=False)


def cached(profile):
    value = _CACHE.get(profile)
    return value if value is not None and time.monotonic() - value.at < TTL else None


def _parse(raw, profile):
    data = raw.get("data")
    if not isinstance(data, list) or len(data) > 1024:
        raise ApiError("protocol")
    seen, result = set(), []
    allowed = set(profile.allowed_models)
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ApiError("protocol")
        name = entry["id"]
        if name not in allowed or not model_id(name):
            continue
        if name in seen:
            raise ApiError("protocol")
        seen.add(name)
        modalities = entry.get("input_modalities")
        if modalities is not None:
            if (not isinstance(modalities, list) or any(not isinstance(one, str) for one in modalities)
                    or "text" not in modalities):
                raise ApiError("protocol")
            modalities = frozenset(modalities) & {"text", "image", "audio", "video"}
        limits = [entry.get(key) for key in ("context_window", "max_output_tokens")]
        if any(one is not None and not positive_int(one) for one in limits):
            raise ApiError("protocol")
        result.append(ModelInfo(name, modalities, *limits))
    return ModelList(tuple(result), time.monotonic())


async def refresh_models(profile: ApiProfile, *, authorize=None):
    """Bounded read only GET /models, no inference or model-management endpoints."""
    if not isinstance(profile, ApiProfile):
        raise ContractError("Model discovery requires a configured API profile.")
    last = _ATTEMPTS.get(profile)
    if profile in _BUSY or last is not None and time.monotonic() - last < COOLDOWN:
        raise ContractError("Model refresh is already running or was just requested. Wait a few seconds.")
    _bounded_put(_ATTEMPTS, profile, time.monotonic())
    _BUSY.add(profile)
    try:
        token = credential(profile, authorize)
        raw = await _exchange(profile, None, token, (3.0, 10.0), 10.0, models=True)
        if token and token in json.dumps(raw, ensure_ascii=True):
            raise ApiError("credential_echo")
        value = _parse(raw, profile)
        _bounded_put(_CACHE, profile, value)
        return public_models(profile)
    finally:
        _BUSY.discard(profile)


def effective_profile(profile, selected=""):
    """Snapshot settings once per client; repair uses that same selection."""
    effective = profile.select_model(selected)
    listing = cached(profile)
    if listing is None:
        return effective
    entry = next((one for one in listing.entries if one.id == effective.model), None)
    if entry is None:
        raise ContractError("The selected model was not returned by this server's model list. Refresh models or select an available allowed model; no fallback was used.")
    caps, limits = effective.capabilities, effective.limits
    if entry.modalities is not None:
        caps = replace(caps, modalities=caps.modalities & entry.modalities)
    contexts = [one for one in (limits.context_tokens, entry.context_tokens) if one is not None]
    context = min(contexts) if contexts else None
    output = min(one for one in (limits.output_tokens, entry.output_tokens, context) if one is not None)
    return replace(effective, capabilities=caps, limits=replace(limits, context_tokens=context, output_tokens=output))


async def probe_profile(profile: ApiProfile, *, health=False, authorize=None):
    """Explicit local CLI diagnosis; no inference, profile edits or process control."""
    if not isinstance(profile, ApiProfile) or type(health) is not bool:
        raise ContractError("An API probe requires a configured profile and a boolean health option.")
    resolve = authorize
    if health:
        token = credential(profile, authorize)
        await _exchange(profile, None, token, (3.0, 3.0), 3.0, health=True)
        resolve = lambda _profile: token
    result = await refresh_models(profile, authorize=resolve)
    return dict(result, health="ok" if health else "not_requested")


async def test_connection(profile: ApiProfile, *, selected="", authorize=None):
    """Fresh read-only draft probe. Never populate inference/discovery caches."""
    if not isinstance(profile, ApiProfile):
        raise ContractError("A connection test requires a validated API profile.")
    model = profile.select_model(selected).model
    started = time.monotonic()
    token = credential(profile, authorize)
    raw = await _exchange(profile, None, token, (3.0, 10.0), 10.0, models=True)
    if token and token in json.dumps(raw, ensure_ascii=True):
        raise ApiError("credential_echo")
    listing = _parse(raw, profile)
    available = [one.id for one in listing.entries]
    return {"ok": True, "model": model, "model_available": model in available,
            "models": available, "elapsed_seconds": round(time.monotonic() - started, 3),
            "credential": "sent" if token else "none"}


def public_models(profile):
    listing = cached(profile)
    present = {one.id for one in listing.entries} if listing is not None else None
    models = []
    for name in profile.allowed_models:
        if not model_id(name):
            continue
        available = None if present is None else name in present
        effective = effective_profile(profile, name) if available is not False else profile.select_model(name)
        models.append({"id": name, "available": available, "modalities": sorted(effective.capabilities.modalities),
                       "formats": sorted(effective.capabilities.formats), "parameters": sorted(effective.capabilities.parameters),
                       "output_tokens": effective.limits.output_tokens, "context_tokens": effective.limits.context_tokens})
    return {"profile_id": profile.id, "default_model": profile.model if model_id(profile.model) else "",
            "models": models, "source": "server" if listing is not None else "configuration",
            "cache_seconds": TTL,
            "note": "Only locally allowed model IDs are shown. Refresh queries this profile's /models; support not declared locally is never inferred."}
