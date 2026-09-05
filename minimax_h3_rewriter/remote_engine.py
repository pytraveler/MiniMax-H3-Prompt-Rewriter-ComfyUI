"""Generating through an already-running llama.cpp server.

Every other backend in this pack carries the model on the ComfyUI machine: a
transformers checkpoint in this process, a llama-cpp-python wheel, or the
official binaries in a subprocess. This one runs none of it. ``llama-server``
and llama-swap both speak the OpenAI-compatible chat API, so the node sends the
messages over HTTP and reads the streamed answer back -- the weights, the KV
cache and the sampling all live wherever ``server_url`` points, which may be
another process, another card or another machine entirely.

What that buys is decided on the Options node. Pick 'remote (llama.cpp
server)' as the ``gguf_runtime`` and:

- **Nothing is downloaded and nothing is loaded here.** ``model_path``,
  ``adapter_path``, ``gpu_layers``, ``n_ctx`` and ``keep_model_loaded`` stop
  meaning anything: the files and flags they name live on the server, and the
  LoRA is whatever the serving model has attached, so ``use_lora`` is likewise
  a no-op. A rewrite still fills the progress bar the same way, because the
  tokens still stream through this process.
- **The chat template is the server's.** The messages are handed over as roles
  and content, and the server applies its model's own template -- which is
  also what lets `caption` send pictures and sounds as content parts, exactly
  like the local ``llama-server`` path in ``server_engine``.
- **The context is the server's.** ``n_ctx`` is honoured or not by whichever
  server answers; this side cannot widen it, so ``run_messages`` skips its
  local widening entirely.

The failure mode worth naming is that a missing server is a missing answer:
an endpoint that does not answer raises rather than falling back to a local
backend, because quietly spending a five-gigabyte download on the *other* side
of a typo is into the same category of surprise the rest of this pack refuses.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request

from . import checks, runner
from .constants import normalize_seed
from .progress import NodeProgress

log = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "http://127.0.0.1:9090"

PREVIEW_TAIL = 280

CHARS_PER_TOKEN = 4.0

LOOP_EVERY = 32

REQUEST_SECONDS = 900.0

MEDIA_TYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


def normalize_url(value: str) -> str:
    """A base URL without a trailing slash, or a refusal naming the default."""
    value = (value or "").strip().strip('"')
    if not value:
        return DEFAULT_SERVER_URL
    if "://" not in value:
        raise RuntimeError(
            f"server_url is '{value}', which is not an address like "
            f"'{DEFAULT_SERVER_URL}'. Give the scheme and the port -- 'http://' or "
            f"'https://' -- and leave off any path; the node appends "
            f"/v1/chat/completions itself."
        )
    return value.rstrip("/")


def server_model(settings: dict) -> str:
    """Which model on the server to ask for, or '' to let the server decide.

    llama-swap picks a model from the ``model`` field of each request; left
    empty, it serves whatever it was told to swap to by default. A plain
    ``llama-server`` ignores the field, which is exactly what an empty value
    achieves here.
    """
    return (settings.get("server_model") or "").strip()


def reachable(url: str, timeout: float = 2.0) -> bool:
    """Whether something that speaks the OpenAI API is listening at ``url``."""
    try:
        request = urllib.request.Request(f"{normalize_url(url)}/v1/models")
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def models(url: str) -> list[str]:
    """The model names the server offers, or [] when it will not say.

    llama-swap answers ``/v1/models`` with what it can swap to; a plain
    llama.cpp server answers with the single model it holds. Called only for
    the log line -- the request itself is sent with whatever ``server_model``
    says, because the list is advisory and the truthful unit test is the chat
    call that follows.
    """
    try:
        request = urllib.request.Request(f"{normalize_url(url)}/v1/models")
        with urllib.request.urlopen(request, timeout=5) as answer:
            payload = json.loads(answer.read().decode("utf-8", errors="replace"))
        return [entry.get("id") or "" for entry in payload.get("data") or [] if entry.get("id")]
    except (urllib.error.URLError, OSError, ValueError):
        return []


def _unreachable(url: str) -> RuntimeError:
    return RuntimeError(
        f"The llama.cpp server at '{normalize_url(url)}' did not answer. Is llama-swap (or "
        f"llama-server) actually running on that address? The default is "
        f"'{DEFAULT_SERVER_URL}'; the Options node's 'server_url' field says anything else. "
        f"Check the port, and that the server process is the one you think it is -- this "
        f"node is pointing at a service, and a service that is not there is a dead run."
    )


def _media_part(kind: str, path: str) -> dict:
    """One attachment as an OpenAI content part, exactly as ``server_engine`` sends it.

    A picture goes in as a base64 data URI, a sound as an ``input_audio``
    part. This is the only wire format the server offers to its projector, so
    the canonical implementation lives in one place more than it would like:
    any change here should check ``server_engine.content_parts`` too.
    """
    if kind == "image":
        mediatype = MEDIA_TYPE.get(os.path.splitext(path)[1].lower(), "image/png")
        with open(path, "rb") as handle:
            uri = "data:" + mediatype + ";base64," + base64.b64encode(handle.read()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": uri}}
    if kind == "audio":
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": "wav"}}
    raise ValueError(f"unknown attachment kind '{kind}'")


def content_parts(
    instruction: str, attachments: list[tuple[str, str]], media_marker: str | None = None
) -> list[dict]:
    """The user turn as content parts, media in the order it was attached.

    With ``media_marker`` given and present in the instruction -- which is how
    the 8B and Omni writers build their turns -- each marker is replaced by the
    next attachment in order, so a picture stays attached to the text naming
    it, the same way ``llama-mtmd-cli`` splices the frames in at the markers.
    Without it the media line up first and the instruction follows, which is
    what ``server_engine`` does and what a caption's plain question needs.
    """
    if media_marker and media_marker in instruction:
        remaining = list(attachments)
        parts: list[dict] = []
        for chunk in instruction.split(media_marker):
            if chunk:
                parts.append({"type": "text", "text": chunk})
            if remaining:
                parts.append(_media_part(*remaining.pop(0)))
        return parts
    parts = [_media_part(kind, path) for kind, path in attachments]
    parts.append({"type": "text", "text": instruction})
    return parts


def _delta(raw: bytes) -> str:
    """One token's worth of text out of a server-sent-events line, or ""."""
    line = raw.decode("utf-8", errors="replace").strip()
    if not line.startswith("data:"):
        return ""
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return ""
    try:
        parsed = json.loads(payload)
    except ValueError:
        log.debug("[minimax_h3_rewriter.remote_engine._delta] unparsed: %.120s", payload)
        return ""
    choices = parsed.get("choices") or [{}]
    return (choices[0].get("delta") or {}).get("content") or ""


