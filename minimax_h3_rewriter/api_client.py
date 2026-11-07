"""One bounded Chat Completions request to a server-configured recipient.

No automatic retries, tools, file access, package installation or registration.
aiohttp permits cancellation while waiting for headers and a total deadline,
including a slowly arriving body. TLS follows download's system + certifi trust.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import ssl
import time
from typing import Callable

import aiohttp
import certifi
import requests

from . import download
from .api_contract import ApiResult, ContractError, TokenUsage, WriterJob, finite_number
from .api_profiles import ApiProfile, ProfileSet

_MESSAGES = {
    "credential_unavailable": "This profile needs a locally configured API key.",
    "credential_failed": "The server-side credential could not be obtained. Check it with the local API profiles tool.",
    "cancelled": "The API request was cancelled.",
    "timeout": "The API request timed out. It was not retried; the server may have processed it.",
    "connection": "The API connection failed. The request was not retried.",
    "tls": "TLS certificate verification failed for the API recipient.",
    "redirect": "The API redirected the request. No redirect was followed.",
    "unauthorized": "The API rejected the credential (401).",
    "forbidden": "The API refused access (403).",
    "rate_limit": "The API rate limit was reached (429). The request was not retried.",
    "server": "The API reported a server error. The request was not retried.",
    "http": "The API returned an unsuccessful HTTP status.",
    "response_limit": "The API response exceeds this profile's byte limit.",
    "protocol": "The API response does not match the Chat Completions contract.",
    "refusal": "The model refused the request.",
    "unfinished": "The model did not complete its answer. It was not retried.",
    "tool_call": "The model requested a tool or function. Tool execution is not supported.",
    "empty": "The model returned an empty final answer.",
    "credential_echo": "The API response contains the credential; it was discarded.",
}

_CONNECTION_REASONS = {
    "dns": "The API hostname could not be resolved.",
    "proxy": "The configured proxy connection failed.",
    "denied": "The operating system denied network access.",
    "refused": "The destination refused the connection.",
    "reset": "The connection was reset by the destination or an intermediary.",
    "response": "The connection closed before the API response was complete.",
    "request": "The HTTP client rejected the connection or request configuration.",
    "connect": "The TCP connection to the API could not be established.",
}


def _connection_reason(error):
    """Classify by exception type/number only; never copy vendor error text."""
    if isinstance(error, aiohttp.ClientConnectorDNSError):
        return "dns"
    if isinstance(error, (aiohttp.ClientProxyConnectionError, aiohttp.ClientHttpProxyError)):
        return "proxy"
    cause = error.os_error if isinstance(error, aiohttp.ClientConnectorError) else error
    number = getattr(cause, "errno", None)
    if isinstance(cause, PermissionError) or number in (errno.EACCES, errno.EPERM, 10013):
        return "denied"
    if isinstance(cause, ConnectionRefusedError) or number in (errno.ECONNREFUSED, 10061):
        return "refused"
    if isinstance(cause, ConnectionResetError) or number in (errno.ECONNRESET, 10054):
        return "reset"
    if isinstance(error, (aiohttp.ServerDisconnectedError, aiohttp.ClientPayloadError)):
        return "response"
    if isinstance(error, (aiohttp.InvalidURL, ValueError)):
        return "request"
    return "connect" if isinstance(error, aiohttp.ClientConnectorError) else None


class ApiError(RuntimeError):
    """Safe public error: no response body, request headers or vendor exception."""

    def __init__(self, code: str, status: int | None = None, *, reason: str | None = None):
        self.reason = reason if code == "connection" and isinstance(reason, str) and reason in _CONNECTION_REASONS else None
        message = _MESSAGES[code]
        if self.reason:
            message += " " + _CONNECTION_REASONS[self.reason]
        super().__init__(message)
        self.code = code
        self.status = status


def _cancelled(check):
    if check is not None and check():
        raise ApiError("cancelled")


def validate_job(job: WriterJob, profile: ApiProfile, *, check_descriptions=True) -> None:
    """Validate capabilities and options before preparing media or accessing keys."""
    if job.profile_id != profile.id:
        raise ContractError("The job does not select this profile.")
    if job.model and job.model != profile.model:
        raise ContractError("The job does not select this model.")
    manifest = job.manifest
    if manifest and len(job.references) > profile.limits.references:
        raise ContractError("The job exceeds the profile's reference limit.")
    if manifest and job.media_mode == "native":
        needed = {"image" if entry.role == "Video" else entry.kind for entry in manifest}
        if needed - profile.capabilities.modalities:
            raise ContractError("The model does not support all requested native media. Choose a suitable profile or explicit descriptions mode.")
    if check_descriptions and manifest and job.media_mode == "descriptions" and any(not entry.description.strip() for entry in manifest):
        raise ContractError("Descriptions mode requires text for every reference, including clip sound.")
    if job.response_format not in profile.capabilities.formats:
        raise ContractError("The model does not support this response format.")
    options = job.generation.to_dict()
    unsupported = sorted(set(options) - profile.capabilities.parameters)
    if unsupported:
        message = "The selected API profile does not support generation parameters: " + ", ".join(unsupported) + "."
        if "seed" in unsupported:
            message += " Set seed=-1 and control after generate=fixed to omit seed."
        raise ContractError(message)
    if job.generation.max_tokens > profile.limits.output_tokens:
        raise ContractError("The job exceeds the profile's output token limit.")


def prepare_payload(job: WriterJob, profile: ApiProfile, images=(), audios=()) -> bytes:
    """Bind complete copies to each manifest component before accessing credentials."""
    from .api_media import MAX_IMAGES, PreparedImage, PreparedVideoFrame
    from .api_multimedia import MAX_REFERENCES, PreparedAudio

    validate_job(job, profile)
    if not isinstance(images, tuple) or len(images) > MAX_IMAGES or any(not isinstance(one, PreparedImage) for one in images):
        raise ContractError("Invalid prepared image transport.")
    if (not isinstance(audios, tuple) or len(audios) > MAX_REFERENCES * 2
            or any(not isinstance(one, PreparedAudio) for one in audios)):
        raise ContractError("Invalid prepared audio transport.")
    sound_parts = {(one.slot, one.component): one for one in audios}
    if len(sound_parts) != len(audios):
        raise ContractError("Duplicate prepared audio components.")
    expected = job.manifest if job.media_mode == "native" else ()
    position = 0
    parts = [{"type": "text", "text": job.messages()[1]["content"]}]
    for entry in expected:
        label = "<" + entry.label + ">"
        if entry.kind == "audio":
            sound = sound_parts.pop((entry.slot, entry.component), None)
            if sound is None:
                raise ContractError("Native audio must match every Audio entry in the manifest.")
            parts.extend(({"type": "text", "text": label}, sound.content()))
            continue
        if position >= len(images) or images[position].slot != entry.slot:
            raise ContractError("Native images must match every reference in manifest order.")
        frames = images[position].frames
        for frame in range(1, frames + 1):
            if position >= len(images) or (images[position].slot, images[position].frame, images[position].frames) != (entry.slot, frame, frames):
                raise ContractError("Native image batches must be complete and in manifest order.")
            image = images[position]
            if (entry.role == "Video") != isinstance(image, PreparedVideoFrame):
                raise ContractError("Video entries require timestamped frames; pictures retain their image role.")
            caption = label
            if entry.role == "Video":
                if frame > 1 and image.timestamp_seconds <= images[position - 1].timestamp_seconds:
                    raise ContractError("Video frame timestamps must increase.")
                caption += f" (video frame {frame} of {frames}; t={image.timestamp_seconds:.6f} seconds in this reference; source t={image.source_timestamp_seconds:.6f} seconds; sampled frames, not a complete video)"
            elif frames > 1:
                caption += f" (batch image {image.frame} of {image.frames}; one reference label)"
            parts.extend(({"type": "text", "text": caption}, image.content()))
            position += 1
    if position != len(images) or sound_parts:
        raise ContractError("Unexpected media outside the selected reference manifest.")
    messages = job.messages()
    if images or audios:
        messages[1] = {"role": "user", "content": parts}
    options = job.generation.to_dict()
    payload = {"model": profile.model, "messages": messages, "stream": profile.stream, **options}
    if profile.stream:
        payload["stream_options"] = {"include_usage": True}
    if job.response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif job.response_format == "json_schema":
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "minimax_h3_prompt", "strict": True, "schema": job.output_schema(),
        }}
    data = None
    try:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeError:
        pass
    if data is None:
        raise ContractError("The job contains invalid Unicode text.")
    if len(data) > profile.limits.request_bytes:
        raise ContractError("The serialized job exceeds the profile's request byte limit.")
    return data


def _ssl_context():
    context = download._SystemTrust.context()
    context.load_verify_locations(certifi.where())
    return context


def _json_object(data):
    parsed = None
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        pass
    if not isinstance(parsed, dict):
        raise ApiError("protocol")
    return parsed


class _CompletionStream:
    """Bounded by the HTTP reader; never publish partial model output."""

    def __init__(self):
        self.pending = bytearray()
        self.lines = []
        self.event = b""
        self.first = True
        self.after_cr = False
        self.done = False
        self.finish = None
        self.text = []
        self.reasoning = []
        self.usage = None

    def feed(self, data):
        if self.after_cr and data.startswith(b"\n"):
            data = data[1:]
        self.after_cr = data.endswith(b"\r")
        parts = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        self.pending.extend(parts[0])
        for part in parts[1:]:
            self._line(bytes(self.pending))
            self.pending.clear()
            self.pending.extend(part)

    def _line(self, line):
        if self.first:
            line = line.removeprefix(b"\xef\xbb\xbf")
            self.first = False
        if not line:
            payload, kind = b"\n".join(self.lines), self.event
            self.lines, self.event = [], b""
            if kind == b"error":
                raise ApiError("server")
            if payload:
                self._data(payload)
            return
        if line.startswith(b":"):
            return
        name, _, value = line.partition(b":")
        if value.startswith(b" "):
            value = value[1:]
        if name == b"data":
            self.lines.append(value)
        elif name == b"event":
            self.event = value

    def _data(self, data):
        if self.done:
            raise ApiError("protocol")
        if data == b"[DONE]":
            if self.finish is None:
                raise ApiError("unfinished")
            self.done = True
            return
        raw = _json_object(data)
        if raw.get("error"):
            raise ApiError("server")
        choices = raw.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ApiError("protocol")
        if not choices:
            if not isinstance(raw.get("usage"), dict):
                raise ApiError("protocol")
        else:
            choice = choices[0]
            if self.finish is not None or not isinstance(choice, dict):
                raise ApiError("protocol")
            index = choice.get("index", 0)
            delta = choice.get("delta")
            if type(index) is not int or index != 0 or not isinstance(delta, dict):
                raise ApiError("protocol")
            if delta.get("role") not in (None, "assistant"):
                raise ApiError("protocol")
            reason = choice.get("finish_reason")
            if delta.get("refusal") or reason == "content_filter":
                raise ApiError("refusal")
            if delta.get("tool_calls") or delta.get("function_call") or reason in ("tool_calls", "function_call"):
                raise ApiError("tool_call")
            for name, fragments in (("content", self.text), ("reasoning_content", self.reasoning)):
                value = delta.get(name)
                if value is not None:
                    if not isinstance(value, str):
                        raise ApiError("protocol")
                    fragments.append(value)
            if reason is not None:
                if reason != "stop":
                    raise ApiError("unfinished")
                self.finish = reason
        if isinstance(raw.get("usage"), dict):
            self.usage = raw["usage"]

    def result(self):
        if not self.done:
            raise ApiError("unfinished")
        return {"choices": [{"message": {"role": "assistant", "content": "".join(self.text),
                                         "reasoning_content": "".join(self.reasoning)},
                             "finish_reason": self.finish}], "usage": self.usage}


async def _exchange(profile, data, token, timeout, deadline, *, models=False, health=False):
    if type(models) is not bool or type(health) is not bool or models and health:
        raise ContractError("Invalid API metadata request.")
    metadata = models or health
    headers = {"Content-Type": "application/json",
               "Accept": "text/event-stream, application/json" if profile.stream and not metadata else "application/json",
               "User-Agent": download.USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    proxy = None
    budget = aiohttp.ClientTimeout(total=deadline, connect=timeout[0], sock_read=timeout[1],
                                   ceil_threshold=float("inf"))
    failure = None
    failure_reason = None
    body = bytearray()
    received = 0
    stream = None
    url = profile.health_url if health else profile.models_url if models else profile.completions_url
    try:
        if profile.network == "cloud":
            proxies = requests.utils.get_environ_proxies(url)
            proxy = proxies.get("https") or proxies.get("all")
        async with aiohttp.ClientSession(timeout=budget, trust_env=False,
                                         cookie_jar=aiohttp.DummyCookieJar()) as session:
            request = session.get if metadata else session.post
            async with request(url, data=data, headers=headers,
                               ssl=_ssl_context(), proxy=proxy, allow_redirects=False) as response:
                status = response.status
                if 300 <= status < 400:
                    raise ApiError("redirect", status)
                if not 200 <= status < 300:
                    code = {401: "unauthorized", 403: "forbidden", 429: "rate_limit"}.get(status)
                    raise ApiError(code or ("server" if status >= 500 else "http"), status)
                if not metadata and profile.stream and response.content_type == "text/event-stream":
                    stream = _CompletionStream()
                async for chunk in response.content.iter_chunked(65536):
                    received += len(chunk)
                    limit = min(profile.limits.response_bytes, 8192 if health else 1 << 20) if metadata else profile.limits.response_bytes
                    if received > limit:
                        raise ApiError("response_limit")
                    if stream is None:
                        body.extend(chunk)
                    else:
                        stream.feed(chunk)
                        if stream.done:
                            break
    except (aiohttp.ClientSSLError, ssl.SSLError):
        failure = "tls"
    except asyncio.TimeoutError:
        failure = "timeout"
    except (aiohttp.ClientError, OSError, ValueError) as error:
        failure = "connection"
        failure_reason = _connection_reason(error)
    if failure:
        raise ApiError(failure, reason=failure_reason)
    if health:
        if token and token.encode("ascii") in body:
            raise ApiError("credential_echo")
        return {}
    return stream.result() if stream is not None else _json_object(body)


def credential(profile, authorize):
    """Resolve only the connection profile, never a selected model's replacement."""
    if profile.secret.kind == "none":
        return None
    if authorize is None:
        raise ApiError("credential_unavailable")
    token = None
    try:
        token = authorize(profile)
    except Exception:
        pass
    if not isinstance(token, str) or not token or len(token) > 65536 or any(not 33 <= ord(char) < 127 for char in token):
        raise ApiError("credential_failed")
    return token


