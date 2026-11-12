# API Writer security review notes

This document describes the API Writer's review surface and the checks to run
before publishing. It is not a Registry approval or an audit of every node in
ComfyUI. The existing local writers retain their own process and file access.

## Registry requirements and current status

The [Comfy Registry standards](https://docs.comfy.org/registry/standards) were
checked on 2026-11-07. They prohibit built-in `eval`/`exec`, runtime package
installation through subprocesses, and obfuscated code. Encrypting credential
data leaves this implementation's Python and JavaScript source available for
review. Dependencies are declared in `pyproject.toml` and `requirements.txt`;
the API path does not install packages, launch a model server or execute model
output. `model.eval()` and JavaScript `RegExp.exec()` are unrelated operations.

On 2026-11-12, `tools/registry_status.py --version 0.35.0 --tries 3` confirmed
that **0.35.0 is not published yet**. The node listing is Active; the latest
published version is **Flagged 0.34.0**, with the policy verdict
`policy-v0.8: code-execution`. Version 0.33.0 has 12 informational scanner
findings and no policy verdict. Local tests neither remove a published flag
nor predict the candidate's Registry verdict. The status tool returns 1 when
an explicitly requested candidate version is not listed.

The 12 findings map to these existing operations (line numbers are from the
published 0.33.0 report):

| Report location | Count | Operation and boundary |
|---|---:|---|
| `catalog.py:833,835` | 2 | Desktop `open`/`xdg-open` opens the pack-selected model catalogue path. Argument lists, no shell; the request does not supply a command or path. |
| `guides.py:192,194` | 2 | Desktop `open`/`xdg-open` opens the configured guide directory. Argument lists, no shell. |
| `runner.py:234` | 2 | Starts the separately selected local llama.cpp executable with an argument list and an environment copy; Linux prepends its library directory to `LD_LIBRARY_PATH`. The API Writer never calls this launcher. |
| `discovery.py:359` | 1 | Reads `HF_HOME` to discover the local Hub cache; does not write the variable. |
| `download.py:73` | 1 | Reads the operator's `HF_ENDPOINT` for the existing download mirror. |
| `llamacpp.py:259` | 1 | Reads the operator's explicit executable-directory override and validates candidates. |
| `ollama_store.py:102` | 1 | Reads the configured local model-store path; does not obtain Ollama account credentials. |
| `server_engine.py:77` | 1 | Reads the local engine choice from a fixed set. |
| `server_engine.py:100` | 1 | Binds a temporary loopback socket to choose the existing local engine's port. |

These explanations identify inputs and effects; they do not establish that all
legacy HTTP routes are authorized for Internet exposure. In particular,
desktop-open, local downloads, model-list editing and other installed packs
remain outside the new provider editor's administrator-token boundary.

## Full Qwen rewriter runtime

Version 0.35.0 adds full LightX2V Qwen SFT/GGUF checkpoints. The SFT path uses
the existing Transformers loader with `trust_remote_code=False`. The GGUF
path starts and owns a local `llama-server` through the existing server helper,
with an argument list and no shell. Cancellation, failure and unload close
that owned process. Reference audio is supplied as explicit text descriptions;
these vision checkpoints receive no waveform.

The conversion and model-card scripts under `tools/` are maintainer commands,
not runtime imports. Registry packages exclude `tools/`; GitHub release ZIPs
include them. The model source revisions and conversion hashes accompany the
separately hosted GGUF files.

## Local captioner thinking control

Version 0.34.0 also adds `llama_flags.py` for GGUF captions (#24). It runs the
already selected local llama.cpp executable with the fixed `--help` argument,
without a shell or model input. Probes have a 10-second deadline, check for
cancellation, reap their child process and cache advertised flags by executable
path, modification time and size (up to 32 entries).

Explicit caption thinking control can select the sibling `llama-server` for a
single reference. Unsupported flags are omitted with a warning; the existing
CLI fallback and operator's `MINIMAX_H3_MTMD_SERVER=never` override remain.
This local engine path is separate from API Universal Writer. A new scanner
report may therefore include this additional use of the existing launcher.

## New file, network and system operations

| Area | Access and limits |
|---|---|
| `api_config.py`, `api_profiles.py` | Fixed profile and store names beneath ComfyUI's selected user directory; strict JSON schema, validated network mode and endpoint. No package-directory fallback for keys. Workflows select IDs, not arbitrary URLs, headers or environment names. |
| `api_client.py`, `api_models.py` | Chat Completions POST and explicit `/models` GET to the configured recipient. TLS verification, no redirects, no `.netrc`, local requests bypass ambient proxies. Bounded bodies and deadlines; no automatic retries. Returned tool calls, refusals, incomplete text and credential echoes are rejected. |
| `api_media.py`, `api_multimedia.py` | Reads connected ComfyUI media objects or decoded RefMods. Sends bounded JPEG/frame/WAV copies with labels and timestamps. Does not follow paths/URLs found in prompts or model replies. Descriptions mode sends explicit text only. Original references pass through. |
| `api_secrets.py` | Windows current-user DPAPI calls system DLLs from System32. Linux/server storage uses AES-256-GCM and an operator-supplied master key, with restrictive file modes. No plaintext fallback. Explicit environment credentials read only the configured name. |
| `api_management.py`, `api_management_routes.py` | Local same-origin editing or configured HTTPS origin plus administrator token. Forwarded TLS is accepted only from a loopback reverse proxy with the configured Host. Small JSON bodies, expiring sessions, revision checks and one-use challenges. Saved keys cannot be reassigned to an edited recipient without replacement/deletion. |
| Browser editor | Web Crypto wraps a random AES-GCM key with a temporary RSA-OAEP public key. Secret input and administrator token stay out of workflow and browser storage; saved API keys are never returned. Model text is rendered as text. |

`Test connection` uses a separately staged test operation, fresh bounded
`GET /models`, and the same credential-recipient rules. It neither writes files
nor updates discovery/inference caches. A public model listing cannot verify a
credential or generation capability. A timeout or disconnect does not prove
that a remote provider stopped processing a previously submitted generation.

Plaintext credentials exist briefly where needed: the browser input, server
memory, and the HTTP Authorization header sent to the selected provider.
DPAPI/server encryption protects the stored copy, not a compromised browser,
custom-node script, server process or OS account. File replacement is atomic
per file; profiles and vault are not a crash-atomic two-file transaction.

## Reproducible checks

Run the Python suite in the development environment. On Windows, real DPAPI
tests need the same ordinary account that owns the test store; an isolated
account without access to its master key cannot exercise that backend.
All automated credentials are dummy values in temporary directories.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp <EMPTY-TEMP-DIRECTORY>
node --test tests/test_api_profiles_transport.mjs
```

The tests cover endpoint/schema rejection, capability and byte limits, missing
keys, vault binding, corrupt ciphertext, hybrid envelopes, replay/expiry,
Origin/HTTPS/admin checks, revision conflicts, key replacement without restart,
timeouts, cancellation, refusal/tool calls, 429/5xx, and safe error reporting.
The template is also checked for portable names and connected outputs. Exercise
its actual DOM controls in classic canvas and Nodes 2.0; canvas-only screenshots
omit prompt boxes and dialogs.

Inspect both artifacts before upload:

```powershell
python tools/check_release.py <REGISTRY-ZIP> --kind registry
python tools/check_release.py <GITHUB-ZIP> --kind release
```

The checker reads the ZIP without extraction. It requires all current runtime
Python/JavaScript files byte-for-byte, the provider example, locales, API docs
and template. It rejects private profile/store names, local plans, `.env`
files, model weights, binaries, logs, HAR files, links, duplicate paths and
unsafe member paths. This is a packaging guard, not a general secret detector;
review newly added data files as well. Do not use `.comfyignore` to conceal
shipped executable source from the scanner.

Registry and release archives differ: Registry omits `tests`, `tools` and CI;
the GitHub release retains maintainer tools/tests but omits `.github` and
Git-only metadata through `.gitattributes`. Never build from an uncontrolled
directory containing credentials. The supplied example profile file contains
references to secrets, not values. After publication, rerun the status tool for
the exact new version and attach its actual findings to any review request.
