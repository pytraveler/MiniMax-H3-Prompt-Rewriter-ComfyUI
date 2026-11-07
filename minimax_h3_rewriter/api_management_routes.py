"""Local browser administration, or explicit HTTPS origin plus operator token."""

import asyncio
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from . import api_config
from .api_client import ApiError
from .api_contract import ContractError, object_fields
from .api_management import MAX_BODY, ManagementError, ProfileManager
from .api_secrets import SecretError

ADMIN_TOKEN_ENV = "MINIMAX_H3_API_ADMIN_TOKEN"
ADMIN_ORIGIN_ENV = "MINIMAX_H3_API_ADMIN_ORIGIN"


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise ValueError()
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def local_origin(request):
    """Require same origin, then local desktop access or explicit remote authority.

    Forwarded TLS is accepted only from a loopback reverse proxy, for the exact
    HTTPS origin configured by the operator, and with the administrator token.
    Headers from a remote peer never establish TLS or bypass authorization.
    """
    try:
        peer = request.transport.get_extra_info("peername")
        if not peer:
            raise ValueError()
        loopback_peer = ipaddress.ip_address(peer[0]).is_loopback
        forwarding = {name.lower() for name in request.headers if name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")}
        if (len(request.headers.getall("Host", [])) != 1 or len(request.headers.getall("Origin", [])) != 1
                or len(request.headers.getall("X-MiniMax-H3-Admin", [])) > 1):
            raise ValueError()
        origin = request.headers.get("Origin", "")
        source = _origin(origin)
        host = _origin(request.scheme + "://" + request.host)
        if request.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
            raise ValueError()
        try:
            loopback_host = host[1] == "localhost" or ipaddress.ip_address(host[1]).is_loopback
        except ValueError:
            loopback_host = False
        local = loopback_peer and loopback_host and not forwarding and source == host
        token = os.environ.get(ADMIN_TOKEN_ENV)
        if not local:
            configured = os.environ.get(ADMIN_ORIGIN_ENV, "")
            allowed = _origin(configured)
            authority = _origin("https://" + request.host)
            if allowed[0] != "https" or source != allowed or authority[1:] != allowed[1:]:
                raise ValueError()
            direct_tls = request.scheme == "https" and not forwarding
            proxy_tls = (request.scheme == "http" and loopback_peer
                         and forwarding <= {"x-forwarded-proto", "x-forwarded-host", "x-forwarded-for", "x-forwarded-port"}
                         and request.headers.getall("X-Forwarded-Proto", []) == ["https"])
            if not direct_tls and not proxy_tls:
                raise ValueError()
            if token is None:
                raise ManagementError("Remote editing requires MINIMAX_H3_API_ADMIN_TOKEN and the exact HTTPS MINIMAX_H3_API_ADMIN_ORIGIN on the server.", 403)
        if token is not None:
            if not 32 <= len(token) <= 512 or not all(33 <= ord(c) < 127 for c in token):
                raise ManagementError("The server administrator token is invalid. Configure 32-512 random printable ASCII characters.", 503)
            supplied = request.headers.get("X-MiniMax-H3-Admin", "")
            if not supplied.isascii() or not secrets.compare_digest(supplied, token):
                raise ManagementError("Administrator token required.", 401)
        return origin
    except (ValueError, TypeError, AttributeError):
        raise ManagementError("API provider editing requires matching Origin and local access, or the configured HTTPS origin with administrator authorization.", 403) from None


def register(routes, prefix, web, *, manager=None):
    service = manager if manager is not None else ProfileManager()
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}
    operations = {"open": service.open, "stage": service.stage, "commit": service.commit, "test": service.test, "close": service.close}

    async def handle(request):
        try:
            origin = local_origin(request)
            if (request.content_type != "application/json" or request.content_length is None
                    or not 0 < request.content_length <= MAX_BODY or request.headers.get("Content-Encoding", "identity") != "identity"):
                raise ManagementError("API editing requires a bounded, uncompressed JSON request.")
            body = await request.content.readexactly(request.content_length)
            raw = api_config.parse_json(body)
            action = request.match_info["action"]
            operation = operations.get(action)
            if operation is None:
                raise ManagementError("Unknown API editing operation.", 404)
            if action == "open":
                object_fields(raw, ())
                result = await asyncio.to_thread(operation, origin)
            elif action == "test":
                result = await operation(raw, origin)
            else:
                result = await asyncio.to_thread(operation, raw, origin)
            return web.json_response(result, headers=headers)
        except ManagementError as error:
            return web.json_response({"error": str(error)}, status=error.status, headers=headers)
        except ApiError as error:
            return web.json_response({"error": str(error), "code": error.code,
                                      "upstream_status": error.status}, status=503, headers=headers)
        except (api_config.ConfigError, ContractError, SecretError) as error:
            return web.json_response({"error": str(error)}, status=400, headers=headers)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError, asyncio.IncompleteReadError):
            return web.json_response({"error": "Invalid API editing request."}, status=400, headers=headers)
        except Exception:
            return web.json_response({"error": "API editing failed. Check the local configuration and declared dependencies."}, status=503, headers=headers)

    routes.post(prefix + "/api/manage/{action}")(handle)
