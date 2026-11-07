# API Universal Writer

[Русская инструкция](api_writer_RU.md) | [Security review notes](api_writer_security.md)

**MiniMax-H3 API Universal Writer** writes H3 prompts through a running
llama-server or an OpenAI-compatible remote API. This is an additional node;
the existing local writers work as before. It accepts images, video, audio and
decoded RefMods, with a reference strip, prompt library and `repeat_last`.
Video travels as sampled frames with timestamps. An explicit descriptions mode
lets a text-only model work from descriptions supplied by you or a RefMod.

The workflow stores a connection ID and an optional model ID. Configure the endpoint, models and key
in **API providers** beside the model selector, or through the local CLI. The pack does not start or stop your LLM
server. Restart ComfyUI after installing this code so it registers the new node.

Start with **11 - Write through an API** in the pack's workflow templates. It
contains API Writer, API Writer Options and core Preview Any nodes for the
prompt, findings and report. Configure a provider and choose it before queuing:
the template deliberately uses `configure-a-profile`, with no endpoint, key,
private model ID or personal media file.

## Provider support

This version implements the OpenAI-compatible **Chat Completions** protocol.
Provider names do not imply that every API or model from that provider works.

| Connection | Verification and boundary |
|---|---|
| llama.cpp / llama-server | Live local text, images, sampled video and WAV audio on suitable models; model selection and cancellation checked. The external process is managed separately. |
| DeepSeek ordinary API | Live custom text, H3 text, I2VA and Ref2VA with sampled images. Audio is not declared by the supplied profile. |
| Ollama, including WSL2 | Live `/v1` text, image and cancellation checks. Native `/api/chat`, model pulling and `keep_alive` controls are outside this adapter. |
| vLLM | Chat Completions contract, aliases, errors and limits checked with loopback test servers. No live vLLM engine was available for this release preparation. |
| Other compatible API | Configure exact endpoint, served model IDs, parameters and limits yourself; no live provider certification is implied. |
| Gemini native API / Anthropic Messages | Not implemented by this Chat Completions adapter. A separate protocol adapter is needed. |

