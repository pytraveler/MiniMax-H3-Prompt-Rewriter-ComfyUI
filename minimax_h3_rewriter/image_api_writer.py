"""H3 Image prompt expansion with a configured Chat Completions model."""

from __future__ import annotations

from . import api_config, image_prompt as image
from .api_client import _cancelled, chat_payload, complete_chat, credential, validate_generation
from .api_contract import ContractError, Generation
from .api_media import prepare_images_async
from .api_models import effective_profile
from .api_options import resolve_options
from .api_secrets import SecretResolver


async def expand(text, references, ratio, profile_id, model, options, image_max_side, *, cancelled=None):
    _cancelled(cancelled)
    if not profile_id:
        raise ContractError("Choose an API profile and model for API Writer. Use API providers to add a connection.")
    connection = api_config.load_profiles().resolve(profile_id)
    profile = effective_profile(connection, model)
    if references and "image" not in profile.capabilities.modalities:
        raise ContractError("Image Edit requires image input. Select a vision-capable API model and enable image in its Allowed models settings.")
    if len(references) > profile.limits.references:
        raise ContractError("Too many pictures for the selected API model. Check its Reference limit or disable some pictures.")

    settings = resolve_options(options)
    budget = settings.max_tokens if options is not None else min(settings.max_tokens, profile.limits.output_tokens)
    generation = Generation(budget, None if settings.temperature == -1 else settings.temperature)
    validate_generation(generation, settings.response_format, profile)
    copies = await prepare_images_async(references, max_side=image_max_side,
                                        byte_limit=profile.limits.request_bytes, cancelled=cancelled)
    messages, field = image.chat_request(text, references, ratio, attachments=tuple(one.content() for one in copies))
    schema = {"type": "object", "properties": {"expanded_caption": {"type": "string"}, field: {"type": "string"}},
              "required": ["expanded_caption", field], "additionalProperties": False}
    data = chat_payload(profile, messages, generation, settings.response_format,
                        schema_name="minimax_h3_image_prompt", schema=schema)
    _cancelled(cancelled)
    token = credential(connection, SecretResolver())
    timeout = settings.timeout_seconds
    answer = await complete_chat(profile, data, token, (5.0, timeout), timeout, cancelled=cancelled)
    caption, ratio = image.read_expansion(answer.text, ratio, field, budget="max_tokens in API Writer Options")
    return caption, answer.reasoning, ratio, [f"API profile: {answer.profile_id} | model: {answer.model}"]
