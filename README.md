# MiniMax-H3 Prompt Rewriter for ComfyUI

ComfyUI nodes for the [LightX2V MiniMax-H3 T2VA Prompt Rewriter LoRA](https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA).
A short prompt goes in; a structured, production-ready audio-video description
for [MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) comes out — entirely locally.

[Русская версия](README_RU.md) · [Changelog](CHANGELOG.md)

<p align="center">
  <a href="https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI?display_name=tag"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <a href="https://registry.comfy.org/publishers/darkil/nodes/minimax-h3-prompt-rewriter"><img alt="ComfyUI Registry" src="https://img.shields.io/badge/ComfyUI-Registry-1B98E0"></a>
  <a href="https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA"><img alt="Hugging Face" src="docs/badges/hf-lora.svg"></a>
  <a href="https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-GGUF"><img alt="GGUF adapter, 27B" src="docs/badges/gguf-27b.svg"></a>
  <a href="https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-8B-GGUF"><img alt="GGUF adapter, 8B" src="docs/badges/gguf-8b.svg"></a>
  <a href="https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-Omni-GGUF"><img alt="GGUF adapter, Omni" src="docs/badges/gguf-omni.svg"></a>
  <a href="https://www.youtube.com/watch?v=h3rZTIRB_G8"><img alt="YouTube" src="https://img.shields.io/badge/YouTube-review-FF0000?logo=youtube&logoColor=white"></a>
</p>

![The rewriter node in ComfyUI: a short prompt on the left, the structured shot-by-shot description, soundscape and music fields on the right](docs/node_preview.png)

The prompt may be in any language the base model reads; the rewrite comes back in
English, which is what MiniMax-H3 expects.

```text
"A red fox walks through a snowy forest at dawn."  +  16:9  +  15s
                              │
                              ▼
              Qwen3.6-27B + Prompt Rewriter LoRA
                              │
                              ▼
   integrated_multimodal_description: [Shot 1] ... [Shot 2] 0:06 ...
   overall_soundscape: ...
   non_diegetic_music: ...
                              │
                              ▼
              MiniMax-H3 video + synchronized audio
```

There are three ways to get that output, and the pack ships all of them:

| | Rewriter node | Rewriter 8B | Rewriter Omni | Writer nodes |
|---|---|---|---|---|
| Where the format comes from | the LoRA — a 27B trained until H3 output came out of it unprompted | a second LoRA, on a model that also sees | a third LoRA, on a model that also hears | MiniMax's own writing guide, in the system prompt |
| Model | Qwen3.6-27B only | Qwen3-VL-8B-Instruct only | Qwen2.5-Omni-7B only | any instruction-following GGUF |
| Smallest working setup | ~10 GB download, ~13 GB VRAM | ~6.1 GB download, ~9 GB VRAM | ~6.2 GB download, ~9 GB VRAM | **2.6 GB download, ~5 GB VRAM** |
| Tasks | T2VA | T2VA, I2VA, FL2VA, L2VA | T2VA, I2VA, FL2VA, L2VA, **Ref2VA** | T2VA, I2VA, FL2VA, L2VA, Ref2VA |
| Reference frames | described to it in words | **it looks at them** | **it looks at them** | described to it in words |
| Clips and sound | described to it in words | described to it in words | **it watches and listens** | described to it in words |
| Quality | the reference | the same trained contract, at a third of the download; wobblier on the alignment line | the only one that hears; six fields on Ref2VA | close, and it runs on hardware the LoRA cannot touch |

