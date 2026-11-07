"""Model metadata routes; no profile or credential administration inputs."""

from . import api_config, api_models
from .api_client import ApiError
from .api_contract import ContractError, object_fields
from .api_secrets import SecretResolver


def register(routes, prefix, web):
    headers = {"Cache-Control": "no-store"}

    @routes.get(prefix + "/api/models")
    async def model_choices(request):
        try:
            query = object_fields(dict(request.query), ("profile_id",), ("profile_id",))
            profile = api_config.load_profiles().resolve(query["profile_id"])
            return web.json_response(api_models.public_models(profile), headers=headers)
        except (api_config.ConfigError, ContractError) as error:
            return web.json_response({"error": str(error)}, status=400, headers=headers)

    @routes.post(prefix + "/api/models/refresh")
    async def refresh_choices(request):
        if request.content_type != "application/json" or request.content_length is None or request.content_length > 2048:
            return web.json_response({"error": "Model refresh requires a small JSON object with profile_id."}, status=400, headers=headers)
        try:
            raw = api_config.parse_json(await request.read())
            raw = object_fields(raw, ("profile_id",), ("profile_id",))
            profile = api_config.load_profiles().resolve(raw["profile_id"])
            result = await api_models.refresh_models(profile, authorize=SecretResolver())
            return web.json_response(result, headers=headers)
        except (api_config.ConfigError, ContractError) as error:
            return web.json_response({"error": str(error)}, status=400, headers=headers)
        except ApiError as error:
            return web.json_response({"error": str(error)}, status=503, headers=headers)
        except (ValueError, TypeError, RecursionError, OverflowError):
            return web.json_response({"error": "Invalid model refresh request."}, status=400, headers=headers)