def _sampling(
    seed: int,
    greedy: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    repetition_penalty: float,
) -> dict:
    """The request body's sampling half, in the spelling llama.cpp speaks.

    ``top_k`` and ``repeat_penalty`` are what the server calls them, not the
    OpenAI names for their nearest equivalents, so the body is built here
    rather than by an OpenAI client halfway to translating.

    ``chat_template_kwargs`` carries the thinking switch, the same
    ``enable_thinking=False`` the pack passes to every local run: without it a
    reasoning model spends the whole token ceiling on ``reasoning_content`` and
    the ``content`` field comes back empty, which is not a rewrite anyone
    asked for. Newer llama.cpp servers understand the key and older ones ignore
    it, and the direction that fails safely is the one that asks.
    """
    body = {
        "max_tokens": int(max_new_tokens),
        "repeat_penalty": float(repetition_penalty),
        "seed": normalize_seed(seed),
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if greedy:
        body["temperature"] = 0.0
    else:
        body.update(
            temperature=float(temperature),
            top_p=float(top_p),
            top_k=int(top_k),
        )
    return body


def _chat(
    messages: list[dict],
    url: str,
    model: str,
    body: dict,
    progress: NodeProgress | None = None,
    on_text=None,
) -> str:
    """POST the chat completion and read the streamed answer back.

    ``on_text`` follows ``run_messages`` and the other engines' callbacks:
    called with the whole answer so far, and returning something truthy ends
    the request and keeps what was written. Loop detection runs whether or not
    there is a bar to draw.
    """
    request = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    pieces: list[str] = []
    produced = 0
    interrupted = False
    looped = False
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_SECONDS) as answer:
            for raw in answer:
                if runner.interrupted():
                    interrupted = True
                    break
                piece = _delta(raw)
                if not piece:
                    continue
                pieces.append(piece)
                produced += 1
                if on_text is not None and on_text("".join(pieces)):
                    log.info("[minimax_h3_rewriter.remote_engine] answer stopped early by the caller")
                    break
                if produced % LOOP_EVERY == 0 and checks.looping("".join(pieces)):
                    looped = True
                    log.info(
                        "[minimax_h3_rewriter.remote_engine] stopping at a repetition after %d tokens",
                        produced,
                    )
                    break
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(
            f"The server at '{url}' refused the request ({error.code}): {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise _unreachable(url) from error
    except OSError as error:
        raise _unreachable(url) from error

    if interrupted:
        import comfy.model_management as mm

        raise mm.InterruptProcessingException()

    if progress is not None:
        if looped:
            progress.finish(f"Stopped at a repetition · {produced} tokens")
        else:
            progress.finish(f"Done · {produced} tokens")
    return "".join(pieces).strip()


def rewrite(
    messages: list[dict[str, str]],
    server_url: str = DEFAULT_SERVER_URL,
    server_model: str = "",
    seed: int = 42,
    greedy: bool = True,
    max_new_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.8,
    top_k: int = 20,
    repetition_penalty: float = 1.05,
    progress: NodeProgress | None = None,
    label: str = "",
    **ignored,
) -> str:
    """One text completion against ``server_url``, and the answer alone.

    ``**ignored`` picks up everything the local engines take that a server has
    already decided -- ``model_path``, ``adapter_path``, ``gpu_layers``,
    ``n_ctx``, ``keep_loaded``, ``device`` -- so ``_gguf_text`` and
    ``run_messages`` can hand the same call straight over.
    """
    url = normalize_url(server_url)
    name = (server_model or "").strip()
    if progress is not None:
        progress.set_total(max(int(max_new_tokens), 1))
        title = f"{label + ': ' if label else ''}Generating on {url}"
        title += f" · {name}" if name else ""
        progress.update(0, f"{title}\n0 tokens")

    def report(whole: str) -> bool:
        """Drive the bar; the loop check runs in ``_chat`` either way."""
        if progress is not None:
            progress.update(
                min(len(whole) / CHARS_PER_TOKEN, float(max_new_tokens)),
                f"{label + ': ' if label else ''}{len(whole)} chars\n{whole[-PREVIEW_TAIL:]}",
            )
        return False

    body = {
        "messages": list(messages),
        **_sampling(seed, greedy, max_new_tokens, temperature, top_p, top_k, repetition_penalty),
    }
    if name:
        body["model"] = name

    return _chat(messages, url, name, body, progress, report)


def caption(
    instruction: str,
    attachments: list[tuple[str, str]],
    system_prompt: str = "",
    server_url: str = DEFAULT_SERVER_URL,
    server_model: str = "",
    seed: int = 42,
    greedy: bool = True,
    max_new_tokens: int = 256,
    temperature: float = 0.7,
    top_p: float = 0.8,
    top_k: int = 20,
    repetition_penalty: float = 1.05,
    media_marker: str | None = None,
    progress: NodeProgress | None = None,
) -> str:
    """Describe the attachments against ``server_url``, media in order.

    The wire shape is what ``server_engine`` already speaks to a locally-run
    ``llama-server``: the user turn is content parts, with pictures as base64
    data URIs and sounds as ``input_audio``. `media_marker` lets a turn whose
    text names the assets splice each one in next to the sentence naming it,
    which is how the frame-carrying writers arrive here.
    """
    url = normalize_url(server_url)
    name = (server_model or "").strip()

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content_parts(instruction, attachments, media_marker)})

    body = {
        "messages": messages,
        **_sampling(seed, greedy, max_new_tokens, temperature, top_p, top_k, repetition_penalty),
    }
    if name:
        body["model"] = name

    if progress is not None:
        progress.set_total(max(int(max_new_tokens), 1))
        progress.update(0, f"Describing on {url} · {len(attachments)} attachment(s)\n0 tokens")

    def report(whole: str) -> bool:
        if progress is not None:
            progress.update(
                min(len(whole) / CHARS_PER_TOKEN, float(max_new_tokens)),
                f"Describing · {len(whole)} chars\n{whole[-PREVIEW_TAIL:]}",
            )
        return False

    return _chat(messages, url, name, body, progress, report)