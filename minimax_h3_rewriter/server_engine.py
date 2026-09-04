"""One multimodal model, held open across a run's references.

``mtmd_engine`` starts ``llama-mtmd-cli`` once per description, which is right
for one picture and wasteful for six: the loop in ``multi_caption`` and
``universal`` calls it per asset, and each call reloads the model and the
projector from scratch. Measured on an 8B captioner with a warm file cache,
that is about three seconds an asset before a pixel is read; on a cold disk, or
one of the larger captioners, it is considerably worse.

``llama-server`` is the same library behind an HTTP endpoint, and it is already
on disk -- the release archive puts it beside ``llama-mtmd-cli``, so nothing is
downloaded for this. It is started before the loop, asked once per asset, and
killed after. Same model, same projector, same sampling, same answers; the
loading happens once.

Three things shape the code:

- **It is an optimisation, never a requirement.** Every failure here -- no
  binary in this build, a port that will not bind, a server that never reports
  healthy -- returns rather than raises, and the caller goes back to starting a
  process per asset. A caption run must not fail because a speed-up did.
- **The wire format is not llama.cpp's own.** Attachments go through the
  OpenAI-compatible chat endpoint as content parts, base64 in a data URI for a
  picture and an ``input_audio`` part for a sound, because that is the only
  route the server offers to the projector. The files on disk are the same ones
  the command line would have named.
- **The child is tied to this process by the kernel.** A one-shot binary that
  outlives a crash is gone in seconds anyway; a server holding a model on the
  card is not. ``runner.spawn`` puts it in a job object that dies with
  ComfyUI -- see ``runner._adopt``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request

from . import devices, runner

log = logging.getLogger(__name__)

HOST = "127.0.0.1"

STARTUP_SECONDS = 900.0
HEALTH_INTERVAL = 0.4

REQUEST_SECONDS = 900.0

STDERR_KEEP = 80

MEDIA_TYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}

CHOICE_ENV = "MINIMAX_H3_MTMD_SERVER"
AUTO, NEVER, ALWAYS = "auto", "never", "always"


class ServerUnavailable(RuntimeError):
    """The server could not be started. Never fatal: the binary still works."""


def wanted(assets: int) -> bool:
    """Whether a run of this many assets should hold a server open.

    One asset is the case the server loses: it pays a process, a port and a
    handshake to save a load it was going to do exactly once anyway. Two is
    already ahead. The variable is here because a machine where this misbehaves
    needs a way back that does not involve editing the pack.
    """
    choice = (os.environ.get(CHOICE_ENV) or AUTO).strip().lower()
    if choice == NEVER:
        return False
    if choice == ALWAYS:
        return True
    if choice != AUTO:
        log.warning(
            "[minimax_h3_rewriter.server_engine.wanted] %s is '%s', which is none of "
            "%s/%s/%s -- treating it as %s",
            CHOICE_ENV, choice, AUTO, NEVER, ALWAYS, AUTO,
        )
    return assets > 1


def free_port() -> int:
    """A port nothing is listening on, as of a moment ago.

    There is no way to reserve one for a child, so this is a race by
    construction: the port is released here and claimed by the server a moment
    later. ``start`` treats a bind failure as one more reason to fall back
    rather than as an error, which is the honest handling of a race that can be
    narrowed and not closed.
    """
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _data_uri(path: str) -> str:
    kind = MEDIA_TYPE.get(os.path.splitext(path)[1].lower(), "image/png")
    with open(path, "rb") as handle:
        return f"data:{kind};base64," + base64.b64encode(handle.read()).decode("ascii")


def content_parts(instruction: str, attachments: list[tuple[str, str]]) -> list[dict]:
    """The user turn, media first, exactly as the command line orders it.

    Order carries meaning here: the frames of a clip are chronological, and a
    first-and-last pair is told apart by which came first. ``--image a --image b
    --prompt ...`` puts the media ahead of the instruction, so this does too.
    """
    parts: list[dict] = []
    for kind, path in attachments:
        if kind == "image":
            parts.append({"type": "image_url", "image_url": {"url": _data_uri(path)}})
        elif kind == "audio":
            with open(path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
            parts.append({
                "type": "input_audio",
                "input_audio": {"data": encoded, "format": "wav"},
            })
        else:
            raise ValueError(f"unknown attachment kind '{kind}'")
    parts.append({"type": "text", "text": instruction})
    return parts


def build_command(
    binary: str,
    model_path: str,
    mmproj_path: str,
    port: int,
    gpu_layers: int,
    n_ctx: int,
    device: str = devices.AUTO,
    adapter_path: str | None = None,
) -> list[str]:
    """The same flags ``mtmd_engine.build_command`` uses, minus the one-shot ones.

    Sampling, the seed and the token ceiling are absent on purpose: on the
    command line they are properties of the single run, and here they are
    properties of each request, so they travel in the body instead. Everything
    that describes the *model* is still decided once, here.
    """
    layers = devices.layers_for(device, gpu_layers)
    layers = 999 if layers < 0 else layers
    command = [
        binary,
        "--model", model_path,
        "--mmproj", mmproj_path,
        *devices.llama_arguments(device),
        "--n-gpu-layers", str(layers),
        "--ctx-size", str(int(n_ctx)),
        "--host", HOST,
        "--port", str(int(port)),
        "--no-webui",
    ]
    if adapter_path:
        command += ["--lora", adapter_path]
    return command


def request_body(
    instruction: str,
    attachments: list[tuple[str, str]],
    seed: int = 42,
    greedy: bool = True,
    max_new_tokens: int = 256,
    temperature: float = 0.7,
    top_p: float = 0.8,
    top_k: int = 20,
    system_prompt: str = "",
) -> dict:
    """The chat request, as the command line's flags would have spelled it.

    Apart on purpose: this is the half of the two paths that has to agree with
    the other, and the only way to check that it does without a model resident
    is to be able to look at it. See ``mtmd_engine.DEFAULT_SYSTEM`` for the
    part of the agreement that had to be found out the hard way.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content_parts(instruction, attachments)})

    body = {
        "messages": messages,
        "max_tokens": int(max_new_tokens),
        "seed": int(seed),
        "stream": True,
    }
    if greedy:
        body["temperature"] = 0.0
    else:
        body.update(temperature=float(temperature), top_p=float(top_p), top_k=int(top_k))
    return body


