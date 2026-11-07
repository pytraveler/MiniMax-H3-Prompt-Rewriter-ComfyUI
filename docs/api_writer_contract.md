# API Universal Writer: contracts and execution

`MiniMaxH3ApiUniversalWriter` is an additional ComfyUI node for all five H3 tasks,
with image/audio input, sampled video frames, decoded RefMods and text output.
It sends the guide or a complete custom system message and the raw user prompt
to an administrator's configured Chat Completions server. See
[setup and use](api_writer.md). Existing writers keep their execution paths.

The implementation separates these responsibilities:

| Module | Responsibility |
|---|---|
| `api_contract.py` | Versioned job, model capabilities, reference manifest and API result |
| `api_profiles.py` | Non-secret server configuration, endpoint validation and profile selection |
| `api_client.py` | Request validation, Chat Completions transport and safe error classification |
| `api_config.py` | Strict local profile loading, configured user directory and atomic writes |
| `api_admin.py` | Local CLI, shipped with the Registry package; the repository also has a `tools/api_profiles.py` wrapper |
| `api_secrets.py` | Current-user DPAPI, explicit environment resolution and system-store interface |
| `api_models.py` | Allowlisted discovery, bounded metadata cache and per-model settings |
| `api_model_routes.py` | Local/cached choices and an explicit read-only provider refresh |
| `api_management.py` | Revision-checked profile edits, one-use encrypted key transfer and coordinated vault/config writes |
| `api_management_routes.py` | Local-origin access or explicit HTTPS origin with administrator authorization |
| `api_text.py` | H3 section parsing, checks, reports and optional single repair |
| `api_media.py` | Strict image strip state and in-memory JPEG preparation |
| `api_multimedia.py` | Mixed reference metadata, bounded video/audio decoding, WAV copies and explicit descriptions mode |
| `api_options.py` | Immutable, validated generation and transport settings; separate API Writer Options node |
| `api_writer.py` | ComfyUI node, original reference bundle, library, reuse and cancellation |

Importing them does not read profiles, inspect credentials, download guides or
contact servers. `INPUT_TYPES()` reads non-secret profiles to build its dropdown.
`requests`, `aiohttp`, `av` and `cryptography` are declared in both installation manifests. PyAV
is imported only during audio/video preparation. The API
transport uses aiohttp for cancellation and a total deadline; it reuses
`download._SystemTrust.context()` with certifi added, as the requests adapter
normally does. Certificate and hostname verification stay enabled. Existing
download/model transports are unchanged.

## Profiles

[api_profiles.example.json](../minimax_h3_rewriter/api_profiles.example.json)
contains two templates and no keys:

- `llama-server-local`: `http://127.0.0.1:8080/v1`, model alias `local-model`.
  Replace the alias with the one served by an already running llama-server.
  Capabilities default to text, with optional JSON output and seed. A different
  model or server build may require editing those declarations. The schema
  envelope follows llama.cpp's [Chat Completions parser](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-common.cpp),
  checked on 2026-11-05.