The first three columns are also available as one node — [Universal Rewriter](#minimax-h3-universal-rewriter) — where a tab swaps the adapter and everything else stays where it is.

Two of the four read text only. [Reference Caption](#minimax-h3-reference-caption)
turns an image, an audio clip or a video into the text they need — 3 to 5 seconds
per asset on a 3.4 GB model. When a whole shot's worth of references is waiting,
[Multi Reference Caption](#minimax-h3-multi-reference-caption) does all of them at
once — or [Universal Writer](#minimax-h3-universal-writer) describes them and writes
the prompt in the same node, with their order a widget you can drag rather than a
consequence of which slot you happened to use. The 8B rewriter needs none of that
for its reference *frames*: connect the picture and it reads it. The Omni rewriter
needs none of it at all — a clip and a sound reach it as themselves.

If your card has 8 GB, skip to [the writer nodes](#minimax-h3-prompt-writer-t2vai2vafl2val2va).

## What you need before installing

The LoRA route is a 27-billion-parameter language model, not a small helper.
There is no way around the following, because the adapter is bound to one
specific base model. **The writer nodes have none of these requirements** — see
their table below.

| Resource | Requirement |
|---|---|
| Disk | **~52 GB** for `Qwen/Qwen3.6-27B` + **~3.5 GB** for the adapter — or **~10–16 GB** total on the GGUF route |
| VRAM (`nf4`, default) | **~16 GB** |
| VRAM (`int8`) | ~28 GB |
| VRAM (`bfloat16`) | ~54 GB, spills into system RAM via accelerate |
| VRAM (GGUF) | ~13–19 GB depending on the quant, lower still with fewer offloaded layers |
| Packages | `transformers`, `peft`, `accelerate`, and `bitsandbytes` for `nf4`/`int8`. **Nothing at all on the GGUF route:** `llama-cpp-python` is used when it happens to be installed, and the official llama.cpp binaries are fetched when it is not |

> **The MiniMax-H3 text encoder cannot be reused for this.** It is a different
> model (Qwen3-VL-32B, vocabulary 151936) from the LoRA's base (Qwen3.6-27B,
> vocabulary 248320); it contains none of the linear-attention `in_proj_*`
> modules the adapter targets; it is truncated to the first 50 of 64 layers; and
> it ships without `lm_head` or a final norm, so it cannot generate text at all.
> It only produces hidden states for the DiT.

## Install

Clone into `ComfyUI/custom_nodes/` and install the requirements into the same
Python environment ComfyUI runs on:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI
```

For ComfyUI portable on Windows:

```bat
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\MiniMax-H3-Prompt-Rewriter-ComfyUI\requirements.txt
```

Or install it from the Comfy registry through ComfyUI-Manager.

### Example workflow

A ready workflow ships with the pack and appears in ComfyUI's template browser
(*Workflow → Browse Templates*) under this node pack's name once it is
installed. It is built on the stock MiniMax-H3 templates from ComfyUI's own
gallery — the Ref2VA and FL2VA branches — with the prompt side wired in ahead
of each: a Universal Rewriter and a Universal Writer per branch, so both routes
to a prompt are there to compare. The MiniMax-H3 checkpoints it names are the
ones the stock templates use, and ComfyUI offers to download any that are
missing.

For a community take, [axiomgraph's workflows](https://github.com/axiomgraph/ComfyUIWorkflow)
pair the Omni rewriter with FL2VA (GPL-3.0, and they use a few extra node
packs — grab them from their repository).

## Nodes

### MiniMax-H3 Prompt Rewriter

The main node. It downloads whatever is missing, loads the model, generates, and
releases the VRAM again.

**Outputs**

| Name | Contents |
|---|---|
| `rewritten_prompt` | The full rewrite, ready to paste into a MiniMax-H3 text input |
| `integrated_multimodal_description` | Just the shot-by-shot visual section |
| `overall_soundscape` | Just the diegetic audio section |
| `non_diegetic_music` | Just the score section |

**Inputs**

- `prompt` — the short prompt to expand.
- `model` — the base model. The list holds every entry from your model list plus
  every Qwen3.6-27B already on disk (prefixed `on disk:`). Anything not present
  is downloaded on first use, resuming if interrupted. The **Open model list**
  button edits the list — see below.
- `resolution` / `duration` — conditions the rewrite is composed for. Keep them
  equal to what you pass to MiniMax-H3, or the shot pacing will not match. The
  list runs `48:9` down to `9:16`; the two ultrawides are the multi-monitor
  case — 32:9 is two 16:9 screens side by side, 48:9 three. They condition how
  the shot is composed, which is all this node decides; what a generator will
  render at that shape is its own question.
- `aspect_ratio` — **the same setting, on a socket**, and it wins while something
  is connected. Every writer and rewriter node in the pack has it, and it takes a
  `STRING` or a `COMBO` link, so the primitive that already sets ComfyUI's
  **Resolution Selector** can drive this from the same wire. It is there
  because the shape of the frame is usually decided elsewhere in the graph and
  spelled differently there: ComfyUI's own **Resolution Selector** calls 16:9
  `16:9 (Widescreen)`, a size node says `3840x1080`, a divider says `1.78`. All
  three are read, and a label around the pair is read through — the number pair
  is what counts. A frame size within 2% of a listed ratio is called by its
  name, so `1376x768` (which is what Resolution Selector produces for 16:9 at
  1 MP) arrives as `16:9` rather than as `43:24`; a ratio that is nothing on the
  list passes through as itself, which is how `2.39:1` or `5:4` gets in.
  Something that is not a ratio at all is refused by name rather than composed
  for. The `resolution` widget itself has no socket, so there is one way in and
  it is the one that reads what other nodes write. **The picker greys out while
  the wire is connected** — dimmed, nothing lit, clicks refused — because a lit
  square would be naming a ratio the run is not going to use. **Unplugging clears
  the field**: an upstream node writes its value into the widget — that is how a
  wire feeds a widget input at all — so the text would otherwise stay behind, and
  it is not inert while it sits there. Anything in `aspect_ratio` outranks the
  picker, including a leftover.
- `quantization` — how to load an *unquantized* checkpoint: `nf4` (default,
  ~16 GB VRAM), `int8` (~28 GB), `bfloat16` / `float16` (~54 GB). Ignored when
  the checkpoint brings its own quantization.
- `greedy` — on by default for deterministic output. Turn it off to sample.
- `seed`
- `keep_model_loaded` — **off by default.** The 27B model is released as soon as
  the rewrite finishes, so the same GPU can run H3 video generation next. Turn it
  on only when iterating on prompts back-to-back.
- `options` — optional; connect a **MiniMax-H3 Rewriter Options** node.

### MiniMax-H3 Prompt Rewriter 8B (sees frames)

The same idea on a much smaller model that is also multimodal. LightX2V's second
adapter is trained on
[Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct), so
where the 27B has to be *told* what a reference frame contains, this one is shown
the frame and writes the alignment line from what it sees. It covers four tasks
rather than one, and fits on a card the 27B cannot go near.

![The 8B rewriter node in ComfyUI, set to T2VA: the options node on the left, the rewriter with its first_frame and last_frame inputs in the middle, and the finished rewrite on the right with numbered shots, (S1) and (S2) speaker ids and a <d>[English] Hello.</d> dialogue tag](docs/node_rewriter_8b.png)

**Outputs** are the same four as the rewriter above, so the two are
interchangeable downstream.

**Inputs**

- `prompt`, `resolution`, `duration`, `greedy`, `seed` — as above.
- `model` — a Qwen3-VL-8B base, in either shape the adapter is published for.
  A **GGUF** entry is two files from the same conversion, the model and its
  projector. A **safetensors** entry is the official
  [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
  folder, which is what the adapter was trained on and what it is published as.
  Entries prefixed `on disk:` are already in your model folders.
  **Only the 8B fits the adapter**; a Qwen3-VL of another size is refused by name
  and number before anything is downloaded.
- `quantization` — how to load a **safetensors** base: `nf4` needs about 8 GB of
  VRAM, `int8` about 13, `bfloat16` about 20. Ignored for GGUF, which carries its
  own.
- `task` — `T2VA`, `I2VA`, `FL2VA`, `L2VA`. The model's own name for these is
  T2AV, I2AV, FL2AV and L2AV; they are the same four tasks.
- `first_frame` / `last_frame` — optional IMAGE inputs. `I2VA` reads
  `first_frame`, `L2VA` reads `last_frame`, `FL2VA` reads both, `T2VA` reads
  neither. Connect the wrong one and the node says which is missing before
  anything loads — which end of the clip a picture belongs to is part of what
  the model is told.
- `keep_model_loaded` — on a **safetensors** base every task honours it: the
  model is loaded in ComfyUI's own process and stays there. On a **GGUF** base
  only `T2VA` can, because the three tasks with frames run through
  `llama-mtmd-cli`, a fresh process each time that takes the model with it when
  it exits. The node says which it did rather than ignoring the switch.
- `options` — the same options node as everything else. Its `adapter` dropdown
  lists both LoRAs; the first entry picks whichever one matches the base model
  you chose, so it needs no attention.

**What it costs**

| | Download | VRAM |
|---|---|---|
| Q4_K_M base + projector + Q8_0 adapter | 4.7 + 0.7 + 0.7 GB | ~9 GB |
| Q8_0 base + projector + F16 adapter | 8.1 + 0.7 + 1.3 GB | ~13 GB |
| safetensors base + adapter, `nf4` | 17.5 + 2.8 GB | ~8 GB |
| safetensors base + adapter, `bfloat16` | 17.5 + 2.8 GB | ~20 GB |

The GGUF rows pay for the runtime as well, once per machine: `I2VA`, `FL2VA` and
`L2VA` run through `llama-mtmd-cli`, so the first of them fetches the official
llama.cpp build — 34 MB, or 511 MB where `llama_backend` resolves to CUDA — and
an installed `llama-cpp-python` does not cover it. `T2VA` is the one task that
can take the wheel instead.

The GGUF route is much the smaller download and needs nothing installed. The
safetensors route is the shape the adapter was published in, keeps the model
resident for every task rather than only for `T2VA`, and is the one to reach for
if you already have the checkpoint — but it needs `transformers` and `peft`,
which the pack lists as dependencies.

**What to expect of it.** All four tasks produce the trained shape: `T2VA` starts
straight in on the three fields, and the other three open with the alignment
sentence MiniMax-H3 itself reads. The shot markers are the visible difference the
adapter makes — with `use_lora` off the same model still fills the three fields,
because the contract is in the system prompt, but it stops writing `[Shot 2]`
cut markers and the answer comes back about a third as long.

It is an 8B, and it shows in one place: the alignment line's timestamp is
sometimes formatted to three decimals instead of two, and on `FL2VA` the final
picture is occasionally credited to `Shot 1` rather than the last shot. The
27B does not do this. Nothing downstream parses that line, so it costs
correctness nowhere — but it is worth a glance before pasting.

### MiniMax-H3 Prompt Rewriter Omni (sees and hears)

LightX2V's third adapter, and the first that listens. It is trained on
[Qwen2.5-Omni-7B](https://huggingface.co/Qwen/Qwen2.5-Omni-7B) — the same model
this pack's captioners already use — so a reference reaches it as the asset
itself: the picture, the clip, or the sound. It is also the only one of the three
that covers **Ref2AV**, the full-reference task, which answers with six fields
instead of three.

![The Omni rewriter node set to REF2AV: four reference sockets down the left with a checkbox on each row, eight outputs on the right from rewritten_prompt down to non_diegetic_music, and between them a strip of three coloured squares - a blue "pic 1" over ref_0, a blue "pic 2" over ref_1 and a purple "aud 1" over ref_2 - above the line "drag to reorder - that order numbers the labels - click a square to switch it off". Below that the five tasks with REF2AV lit, six aspect-ratio rectangles drawn to proportion with 16:9 chosen, a duration of 10.0, a Russian prompt, and an on-disk Qwen2.5-Omni-7B Q8_0 with its projector. On the right the finished rewrite fills all six Ref2AV fields, naming Subject 1, Subject 2, Picture 1, Picture 2 and Audio 1 across two shots](docs/node_rewriter_omni.png)

**Outputs.** Seven, and which of them fill depends on the task. The four frame
tasks return the same three as the other two rewriters, so they are
interchangeable downstream. `Ref2AV` returns six: `subject_definitions`,
`summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`
and `non_diegetic_music` — the same set the
[Ref2VA writer](#minimax-h3-prompt-writer-ref2va) produces, and the same meanings.
`rewritten_prompt` always carries the whole answer.

**Inputs**

- `prompt`, `resolution`, `greedy`, `seed` — as above.
- `references` — one growing socket that takes an IMAGE, a VIDEO or an AUDIO.
  There is no wrong socket to plug into: what a reference is called follows from
  what it is. Pictures are numbered among pictures and sounds among sounds, so
  connecting a sound between two pictures does not renumber them.
- `task` — `T2AV`, `I2AV`, `L2AV`, `FL2AV`, `REF2AV`. Only `REF2AV` takes clips
  and sound; the other four are written from pictures alone, and connecting a
  sound to one of them is refused by name before anything loads.
- `duration` — **the node snaps it.** MiniMax-H3 generates on a 17n+5 frame grid
  at 24 fps, so most lengths do not exist: ask for 10 seconds and it is 243
  frames, 10.13 s, and *that* is the number written into the turn and quoted back
  in the alignment line. The widget is what you meant; the line and the video
  agree because of the snapping.
- `model` — a Qwen2.5-Omni-7B base, as a **GGUF** pair (the model and its
  projector) or as the official **safetensors** folder. Entries prefixed
  `on disk:` are already in your model folders. Two kinds of near-miss are marked
  rather than hidden: a Qwen2.5-Omni-**3B** is `(wrong size for the adapter)`,
  and a **Qwen2.5-VL-7B** — which is the same architecture string, the same 28
  blocks and the same width, so the adapter *would* attach — is
  `(vision only, not an Omni build)`, because its projector has no audio encoder
  and the rewrite would be about sound that was never heard. The **Open model
  list** button edits the `models_omni` section — see below.
- `quantization` — how to load a **safetensors** base: `nf4` about 9 GB of VRAM,
  `int8` about 12, `bfloat16` about 20. Ignored for GGUF. **Pick the largest your
  card holds** — see below.
- `max_frames` — how many frames to take from a clip, spread evenly. Each frame
  is its own picture to the model.
- `reference_layout` — the strip's state as JSON. It is a widget so the
  arrangement travels with the workflow and through the API; the interface draws
  it as squares instead.
- `keep_model_loaded` — honoured on a **safetensors** base. On a **GGUF** base a
  task with references runs through `llama-mtmd-cli`, a fresh process that takes
  the model with it when it exits, and the node says so rather than ignoring the
  switch.
- `options` — the same options node as everything else.

**The strip is the ordering.** Every connected reference appears as a coloured
square — blue for a picture, green for a clip, purple for a sound — showing what
it will be called and which socket it came from. Drag to reorder, and that order
is what numbers the labels: dragging the second picture to the front is what
makes it `<Picture 1>`. Click a square to switch it off without unplugging it.
The task strip greys out a task the connected references cannot serve and says
why on hover, so `FL2AV` with one picture is visibly unavailable rather than a
failure two minutes later.

There is deliberately **no relabelling here**, unlike the Universal Writer's
strip. There, a picture can be told to stand for a subject or a clip, because the
guide-driven turn names its references by hand. Here the socket settles it — and
a *subject* is something this adapter **produces** in `subject_definitions`, not
something the request supplies.

**What it costs**

| | Download | VRAM |
|---|---|---|
| Q4_K_M base + projector + Q8_0 adapter | 4.4 + 1.4 + 0.34 GB | ~9 GB |
| Q8_0 base + projector + F16 adapter | 8.1 + 1.4 + 0.65 GB | ~13 GB |
| safetensors base + adapter, `nf4` | 22.4 + 1.3 GB | ~9 GB |
| safetensors base + adapter, `bfloat16` | 22.4 + 1.3 GB | ~20 GB |

A GGUF base pays for the llama.cpp runtime as well, once: every task but `T2VA`
carries references and therefore runs through `llama-mtmd-cli` — 34 MB, or
511 MB where `llama_backend` resolves to CUDA, and an installed
`llama-cpp-python` makes no difference to it.

The GGUF adapter is converted from LightX2V's own safetensors with llama.cpp's
`convert_lora_to_gguf.py` and published at
[pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-Omni-GGUF](https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-Omni-GGUF).
The `Q8_0` build is half the size of the `F16` and behaves the same.

**Quantization buys VRAM, not speed.** Which is the opposite of the intuition — a
smaller model should move fewer bytes and go faster. Measured on this adapter, on
one card, same prompt, same two pictures, same 256 tokens:

| | VRAM | Generation |
|---|---|---|
| `bfloat16` | 19.4 GB | **18.6 tok/s** |
| `nf4` | 8.6 GB | 15.1 tok/s |
| `int8` | 12.0 GB | 6.0 tok/s |

`int8` is the worst of the three on both counts: slower than `nf4` *and* larger
than it. That is not a quirk of this adapter — bitsandbytes' `load_in_8bit` is
LLM.int8(), which splits every matmul into an fp16 outlier part and an int8 part
and recombines them, casting the activations each time. It is a scheme for
fitting a model that would not fit, and it costs what it costs. `nf4` is the
better small option and dequantizes on every matmul too, which is why it does not
beat `bfloat16` either. So quantize only down to what the card actually holds:
with 24 GB or more, `bfloat16` is both the fastest and the most faithful.

The same applies to any safetensors base in this pack, since the mechanism is
bitsandbytes' rather than the model's. **And none of it applies to the GGUF
route**, where llama.cpp has real quantized kernels: the same FL2AV that takes
26 seconds through `bfloat16` safetensors takes 10 through `Q4_K_M`.

**Pictures are scaled before the model sees them.** LightX2V's own inference
script caps a picture at 301056 pixels — 384 tokens — and a frame from a clip at
100352, and this node does the same. It is not only a saving: a 1616×1616 picture
is 3249 tokens, two of them overflow an 8k context before a word of the prompt is
counted, and the model is being shown a shape it never saw in training. The
context is then sized to what the turn actually costs, so a `Ref2AV` with eight
references widens it instead of failing.

**The safetensors route shows pictures only.** ComfyUI's in-process Transformers
path has no way to hand the model a sound, so a clip or a sound on that base is
refused rather than silently dropped from the turn. Pick a GGUF base to use them.

### MiniMax-H3 Universal Rewriter

All three prompt-rewriter LoRAs in one node, with a tab at the top choosing which
one runs. [Prompt Rewriter](#minimax-h3-prompt-rewriter),
[Prompt Rewriter 8B](#minimax-h3-prompt-rewriter-8b-sees-frames) and
[Prompt Rewriter Omni](#minimax-h3-prompt-rewriter-omni-sees-and-hears) are
unchanged and still there — nothing you have already built stops working.

The three adapters are not three settings of one thing. The 27B is text:
Qwen3.6-27B, one task, and a reference frame reaches it only as a sentence
somebody wrote. The 8B is multimodal: Qwen3-VL-8B, four tasks, and the picture
itself. The Omni is multimodal and hears as well: Qwen2.5-Omni-7B, the same four
tasks and a fifth of its own. Different base, different size, different
download.

Which is exactly why choosing between them by hand is tedious. The prompt is the
same prompt, the aspect ratio is the same aspect ratio, the duration is the same
duration — so trying another adapter meant retyping all of it into a second node
and then keeping them in step.

![The Universal Rewriter on its Omni tab, running Ref2VA: four reference rows down the left — first_frame, last_frame, reference_video and reference_audio, each connected and switched on — and eight outputs on the right, the three every task fills first and the four Ref2VA adds after them. Three tabs across the top with "Omni LoRA / sees, hears" lit, a task strip with Ref2VA lit, eight aspect-ratio rectangles drawn to proportion from 48:9 to 9:16 with 16:9 chosen, a duration slider at 10, the prompt "A blue whale breaching at sunset, filmed from a drone", and below them model_omni pointing at an on-disk Qwen2.5-Omni-7B Q8_0 with quantization_omni set to bfloat16, then aspect_ratio, repeat_last and three buttons: Open model list, Save the last prompt and Prompt library. On the right the six-field answer, every reference marked fully_preserved in the retention analysis](docs/node_universal_rewriter.png)

*One node, three tabs. Nothing above the model rows belongs to a tab — the
task, the ratio, the duration and the prompt are one set of values, whichever
adapter is lit. Below them each tab holds its own model and quantization, still
set to whatever you last chose, including across a save and load. The task strip
is the other difference: the 27B tab lights `T2VA` alone, the 8B tab adds the
three frame tasks, and the Omni tab adds `Ref2VA` on top of them, which no other
tab can reach.*

**So the tab carries what differs, and nothing else:**

| Belongs to the tab | Shared between them |
|---|---|
| `model_27b` / `model_8b` / `model_omni` | `prompt`, `task`, `resolution`, `duration` |
| `quantization_27b` / `quantization_8b` / `quantization_omni` | `greedy`, `seed`, `keep_model_loaded`, `bypass`, `options`, both frames, the clip and the sound |

The widget the other tab uses is hidden rather than reset, so it is still set to
whatever you last chose when you switch back — including across a save and load.

> **No captioner on the 27B tab, deliberately.** Folding a description of a
> frame into the prompt does reach that adapter, and it is not wasted — the props,
> surfaces and light in it turn up in the shots, in the trained shape, with
> nothing leaking into the answer. But the picture is absorbed into the scene
> rather than pinned to 0.00 seconds, which is exactly what the
> [LoRA's own page](https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA)
> says: T2VA is finished there and FL2VA is not. A widget for it on this node
> would have looked like the frame task the 27B cannot do. When you want it,
> [Reference Caption](#minimax-h3-reference-caption) writes the description and
> it goes in `prompt` like any other text; when the picture has to *be* a frame,
> the 8B tab is the one that was trained for it.

**The task switch is shared, and the 27B tab does not touch it.** On that tab it
shows `T2VA` lit with the three frame tasks greyed out, because that is the
honest picture of a text-only model, and clicking does nothing at all — the value
the other two tabs had is still there when you switch back. On the 8B and Omni
tabs a frame task greys out until the frame it is written from is actually
connected and switched on, the same way `Ref2VA` does on the Universal Writer.

**`Ref2VA` is on the Omni tab, with a clip socket and a sound socket to feed it.**
`reference_video` and `reference_audio` are read by that task and by nothing else
— the 27B and 8B adapters have no ear, and the four frame tasks take pictures
alone, so connecting a sound to `FL2VA` is refused by name rather than quietly
dropped. On `Ref2VA` everything connected becomes a reference the target video
reuses, in socket order: `first_frame` is `<Picture 1>`, `last_frame` is
`<Picture 2>`, then `<Video 1>`, then `<Audio 1>`. The answer comes back in six
fields instead of three; the four extra outputs sit **after** the three every
task fills, so nothing already wired to this node moves.

> **Four references, not twelve.** Order is the whole labelling rule, and with
> four sockets the order is the order of the sockets. Past that, arranging them
> by hand is the thing you actually want — which is what the draggable strip on
> [Prompt Rewriter Omni](#minimax-h3-prompt-rewriter-omni-sees-and-hears) is for,
> along with `max_frames` and any number of pictures. This tab is for the other
> thing: trying the same prompt on a different adapter without leaving the node.

**Four reference inputs, with a checkbox on each row.** `first_frame` and
`last_frame` are the same two the 8B node has; `reference_video` and
`reference_audio` are the two `Ref2VA` adds. A switched-off row counts as
unplugged — which is how a reference gets parked without dragging the wire off.
Run the 27B tab with any of them connected and the node says on itself that it is
not reading them, and where to put them instead, rather than leaving you to
wonder.

**`duration` is a slider, and its range is 4–15 seconds.** Not a property you can
raise, unlike the Universal Writer's: that node writes from a prose guide, while
these two are LoRAs, and 4–15 is the range they were trained on. A number outside
it is a worse prompt rather than a longer video — MiniMax-H3 gets the length from
its own settings, not from this line.

**There is no "Open guide folder" button**, because none of the three adapters
reads a guide: the format is in the system prompt they were trained with. The
model list button is there, and it opens the same `models.json` — `models` for
the 27B tab, `models_8b` for the 8B one, `models_omni` for the Omni one.

> **If the interface script does not load**, the tab strip, the task switch and
> the ratio picker fall back to the plain dropdowns they are built on, and every
> widget is visible at once instead of a tab's worth at a time. The node runs
> exactly the same either way. Those three controls are HTML and work in both of
> ComfyUI's renderers; the checkboxes on the two frame rows are drawn on the
> canvas, which *Modern Node Design (Nodes 2.0)* does not run, so there a frame
> is left out by unplugging it.

> **This node needs a recent ComfyUI**, being written against the v3 node API,
> exactly as the Universal Writer is. On an older install it goes missing and the
> rest of the pack registers as before.

### MiniMax-H3 Rewriter Options

Everything you rarely touch, kept off the main node. Leave it unconnected and the
rewriter uses the decoding parameters the adapter was published with.

![The Rewriter Options node, one output socket and seventeen widgets: max_new_tokens, temperature, top_p, top_k, repetition_penalty, attn_implementation, the adapter to apply, use_lora, merge_lora, auto_download, gpu_layers, n_ctx, gguf_runtime, device, llama_backend, trust_remote_code and prompt_file, with a New prompt file button under them](docs/node_options.png)

| Input | Default | Purpose |
|---|---|---|
| `max_new_tokens` | 2048 | Generation cap |
| `temperature` / `top_p` / `top_k` | 0.7 / 0.8 / 20 | Sampling, used only when `greedy` is off |
| `repetition_penalty` | 1.05 | |
| `attn_implementation` | `sdpa` | `eager` or `flash_attention_2` if you have it |
| `adapter` | the LightX2V repo | Which build of the LoRA to apply — see below |
| `use_lora` | on | Turn off for the plain base-model baseline |
| `merge_lora` | `auto` | Fold the adapter into the weights at load — twice the tokens a second, see below |
| `auto_download` | on | Turn off to fail loudly instead of fetching 52 GB |
| `device` | `auto` | Which GPU runs the language model — see below |
| `trust_remote_code` | **off** | Allow a checkpoint to run the Python it ships with — see below |
| `prompt_file` | `global` | Which set of saved prompts the nodes wired to this one work in — see [the prompt library](#the-prompt-library) |

The same options node feeds the writer nodes and the captioner; `adapter` and
`use_lora` simply do not apply there.

#### `merge_lora` — the adapter as weights rather than as a second matmul

PEFT keeps an attached LoRA beside the base model and computes it on every
token, on top of the base weights. Folding it in once at load does the same
arithmetic ahead of time, and the difference is not small. Measured here on
Qwen3-VL-8B with the 8B adapter, 128 tokens on a 5090:

| | adapter attached | folded in | merge costs |
|---|---|---|---|
| `bfloat16` | 8.98 s — 14.3 tokens/s | **5.13 s — 25.0 tokens/s** | 0.07 s |
| `nf4` | 9.43 s — 13.6 tokens/s | **5.09 s — 25.1 tokens/s** | 4.6 s |

`auto`, the default, takes the free half: it folds the adapter into an
unquantized base, where the merge costs a tenth of a second, and leaves it
attached on a bitsandbytes `nf4`/`int8` one, where merging means dequantizing
every layer and quantizing it back. `on` does it there too, if you would rather
pay 4.6 s at load and have the tokens; `off` is the old behaviour. Nothing here
touches the GGUF route, where llama.cpp applies the adapter its own way.

**A folded run is not word-for-word the attached one.** Merging reassociates the
arithmetic — `W + BA` computed once is not bit-for-bit `Wx + B(Ax)` computed per
token — so a token here and there falls differently and the rest of the sentence
follows it. Measured on the pair above at the same seed: two differences in 128
tokens on `bfloat16`, more on `nf4`, where the requantization adds error of its
own and PEFT prints a warning saying so. Neither answer is the better one, but
they are not the same answer, and a workflow you have tuned to the token is a
reason to leave this on `off`.

#### `adapter` — a dropdown, and what is in it

The list holds, in order:

- **`lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA`**, the default and the entry to
  leave alone. It means *whichever build the model list names for the base model
  you picked* — the PEFT adapter for a `transformers` base, the catalog's GGUF
  for a GGUF one, and the 8B adapter on the 8B node.
- **Every published precision of both LoRAs.** F16 and Q8_0 for each; the Q8_0 is
  half the download and rewrites the same. Picking one that is not on disk
  downloads it from the repository it belongs to.
- **Every `.gguf` LoRA already in your ComfyUI model folders**, prefixed
  `on disk:` with its architecture in the label, so `qwen35` and `qwen3vl` are
  told apart at a glance. This is how a LoRA you converted yourself gets in:
  drop the file in `models/LLM` and it appears.

Choosing a quantisation used to mean editing `models.json` by hand, which is a
power-user path rather than an interface. Saved workflows are unaffected: a combo
serialises as the string it displays, and the old default is still the first
entry, character for character.

Two things are still true of this field, because it is the one setting a
downloaded workflow gets any say in:

- **A network path is refused.** `\\host\share\...` and `//host/share/...` are
  rejected before anything reads them, because merely looking at one is an
  authentication attempt against whatever host is named. A share of your own is
  reachable by drive letter or mount point as usual, and a path in `models.json`
  is not restricted at all — that file is yours and does not travel.
- **The adapter that was applied is logged**, every run. Ask for one that is not
  the configured adapter and the console says so at warning level. A swapped
  LoRA is otherwise invisible: the node still runs and still fills every field,
  it just writes something else.

#### `trust_remote_code` — off, and why

A Transformers checkpoint can carry its own modelling code, named by `auto_map`
in its `config.json`, and loading such a model imports and runs that Python with
your user's rights. Nothing in the shipped list does this: every `transformers`
entry is a Qwen3.6-27B variant, an architecture Transformers supports natively,
and the GGUF entries never reach Transformers at all. So this switch is off, and
for the models this node offers it changes nothing.

It exists for the case where it does matter — a model you added to `models.json`
yourself, with an architecture Transformers does not know. Then the node stops
and says so instead of running the code, and turning the switch on is you saying
which model you trust. The decision is yours to make and not `config.json`'s to
make for you, which is the whole point of it being a widget.

#### `device` — and why it changes `keep_model_loaded`

Every node here has carried the same warning: turn `keep_model_loaded` off,
because the card is needed for video generation the moment the rewrite finishes.
That warning exists only because both models want the same device. **With two
cards they need not.**

Values are `auto`, `cpu`, and one `cuda:N` per GPU ComfyUI can see. One spelling,
three backends:

| | `auto` | `cuda:1` | `cpu` |
|---|---|---|---|
| llama.cpp binaries | unchanged | `--device CUDA1` | `--device none`, 0 layers |
| llama-cpp-python | unchanged | `main_gpu=1`, split mode `NONE` | `n_gpu_layers=0` |
| Transformers | `device_map="auto"` | `device_map={"": "cuda:1"}` | `{"": "cpu"}` |

The important part is not the placement. **On another card, ComfyUI's own models
are no longer evicted first.** Every backend here unloads them unconditionally
today, which is right when both want the same VRAM and pure waste when they do
not — it costs a full reload of the diffusion model after every rewrite. Pick a
second card and the rewriter loads beside it, `keep_model_loaded` becomes worth
switching on, and nothing has to move.

Two details worth knowing. A device the machine does not have is **refused, not
quietly demoted** — a workflow carried over from a two-card machine says so,
instead of running on the wrong card and evicting a batch mid-flight. And the
numbering is ComfyUI's own: started with `--cuda-device 1`, ComfyUI sees exactly
one device and it is `cuda:0`, for the subprocesses too.

The values are deliberately plain rather than `cuda:1 · RTX 4090`: a label with
the card's name reads better and breaks every saved workflow the day the card is
replaced. The tooltip names what is in each slot.

### MiniMax-H3 Prompt Writer (T2VA/I2VA/FL2VA/L2VA)

The same three output fields, without the LoRA and without the 27B. MiniMax's own
[prompt-writing guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)
goes into the system prompt and any instruction-following GGUF writes to it. A
5.2 GB Qwen3.5-9B fills all three fields, with correct camera vocabulary, speaker
IDs and `<d>[English] …</d>` dialogue, in about 20 seconds.

![The T2VA writer node beside a text viewer: a four-line prompt on the left, and on the right the shot-by-shot description with its two numbered shots, the soundscape paragraph, and non_diegetic_music reading N/A](docs/node_t2va.png)

*T2VA, 10 seconds at 16:9, on a local Qwen3.6-27B — 11.2 s. The prompt went in
in Russian and the description came back in H3's English format, with the cut at
00:03.000 that the prompt asked for and `non_diegetic_music: N/A` because it
asked for no music.*

The trade is worth stating plainly. The LoRA **is** the format: a 27B was trained
until H3 output came out of it with a seven-line system prompt. Here the format is
carried by ~4 000 tokens of instructions the model has to follow, so expect the
LoRA's prose to be denser and its formatting more reliable. Expect this to run at
all on hardware the LoRA cannot touch.

Outputs are identical to the rewriter node's — same names, same order — so the two
are interchangeable in a saved workflow.

Beyond the rewriter's inputs:

- `task` — **T2VA**, **I2VA**, **FL2VA** or **L2VA**. Everything but T2VA also
  emits the alignment instruction line H3 requires as the very first line, with
  the duration already substituted to two decimals; only the final shot number is
  left to the model.
- `reference_material` — **this node reads text, not pixels.** For I2VA, FL2VA and
  L2VA, describe what the reference frames show, by hand or from the
  [Reference Caption](#minimax-h3-reference-caption) node upstream. Without it the
  model invents a first frame that has nothing to do with your image.

`model` offers the `writers` section of the model list plus every GGUF already in
your ComfyUI model folders, whatever its architecture. Nothing here has to be
Qwen3.6-27B, and nothing has to be installed: without `llama-cpp-python` the node
runs the official llama.cpp binaries, exactly as the GGUF route below does.

| Suggested writer | Download | VRAM with the guide in context |
|---|---|---|
| Qwen3.5-4B `Q4_K_M` | 2.6 GB | ~5 GB — start here on an 8 GB card |
| Qwen3.5-9B `Q4_K_M` | 5.3 GB | ~8 GB — best writing per gigabyte |
| Qwen3.5-9B Uncensored (HauhauCS) `Q4_K_M` | 5.2 GB | ~8 GB — does not decline scenes the stock model refuses |
| Gemma 3 12B Instruct `Q4_K_M` | 6.8 GB | ~10 GB — non-Qwen alternative |
| Mistral Small 3.2 24B Instruct `Q4_K_M` | 13.4 GB | ~17 GB — for 16 GB cards and up |

`n_ctx` is raised automatically: the base guide needs about 9 200 tokens of
context and the full-reference guide about 12 300, against the 8 192 default that
suits the LoRA's short system prompt. Letting llama.cpp truncate instead would
drop the *front* of the prompt — the guide and the output contract — and the
answer would come back in some other format with nothing to say why.

If a field comes back missing, the node still returns everything it got and says
which fields are absent, on the node. Lower the temperature or move up a size.

#### `system_prompt` — pointing the writer at another model

The guide is the whole of what makes this an H3 writer: it goes into the system
prompt and nothing else in the node knows the format. So replacing that text is
what turns these nodes into a writer for something else — LTX, Krea, Wan, or a
house style of your own. Write it into `system_prompt` and the assembled guide is
not used; on an empty field nothing changes.

The guide is then not even fetched, which matters on a machine kept offline:
there is no 24 KB document downloaded to be ignored.

The shortest way in is
[Guide Prompt (any LLM)](#minimax-h3-guide-prompt-any-llm), which hands you the
stock system prompt on an output. Take it, edit it, connect it back.

Two things do not move. The task message is never replaced — it carries the
prompt, the aspect ratio and the duration, which any guide needs. And the answer
is still split into the H3 sections, so a guide that replies with one paragraph
fills `rewritten_prompt` and leaves the section outputs empty. That is worth
knowing rather than worth avoiding.

### MiniMax-H3 Prompt Writer (Ref2VA)

Full-reference mode, from MiniMax's
[full-reference guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md).
Six sections instead of three, each its own output:

| Output | Contents |
|---|---|
| `subject_definitions` | what each `<Subject N>` / `<Picture N>` / `<Video N>` / `<Audio N>` label denotes |
| `summary` | the `[task type]` prefix and the reference relationships in one paragraph |
| `retention_analysis` | per label: `fully_preserved`, `partially_preserved`, `attribute_transfer`, `weak_reference`, `fully_copy`, … |
| `detailed_description` | the body, shot by shot, with labels cited where their roles apply |
| `overall_soundscape` | ambience and physical sound |
| `non_diegetic_music` | audience-only score |

![The Ref2VA writer node beside a text viewer showing all six sections: subject_definitions binding Subject 1 and Picture 1, the summary with its reference-generation task type, the retention analysis marking both labels fully_preserved, the shot-by-shot body, the soundscape and non_diegetic_music](docs/node_ref2va.png)

*The same clip as above with one `Picture 1:` line added to `reference_assets` —
17.4 s. The model picked the task type, bound `<Subject 1>` to the cat and
`<Picture 1>` to the drawing's style, and wrote the retention analysis itself.*

`reference_assets` is **required** and is refused when empty: full-reference mode
describes how a target video reuses assets, so with no assets it is simply T2VA
under another name. One per line —

```text
Picture 1: young woman, long dark hair, blue cardigan, thin silver necklace
Picture 2: corner cafe interior, brick wall, brass lamps, rain on the window
Audio 1: voice-timbre reference for the woman — low, unhurried, slight rasp
```

— and the model binds the labels, picks the task type, and writes the retention
analysis from them. Or let
[MiniMax-H3 Reference Caption](#minimax-h3-reference-caption) write those lines
for you from the assets themselves.

### MiniMax-H3 Universal Writer

One node for a whole shot: the references, the order they are in, and the
rewrite. It does what [Multi Reference Caption](#minimax-h3-multi-reference-caption)
and both writer nodes do, in a single box, for all five tasks. Those three stay
exactly as they are — nothing you have already built stops working.

The reason to fold them together is **order**. `Picture 1` and `Picture 2` are
not interchangeable in FL2VA: one opens the video and the other closes it. Until
now the only thing deciding which was which was the order the caption node
happened to write them in, which came in turn from which slot each was plugged
into. Real, load-bearing, and nowhere on screen.

![The Universal Writer node: five reference rows each with a checkbox, four of them connected, then a strip of four coloured squares reading subj 1 over ref_0, pic 1 over ref_1, vid 1 over ref_2 and aud 1 over ref_3, each with an "+ instr" button and a line of help under them, a task switch with Ref2VA lit, eight aspect-ratio rectangles drawn to proportion from 48:9 to 9:16 with 16:9 chosen, a duration slider at 10, the prompt, the captioner and writer dropdowns, and four buttons at the bottom: Open model list, Open guide folder, Save the last prompt and Prompt library. On the right the six-field answer, written around <Subject 1>, <Picture 1>, <Video 1> and <Audio 1>](docs/node_universal_writer.png)

*Four references on one growing socket — an image used as a subject, an image
used as a frame, a clip and a sound — and on the right an answer written around
exactly those labels: `<Subject 1>`, `<Picture 1>`, `<Video 1>`, `<Audio 1>`. The
squares are in slot order here because nothing has been dragged; the number is a
position and renumbers the moment anything moves, while the slot name under it is
what stays with a square.*

**One socket takes an image, a clip or a sound**, and more slots appear as you
fill them. There is no wrong socket to plug into here, because which socket you
used no longer decides anything.

**The strip under the inputs decides it.** Every connected reference gets a
square, coloured by what it is for and numbered exactly as the block will number
it. Under the number is the slot it arrived on, and that is the part of a square
that stays with it: the number is a position, so it renumbers the moment anything
moves.

| Square | Label | What it means |
|---|---|---|
| `pic` | `<Picture N>` | an image serving as an actual frame — first, last, key, composition anchor |
| `subj` | `<Subject N>` | reusable visible content — a person, an animal, a place, a costume, a style |
| `vid` | `<Video N>` | a clip, or a batch of frames you want read as one |
| `aud` | `<Audio N>` | a voice timbre, music, ambience, an effect |

- **Drag a square** to move it. The numbering follows immediately.
- **Click its label** to change what an image is for: `pic` → `subj` → `vid` and
  round again. A clip and a sound are what they are, so those do not cycle.
- **Click its number** to switch that reference off without unplugging it — the
  checkbox on the slot's own row does the same thing from the other side.
- **Click the band under it** to ask that one reference something other than its
  role's usual question. `+ instr` adds your line to the role's question,
  `= instr` asks it instead; right-click takes it back, hovering reads it out.

So one socket still produces the four labels Ref2VA allows, and the distinction
Multi Reference Caption makes structurally — a subject is not a frame — is made
here on the square instead.

**The task switch and the aspect-ratio picker are the same idea**: the choice is
the picture rather than a line of text in a dropdown. And the task switch reads
the strip: a task greys out while the strip cannot supply what it is written
from, so with nothing connected only `T2VA` is lit, one picture lights `I2VA` and
`L2VA`, and two light `FL2VA`. Three pictures grey all three, because three is as
impossible for `I2VA` as none — it is the same count the node refuses on, moved
from the run to the sight of it, and the greyed button carries the same sentence as a tooltip. Badges
are counted, not sockets: turn a picture into a subject and the task it was
blocking lights up. A task already chosen and no longer possible turns red rather
than quietly failing later.

Every rectangle in the picker is drawn to its own proportion within one budget,
so the wide end reads as how little height is left: `21:9`, `32:9` and `48:9`
come out at roughly 15, 10 and 6 pixels tall. A node too narrow to hold the whole
row wraps it onto a second line rather than cutting the last rectangles off, and
grows by exactly that much.

**`duration` is a slider, in tenths of a second.** How far it reaches is the
node's own `max_duration` property — right-click the node, Properties Panel —
and it is 30 seconds until you change it. A widget's range is fixed when the node
is declared and one number cannot suit every graph, so the server accepts up to
ten minutes while the slider spans whatever you actually work with.

**Two model widgets, because there are two jobs.** `caption_model` reads the
references and `writer_model` writes the prompt from MiniMax's guide; `clip`
works here exactly as it does on the caption nodes, and is used for the
references when connected. On T2VA no captioner is touched at all.

**What each task expects of the strip:**

| Task | Pictures | Everything else on the strip |
|---|---|---|
| T2VA | none | ignored entirely, and the node says so rather than describing them |
| I2VA | exactly one — the first frame | goes in as reference material |
| L2VA | exactly one — the final frame | goes in as reference material |
| FL2VA | exactly two — first, then last | goes in as reference material |
| Ref2VA | any number | at least one reference of some kind is required |

A mismatch is refused before anything is downloaded or loaded, and the message
names the strip rather than the sockets, because the strip is where the fix is:
an image that is in the way gets switched off or given a different label, not
unplugged.

**The outputs are the union of both writers'** — the three T2VA fields, the six
Ref2VA fields, and the reference block itself. A task that does not write a field
leaves it empty, because a node's outputs cannot change with the value of one of
its widgets.

> **One difference from Multi Reference Caption.** That node writes the block in
> the guide's own order — subjects, pictures, videos, audio — whatever the wiring
> says. This one writes it in strip order, because the strip is the whole point.
> An untouched strip is in slot order.

> **If the interface script does not load**, the strip, the task switch and the
> ratio picker fall back to the plain widgets they are built on — one text field
> and two dropdowns — and the node still runs. Those three are HTML and work in
> both of ComfyUI's renderers. The checkboxes on the input rows are drawn on the
> canvas, which *Modern Node Design (Nodes 2.0)* does not run; there the number
> on each square is the way to switch a reference off. Multi Reference Caption
> draws the same squares and needs no checkboxes at all any more.
>
> The three drawn controls are also kept off the right-side **Parameters**
> panel, which has no way to draw a control like this and would have to take it
> off the node to try. The node is where they live. Everything else about the
> node -- the prompt, the models, the duration slider -- is in the panel as
> usual.

> **This node needs a recent ComfyUI**, for the same reason Multi Reference
> Caption does: its growing inputs are `io.Autogrow` from the v3 node API. On an
> older install these two go missing and the rest of the pack registers as
> before.

### MiniMax-H3 Reference Caption

The writer nodes read text, not pixels. This is where the text comes from:
connect an image, an audio clip or a video, and a small multimodal model
describes it into one labelled line of `reference_assets`.

Measured on a 3.4 GB Qwen2.5-Omni-3B: **3 s for a frame, 2 s for an audio clip,
5 s for a video.** It runs `llama-mtmd-cli`, which ships beside
`llama-completion` in the same archive, so a machine whose rewrites already ran
on the binaries downloads no runtime for this.

**A machine whose rewrites did not, pays for it here.** `llama-cpp-python` is
what `gguf_runtime = auto` picks whenever the wheel is importable — and a recent
ComfyUI portable ships one — while a safetensors base never touches llama.cpp at
all; neither route has ever fetched an archive, so the first caption is where it
arrives: 34 MB, or 511 MB where `llama_backend` resolves to CUDA. The wheel does
not spare it either, however it was compiled: multimodal input goes through
`llama-mtmd-cli`, a program, and the wheel is a set of shared libraries with no
executables in it. `gguf_runtime` is therefore a writer setting — the caption
nodes run the binaries whatever it says.

**Chain them by wiring** `reference_assets` into the next node's `previous`. Each
label is numbered *within its own category*, which is the guide's own rule
(«`<Video N>` and `<Audio N>` are numbered independently»), so four assets come
out as `Picture 1`, `Picture 2`, `Video 1`, `Audio 1` — not 1 through 4.

![Two Reference Caption nodes chained: a Load Image feeds the first with role Picture, a Load Audio feeds the second with role Audio, reference_assets runs from the first node into the second node's previous input, and the viewer on the right shows both lines, Picture 1 and Audio 1](docs/node_ref_caption.png)

*An image and an audio clip through Qwen2.5-Omni-7B — 4.4 s and 3.7 s. The
second node received the first one's block on `previous` and appended to it, and
the `Audio` role described the voice rather than transcribing it: "a male
speaker with a calm and measured delivery, speaking in Russian".*

`role` picks both the label and the question asked, and the questions differ on
purpose:

| `role` | What the model is asked for |
|---|---|
| `Subject` | the features that must stay consistent across shots — build, hair, clothing and their colours, carried objects |
| `Picture` | the frame as a shot — style, shot size, camera angle, placement, environment, lighting |
| `Video` | subjects, actions in order, camera movement, cuts and pacing |
| `Audio` | **the sound, not the words** — voice timbre, apparent age and gender, delivery and rate, instrumentation, tempo, ambience |

That last row is the one that matters. `<Audio N>` in the guide is usually a
*timbre* reference, and a transcript throws away exactly the part that is needed,
so the instruction says "do not transcribe" outright. If you also want the spoken
words verbatim for a `<d>` block, that is an ASR job — run Whisper alongside and
paste the line in.

Other inputs:

- `description` — type it yourself and **no model runs at all**. The fastest way
  to add an asset you can describe in six words.
- `instruction` — override the role's question entirely.
- `max_frames` — how many frames to take from an IMAGE batch **or a VIDEO**,
  spread evenly. All of them would overflow both the context and the wall clock:
  two seconds at 25 fps is 56 images through the vision tower, and thirty seconds
  is 750. This is what makes the cost of describing a clip independent of its
  length.
- `context_size` — `0` sizes the context from the references and from the card,
  which is not the same as the model's own: llama.cpp reserves the whole KV cache
  before it reads a pixel, and a model trained for 262144 tokens asks tens of
  gigabytes for it — Qwen3-VL-8B asks 36 GB, on a card that would have captioned
  the picture in three seconds. One 1024×1024 frame is already twenty-odd media
  chunks, so the count of references is half of the answer and the memory of the
  device is the other half. Type a number to say it yourself; too small a value
  fails the run instead of truncating it.

**A thinking model does not think into the block.** Whatever a captioner writes
between `<think>` and `</think>` is cut before the caption becomes a line of
`reference_assets`, on both the GGUF and the `CLIPLoader` path — otherwise an
empty `<think> </think>` opens every caption, and a model that really reasons
sends its whole deliberation on into the writer's prompt.

Cutting it is only half, and the other half cannot be a flag. The writers render
the chat template themselves and pass `enable_thinking=False`; a caption cannot,
because `llama-mtmd-cli` applies the template itself — that is what puts the
media tokens in the right place — and offers no switch for the thought channel.
The reasoning is therefore still charged against `--predict`, so every caption
question ends with a line asking for the description alone. If a model of yours
still insists, `instruction` is where to say so in your own words.

> **Not every multimodal GGUF works here**, and the ones that do not fail loudly.
> llama.cpp's `mtmd` has to understand the projector format: Gemma 4's aborts the
> process outright on `b10310` — with Google's own file and with unsloth's alike,
> on the CUDA and CPU builds alike — while `llama-completion` runs the same model
> as text with no trouble. So the `captioners` list holds only pairs that have
> actually been run:
>
> | Captioner | Download | Modalities |
> |---|---|---|
> | Qwen2.5-Omni-3B `Q4_K_M` + mmproj | 3.4 GB | image, audio, video |
> | Qwen2.5-Omni-7B `Q4_K_M` + mmproj | 5.8 GB | image, audio, video |
>
> A model and its `mmproj` sitting together in one folder under `models/LLM` is
> offered automatically, so you can try another without editing anything.

### MiniMax-H3 Multi Reference Caption

The same job for a whole shot, in one box. A chain is exact, but it grows: five
references are five nodes, five wires and five chances to leave the wrong role on
one of them. This node folds the chain into a single box and takes the role out
of your hands entirely — **the group an asset is plugged into is its label.**

That is the guide's vocabulary made structural. Ref2VA defines exactly four
reference labels and forbids inventing more, so four groups of inputs cover the
format completely, and describing an image as audio stops being *possible* rather
than merely discouraged.

| Group | Label | What belongs here |
|---|---|---|
| `subjects` | `<Subject N>` | reusable visible content — a person, an animal, a place, a costume, a style |
| `pictures` | `<Picture N>` | an image serving as an actual frame: first, last, key, composition anchor, storyboard panel |
| `videos` | `<Video N>` | whole-video relationships — an edit source, a continuation point, camera work and pacing |
| `audios` | `<Audio N>` | a voice timbre, music, ambience, an effect |

Slots grow as you fill them, one spare always waiting, so the node is as tall as
the shot needs and no taller.

![The Multi Reference Caption node beside a text viewer: seven reference slots each with a checkbox on its own row, the connected video_0 slot unchecked while subject_0 and audio_0 stay checked, and the viewer showing a block of exactly two lines, Subject 1 describing a turtle with gem-like stones on its shell and Audio 1 describing a male voice over birdsong and splashing water](docs/node_multi_ref_caption.png)

*Two assets through Qwen2.5-Omni-3B — 6.0 s. The video is wired up and switched
off, so the block came out as `Subject 1` and `Audio 1` with no `Video` line at
all: the reference stays in the graph, it just costs nothing on this run.*

**The strip under the inputs is one coloured square per connected reference**,
in the order the block will be written and labelled with what each will be
called. Click a square to switch that reference off without unplugging anything:
a caption costs a model load and seconds to minutes, which makes "everything
except this one" the ordinary thing to want, and pulling the wire out to get it
throws away the wiring you meant to keep. The state is saved with the workflow
and travels through the API like any other value.

Squares here do not drag and their labels do not cycle, unlike the
[Universal Writer](#minimax-h3-universal-writer)'s: this node writes the block in
the guide's own order, and the group an asset is plugged into is what names it.

**The band under a square asks that one reference something else.** Dark while it
is asked its role's usual question, lit once it is not. Click to write the
question, right-click to take it back, hover to read it. It is per reference
rather than per node on purpose — a node describing a picture, a clip and a sound
at once has no single question that suits all three.

The checkbox in that little window decides which of two things your text is, and
the band then says which: `+ instr` for a line **added** to the role's question,
`= instr`, on a solid band, for one asked **instead** of it. Both are needed.

| What you write | Which mode | Why the other one fails |
|---|---|---|
| `Do not mention the window.` | added | A rule is not a question. Asked on its own it leaves nothing wanting a description, and the model answers the rule: `Subject 1: No`. |
| `Always answer "blah blah blah".` | instead | Left after the role's question it contradicts it, and a small model settles that by describing anyway. |

Asked *instead*, your text also takes over from `length`: whoever writes the
question owns the shape of its answer, so state the length yourself if it
matters. Added, the role's question and the length preset both stay where they
were.

**A `videos` slot takes a `VIDEO` or an `IMAGE` batch**, whichever your loader
hands out — VideoHelperSuite's `Load Video (Upload)` wires straight in. Frames
are sampled evenly up to `max_frames` either way, so the cost of a clip stays
independent of its length.

**The block is written in the guide's order** — subjects, pictures, videos, audio
— rather than in wiring order, and each label is numbered within its own
category, continuing from whatever arrives on `previous`. So this node still sits
in a chain with single caption nodes on either side.

`model`, `length`, `seed`, `max_frames`, `context_size` and `bypass` are shared
by every asset in the node; the question is not, and lives on the band under each
square. `role` and `description` are gone on purpose: the group is the role, and
a description you write out yourself belongs to one asset at a time. Keep
[Reference Caption](#minimax-h3-reference-caption) for an asset you would rather
describe by hand.

> **This node needs a recent ComfyUI.** Its growing inputs are `io.Autogrow` from
> the v3 node API. On an older install it is the only node that goes missing —
> the rest of the pack registers exactly as before.

### Captioning with a model ComfyUI already has loaded

Both caption nodes and the Universal Writer have a `clip` input. Connect a multimodal text encoder from
`CLIPLoader` and every asset is described by *that* model instead of by the GGUF
in `model` — which then stays exactly where ComfyUI's allocator put it.

That is the whole point. The GGUF route runs `llama-mtmd-cli`, one process per
asset, so a shot with five references reads the weights off disk five times. A
loaded encoder is read **once**, and reruns while you tune the wording cost
nothing at all. Measured here: three assets — an image, a clip and its
soundtrack — through Gemma-4 12B in **24 s**, with the log showing a single
`Requested to load` for all of them.

Do not read that as "faster", though. The same three assets through the 3.4 GB
Qwen2.5-Omni-3B on the GGUF route took **16 s**, loads and all: a small model
loads quickly enough that three reloads cost less than a 12B costs to run. What
this route buys is a *bigger* model at no reload penalty, and reruns that skip
the loading entirely — and the gap widens with every asset and every rerun.

Which encoders qualify, and what each can take in:

| Encoder | Sees | Hears |
|---|---|---|
| Qwen3-VL 4B / 8B | yes — a batch of frames is read frame by frame | no |
| Gemma-4 12B | yes | **yes** — the "unified" build, audio projected straight in |
| Gemma-4 E2B / E4B | yes | has the parts, but see below |
| Gemma-4 31B | yes | no |

Gemma-4 is recognised from the checkpoint itself, so the `type` widget on
`CLIPLoader` does not matter for it. Connect a vision-only encoder with audio
wired up and the node refuses before anything runs, the same way it refuses a
projector without an audio tower.

> **Two things to know about Gemma-4 E2B/E4B**, both observed on ComfyUI 0.30
> with an fp8 E4B and both reproduced with ComfyUI's own `Generate Text` node on
> the same file — so neither is something this pack can fix:
>
> - **Audio does not arrive.** The caption comes back saying no clip was
>   provided. The node logs a warning when it sees this shape of encoder rather
>   than refusing outright, because another checkpoint may behave. Gemma-4 12B
>   takes audio correctly.
> - **It reasons out loud.** The prompt primes an empty thought channel and this
>   build fills it anyway. The caption nodes cut everything up to the closing
>   tag, so what reaches `reference_assets` is the answer alone — but if you wire
>   `Generate Text` up yourself, that is yours to strip.

Two widgets are inert on this route and say so in their tooltips: `model`, which
is not consulted at all, and `context_size`, which is a llama.cpp KV-cache knob.
`max_frames` still applies — the frames are thinned here, before the encoder sees
them, rather than being left to the encoder's own 1 fps subsampling.

The GGUF route is not going anywhere: it reaches models ComfyUI has no encoder
for, and it needs nothing loaded in advance. Leave `clip` unconnected and nothing
about either node changes.

### MiniMax-H3 Guide Prompt (any LLM)

Builds the guide-based `system_prompt` and `user_prompt` and returns them as
strings, without running anything. Costs no VRAM and no time. Wire them into
whichever LLM node you already use — local, API, remote — when you would rather
not run the model here. It covers all five tasks, Ref2VA included.

A third output, `prompt`, is both of them in one string, because most LLM nodes
take exactly one — **including ComfyUI's own `Generate Text`**, which since 0.30
runs a language model in ComfyUI's own process off a model loaded by `CLIPLoader`.
That is the shortest route to this pack's output with no GGUF downloaded at all,
if you already keep a Qwen3-VL or Gemma-4 text encoder for an image model:

![Load CLIP feeding Generate Text, with the Guide Prompt node's third output wired into its prompt input and a text viewer on the right showing the finished rewrite: subject_definitions binding Subject 1 and Picture 1, the summary with its reference-generation task type, the retention analysis, the shot-by-shot body with timecodes, the soundscape and non_diegetic_music](docs/node_guide_prompt.png)

*Ref2VA through a 4B Qwen3.5 — 37.7 s, and all six sections came back. The node's
own status line is the number worth knowing: a 25 042-character system prompt is
**about 10 240 tokens of context** before the model writes a word, which is what
decides whether a given encoder can take this at all.*

Three settings on `Generate Text` decide whether it works:

- **`max_length`** — its default of 512 is the *output* budget, and six Ref2VA
  sections do not fit in it. Around **2048** is right, which is what this pack's
  own writers use (`max_new_tokens` in the options node). Measured on a 4B
  Qwen3-VL: a Ref2VA rewrite came back complete in **53 s including the model
  load**, stopping on its own at roughly 580 tokens — inside 2048 with room to
  spare, and past 512 by enough that the default would have cut it mid-section.
- **`thinking`** — off. The guide asks for fields and nothing else; reasoning
  spends the budget above on prose you then have to strip.
- **`use_default_template`** — leave it on, and leave `format` on `plain`.

`format` is the one knob worth knowing about. On `plain` the two prompts are
joined with a blank line and the LLM node wraps the result in the model's own
chat template, which puts the whole guide inside the *user* turn. On `chatml` the
turns are written out here instead: a Qwen text encoder skips its own template as
soon as the text starts with `<|im_start|>`, so the guide arrives as a real
system message. That branch also skips Qwen's thinking suppression, so the empty
`<think>` block is written for you. It is Qwen-shaped by construction — on Gemma
or anything else, stay on `plain`.

### Repeating the last answer

Every writer, rewriter and captioner has a `repeat_last` switch. Turn it on and
the node hands back the answer it produced last instead of running the model
again — with nothing kept yet it runs once, keeps what it wrote and says so, and
from then on returns that same text for as long as the switch is on, whatever
else you change. That is the point of it: a fifty-second rewrite is not something
to pay for twice while you wire up everything downstream of it.

ComfyUI's own cache cannot do this. A node's cache key is its class, its
`IS_CHANGED` value and every input it received, so editing the prompt is exactly
what drops the entry — and `IS_CHANGED` can only add invalidation, never mask it.
So the answers are kept in a store of the pack's own, one per node.

That store lives in memory for the ComfyUI session and nowhere else: it is not
written to disk, it does not travel with the workflow, and a restart empties it.
Every run says where its answer came from in three places — the caption under the
node, the ComfyUI console, and the switch's own tooltip, which carries the time
it was kept, its length and the opening of the text. When the inputs no longer
match the ones that produced it, all three say so rather than pretending nothing
changed. `bypass` still wins over it.

On the two caption nodes it is the caption that is kept, not the assembled block:
the numbering belongs to the chain, so the line is written again around whatever
arrives on `previous`, and an asset connected since is described for real.

The node's own last answer is what the switch hands back by default, and
[the prompt library](#the-prompt-library) is how you point it at something else
instead. One switch either way: `repeat_last` decides whether a kept prompt is
handed on at all, the library window decides which one.

### The answer is checked

Every fresh answer is read back against the rules of MiniMax's own writing
guides before it leaves the node — a self-check, silent when it finds nothing.
When it does find something, it says so in three places: a toast in the ComfyUI
window with the first few findings, because the caption under a node is easy
to miss from the other end of the graph; the caption itself, which holds the
full list above the answer; and the console, for runs nobody is watching. What
it looks at:

- **Shot structure.** `[Shot 1]` opens the description and carries no cut time;
  later shots are numbered in order, each with an `At MM:SS.mmm` timestamp that
  is later than the one before it and inside the requested duration.
- **Dialogue markup.** `<d>` and `</d>` come in pairs, and every block starts
  with its `[Language]` tag.
- **Reference tags, both ways.** A tag the task cannot carry — `<Video 1>` in a
  frame task, any tag at all in T2VA — is flagged, and so is the opposite: a
  reference that reached the node but is never cited, which the generator still
  receives with no say in what it is for. The nodes that see their references
  check the numbers against what is actually connected; the text-only writers
  skip that half rather than guess.
- **Full-reference bookkeeping.** Every `<Subject N>` that
  `subject_definitions` introduces owes `retention_analysis` a line, and the
  guide's 350–500 words for `detailed_description` is noted when missed.
- **The alignment line.** I2VA, FL2VA and L2VA open with a fixed sentence
  telling H3 where the reference frames land; its absence is worth knowing
  about before a render finds out.

![A warning toast titled "Self-check: MiniMax-H3 Universal Writer", listing three warnings marked with an exclamation point — 5 fields missing from the answer, naming subject_definitions, summary, retention_analysis, overall_soundscape and non_diegetic_music with the advice to lower the temperature or try a larger writer model; the description has no Shot 1, shots being how H3 reads structure; Picture 1 and Picture 2 connected but never cited, the model still receiving them with no say in what they are for — and one note marked with a dash: detailed_description is 1286 words where the guide suggests 350–500](docs/self_check_alert.png)

*One deliberately bad run, read back. The `!` lines are warnings — things H3
will likely misread; the `-` line is a note — something the guide merely
suggests. The toast shows the first few findings; the full list sits in the
caption under the node and in the console, where it survives the toast's eight
seconds.*

Findings are said, never enforced: the answer ships exactly as written, because
the model is sometimes right to bend a rule and only you know whether this is
that time. Warnings (`!`) are things H3 will likely misread; notes (`-`) are
things the guide merely suggests. A prompt handed on from the library is not
re-checked — it was checked when it was written, and the caption stays with the
record's name instead.

The same toast carries the nodes' own warnings, under **Heads-up** instead of
**Self-check**: a reference the chosen task will not read, a saved prompt whose
references no longer match what the node is shown, a captioner that came back
with nothing for a connected asset. These were always said on the node and in
the console; now they are also impossible to miss.

### The prompt library

`repeat_last` holds one answer per node and forgets it when ComfyUI restarts. The
library is the other half: prompts you name and keep, in a JSON file under the
ComfyUI user directory, available to every workflow and every node.

**Saving.** The **Save the last prompt** button on any writer or rewriter opens a
box asking for a name, a description and any number of groups — the groups already
in the file are offered as chips, and a new one is a word and Enter. What gets
saved is the run itself: the text, the sections after it, the task, the model, the
ratio, the duration, the seed, and a 50x50 thumbnail of every reference the node
was shown.

Those thumbnails are taken **during the run**, which is the only moment they can
be. By the time the box opens the node has returned strings and the tensors are
gone, so the picture, the first frame of the clip and the measurements are captured
as the answer is produced and travel with it. What is measured is what genuinely
exists: an image has a size; a clip that arrived as a real `VIDEO` has frames,
seconds and a frame rate, while one that arrived as a batch of images has a frame
count and nothing else, because a batch has no container to ask; a sound has a
duration, a rate and a channel count. References are labelled by position —
`ref1-image`, `ref2-audio` — rather than by file name, which a node cannot see
anyway.

**Using one.** The **Prompt library** button opens the list: this node's own last
answer first, then everything in the file, newest first. Filter by group with the
chips, or type in the search box to keep only the records whose name, description,
prompt, groups, references or settings contain what you typed. **Use** points the
node at a record; **Copy** puts the prompt on the clipboard without pointing the
node at anything; **Delete** removes it from the file for good.

![The prompt library window, titled “Prompt library” over the line “Hand a saved prompt straight to this node’s output. No model is loaded.”: a search box across the top reading “Search names, descriptions, prompts, references” with the file dropdown beside it on `global`, and four group chips under it — bakery, dinosaur, fish, joke. First the node’s own Last Prompt row, its Use and Copy greyed out beneath “Nothing kept yet. Run this node once and its answer is what gets saved.” Then two saved records: “Дино и пеламида”, three thumbnails down its left and the line Ref2VA · 16:9 · 10s · 3 images + 1 audio · joke, dinosaur, fish above its description and the opening of its subject_definitions; and “Дино”, one thumbnail and the line I2VA · 16:9 · 15s · 1 image · joke, bakery, dinosaur, its card tinted blue because that is the record this node is pointed at. Use, Copy and Delete run down the right of every row, and Write a new one and Close sit at the foot of the window](docs/prompt_library.png)

*Everything on a card is searchable — the name, the description, the prompt, the
groups, the settings and the references — which is what keeps a file of a hundred
records usable. The line under the name ends with what the record was written for,
`3 images + 1 audio`, in the same words the node uses when it warns that what it is
being shown no longer matches; the thumbnails beside it say which pictures those
were. The tinted card is the current choice. The Last Prompt row sits above the
file because it is not in it — it is this session’s own answer, which is why it
has no **Delete**.*

A chosen prompt reaches `rewritten_prompt` and the section outputs exactly as a
fresh one would — no model is loaded, nothing is generated, and the run takes about
a tenth of a second. A record written by another kind of node works too: when the
classes match the outputs come back verbatim, and otherwise the text is split into
sections by the node reading it, the same way it splits an answer it wrote itself.

**A record with references is pinned to them.** A T2VA prompt is self-contained
and travels anywhere. A prompt for one of the frame tasks or for Ref2VA is not:
it names its references and describes them — `<Subject 1>` *is* the blue dinosaur
with the textured scaly skin — so giving that prompt a different set of assets
hands the generator a description of something that is not in front of it. The
thumbnails on the card are how you see what a record was written for, and the
node checks as well: when the kinds and counts of what it is being shown no
longer match what the record was written for, it says so on the node and in the
console before handing the prompt on. It says it rather than refusing — reusing a
description as a template is a legitimate thing to do, and only you know whether
that is what you meant. What no check can catch is one picture swapped for
another of the same kind; that is what the thumbnail is for.

**One switch decides, the window decides what.** Choosing a record switches
`repeat_last` on, because that switch is what hands a kept prompt on at all.
Turning it off gives the node back to the model and leaves the choice waiting;
**Write a new one** does both, forgetting the choice as well. The button says which
it is — `Library: Storm at sea`, or the same with `(repeat_last is off)` after it —
and the badge beside the node title reads `REPEAT` or `LIBRARY`.

The choice itself lives in a hidden widget, so it is saved with the workflow and
reaches an API run: a graph reopened tomorrow returns the same prompt. If the
record has been deleted since, the run stops and says so rather than quietly
writing a new one — the graph asked for a particular prompt.

**Sets.** `prompt_file` on the Options node names the file the nodes wired to it
save into and list, `global` unless you say otherwise, and **New prompt file**
beside it makes another. One file is one working set, which is the cheapest way to
keep a project's prompts apart from everything else. They live in

```text
ComfyUI/user/minimax_h3_rewriter/prompts/
```

as plain JSON, so they can be edited by hand, copied between machines or deleted.
A record with one thumbnail and a full-length prompt is about 11 KB.

### The guides are fetched, not bundled

The two guides are not shipped inside this pack. They are MiniMax's
Documentation, and the MiniMax H3 Community License grants the right to
redistribute the Materials only within its "Applicable Territory" — worldwide
*excluding* the European Union, the United Kingdom, the Republic of Korea and the
United States — and only alongside a copy of the agreement and a NOTICE file. A
node pack on a registry cannot honour a territory boundary, so each installation
fetches its own copy directly from MiniMax on first use: 16 KB and 24 KB, once,
under the same `auto_download` switch as everything else.

They land in

```text
ComfyUI/user/minimax_h3_rewriter/guides/
```

which the **Open guide folder** button opens. Editing a copy changes the system
prompt, and a fetch never overwrites a file that is already there — trimming the
guide is the cheapest way to fit a small model's context.

### The model list

The **Open model list** button on the rewriter node opens

```text
ComfyUI/user/minimax_h3_rewriter/models.json
```

in your desktop's JSON editor — on the machine running ComfyUI, which is not
necessarily the one looking at the browser tab. It is seeded from the packaged
copy on first use, so updating the node pack never overwrites your edits.

It holds five lists with the same fields. **`models`** feeds the LoRA rewriter
and has to be Qwen3.6-27B:

```json
{
  "name": "Qwen3.6-27B FP8",
  "repo": "Qwen/Qwen3.6-27B-FP8",
  "download_gb": 28.8,
  "vram": "~29 GB, no extra package needed"
}
```

**`models_8b`** feeds the 8B rewriter, has to be Qwen3-VL-8B-Instruct, and needs
an `mmproj` beside the model — being multimodal, it is two files:

```json
{
  "name": "Qwen3-VL-8B-Instruct GGUF Q4_K_M",
  "repo": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
  "file": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
  "mmproj": "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
  "format": "gguf",
  "download_gb": 5.4,
  "vram": "~9 GB with the adapter"
}
```

**`models_omni`** feeds the Omni rewriter, has to be Qwen2.5-Omni-7B, and takes
the same two files — with the difference that its projector has to carry an audio
encoder, or the adapter attaches to a model that cannot hear:

```json
{
  "name": "Qwen2.5-Omni-7B GGUF Q4_K_M",
  "repo": "ggml-org/Qwen2.5-Omni-7B-GGUF",
  "file": "Qwen2.5-Omni-7B-Q4_K_M.gguf",
  "mmproj": "mmproj-Qwen2.5-Omni-7B-Q8_0.gguf",
  "format": "gguf",
  "download_gb": 6.2,
  "vram": "~9 GB with the adapter and a 12k context"
}
```

Three lists rather than more entries in `models`, because an entry from any of
them would fail to load in either other node — different architecture, different
adapter. The three adapters live apart for the same reason, under `adapters`,
`adapters_8b` and `adapters_omni`.

**`writers`** feeds the writer nodes and can be anything, as long as it is a GGUF
language model with a chat template:

```json
{
  "name": "Qwen3.5-4B",
  "repo": "unsloth/Qwen3.5-4B-GGUF",
  "file": "Qwen3.5-4B-Q4_K_M.gguf",
  "format": "gguf",
  "download_gb": 2.6,
  "vram": "~5 GB with the guide in context"
}
```

**`captioners`** feeds the reference caption node and needs one extra field,
`mmproj` — a multimodal model is two files and both come from the same
conversion:

```json
{
  "name": "Qwen2.5-Omni-3B",
  "repo": "ggml-org/Qwen2.5-Omni-3B-GGUF",
  "file": "Qwen2.5-Omni-3B-Q4_K_M.gguf",
  "mmproj": "mmproj-Qwen2.5-Omni-3B-Q8_0.gguf",
  "format": "gguf",
  "download_gb": 3.4
}
```

#### Pointing an entry at a file you already have

`repo` need not be a Hugging Face id. Give it a **folder on this machine** and the
file is read straight out of it, with nothing downloaded and nothing copied:

```json
{
  "name": "Gemma 4 26B",
  "repo": "X:/models/gemma-4-26B-A4B-it-UD-Q8_K_XL",
  "file": "gemma-4-26B.gguf",
  "format": "gguf"
}
```

Or put the whole path in `file` and leave `repo` out — both forms work, in all
three sections, because both are what people actually type. Two things to watch:

- **Backslashes must be doubled in JSON**, or written as forward slashes.
  `"X:\Programs\..."` is not valid JSON at all — `\P` is not an escape — and one
  bad character makes the *whole file* unreadable, not just that entry. Windows
  accepts `X:/Programs/...` everywhere, so that is the easier habit.
- A path that does not exist is **reported, not downloaded around**. The node
  names the file it could not find and stops.

For a whole folder of GGUFs you keep elsewhere, an entry each is the long way
round: point ComfyUI's `extra_model_paths.yaml` at it under the key `LLM` and
every file in it is offered automatically, with `on disk:` in front of the name.

Add an entry, **refresh the browser tab** — ComfyUI need not restart — and it is
in the dropdown. Keep `name` stable: saved workflows remember the label, and a
node whose stored choice has vanished says so by name instead of silently picking
something else.

#### When the list itself is broken

A syntax error in `models.json` used to be the quietest failure in the pack: the
parse threw, the packaged defaults were served instead, and the dropdown looked
ordinary. An edit that never took was indistinguishable from an edit that did
nothing.

Now the first entry in every model dropdown says so, with the line and column:

```text
!! models.json is not valid JSON — Invalid \escape: line 3 column 30 (char 46) — showing the packaged list instead
```

The rest of the list is still there and ComfyUI still runs; picking that first
entry and hitting Run repeats the message and names the file to fix. Fix it,
refresh the tab, and it goes away.

#### Updates add new entries without touching yours

Your copy is never overwritten, but models added to the pack after you installed
**are merged in** — otherwise "we will not touch your list" quietly becomes "you
will never see anything new", with nothing anywhere to say the node knew about
more.

The rule is set algebra, not a version check. Beside the lists your file records
`seed_offered`: every name the packaged list has ever put in front of this
installation. An update then adds

```text
names in the pack  −  names in your file  −  names you were already offered
```

so an entry you deleted stays deleted, one you renamed is not duplicated, and a
genuinely new one arrives. Deleting a name from `seed_offered` offers that entry
again on the next start.

One exception, once: a file written before this existed has no record of what it
was offered, so on the first update everything missing comes back — including
anything you had deleted by hand. The previous file is kept beside it as
`models.json.bak`, and from then on your deletions stick.

Merges are logged to the ComfyUI console by name. A file the node cannot parse is
left exactly as it is and the packaged list is used for that session, so a stray
comma costs you a restart, not your edits.

## Where the weights go

```text
ComfyUI/models/LLM/
├── Qwen3.6-27B/                          # base model, ~52 GB
├── MiniMax-H3-Prompt-Rewriter-LoRA/      # adapter, ~3.5 GB
├── Qwen3.5-9B-Q4_K_M.gguf                # a writer model, one file
└── Qwen2.5-Omni-3B/                      # a captioner: model + mmproj together

ComfyUI/user/minimax_h3_rewriter/
├── models.json                           # your model list
├── guides/                               # the two writing guides, 40 KB
└── runtime/                              # llama.cpp binaries, if fetched
```

**Already downloaded the LoRA?** Point the `adapter` widget at that folder
directly (an absolute path is accepted) and nothing is fetched again.

## Using a model you already have

52 GB is a lot to ask, so the node looks for the base model before offering to
download it. The `model` dropdown lists every Qwen3.6-27B found in

- all directories ComfyUI registers for `LLM`, `transformers`, `diffusers`,
  `text_encoders` and `clip` — including anything mapped in through
  `extra_model_paths.yaml`, and
- the Hugging Face cache (`HF_HOME` / `HF_HUB_CACHE` / `~/.cache/huggingface/hub`),

so a copy pulled earlier by any other tool is reused instead of downloaded twice.
Only directories whose weights are actually present are listed; a cache entry
holding nothing but `config.json` is not offered.

### Smaller repackings

Any repository you put in the model list is checked from a 4 KB `config.json`
**before** a single weight moves. Every repacking keeps
the same fingerprint (`model_type qwen3_5`, hidden 5120, 64 layers, vocab 248320),
so what actually decides the outcome is the quantization runtime:

| Build | Download | Runtime package | LoRA attaches? | Verdict |
|---|---|---|---|---|
| `quant_method: bitsandbytes` (nf4 repack) | **~17 GB** | `bitsandbytes`, already required | yes | **works as shipped — the low-resource route** |
| `Qwen/Qwen3.6-27B` (bf16) | 52 GB | none; `bitsandbytes` for `nf4`/`int8` | yes | **works as shipped** |
| `Qwen/Qwen3.6-27B-FP8` | 29 GB | `kernels`, plus a kernel fetched from the Hub | yes | advanced only; needs >29 GB of VRAM |
| `quant_method: awq` | ~19 GB | `autoawq` | yes | works after installing it |
| `quant_method: gptq` | ~19 GB | `gptqmodel` | yes | works after installing it |
| `quant_method: compressed-tensors` | ~19 GB | `compressed-tensors` | **no** | not supported |
| `quant_method: modelopt` (NVFP4) | ~20 GB | `nvidia-modelopt` | **no** | not supported |

PEFT ships LoRA dispatchers for bitsandbytes, AWQ, GPTQ, HQQ, EETQ, AQLM and
torchao layers, and plain `nn.Linear` covers bf16/fp16 and FP8. For
compressed-tensors and modelopt there is no dispatcher, so the adapter cannot be
attached at all — no package will fix that.

> A repository's **name** is not its format: `cyankiwi/Qwen3.6-27B-AWQ-INT4` and
> `unsloth/Qwen3.6-27B-NVFP4` are both `compressed-tensors`, so neither can take
> the LoRA. The node reports the real `quant_method` and the exact reason before
> downloading anything.

### If the node says a package is missing

Nothing in the recommended routes needs one. bf16, the nf4 repacks and the
`nf4`/`int8` options all run on packages this pack already declares, so a
missing-package message only appears for a build you went looking for.

When it does appear it names the package **and the command for the interpreter
that is actually running ComfyUI** — `pip install kernels` typed into an
ordinary terminal installs into whichever Python is on PATH, which on a portable
install is never the right one, and the node goes on refusing:

```
This base model cannot run the prompt-rewriter LoRA.
  - the 'fp8' checkpoint needs the 'kernels' package, which is not installed in
    this Python environment. Install it with:
      "…\python_embeded\python.exe" -m pip install kernels
    Note: installing it is only half of it: the FP8 matmul is a Triton kernel
    that transformers then downloads from 'kernels-community/finegrained-fp8'
    on the first generation, and that needs a build matching this torch and
    CUDA version.
```

Run the line as printed and restart ComfyUI. The pack installs nothing into your
environment on its own, and never will: a node that silently pip-installs is a
node that can break an unrelated part of ComfyUI while you watch a progress bar.

## Smallest download without any extra install

A `bitsandbytes` nf4 repack of Qwen3.6-27B is **~17 GB to download and ~16 GB of
VRAM**, and it needs nothing this node does not already require. That is the
route to point people at: same code path as the official checkpoint, no new
dependency, a third of the download.

Those repacks are third-party — the node verifies the architecture from
`config.json` before fetching anything, which proves the shape is right, not that
the uploader is trustworthy. Judge that yourself, or make your own repack once
from the official weights.

## GGUF — smaller still, and nothing to install

Pick a `[gguf]` entry from the model list and the rewriter runs under llama.cpp
instead of Transformers. **No pip install is involved.** If `llama-cpp-python`
happens to be in ComfyUI's environment the node uses it; if it is not, it runs an
llama.cpp the machine already has, and failing that fetches the official binaries
(~34 MB, or ~511 MB where `llama_backend` resolves to CUDA — see the table below)
into `ComfyUI/user/minimax_h3_rewriter/runtime/` and runs
`llama-completion` as a subprocess. Same download switch as the weights:
`auto_download`.

The subprocess reloads the model on every run, which costs nothing in practice —
the node's default is `keep_model_loaded = False`, because the card is needed
for video generation the moment the rewrite finishes, and the in-process backend
already unloads after every run too. What the binary backend genuinely cannot do
is honour `keep_model_loaded = True`. Two things come free with it: VRAM is
returned by the operating system rather than by a deallocator, and a llama.cpp
crash takes down a child process instead of ComfyUI and its queue.

Two options in the options node control this, and they answer different
questions. **`gguf_runtime`** picks *what runs the model*:

| `gguf_runtime` | Meaning |
|---|---|
| `auto` | llama-cpp-python if it is importable, the binaries otherwise |
| `llama-cpp-python` | force the wheel; fails with a clear message if it is absent, rather than quietly using something else |
| `llama.cpp` | force the binaries, even when a wheel is installed — the way out when the installed wheel is broken |

Only `llama-cpp-python` can honour `keep_model_loaded`; the binaries hand the
model back to the operating system when the subprocess exits.

**`llama_backend`** then picks *which official build to fetch*, and applies only
when the binaries are in use:

| `llama_backend` | Download | Notes |
|---|---|---|
| `auto` | 34 or 511 MB | **the fastest build this machine can run, not the smallest.** CUDA on Windows with an NVIDIA card of compute capability 8.6, 8.9, 12.0 or 12.1 — the archive carries native SASS for exactly those and no PTX to fall back on — and Vulkan everywhere else |
| `vulkan` | 34 MB | NVIDIA, AMD and Intel alike; about half the CUDA throughput. Pick it explicitly to keep the download small on a card `auto` would send to CUDA |
| `cuda` | 511 MB | ~2× faster on NVIDIA; **Windows only** — upstream publishes no Linux CUDA build, so on Linux you compile one yourself and the node runs it |
| `cpu` | 17 MB | no GPU at all |

**An llama.cpp you already have is run as it is.** Before fetching anything the
node looks in four places, in order: the path in `MINIMAX_H3_LLAMA_BIN`, the
path written in `ComfyUI/user/minimax_h3_rewriter/llama_bin.txt`, the copy it has
already unpacked **for the backend in use**, then `PATH`. A build you compiled
yourself therefore needs no setting at all — put its `build/bin` on `PATH`, or
name it outright:

```sh
export MINIMAX_H3_LLAMA_BIN=/opt/llama.cpp/build/bin   # the folder, or the binary in it
```

**On a server, prefer the file.** An export reaches a server started from that
same shell and nothing else — a systemd unit, a container entrypoint or a
launcher script hands the process an environment of its own, and never reads
your `~/.bashrc`. One line in `llama_bin.txt` is read by the node itself and
does not care who started ComfyUI:

```sh
echo /opt/llama.cpp/build/bin > ~/comfy/ComfyUI/user/minimax_h3_rewriter/llama_bin.txt
```

`llama_backend` then stops mattering: it picks which archive to download, not
what an existing binary was compiled against. This is the road to a CUDA
llama.cpp on Linux — build it once with `-DGGML_CUDA=ON`, name it here, and
`device = cuda:0` does what it says. The caption nodes look for
`llama-mtmd-cli` beside it; a build without that target sends the node back to
the archive for that one job.

**The unpacked copy is per backend**, in `runtime/<release>-<backend>`, so an
archive already sitting in `runtime/b10310-vulkan` does not answer for a run that
resolved to CUDA — the node fetches that one too. Changing `llama_backend` is
therefore a second download, and unpacking a build by hand means putting it in
the folder named for the backend you will actually run.

A path pointing at nothing is an error rather than a quiet download, so a typo
says so instead of costing half a gigabyte. And when nothing is found anywhere,
the refusal prints what the ComfyUI **process** actually had — the variable, the
file, the folder and its `PATH` — instead of repeating advice you may already
have taken in a shell it never saw.

> **Why not the `llama-cpp-python` CUDA wheels.** Both current ones fail on
> ordinary consumer hardware, in two unrelated ways:
>
> | Wheel | Build flags | What happens |
> |---|---|---|
> | `v0.3.34-cu130` | `AVX512 = 1`, `ARCHS = 750..900` | weights load, then `llama_init_from_model` dies with `0xC000001D` — no consumer Intel 12th–14th gen chip has AVX-512 |
> | `v0.3.34-cu132` | `AVX512 = 0`, `ARCHS = 750..900` | reaches the first kernel, then `the provided PTX was compiled with an unsupported toolchain` — no `sm_120` in the list, so an RTX 50-series card falls back to JIT, which a driver older than the build's toolkit refuses |
> | `v0.3.34-vulkan` | `AVX512 = 0`, no arch list | works, and picks up `NV_coopmat2` where the driver offers it |
>
> The official release archives have neither problem. They carry **14** CPU
> backend variants and choose one at run time, which is why the same model runs
> fine under `llama-cli` on the machine where the cu130 wheel dies. And their
> CUDA archive carries native SASS with no PTX at all —
> `cuobjdump --list-elf` reports `sm_86 sm_89 sm_120a sm_121a` — so the driver
> is never asked to compile anything.
>
> If you want the in-process backend anyway, the Vulkan wheel is the one that
> works: `pip install https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.34-vulkan/llama_cpp_python-0.3.34-py3-none-win_amd64.whl`

| Base quant | Download | VRAM with the adapter |
|---|---|---|
| `Q4_K_M` | 15.7 GB | ~19 GB |
| `IQ4_XS` | 14.4 GB | ~18 GB |
| `UD-Q3_K_XL` | 13.5 GB | ~17 GB |
| `UD-IQ2_M` | 10.1 GB | ~13 GB, noticeably lower fidelity |

Lower `gpu_layers` in the options node to fit a smaller card, at the cost of
speed. With `Q4_K_M` fully offloaded to a high-end consumer NVIDIA card, CUDA
generates at roughly **50 tok/s** with the adapter against 78 tok/s without it —
that ~35% is llama.cpp doing the adapter's matmuls — and Vulkan at roughly half
the CUDA figure.

> **A smaller Qwen3.5 is not a substitute.** Qwen3.5-9B carries the same
> `general.architecture = qwen35` in its header, so it looks like a match and
> the model list will show it — but it has 32 blocks of width 4096 where the
> adapter needs 64 of 5120. llama.cpp refuses to attach the LoRA
> (`tensor 'blk.0.attn_gate.weight' has incorrect shape`) and the run fails.
> The node checks those two header numbers first and says so before anything is
> downloaded, and labels such files `(wrong size for the adapter)` in the
> dropdown. If you see a 9B producing a plausible-looking rewrite, it is running
> **without** the adapter: the format comes from the system prompt, not from the
> LoRA. That is not a dead end — it is precisely what the
> [writer nodes](#minimax-h3-prompt-writer-t2vai2vafl2val2va) do on purpose, with
> the full guide in the prompt instead of seven lines.

The GGUF route uses a **converted** adapter, not the PEFT one — F16 and Q8_0 of
[the 27B LoRA](https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-GGUF) and of
[the 8B one](https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-8B-GGUF). It is fetched
without asking; to use one of your own, drop the `.gguf` into `models/LLM` and
pick it from the options node's `adapter` list, or set `adapters.gguf.repo` in
the model list. The prompt is built from the GGUF's own chat template with
`enable_thinking=False`, and the result is byte-identical to what
`transformers.apply_chat_template` produces — the model sees exactly the text the
LoRA was trained on.

When the checkpoint carries its own quantization, the `quantization` widget is
ignored — bitsandbytes is not stacked on top of AWQ or FP8.

### Progress on the node

Downloads, weight loading, and token generation all report onto the node itself
through ComfyUI's own progress channels — a bar plus a caption with the current
file, transferred size, speed and ETA. No custom frontend extension is involved,
so nothing breaks when the ComfyUI frontend updates.

### Environment variables

| Variable | Effect |
|---|---|
| `HF_TOKEN` | Access token for gated or private repositories |
| `HF_ENDPOINT` | Mirror to download from instead of `huggingface.co` |

## Notes

- **Speed.** Qwen3.6-27B is a hybrid model: 48 of its 64 layers use linear
  attention. Without `flash-linear-attention` and `causal-conv1d` installed,
  Transformers falls back to a slower pure-PyTorch path and says so in the
  console. The fallback is correct, just slower; both packages are optional and
  awkward to build on Windows.
- **Determinism.** With `greedy` on, the same prompt, resolution, duration and
  seed produce the same rewrite, and ComfyUI caches the node accordingly.
- **Interruption.** Cancelling a run stops both a download and a generation in
  progress; a partial download resumes on the next run.
- **Format on the writer nodes is followed, not guaranteed.** A general model is
  obeying instructions rather than reproducing a distribution it was trained on.
  Keep `greedy` on — small models drift out of the format as soon as they sample —
  and if a field is missing the node returns everything it did get and names the
  gap on the node rather than failing.
- **A chat template that rejects a system role still works.** Gemma's calls
  `raise_exception` on one; the guide is then folded into the first user turn, as
  those models' own cards prescribe.
- **Listing your GGUF models is nearly free.** Building the dropdown needs six
  values from the first few kilobytes of each file, but `gguf.GGUFReader`
  materialises the whole header the moment it opens one — including
  `tokenizer.ggml.tokens`, a quarter of a million strings. Ten files in a model
  folder cost 31 seconds, paid the first time ComfyUI answered `/object_info`.
  The header is now walked directly and skipped past, which is the same six
  values in **0.4 s**. A half-downloaded file is still refused, by checking its
  tensor offsets against its size rather than by failing to map them.
- **The captioner writes media to a temporary folder** — an IMAGE and a VIDEO
  become PNGs, an AUDIO a 16-bit WAV written with the standard library — and
  removes the folder afterwards, including when the child process crashes.
- **A VIDEO is sampled here, not passed to `llama-mtmd-cli --video`.** That flag
  feeds the file to `ffprobe` through *stdin*, and when the MP4 carries its `moov`
  atom at the front — which is what "faststart" means, and what ComfyUI, phones
  and most of the web produce — ffprobe has what it needs after a few kilobytes
  and exits without reading the rest. llama.cpp is still writing the remaining
  megabytes into that pipe and blocks there for good: no output, no error, no end.
  Same clip with `moov` moved to the end runs in six seconds. Frames are decoded
  in-process instead, which also makes `max_frames` mean something for a VIDEO.
- The rewrite may add details a short prompt never stated. Review it before
  generating when identity, dialogue, timing or composition must be exact.

## Credits

The model work is entirely [LightX2V](https://github.com/ModelTC/LightX2V)'s —
this repository only wires their adapter into ComfyUI. If you find it useful,
star **[ModelTC/LightX2V](https://github.com/ModelTC/LightX2V)**, where the
MiniMax-H3 inference support and future rewriter tasks (FL2VA, Ref2VA) are
maintained.

| Component | Source |
|---|---|
| LoRA adapter | [lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA](https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA) |
| LoRA adapter, 8B | [lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B](https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B) |
| Base language model | [Qwen/Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) |
| Base language model, 8B | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) |
| Video/audio generator | [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) |
| Prompt-writing guides | [MiniMaxAI/MiniMax-H3 `docs/`](https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/docs) — fetched at run time, see above |
| Inference framework | [ModelTC/LightX2V](https://github.com/ModelTC/LightX2V) |

The prompt templates in `minimax_h3_rewriter/prompt_template.py` and
`prompt_template_8b.py` are reproduced byte-for-byte from their adapter
repositories; changing them degrades the rewrite.

Use of MiniMax-H3 is governed by the licence and acceptable-use terms in the
[official MiniMax-H3 repository](https://huggingface.co/MiniMaxAI/MiniMax-H3).

Thanks to [AxiomGraph](https://www.youtube.com/@AxiomGraph) for the first video
review of these nodes —
[«Stop Struggling With MiniMax H3 Prompts. Do This Instead.»](https://www.youtube.com/watch?v=h3rZTIRB_G8) —
and for the community workflows linked above.

## Licence

MIT — see [LICENSE](LICENSE). This covers the ComfyUI integration code only; the
model weights carry their own licences.