class Server:
    """A resident ``llama-server``, asked once per reference."""

    def __init__(self, binary: str, command: list[str], port: int, n_ctx: int = 0):
        self.binary = binary
        self.command = command
        self.port = port
        self.n_ctx = int(n_ctx)
        self.process = None
        self._stderr: list[str] = []
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return f"http://{HOST}:{self.port}"

    def _watch(self, stream) -> None:
        """Keep the last of the child's stderr, and keep its pipe from filling.

        Both halves matter. A pipe nobody reads fills at 64 KB and blocks the
        writer, which would wedge the server mid-load; and when a start fails,
        llama.cpp's own last words are the only useful thing to put in the log.
        """
        try:
            for line in iter(stream.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                with self._lock:
                    self._stderr.append(text)
                    del self._stderr[:-STDERR_KEEP]
        except Exception:
            log.debug("[minimax_h3_rewriter.server_engine._watch] reader stopped",
                      exc_info=True)

    def tail(self, lines: int = 12) -> str:
        with self._lock:
            return "\n".join(self._stderr[-lines:])

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base}/health", timeout=2) as answer:
                return answer.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def start(self, seconds: float = STARTUP_SECONDS, on_wait=None) -> None:
        log.info("[minimax_h3_rewriter.server_engine] %s", " ".join(self.command))
        try:
            self.process = runner.spawn(self.command, self.binary)
        except OSError as error:
            raise ServerUnavailable(f"could not start '{self.binary}': {error}") from error

        for stream in (self.process.stdout, self.process.stderr):
            threading.Thread(target=self._watch, args=(stream,), daemon=True).start()

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ServerUnavailable(
                    f"{os.path.basename(self.binary)} exited with code "
                    f"{self.process.returncode} while loading.\n{self.tail()}"
                )
            if self._healthy():
                return
            if on_wait is not None:
                on_wait(time.monotonic() - (deadline - seconds))
            time.sleep(HEALTH_INTERVAL)

        raise ServerUnavailable(
            f"{os.path.basename(self.binary)} did not answer /health within "
            f"{seconds:.0f} s.\n{self.tail()}"
        )

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=30)
        except Exception:
            log.debug("[minimax_h3_rewriter.server_engine.close] not reaped", exc_info=True)
        finally:
            runner._LIVE.discard(process)

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *_exception) -> None:
        self.close()

    def ask(
        self,
        instruction: str,
        attachments: list[tuple[str, str]],
        seed: int = 42,
        greedy: bool = True,
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        top_p: float = 0.8,
        top_k: int = 20,
        system_prompt: str = "",
        on_text=None,
    ) -> str:
        """Describe one set of attachments. Raises ``runner.ChildFailed`` on failure.

        Failures here are *not* ``ServerUnavailable``: by this point the model
        is loaded and answering, so a request that goes wrong is the run's
        problem and not a reason to start over with the binary. Falling back
        mid-loop would also mean two captioners in one strip.

        ``on_text`` follows ``runner.run``: called with the whole answer so far,
        and returning something truthy ends it and keeps what was written.
        """
        body = request_body(
            instruction, attachments, seed, greedy, max_new_tokens,
            temperature, top_p, top_k, system_prompt,
        )

        request = urllib.request.Request(
            f"{self.base}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        pieces: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_SECONDS) as answer:
                for raw in answer:
                    if runner.interrupted():
                        import comfy.model_management as mm

                        raise mm.InterruptProcessingException()
                    piece = _delta(raw)
                    if not piece:
                        continue
                    pieces.append(piece)
                    if on_text is not None and on_text("".join(pieces)):
                        log.info(
                            "[minimax_h3_rewriter.server] answer stopped early by the caller"
                        )
                        break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:600]
            raise runner.ChildFailed(
                f"{os.path.basename(self.binary)} refused the request "
                f"({error.code}): {detail}\n{self.tail()}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise runner.ChildFailed(
                f"{os.path.basename(self.binary)} stopped answering: {error}\n{self.tail()}"
            ) from error

        return "".join(pieces).strip()


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
        log.debug("[minimax_h3_rewriter.server_engine._delta] unparsed: %.120s", payload)
        return ""
    choices = parsed.get("choices") or [{}]
    return (choices[0].get("delta") or {}).get("content") or ""


def open_server(
    binary: str,
    model_path: str,
    mmproj_path: str,
    gpu_layers: int,
    n_ctx: int,
    device: str = devices.AUTO,
    adapter_path: str | None = None,
    on_wait=None,
) -> Server | None:
    """Start a server for this model, or return None having said why in the log.

    The one place the fallback is decided, so every caller gets the same
    behaviour: a reason written down once at INFO, and a caption run that
    carries on with ``llama-mtmd-cli``.
    """
    if not binary:
        return None
    try:
        port = free_port()
    except OSError as error:
        log.info("[minimax_h3_rewriter.server_engine] no free port (%s)", error)
        return None

    server = Server(
        binary,
        build_command(binary, model_path, mmproj_path, port, gpu_layers, n_ctx,
                      device, adapter_path),
        port,
        n_ctx,
    )
    try:
        server.start(on_wait=on_wait)
    except ServerUnavailable as error:
        log.info(
            "[minimax_h3_rewriter.server_engine] %s -- describing one process at a "
            "time instead", error,
        )
        server.close()
        return None
    log.info(
        "[minimax_h3_rewriter.server_engine] %s ready on port %d",
        os.path.basename(model_path), server.port,
    )
    return server