GLM/Kimi coding subscriptions are not ordinary integration API credentials.
The [GLM Coding Plan tool list](https://docs.z.ai/devpack/tool/others) limits
subscription use to supported environments; this node is not on that list.
[Kimi's platform comparison](https://www.kimi.com/code/docs/en/kimi-code/faq.html#empty-model-list-during-login)
separates Code membership endpoints/keys from its pay-as-you-go platform.
These restrictions were rechecked on 2026-11-07. The pack does not impersonate a
coding client or silently switch a subscription to a billed API.

## Edit providers in ComfyUI

Click **API providers** beside **Refresh models** on the API Universal Writer.
The window uses the pack's resizable library-window controls. Select an existing
connection or choose a template and **Add provider**. Set its ID, display name,
Chat Completions base URL, network mode and streaming support.

Under **Allowed models**, add exact served IDs or aliases. Each model has its own
input modalities, output formats, supported parameters and limits. Set one as
the default. These declarations describe what the server can already do;
checking **audio** or **video** does not install support. Sampled video requires
**image** input; the **video** flag records a declaration for this adapter. Generation values still
come from the API Writer Options node.

Choose **Encrypted server storage** to enter a key. **Keep existing key /
configure later** leaves a saved key unchanged. **Enter or replace key** accepts
a new one; **Delete saved key** removes it. Environment mode stores only the
variable name and does not edit the server environment. A saved key is never
loaded into a password field or returned through an endpoint.

**Test connection** checks the current form, including unsaved providers, model
IDs, endpoints and new keys. It sends a fresh `GET /models` from the ComfyUI
server, with a 3-second connection limit and a 10-second total limit, and reports
whether the highlighted model was listed. Only locally allowed IDs are returned.
The test does not save settings/keys, populate the generation model cache, load
a model or generate text. It cannot verify declared modalities, formats or
generation parameters. A public `/models` endpoint may ignore credentials, so
success there does not prove a key is valid for generation. HTTP 401/403 and
connection/TLS/timeout errors are shown without returning provider response bodies.
A typed key uses the same one-use encrypted envelope as saving; it remains in
the input for **Save provider**, or is cleared when the window closes. Saved
keys cannot be sent to an edited recipient: re-enter a key for the new endpoint.
Tests run one at a time per editor, with a three-second cooldown. No automatic
retry or fallback request is made if `/models` is unavailable.

**Save provider** writes server configuration without calling a model. It updates
the profile/model choices on open API Writer nodes. **Use in this node** explicitly
selects the saved connection and the model highlighted in the window, including
when it is the profile's default model. It does not change the profile default.
The highlighted model is preserved after saving, and reopening the node's own
profile highlights its current explicit model. Save edited settings before
applying them to a node. Deleting a
provider leaves its ID in existing workflows, so they fail visibly rather than
switching to another recipient. Existing profile IDs are fixed; create a new
provider to use another ID.

Changing models on the same connection updates the encrypted key's allowed-model
binding during save. Changing its endpoint or credential source requires a new
key or explicit deletion of the old one. Concurrent changes from another window
or the CLI are rejected; reopen the editor before saving again.

Each editor request has a 20-second deadline, including response-body reading.
The status distinguishes settings validation, key encryption and saving. A
network error, invalid reply or lost commit response releases the waiting
state. If the save result is unknown, **Close** remains available and another
save is blocked until you reopen the window and check the stored settings.
Closing or timing out does not prove a server write was cancelled. The editor
never retries a save automatically.

Reference strips release discarded image/video downloads, playback and hover
timers when redrawn or removed. Old video previews no longer occupy browser
connections needed by the provider editor and other ComfyUI requests.

New keys travel as a one-use encrypted envelope: Web Crypto uses AES-256-GCM,
and wraps that random AES key with the server's temporary RSA-3072-OAEP/SHA-256
public key. The 90-second challenge fixes the profile operation on the server
and can be consumed only once. Private RSA keys stay in memory. The API key is
briefly plaintext in the browser input and server memory, where it is needed
to encrypt/store it and authorize requests. This does not protect against a
compromised browser extension, custom-node script or server account.

### Remote ComfyUI and Linux storage

Direct local access through `localhost` or a loopback IP needs no separate token
unless the operator enables one. Remote editing requires HTTPS and these server
settings, supplied before ComfyUI starts:

| Environment variable | Value |
|---|---|
| `MINIMAX_H3_API_ADMIN_ORIGIN` | Exact browser origin, for example `https://comfy.example.com`, including a nonstandard port if used, without a trailing slash |
| `MINIMAX_H3_API_ADMIN_TOKEN` | A separate random administrator token, 32-512 printable ASCII characters |
| `MINIMAX_H3_VAULT_KEY` | On Linux: base64 of 32 cryptographically random bytes, kept stable across restarts |

Use the hosting provider's secret manager or a protected service configuration.
For generating the two secrets yourself, these commands print them to your
terminal; keep their output private:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
python3 -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
```

The first value is the administrator token; the second is the Linux vault key.
Keep a separate secure backup of the vault key. Losing or simply replacing it
makes existing ciphertext unreadable; changing it is not a key-rotation procedure.
The pack never writes that master key beside the encrypted file or sends it to
the browser. On Windows it keeps using current-user DPAPI, without a vault-key
environment variable.

On Linux, `api_secrets.enc` uses AES-256-GCM with a fresh 96-bit nonce per write
and authenticated format metadata. New files use mode `0600`, with a `0700`
configuration subdirectory. Without a configured vault key, explicit environment
API credentials remain available and browser key entry is disabled.

The window asks for the administrator token when needed. It holds it only in
that window's memory and sends it in a request header; it is not saved in the
workflow or browser storage. Setting the token also protects local editing.
This authorization covers the editor, not all of ComfyUI: protect the rest of
an Internet-facing ComfyUI installation separately.

A TLS reverse proxy must preserve the public `Host`, set
`X-Forwarded-Proto: https`, and reach ComfyUI over loopback. Forwarded TLS headers
from remote peers are rejected. Direct ComfyUI TLS also works. An SSH tunnel to
localhost is an alternative; plain HTTP to a VDS/LAN address cannot administer
profiles even though the API-key envelope itself is encrypted. Web Crypto needs
a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts).

The [Registry standards](https://docs.comfy.org/registry/standards) prohibit code
obfuscation and runtime package installation; they do not prohibit credential
encryption. This implementation uses readable source and the declared
`cryptography` dependency, with no runtime installer. A Registry scan/review of
the actual release is still required.

## Configure connections

Run the following from the pack directory with a Python environment containing
its declared dependencies. Use the **same OS account as ComfyUI**. The examples
use the repository's development environment; a normal installation can use
its ComfyUI Python instead.

`--user-directory` is the existing directory ComfyUI uses. For a default
portable installation it is `<ComfyUI>/user`; if you launch ComfyUI with
`--user-directory`, pass that exact directory here too.

```powershell
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' init
```

For a new configuration, `init --include-local-runtimes` also creates anonymous,
text-only Ollama and vLLM templates. Replace their placeholder model IDs before
use. Both forms of `init` refuse to overwrite an existing configuration; the
ordinary `init` still creates only the original llama-server/DeepSeek pair.

If portable Python omits the current directory from its module search path,
run `python.exe minimax_h3_rewriter\api_admin.py` with the same arguments instead.
The administrator module ships in Registry installations too; the `tools`
directory is only a repository convenience.

This creates `<user-directory>/minimax_h3_rewriter/api_profiles.json` with
non-secret llama-server and DeepSeek templates. It never overwrites an existing
file. Edit that JSON locally:

- For `llama-server-local`, set `base_url` to the running server's `/v1` base
  and `model` to its served alias. The default is `http://127.0.0.1:8080/v1`
  with a placeholder `local-model`. The template is anonymous and text-only.
- For `deepseek`, the template uses the ordinary DeepSeek API and a vault record
  named `deepseek`. Coding-plan credentials for GLM or Kimi are not interchangeable
  with ordinary API keys. This stage does not integrate coding subscriptions.
- Declare only the formats, generation parameters and modalities the chosen
  model supports. The node does not infer support from a model name or silently
  fall back to another provider.
- Connections use streaming by default, including existing profiles without a
  `stream` field. The node collects the stream into one completed answer. This
  lets a forwarding proxy notice a cancelled connection during generation,
  instead of waiting for the entire response. For an API that only supports
  ordinary JSON responses, set `"stream": false` in its local profile. There is
  no automatic retry with different transport settings.

You can validate and save an edited document through the tool:

```powershell
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' import 'D:\my-profiles.json'
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' list
```

The profile dropdown is built from this file. Refresh node definitions or
reload ComfyUI's page after changing profile IDs. A malformed file produces a
configuration error when running the new node; other nodes still register.

## Local Ollama in WSL and vLLM

Ollama in WSL2 serves the same HTTP API as a local Linux installation. Start
your installed Ollama service in WSL, then check it from Windows:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/v1/models' -TimeoutSec 5
```

[WSL supports Windows access to Linux services through localhost](https://learn.microsoft.com/en-us/windows/wsl/networking#accessing-linux-networking-apps-from-windows-localhost).
If that connection fails, check the running distribution, service and WSL
network configuration first. A direct private WSL address requires an explicit
`network: "lan"` profile. The node uses the configured HTTP recipient; WSL and
the LLM service remain under your control.

The optional templates use these settings:

| Profile ID | Base URL | Model to configure | Credential |
| --- | --- | --- | --- |
| `ollama-local` | `http://127.0.0.1:11434/v1` | Exact installed ID including its tag, replacing `local-model:tag` | Anonymous |
| `vllm-local` | `http://127.0.0.1:8000/v1` | Served model ID or alias, replacing `served-model` | Anonymous, or your explicit vault/environment reference |

Both use `chat_completions`, streaming, text output and the common
`max_tokens`, `temperature` and `seed` parameters. Each starts with text input
only. For an existing configuration, add the needed profile locally rather
than running `init` again. Declare its image/audio support, response formats
and token limits for the actual model and server. Extra `models` entries keep
their own conservative defaults as described below.

The [Ollama compatible API](https://docs.ollama.com/api/openai-compatibility)
accepts Chat Completions, image parts, streaming and model listing. The local
service does not require a dummy API key. Context size and model retention are
server settings for this connection: use your Ollama configuration, including
`OLLAMA_CONTEXT_LENGTH` and `OLLAMA_KEEP_ALIVE` where needed. A native `/api/chat`
adapter is not part of this client. Video remains timestamped JPEG frames;
audio requires explicit support for the writer's `input_audio` content parts.

For vLLM, the connection follows the
[OpenCode runtime pattern](https://opencode.ai/docs/providers/#runtimes): a base
URL, a readiness check and a model inventory, with capabilities configured
separately. The existing Python client sends generation requests to
`/v1/chat/completions`. The [vLLM server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
must provide a suitable chat template. Set limits to match its context and
multimodal configuration. Server defaults can also come from the model's
`generation_config.json`; omitting temperature uses those defaults.

Check a configured connection from the local administrator tool:

```powershell
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' probe ollama-local
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' probe vllm-local --health
```

`probe` reads the configured `/models` and prints only the allowed IDs and
public capabilities. With `--health`, it first checks `/health` on the same
recipient, preserving any proxy prefix before `/v1`. Health allows three
seconds and at most 8 KiB; model listing allows ten seconds and at most 1 MiB,
each also bounded by the profile's response limit. Redirects and retries are
disabled. A failed health check stops before model discovery.

The probe makes no generation or model-management requests and does not edit
profiles. Authenticated probes use the configured server-side credential;
normal legacy vault-binding migration rules still apply. The node's
**Refresh models** continues to query only `/models`, and the local model
allowlist still controls the choices. A successful probe confirms HTTP access
and listing, not generation quality or multimodal support.

## Save or remove a key on Windows

```powershell
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' key-set deepseek
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' key-delete deepseek
```

`key-set` prompts twice with hidden input in your local terminal. There is no
command-line key argument, node key widget or key-reading HTTP endpoint. Do not put
the key in a workflow, a prompt, a screenshot or a chat message.

Saved keys use Windows [current-user DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
in `api_secrets.enc` alongside the non-secret profiles file. Temporary writes
also contain ciphertext only; replacement is atomic and local writers use a
file lock. No master key is saved next to this file. An invalid file or failed
decryption stops the operation and preserves the original store. Moving to
another user or computer generally requires entering the keys again.

The encrypted record is bound to the profile ID, endpoint, protocol, network,
allowed model IDs and secret reference. Switching between these models, or
changing the default within the same allowed set, does not require another key.
After changing the recipient, profile ID or secret source, enter a key again in
the window or run `key-set` again. The same CLI commands work on Linux with the
server vault key configured.
Legacy records remain readable. Expanding a legacy profile with additional
models upgrades its encrypted binding on first credential use, only when its
original default model and complete destination still match. No plaintext
copy is written. After later changes to an already migrated model set, run:

```powershell
.\.venv\Scripts\python.exe -m minimax_h3_rewriter.api_admin --user-directory 'D:\ComfyUI\user' key-migrate deepseek
```

Credentials are read for each request; changing a saved key does not require
a ComfyUI restart. A `401` is a credential rejection. Connection failures can
instead identify DNS, proxy, operating-system access, refusal/reset or an
incomplete response. These details use fixed messages, never raw exceptions,
headers or proxy credentials. Failed requests are not retried automatically.

This preserves the key and verifies the same profile, recipient, protocol,
network and secret reference. It cannot migrate a key to another endpoint.
The tool's `list` command prints availability, never the key. It does not make
an API request or test billing/access.

## Choose a model within a connection

The **model** selector sits below `profile_id`. **From profile** (the default)
uses that profile's existing `model`, so old workflows retain their behavior.
An explicit choice saves the model ID in the workflow. Selecting another
profile preserves that choice and marks it if it is not allowed there; choose
**From profile** or an allowed ID deliberately. There is no automatic fallback.

The existing top-level `capabilities` and `limits` describe the default model.
Add an optional `models` array for other allowed IDs, each with its own support
and limits. For example, add this field to the DeepSeek profile, keeping its
existing default model and other connection fields:

```json
"models": [
  {
    "id": "deepseek-v4-pro",
    "capabilities": {
      "modalities": ["text"],
      "formats": ["text", "json_object"],
      "parameters": ["max_tokens", "temperature"]
    },
    "limits": {"output_tokens": 8192, "context_tokens": 1048576}
  }
]
```

This is a conservative declaration based on the
[DeepSeek model-list documentation](https://api-docs.deepseek.com/api/list-models/),
checked on 2026-11-07; it does not prove access for an account. Additional entries
default to text input/text output with conservative limits if fields are omitted;
they never inherit vision/audio support from the default model. Do not duplicate
the default ID in `models`. Up to 64 models including the default are supported.
After editing the file, reload the page to reread local choices.

**Refresh models** explicitly reads `<base_url>/models` on the ComfyUI server,
using that connection's credential. Opening or restoring a workflow only reads
local configuration/cache; it makes no provider call. The list is filtered to
locally allowed IDs. Unknown models are not automatically authorized. Only IDs,
availability and validated capabilities/limits reach the selector; router paths,
process arguments and credentials do not. Requests allow 10 seconds and at most
1 MiB (or the profile's smaller response limit), without redirects or retries.
Refreshes have a three-second cooldown; availability metadata is cached for
60 seconds. Network errors appear next to the selector and preserve the choice.

Available metadata can narrow locally declared modalities or token limits.
Missing metadata grants no extra support. An unavailable allowed model remains
visible, and a fresh cached server list stops its execution before HTTP or key
access. Without a fresh list, the admin declaration applies and the API can
still reject a model; there is no fallback. Repair pins the same model/settings.
Reports and saved prompt information record the model actually used. Repeating
a kept prompt reports its original model, even after the selector changes.

For a single-model llama-server, configure its served alias (`--alias`) and
keep the default; choosing another name cannot select a different GGUF. For a
[llama.cpp router](https://github.com/ggml-org/llama.cpp/tree/master/tools/server#using-multiple-models),
add its configured model IDs to the local allowlist. A generation request sends
the chosen ID in `model`; the router may load it under its own configuration.
The node makes no load/unload/download requests and launches no processes.
An external proxy/launcher must expose a compatible model list and routing;
the node does not assume that it behaves like llama.cpp's router.

DPAPI protects the saved copy. A process running as the same OS user can access
that user's decrypted data; this is not isolation from arbitrary local Python
code. Python strings cannot be guaranteed erased from memory. The node keeps
credentials out of its outputs, reports, session memory and workflow data.

## Explicit environment credentials

For a headless system or an OS without this Windows backend, change a profile's
reference to:

```json
"secret": {"kind": "environment", "name": "MY_DEEPSEEK_API_KEY"}
```

Set that variable in the environment that launches ComfyUI. Only the explicitly
named variable is consulted. The pack does not persist its value or describe
this mode as encrypted storage. There is no automatic fallback from a vault
to environment variables or a plaintext file. macOS Keychain and Linux system
stores have an interface for later integration; they are not implemented yet.

## Write text

Choose `profile_id`, keep `task = T2VA`, and enter the raw request, duration and
aspect ratio. With an empty `system_prompt`, the node reads MiniMax's guide,
fetching it if missing and `auto_download_guide` is enabled. A custom system
prompt replaces the whole system message and skips guide access.

The reference strip is the first control, followed by the task buttons,
aspect-ratio picker and duration slider used by Universal Writer. Click
`+ instr` on a reference to edit its instructions and descriptions.

Connect **MiniMax-H3 API Writer Options** to `options` for these settings:

| Setting | Default |
|---|---|
| `max_tokens` | 4096 |
| `response_format` | text |
| `temperature` | -1 (server default) |
| `timeout_seconds` | 180 |
| `auto_download_guide` | true |
| `video_sample_seconds` | 1 |
| `video_max_seconds` | 15 |
| `batch_video_fps` | 24 |
| `audio_sample_rate` | 16000 |
| `repair_once` | false |

A connected Options node supplies all ten values. Without it, a new writer
uses these defaults. Existing saved API workflows retain their scalar values;
the frontend hides those legacy controls, and connecting Options overrides them.
Endpoint, model and credential settings remain in the local connection profile.

- `validation = h3` splits sections and runs the pack's H3 checks. Findings are
  advisory. Missing fields remain visible rather than being invented.
- `validation = custom` returns plain final text, skips H3 checks and leaves
  section outputs empty. Use it when your system message targets another format.
- `response_format = text` preserves the final answer as received. `json_object`
  or `json_schema` requires supported profile capabilities and exactly the H3
  string fields; the node assembles them into labelled H3 text. No silent format
  fallback occurs.
- `temperature = -1` and `seed = -1` omit those parameters. Otherwise the profile
  must explicitly support them. An API seed does not promise reproducibility.
  New API writers use `control after generate = fixed` to keep the default
  `seed = -1` across requests. For DeepSeek, keep both settings: `randomize`
  produces a positive seed that its profile rejects before sending a request.
  Existing workflows keep their saved control mode; change it to `fixed` and
  set `seed` back to `-1` if the profile does not support seed.
- `max_tokens` is per request and bounded by the profile. Some models count
  reasoning against this budget; increase it if generation ends before final
  text. Truncated, refused or tool-call answers are errors.
- `timeout_seconds` limits the complete HTTP request, including a slow body.
  ComfyUI cancellation closes our stream. A server that observes the disconnect
  can stop generation; a proxy may notice only on its next attempted write.
  During a long prompt prefill this can be delayed until output starts. Other
  services may still finish or bill the submitted request. Failed requests are
  not automatically retried. The pack never stops the whole server or other
  clients' work to cancel its own request.
- `repair_once` in API Writer Options is off by default. Enabling it permits **one extra, potentially
  paid request** for repairable H3 findings. It does not retry transport errors.

Outputs include the full prompt, H3 sections, separate reasoning, findings and
a JSON report with attempt counts, elapsed time and known token usage. Missing
usage stays unknown. Connect `prompt` to the H3 generator's prompt input.

## Use images

Connect an IMAGE to `ref_0`; another free slot appears, up to 12 references.
Each square in the strip represents a connected batch. Drag squares to change
their order, click an image label to cycle Picture, Subject and Video, or click the
image to disable it. The instructions button adds text sent with that reference.
Disabled references are neither encoded nor sent. T2VA ignores all references
and returns an empty `references` bundle, with a note in the report.

| Task | Enabled Picture references |
|---|---|
| I2VA | One, the first frame |
| FL2VA | Two, first and last in strip order |
| L2VA | One, the last frame |
| Ref2VA | At least one reference; Picture, Subject, Video and Audio have separate numbering |

The selected profile must declare `image` in `capabilities.modalities`, and its
model must actually support vision. The llama-server template stays text-only;
enable images only after loading a compatible model and projector yourself.
DeepSeek's `deepseek-flash` accepts inline images through its
[documented vision API](https://api-docs.deepseek.com/guides/vision/).

The node sends JPEG copies directly in the user message. `image_max_side`
defaults to 1024 pixels; resizing keeps aspect ratio, never crops or enlarges.
Copies use quality 90; alpha is composited onto white. Every frame in an IMAGE
batch is sent under that batch's single reference label. The ceiling is 64
images across all batches and 100 million source pixels per image. Exceeding a
limit fails explicitly, without dropping images or starting a partial request.
No local captioner, temporary image files, public image URLs or Files API uploads are used.

The JSON report's `media` lists original/sent sizes, frame positions, byte sizes
and resizing. The complete request, including base64 and prompts, must fit the
profile's `limits.request_bytes` (the templates default to 1 MiB). For several
images, reduce `image_max_side` or raise that limit in the local profile within
the server's own limits. Nothing is silently resized again to fit a request.

The final `references` output preserves the original objects. Connect it to
**MiniMax-H3 Reference Slots** or **Reference Encode** so H3 receives the same
numbered assets as the prompt. Subjects follow Pictures in the generator's
picture bundle. The first eleven text outputs retain their previous positions.
The node also shows the configured recipient and the data to be sent before a
run; viewing that information does not contact the LLM or resolve credentials.

## Use video and audio

The same `ref_0` and subsequent sockets accept VIDEO, AUDIO and RefMod outputs.
VIDEO is always Video; AUDIO is always Audio. An IMAGE batch can be assigned
the Video role. RefMod kinds come from their loader; only an image RefMod can
switch between Picture and Subject.

Video is sent as timestamped JPEG frames under one `Video N` label. It uses
the same `image_url` transport as pictures, so the profile needs `image`, not
a provider-specific video endpoint. This shows selected frames, not every
motion in the source. DeepSeek can receive these frames through its vision
API. A profile declaring only text will fail before any request.

| Control | Effect |
|---|---|
| `video_max_frames` | At most this many frames per clip, default 8; 64 images total across all references |
| `video_sample_seconds` | Requested interval, default 1 second; when the frame cap is reached, samples spread across the selected window |
| `video_max_seconds` | Window from the active trim start, bounded by this value, target duration and H3's 15-second reference limit |
| `batch_video_fps` | Clock for an IMAGE batch assigned Video, default 24; actual VIDEO inputs retain their presentation times |
| `image_max_side` | Maximum side of both picture and video JPEG copies, preserving aspect ratio |
| `audio_max_seconds` | Standalone audio/voice window, default 30 seconds; clip sound follows its video's selected window |
| `audio_sample_rate` | Rate of mono 16-bit PCM WAV copies, default 16000 Hz |

Click a connected VIDEO's label to include its sound: the label turns purple
and shows its additional Audio number. With sound off, **no audio attachment
is created**. The writer uploads neither the original video container nor its
muted/re-encoded equivalent, so an off soundtrack cannot travel inside it.
A silent clip gets no Audio number even with the switch on, and the report
explains that. A track that exists but cannot be decoded causes an error.

Clip sounds precede standalone sounds in Audio numbering, matching Reference
Slots and Reference Encode. The `references` bundle preserves the original
objects and carries the clip sound flags. Sampling, resizing, cropping an API
audio copy or resampling never modifies those originals.

Audio uses `input_audio` with inline base64 WAV. This form is documented by
[llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
and [vLLM](https://docs.vllm.ai/en/latest/features/multimodal_inputs/#audio-inputs).
The selected profile must declare `audio`, and its actual model, projector
and server must accept audio in Chat Completions. For example, llama.cpp lists
Qwen2.5-Omni as supporting audio and images. A server accepting images alone
does not qualify. The DeepSeek template declares no audio input: switch clip
sound off there, use descriptions mode, or select an audio-capable profile.
Adding a modality to a profile does not add that capability to its model.

Audio/video preparation uses PyAV (`av`), declared in the installation
manifests. Nothing is installed during node execution. Text/image-only use
does not import PyAV. File VIDEO inputs are streamed from their connected
local source with nested network protocols and playlist formats disabled;
remote URLs, UNC paths and raw filename strings are rejected. VIDEO inputs
already holding image components use that batch. File inputs have a 2 GiB
ceiling; decode loops have a 20000-frame ceiling. Trim large clips upstream.
ComfyUI accumulated/concatenated `VideoFromList` inputs must first be saved
and reloaded as a local clip; otherwise their component getter would decode
the whole concatenation before any frame limit could apply. Descriptions mode
with clip sound off also works without materializing such a video.
Audio requires one waveform batch; multiple batches are rejected rather than
silently selecting the first. Preparation checks cancellation between decoded
frames and audio chunks and leaves no detached decoder running.

The report adds `media_mode`, `preparation_seconds`, video sampling/window
metadata, actual frame times and audio rate/channel/duration/byte changes.
`elapsed_seconds` measures API writing; preparation is reported separately.
Request byte limits include prompts and every base64 copy. A limit error
does not send a partial request or start an automatic retry.

## Use RefMods and descriptions

Connect visual and/or audio outputs from **MiniMax-H3 RefMod**. In native mode,
visual media must already be decoded by that loader's `vae` input, and a voice
by `audio_vae`. The API writer does not load a VAE or a caption model. A missing
decode stops with the slot and loader input to connect. Visual RefMods use
their stored/decoded pictures and timestamps; photo stacks keep their saved
hint that these are separate photographs. Matching visual/voice halves keep
their saved descriptions and related slot IDs in the manifest. The generator
receives the original RefMods, including their conditioning and strength.

If a visual RefMod has no stored thumbnail, its chip gains a preview after
native reference preparation, before the LLM replies. Up to four thumbnails
come from the JPEG copies already prepared for the request, preserving the
Video label for clips and photo stacks. Hover to cycle their frames. This
does not decode the original reference again or load an extra model.

Choose `media_mode = descriptions` to send text descriptions instead of any
media. Open a square's instructions/describe button and fill its description;
for a VIDEO with sound on, also describe its sound. A RefMod can supply its
saved appearance/voice description without a VAE decode. Every enabled
manifest component must have a description. Instructions alone do not count,
and missing text never triggers a hidden captioner or a media fallback.
An existing captioner can produce descriptions upstream; provide the result
through the editor or a wired `reference_descriptions` JSON string:

```json
{
  "ref_0": {"text": "A woman walks down a wet street.", "sound": "Rain and footsteps."},
  "ref_1": "A low, soft voice."
}
```

In this mode, the model is explicitly told that no media are attached.
Description text also accompanies native media when supplied. Disabled slots
are ignored; their pixels, audio and RefMods are not decoded. The JSON fields
remain ordinary fallback widgets if the frontend extension is unavailable.
Saved widget values are restored by name or their recorded order. Workflows
from before the controls moved retain their values, references and links;
old image workflows default to native mode.

## Reuse and save prompts

`bypass` passes the original prompt and enabled original references through
without reading the guide, opening video/audio sources or making an API
request. Section, reasoning and findings outputs are empty; the report records
`source = bypass` and `requests = 0`. Bypass takes precedence over `repeat_last`
and leaves its kept answer intact. Both switches also have the same clickable
badges as Universal Writer.

After a successful run, `repeat_last` returns the kept prompt without calling
the API. With nothing kept yet it performs one normal run. Current enabled
references still pass through; changing those references does not rewrite the
kept text. The report identifies session/library reuse with `requests = 0`.

Use **Save the last prompt**, **Edit the last prompt** or **Prompt library** on
the node to keep or choose text. The existing library stores the result,
reference thumbnails and selected non-secret run settings under the ComfyUI
user directory. The API node does not add the key, credential source, endpoint,
raw request, system message or encoded transport images to that record. Session
memory lasts until ComfyUI restarts; saved library records persist. A selected
library record is used only while `repeat_last` is enabled.

The [contract](api_writer_contract.md) documents validation and transport limits.
Cloud tests require an ordinary API key configured locally by the user; the
test suite itself uses loopback servers and dummy credentials only.