def _answer(raw: dict) -> tuple[str, str, TokenUsage]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ApiError("protocol")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ApiError("protocol")
    if message.get("refusal") or choice.get("finish_reason") == "content_filter":
        raise ApiError("refusal")
    if message.get("tool_calls") or message.get("function_call") or choice.get("finish_reason") in ("tool_calls", "function_call"):
        raise ApiError("tool_call")
    if choice.get("finish_reason") != "stop":
        raise ApiError("unfinished")
    text, reasoning = message.get("content"), message.get("reasoning_content") or ""
    if not isinstance(text, str) or not isinstance(reasoning, str):
        raise ApiError("protocol")
    if not text.strip():
        raise ApiError("empty")
    raw_usage = raw.get("usage")
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    counts = {name: raw_usage.get(name) for name in TokenUsage.__dataclass_fields__}
    usage = TokenUsage(**{name: value if type(value) is int and value >= 0 else None for name, value in counts.items()})
    return text, reasoning, usage


class ApiClient:
    """Server-owned profiles, injectable authorization and cancellable HTTP.

    complete() is for synchronous ComfyUI workers/CLI; async hosts can await
    complete_async(). Cancelling closes our connection, but a remote service
    may still process/bill an already submitted request. No retries are made.
    """

    def __init__(self, profiles: ProfileSet, *, authorize: Callable[[ApiProfile], str] | None = None,
                 timeout: tuple[float, float] = (5.0, 60.0), deadline: float = 180.0,
                 images: tuple = (), audios: tuple = ()):
        if not isinstance(profiles, ProfileSet):
            raise ContractError("The API client requires server-side profiles.")
        if not isinstance(timeout, tuple) or len(timeout) != 2 or any(not finite_number(value) or value <= 0 for value in timeout):
            raise ContractError("Invalid API timeouts.")
        if not finite_number(deadline) or not 0 < deadline <= 3600:
            raise ContractError("Invalid total API deadline.")
        self.profiles, self.authorize, self.timeout, self.deadline = profiles, authorize, timeout, deadline
        self.images = images
        self.audios = audios
        self._selected = {}

    def complete(self, job: WriterJob, *, cancelled: Callable[[], bool] | None = None) -> ApiResult:
        running = False
        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            pass
        if running:
            raise ContractError("Await complete_async() from an asynchronous host.")
        return asyncio.run(self.complete_async(job, cancelled=cancelled))

    async def complete_async(self, job: WriterJob, *, cancelled: Callable[[], bool] | None = None) -> ApiResult:
        if not isinstance(job, WriterJob):
            raise ContractError("The API client requires a validated writer job.")
        _cancelled(cancelled)
        from .api_models import effective_profile

        connection = self.profiles.resolve(job.profile_id)
        key = (connection, job.model)
        if key not in self._selected:
            self._selected[key] = effective_profile(connection, job.model)
        profile = self._selected[key]
        data = prepare_payload(job, profile, self.images, self.audios)
        token = credential(connection, self.authorize)
        _cancelled(cancelled)
        start = time.monotonic()
        operation = asyncio.create_task(_exchange(profile, data, token, self.timeout, self.deadline))
        try:
            while not operation.done():
                _cancelled(cancelled)
                await asyncio.wait({operation}, timeout=0.05)
            _cancelled(cancelled)
            raw = await operation
        finally:
            if not operation.done():
                operation.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await operation
        _cancelled(cancelled)
        text, reasoning, usage = _answer(raw)
        if token and (token in text or token in reasoning):
            raise ApiError("credential_echo")
        return ApiResult(profile.id, profile.model, text, reasoning, usage, time.monotonic() - start,
                         job.manifest, job.notices)