- `deepseek`: `https://api.deepseek.com`, `deepseek-flash`, a vault record named
  `deepseek`. The record is a reference, not a credential. The template declares
  text/image input and text/JSON-object output from the
  [official model metadata](https://api-docs.deepseek.com/api/list-models/) and
  [Chat Completions documentation](https://api-docs.deepseek.com/api/create-chat-completion/),
  checked on 2026-11-05. Images use the standard inline `image_url` blocks.

`ProfileSet.from_dict()` accepts a version-1 document with a `profiles` list.
It rejects unknown fields, duplicate IDs, unsupported protocols and invalid
endpoints. `api_config.load_profiles()` loads the fixed `api_profiles.json`
under ComfyUI's configured user directory. The local CLI and the authorized
API providers window can edit this configuration. The read-only
`GET /minimax_h3_rewriter/api/profiles` returns ID, display name, recipient,
model, network mode and modalities for the node's disclosure. It omits the
secret reference and never calls a provider or resolves/decrypts credentials.

Each profile contains its endpoint, model, network mode, capabilities, limits,
the boolean `stream` (default `true`) and a `SecretRef`. Allowed credential kinds are `none`, `vault` and
`environment`; the latter two contain an identifier only. `SecretResolver`
implements the client's `authorize(profile)` callback. It consults only the
chosen reference, without credential discovery or fallback. Stored keys are
bound inside the encrypted payload to profile ID, protocol, recipient, network,
allowed model set and secret reference. Legacy single-model records remain
readable. When their original default and destination match, adding allowed
models upgrades that binding atomically on first use. Later model-set changes
use the local `key-migrate` command; recipient changes require key entry again.
Profile enumeration and cache stamps do not decrypt keys.

The editor's `POST /api/manage/{open,stage,commit,test,close}` uses fixed server-owned
files. Editing never calls a provider; only explicit `test` reads `/models`.
All operations require a matching, non-null
Origin and reject cross-site fetches. Direct loopback clients may edit locally.
Remote access additionally requires the exact HTTPS `MINIMAX_H3_API_ADMIN_ORIGIN`
and constant-time verification of `X-MiniMax-H3-Admin` against the server's
`MINIMAX_H3_API_ADMIN_TOKEN`. A configured token also protects local editing.
Only a loopback proxy with the configured public Host may forward HTTPS. These
checks do not authenticate the rest of ComfyUI and do not isolate mutually
untrusted users sharing its OS account or same-origin custom-node scripts.

The body limit is 128 KiB, uncompressed JSON with strict fields; responses are
`no-store`. At most eight in-memory editor sessions live for 15 minutes. A
session is bound to its Origin. Stage validates one profile operation against a
SHA-256 revision of the current file. Commit checks that revision again under
the same interprocess lock as CLI profile/key changes. No endpoint accepts a
path, arbitrary headers or a new reference to another profile's vault record.

For replacement, stage creates a temporary RSA-3072 keypair and a 90-second
one-use challenge. Web Crypto encrypts the API key with a fresh AES-256-GCM key
and 96-bit IV, then wraps the AES key using RSA-OAEP/SHA-256. Both OAEP's label
and GCM's additional authenticated data are `minimax-h3-api-key:v1:<challenge>`.
The staged operation stays on the server; commit contains only the session,
challenge and envelope. Consume the challenge before decryption or writing.
Reject malformed/truncated/tampered envelopes and replay without returning
crypto exceptions or plaintext. Saved keys are never returned to the browser.

Connection tests stage a separate `test` operation. Its challenge cannot be
committed as a save, and a save challenge cannot be consumed by `test`. Tests
read the exact current draft after the same validation and recipient-binding
checks, using a typed encrypted key, an explicit environment reference or the
previous vault binding. Reading a legacy vault record does not migrate it.
Nothing is persisted, and discovery/inference caches are not touched. Only
`GET /models` is sent, with 3-second connect/10-second total bounds, the existing
response limit capped at 1 MiB, no redirects or retries. Per-editor single-flight
and a three-second cooldown apply. Responses contain only allowed model IDs,
selected-model availability, elapsed time and whether a credential was sent.
Provider errors use the client's fixed safe messages and numeric HTTP status,
never raw response bodies or exception text. Success does not prove credentials
were checked by a public metadata endpoint, or validate generation/media support.

Windows keeps the existing DPAPI format. Linux/other headless servers can use
`ServerKeyStore` with a random 32-byte master key supplied in base64 through
`MINIMAX_H3_VAULT_KEY`. Its distinct versioned magic authenticates AES-256-GCM
storage with a fresh 96-bit nonce. The master key is not persisted by the pack;
there is no automatic generation, weak derivation, desktop-keyring requirement
or plaintext fallback. Missing/wrong keys preserve ciphertext. The two
administration secrets cannot be selected as model-credential environment
references. The file location, record bindings, size limits and locks are shared
with DPAPI. New Unix files/directories use 0600/0700.

Same-recipient model-set changes rebind only the matching old record. Recipient
or credential-source changes cannot reuse a saved key silently. Vault ciphertext
is replaced before the profile file; if the profile write fails, restore the previous
ciphertext (and report separately if restoration fails). The two-file update is
not crash-atomic: a crash between replacements may require key entry again,
but binding checks prevent sending an old key to a changed recipient. Plaintext
temporary files and transport retries are not used. See the
[user setup](api_writer.md#remote-comfyui-and-linux-storage) for bootstrap and
the limits of browser-side encryption.

The default two templates are unchanged. Local CLI
`init --include-local-runtimes` optionally adds anonymous, text-only Ollama
(`127.0.0.1:11434/v1`) and vLLM (`127.0.0.1:8000/v1`) templates. They contain
placeholder served IDs and require local configuration. WSL is another host
for the configured HTTP server; the pack never executes WSL or service commands.

Local CLI `probe <profile_id>` reads the configured model inventory.
`--health` first checks the same recipient's `/health`, preserving a prefix
before `/v1`, with a three-second deadline and an 8 KiB ceiling (or the smaller
profile limit). Empty 2xx health responses are valid; their body is never
returned. A failed health check prevents discovery. Both GETs use the normal
TLS/proxy/no-redirect policy and resolve the configured credential once.
The printed result is the existing filtered public model list plus a health
status. This command performs no inference or profile administration; normal
legacy encrypted-binding upgrades during credential resolution still apply.
No browser health route is added, and node metadata refresh remains `/models`
only. A successful probe does not establish media support or writing quality.

A job carries `profile_id` and optional `model` (empty uses the profile default).
It cannot supply an endpoint, headers, API key,
credential reference or extra request body. `ApiClient` resolves the ID through
its server-owned `ProfileSet`; there is no fallback to a different profile.

The optional profile `models` array contains other allowed `ModelSettings`.
Each declares its own capabilities/limits, with conservative defaults rather
than inheriting the default model's modalities. The default ID is not duplicated.
The effective selection determines request validation, media byte ceilings,
payload model and result. Authorization receives the original connection
profile. A client's first completion pins selected settings for its repair too.

`GET /api/models?profile_id=...` (under the pack route prefix) returns only
local declarations/fresh cached metadata. `POST /api/models/refresh` accepts
only a small JSON object with `profile_id`, then explicitly reads the configured
`/models` using the same TLS, proxy, redirect and credential policy as completions.
It accepts no credential values or profile edits. Discovery permits 10 seconds, at most 1 MiB,
1024 server entries and 64 locally allowed IDs. Cache entries expire after
60 seconds; refreshes have a three-second cooldown and the cache is bounded.
Only allowed IDs and validated known metadata are retained; paths, names,
router arguments and unknown fields are discarded. Metadata only narrows
local support/limits. Missing metadata never grants new support. Fresh absence
rejects the choice before completion/key access; without fresh metadata, the
administrator declaration applies. No inference/model-management call is made
by discovery. Workflow loading and graph definition reads never refresh it.

Network modes are explicit:

| Mode | Accepted recipient |
|---|---|
| `loopback` | Literal loopback IPv4/IPv6, HTTP or HTTPS |
| `lan` | Explicit private IP, HTTP or HTTPS; no link-local/unspecified address |
| `cloud` | HTTPS with a public address or administrator-managed DNS name |

Credentials in URLs, query strings, fragments, control characters, encoded
paths and path traversal are rejected. DNS and proxies for cloud connections
remain part of the administrator's configuration. Local connections bypass
ambient proxies. Automatic `.netrc` authentication is disabled for all profiles.
All HTTP redirects are rejected without following them or forwarding a key.

## Jobs and references

`make_job()` accepts the original prompt, task, duration, aspect ratio, profile
ID, explicit guide text or custom system prompt, generation settings and ordered
`ReferenceSpec` metadata. An empty system prompt selects the H3 guide. A custom
system prompt replaces it entirely and requires explicit `h3` or `custom`
validation. The original prompt and custom system text retain their whitespace.
There is no automatic guide download in the contract layer.

Job serialization has version 1. `WriterJob.from_dict()` checks the field set
and types; the endpoint and secret source are outside this schema. All input
is data, including prompt text resembling Python, shell commands or HTML.

References arrive after the caller has arranged the strip, removed disabled
slots, and checked whether clip sound really exists. Metadata retains the
slot ID, role, kind, description, instruction, RefMod origin and related slot
IDs. Original media/RefMods remain with the caller for the downstream reference
bundle; no local filename, tensor or file content is serialized by this layer.

The manifest uses the existing `references.turn_order()` convention. Clip sound
gets an `Audio N` before its `Video N`, and every clip sound is numbered before
standalone audio. Picture, Subject, Video and Audio have separate counters.
Subjects remain after pictures in the downstream picture bundle. The caller
must use this manifest for media preparation, H3 checking and conditioning.

All five H3 tasks are represented. Picture counts and H3 reference capacity are
checked independently of the API's limits. T2VA ignores connected references
and previous reference context and emits a notice. Other tasks carry previous
context as text; it neither creates attached media nor offsets current labels.

There are two explicit media modes:

- `native`: model capabilities and H3 counts are checked before preparation.
  JPEG copies are supplied separately to `ApiClient(images=...)`; the versioned
  job remains metadata-only. Every image/batch frame must match its manifest
  slot in order. Audio, video and RefMod transport still fail explicitly.
- `descriptions`: every manifest entry needs its own nonempty description,
  including a clip's sound. All descriptions, labels and instructions are sent
  in one request. No captioner is invoked and no fallback is automatic.

Generation settings are typed: `max_tokens`, optional `temperature` and optional
`seed`. Unsupported options fail before authorization or HTTP. Output tokens,
reference count, encoded request bytes and received response bytes have profile
limits. `context_tokens` records model metadata; it is not a tokenizer or a
guarantee that arbitrary input fits the model's context window.

Response formats are `text`, `json_object` and `json_schema`, checked against
profile capabilities. H3 JSON requests use the selected task's fields and an
`alignment` field for I2VA/FL2VA/L2VA. Custom validation uses plain text. Schema
to H3 conversion, semantic checks and the optional repair request are handled
by `api_text.py`. The node exposes all five tasks with mixed references.
Video is explicitly sampled to timestamped image parts in this adapter;
capability checks therefore require `image` for Video entries and `audio` for
each actual Audio entry. Opaque native video uploads are not implemented.

## Requests and results

Native images are embedded in the user message as base64 data URLs, preceded
by their reference labels. User and system text retain their original bytes.
The transport accepts encoded images from the in-memory preparation path, not
workflow paths or URLs. All batch frames are retained under one manifest entry;
frame and byte limits fail rather than truncate. Image copies never reach the
reference output or session record. The optional repair uses the same copies.

`api_multimedia.py` binds decoded RefMod pictures/voices and VIDEO/AUDIO inputs
to the same manifest. A sounding clip contributes an Audio component before
its Video component; all clip sounds precede standalone Audio numbering.
Absent tracks produce a notice and no phantom label. An undecodable track is
an error. RefMod saved appearance/voice text and paired slot IDs are retained;
latents and original filenames never enter the HTTP payload.

Each native audio component has exactly one `PreparedAudio`, encoded as mono
16-bit PCM WAV in an `input_audio` part. Each Video entry has a complete set
of `PreparedVideoFrame` copies with increasing actual presentation timestamps.
These are internal objects, never graph-supplied filenames or URLs. The client
rejects missing, duplicate, extra or mislabelled copies before authorization.
Repair retains the same image/audio copies. With explicit descriptions mode,
no attachments are allowed and each enabled component needs nonempty text.

File video decoding uses PyAV on a connected local file handle or BytesIO.
Nested protocols are disabled and accepted demuxers exclude playlists. UNC
paths, remote URLs and relative paths are rejected. Handles/containers close
on errors and cancellation; the caller's BytesIO cursor is restored. Decoded
file frames are resized and encoded before retention, keeping retained copies
within the request budget. Component VIDEO inputs already own their decoded
batch. The writer preserves trim, presentation times and ComfyUI file crop/
rotation. Video windows respect the target duration and H3 reference ceiling;
audio conversion reports its crop/downmix/resampling. Decode loops yield and
check interruption without subprocesses or detached worker threads.

The original scalar inputs remain declared for existing API workflows.
`reference_layout`, `reference_instructions`, `image_max_side`, `repeat_last`
and `library_pick` originally followed those inputs.
The `references` output is appended after the original eleven text outputs.
The image strip uses DOM widgets and the existing strip/preview helpers;
ordinary inputs remain available if the extension is unavailable. Other
writers' declarations and execution are unchanged. The existing badge helper
also recognizes the API writer.
The stage-5 `media_mode`, `reference_descriptions`, video sampling and audio
copy controls follow that existing prefix. Existing `ref_n` sockets broaden
to the IMAGE/VIDEO/AUDIO/RefMod union with their names and links preserved.

The frontend places the strip first and uses the shared task, ratio and duration
controls. `api_writer_migration.js` restores widget values by name or recorded
order, with the original positional order as a fallback. Existing durations
above the new slider's initial offered maximum retain their value and ceiling.
The ten generation/transport controls, including `repair_once`, appear in `MiniMaxH3ApiWriterOptions`.
Their legacy scalar inputs are hidden on the writer and remain effective until
typed `H3_API_WRITER_OPTIONS` is connected. Connected options override all ten
scalars together; they cannot contain endpoints, headers, models or key sources.
The repair control is appended to the Options widgets and optional in its input
schema, preserving saved positions and API prompts created with nine settings.
It defaults to false; without connected Options, the writer's saved repair flag
remains effective.

`bypass` is evaluated before profile loading, options resolution, guide access,
clip sound probing and media conversion. It returns the stripped original
prompt, empty section/reasoning/findings strings, a zero-request report and
the enabled original reference bundle. It takes precedence over library/session
reuse without replacing the kept answer. Normal request preparation still
validates both local options and the selected profile's capabilities/limits.

Requests use `stream: true` and `stream_options.include_usage: true` by default,
with one choice and no tools or function calls. SSE deltas are accumulated into
the same final text/reasoning/usage envelope; partial text is not published.
The parser accepts LF, CRLF and CR line endings, fragmented UTF-8, comments,
multiline data and a final usage-only event. It requires a completed stop and
`[DONE]`, rejects other choices, tool calls, refusal and malformed events, and
counts all decompressed response bytes against the profile limit. SSE pings do
not extend the total deadline. Missing final usage remains unknown.

An administrator can set `stream: false` for an API that requires a unary JSON
request; `stream_options` is then omitted. Existing version-1 profiles without
this field default to streaming. A complete JSON response from a server that
ignores streaming remains readable, but its upstream cancellation is not
guaranteed. No fallback request is made. Transport selection does not change
the credential's recipient binding.

The client makes one attempt. It does not retry after 429, server errors,
connection failures or ambiguous timeouts; a timed-out request may already have
been processed remotely.

Connection and read-idle timeouts default to 5 and 60 seconds in the core.
A separate total HTTP deadline defaults to 180 seconds, including body reading;
slowly arriving bytes do not extend it. The node's timeout sets the total and
read-idle budgets. A cancellation poll runs every 50 ms while awaiting headers
or content, cancels the request task and closes its connection. There is no
detached worker thread. The remote server may still process or bill a submitted
request after the client disconnects. Streaming lets a forwarding proxy observe
a failed downstream write before the entire generation finishes; a buffered
unary response can hide the disconnect until the end. A proxy without a
separate disconnect monitor can still delay cancellation during prefill until
the upstream sends output. This cancels one connection, never a server process
or all of its slots. Each explicitly enabled repair has its
own deadline. Guide retrieval precedes the API call and uses the existing guide
downloader's timeouts.

`ApiResult` carries final text, separate reasoning, token usage, elapsed time,
the configured model/profile IDs, manifest and notices. Missing usage remains
unknown. A result is a completed API answer, not proof of valid H3 output.
Neither a response body nor authorization headers are included in error text.
There is no request/response logger or provider conversation history. The node
keeps final prompt outputs and safe reference thumbnails for its existing
library/session integration, without raw/system prompts, transport copies or
credential details.

Empty final answers, refusal, tool/function calls, incomplete finish reasons,
invalid JSON envelopes and oversized responses raise `ApiError`. Vendor errors
are reduced to fixed error codes without their exception chains. Model text
remains text. A final answer or reasoning that echoes the actual authorization
token is rejected before it reaches node outputs.

## Reading an answer

Plain `text` output is retained as returned, with fields split by the existing
`fields` helper. Missing sections or H3 semantic problems produce advisory
findings via `checks.review()`, matching the pack's existing behavior.
`json_object` and `json_schema` output must contain exactly the requested
nonempty string fields; duplicates, extra keys, fences and non-finite numbers
are errors. These strings are compiled into ordinary labelled H3 text. Neither
mode executes content. `custom` validation returns final text unchanged and
leaves H3 section outputs empty.

Repair is disabled by default. Opting in permits at most one additional request
for fixable findings, skipped if the format is too incomplete. The first answer
is retained unless the second reduces the existing check score. Refusal,
transport errors, truncation or invalid JSON on the first call do not trigger
repair. Cancellation propagates. Failed repair attempts remain counted, and
aggregate usage stays unknown when a request has no usage result.

The JSON report contains configured profile/model IDs, task, validation/format,
attempt/completion counts, total elapsed time, known aggregate token counts,
manifest, findings and notices. It contains no endpoint, key, request headers,
system message or raw prompt. Preparation duration is separate from API elapsed
time. The report includes sampling windows/timestamps and audio-copy changes.
The separate API providers window does not add serialized node widgets or alter
the existing writers' paths.

The tests in `tests/test_api_writer.py`, `tests/test_api_stream.py`,
`tests/test_api_text.py`, `tests/test_api_images.py`, `tests/test_api_multimedia.py`
and `tests/test_api_secrets.py` exercise HTTP on loopback, image/audio identity,
real container decoding and trim, VFR timestamps, sound exclusion, RefMod pairs,
descriptions, byte limits, node execution,
parsing, reuse, deadlines, cancellation and dummy-key storage. They use no real API keys or
paid endpoints. Windows DPAPI tests require an ordinary current-user process;
a restricted sandbox may not have access to the OS master key.
`tests/test_api_management.py` also covers origin/authentication checks, real
test TLS, encrypted envelopes/replay, conflicts, rollback and server-key AEAD.
These use only dummy secrets and temporary directories. The local
security review and Registry snapshot are kept outside the repository as
required by stage 0; release artifacts still need their stage-6 checks.
