"""MiniMax's prompt-only H3 Image endpoint."""

from __future__ import annotations

import json
import os
import re

from . import api_config
from .api_client import ApiError, _cancelled, _request, _wait_for_request
from .api_contract import ContractError
from .api_media import prepare_images_async
from .api_secrets import SecretResolver, valid_token
from .image_prompt import MODEL, read_ratio

GLOBAL = "https://api.minimax.io"
RECIPIENTS = (GLOBAL, "https://api.minimax.cn")
MAX_BODY = 64 << 20
MAX_RESPONSE = 1 << 20
TIMEOUT = 610.0


def _connection(profile_id, reference_count=0):
    if not profile_id:
        key = os.environ.get("MINIMAX_API_KEY")
        if not valid_token(key):
            raise ContractError("Set MINIMAX_API_KEY on the ComfyUI server or select a saved MiniMax profile.")
        return GLOBAL, key, MAX_BODY, MAX_RESPONSE

    profile = api_config.load_profiles().resolve(profile_id)
    base_url = profile.base_url.removesuffix("/v1")
    if (profile.network != "cloud" or base_url not in RECIPIENTS or MODEL not in profile.allowed_models
            or profile.secret.kind == "none"):
        raise ContractError("Select a MiniMax HTTPS profile with a credential and MiniMax-H3-Image in Allowed models.")
    limits = profile.select_model(MODEL).limits
    if reference_count > limits.references:
        raise ContractError("Too many pictures for the MiniMax profile. Raise the model's Reference limit in API providers or disable some pictures.")
    key = SecretResolver()(profile)
    return base_url, key, min(MAX_BODY, limits.request_bytes), min(MAX_RESPONSE, limits.response_bytes)


async def _payload(text, references, ratio, size, limit, cancelled):
    payload = {"model": MODEL, "prompt": text}
    if references:
        for one in references:
            height, width = one.value.shape[1:3]
            if min(width, height) < 128 or max(width, height) > 4 * min(width, height):
                raise ContractError(f"{one.spec.slot}: Context IR needs both image edges at least 128 px and a ratio up to 4:1.")
        copies = await prepare_images_async(references, max_side=2048, byte_limit=limit, cancelled=cancelled)
        payload["images"] = [{"image_url": image.content()["image_url"]["url"]} for image in copies]
        if size:
            payload["size"] = f"{size[0]}x{size[1]}"
        elif ratio != "auto":
            payload["aspect_ratio"] = ratio
    try:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeError:
        raise ContractError("The image instruction contains invalid Unicode text.") from None
    if len(data) > limit:
        raise ContractError("The Context IR request exceeds the allowed byte limit. Use smaller references or fewer images.")
    return data


def _read(body, ratio, key):
    try:
        result = api_config.parse_json(body)
    except (ValueError, RecursionError):
        result = None
    text = result.get("revised_prompt") if isinstance(result, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ContractError("MiniMax Context IR did not return revised_prompt.")
    request_id = result.get("request_id")
    if key in text or isinstance(request_id, str) and key in request_id:
        raise ApiError("credential_echo")
    if ratio == "auto":
        try:
            ratio = read_ratio(result.get("aspect_ratio"))
        except ContractError:
            raise ContractError("MiniMax Context IR did not return a valid aspect_ratio. Set a fixed ratio and try again.") from None
    usage = result.get("usage")
    details = []
    if isinstance(usage, dict):
        tokens = usage.get("input_tokens")
        if type(tokens) is int and tokens >= 0:
            details.append(f"Input tokens billed: {tokens}")
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
        details.append(f"Request ID: {request_id}")
    return text.strip(), ratio, details


async def expand(text, references, ratio, size, profile_id, *, cancelled=None):
    _cancelled(cancelled)
    base_url, key, request_limit, response_limit = _connection(profile_id, len(references))
    data = await _payload(text, references, ratio, size, request_limit, cancelled)
    _cancelled(cancelled)
    request = _request(f"{base_url}/v1/h3_image_context_ir", data, key, (10.0, TIMEOUT), TIMEOUT,
                       network="cloud", limit=response_limit)
    try:
        body, _stream = await _wait_for_request(request, cancelled)
    except ApiError as error:
        messages = {
            400: "MiniMax rejected the image instruction or reference settings (400).",
            402: "MiniMax rejected the Context IR request for insufficient balance (402).",
            422: "MiniMax could not process the image instruction or references (422).",
        }
        if error.status in messages:
            raise ContractError(messages[error.status]) from None
        raise
    return _read(body, ratio, key)
