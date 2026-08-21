# MiniMax-H3 Prompt Rewriter for ComfyUI

ComfyUI nodes for the [LightX2V MiniMax-H3 T2VA Prompt Rewriter LoRA](https://huggingface.co/lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA).
A short prompt goes in; a structured, production-ready audio-video description
for [MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) comes out — entirely locally.

[Русская версия](README_RU.md) · [Changelog](CHANGELOG.md)

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

| | Rewriter node | Rewriter 8B | Writer nodes |
|---|---|---|---|
| Where the format comes from | the LoRA — a 27B trained until H3 output came out of it unprompted | a second LoRA, on a model that also sees | MiniMax's own writing guide, in the system prompt |
| Model | Qwen3.6-27B only | Qwen3-VL-8B-Instruct only | any instruction-following GGUF |
| Smallest working setup | ~10 GB download, ~13 GB VRAM | ~6.1 GB download, ~9 GB VRAM | **2.6 GB download, ~5 GB VRAM** |
| Tasks | T2VA | T2VA, I2VA, FL2VA, L2VA | T2VA, I2VA, FL2VA, L2VA, Ref2VA |
| Reference frames | described to it in words | **it looks at them** | described to it in words |
| Quality | the reference | the same trained contract, at a third of the download; wobblier on the alignment line | close, and it runs on hardware the LoRA cannot touch |

Two of the three read text only. [Reference Caption](#minimax-h3-reference-caption)
turns an image, an audio clip or a video into the text they need — 3 to 5 seconds
per asset on a 3.4 GB model. When a whole shot's worth of references is waiting,
[Multi Reference Caption](#minimax-h3-multi-reference-caption) does all of them at
once — or [Universal Writer](#minimax-h3-universal-writer) describes them and writes
the prompt in the same node, with their order a widget you can drag rather than a
consequence of which slot you happened to use. The 8B rewriter needs none of that
for its reference *frames*: connect the picture and it reads it.

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
| Packages | `transformers`, `peft`, `accelerate`, and `bitsandbytes` for `nf4`/`int8`; `llama-cpp-python` for GGUF |

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
  equal to what you pass to MiniMax-H3, or the shot pacing will not match.
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
- `model` — a Qwen3-VL base and its projector, which are two files from the same
  conversion. Entries prefixed `on disk:` are pairs already in your model
  folders. **Only the 8B fits the adapter**; a Qwen3-VL of another size loads and
  then runs as a plain model with no rewriter, and the node says so before
  downloading anything.
- `task` — `T2VA`, `I2VA`, `FL2VA`, `L2VA`. The model's own name for these is
  T2AV, I2AV, FL2AV and L2AV; they are the same four tasks.
- `first_frame` / `last_frame` — optional IMAGE inputs. `I2VA` reads
  `first_frame`, `L2VA` reads `last_frame`, `FL2VA` reads both, `T2VA` reads
  neither. Connect the wrong one and the node says which is missing before
  anything loads — which end of the clip a picture belongs to is part of what
  the model is told.
- `keep_model_loaded` — **only `T2VA` can honour it.** The three tasks with
  frames run through `llama-mtmd-cli`, a fresh process each time, which takes
  the model with it when it exits.
- `options` — the same options node as everything else. Its `adapter` dropdown
  lists both LoRAs; the first entry picks whichever one matches the base model
  you chose, so it needs no attention.

**What it costs**

| | Download | VRAM |
|---|---|---|
| Q4_K_M base + projector + Q8_0 adapter | 4.7 + 0.7 + 0.7 GB | ~9 GB |
| Q8_0 base + projector + F16 adapter | 8.1 + 0.7 + 1.3 GB | ~13 GB |

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

### MiniMax-H3 Rewriter Options

Everything you rarely touch, kept off the main node. Leave it unconnected and the
rewriter uses the decoding parameters the adapter was published with.

![The Rewriter Options node, one output socket and fifteen widgets: max_new_tokens, temperature, top_p, top_k, repetition_penalty, attn_implementation, the adapter to apply, use_lora, auto_download, gpu_layers, n_ctx, gguf_runtime, device, llama_backend and trust_remote_code](docs/node_options.png)

| Input | Default | Purpose |
|---|---|---|
| `max_new_tokens` | 2048 | Generation cap |
| `temperature` / `top_p` / `top_k` | 0.7 / 0.8 / 20 | Sampling, used only when `greedy` is off |
| `repetition_penalty` | 1.05 | |
| `attn_implementation` | `sdpa` | `eager` or `flash_attention_2` if you have it |
| `adapter` | the LightX2V repo | Which build of the LoRA to apply — see below |
| `use_lora` | on | Turn off for the plain base-model baseline |
| `auto_download` | on | Turn off to fail loudly instead of fetching 52 GB |
| `device` | `auto` | Which GPU runs the language model — see below |
| `trust_remote_code` | **off** | Allow a checkpoint to run the Python it ships with — see below |

The same options node feeds the writer nodes and the captioner; `adapter` and
`use_lora` simply do not apply there.

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

![The Universal Writer node: five reference slots each with a checkbox on its own row, then a strip of four coloured squares reading subj 1 over ref_1, pic 1 over ref_0, vid 1 over ref_3 and aud 1 over ref_2 with a line of help under them, a task switch with Ref2VA lit, six aspect-ratio rectangles drawn to proportion with 1:1 chosen, a duration slider at 7.2, and the prompt and both model dropdowns below](docs/node_universal_writer.png)

*Four references on one growing socket — an image used as a subject, an image
used as a frame, a clip and a sound. The squares are not in slot order: `ref_1`
was dragged to the front, so it is the one the block will call `Subject 1`. The
number is a position and renumbers as things move; the slot name under it is
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

So one socket still produces the four labels Ref2VA allows, and the distinction
Multi Reference Caption makes structurally — a subject is not a frame — is made
here on the square instead.

**The task switch and the aspect-ratio picker are the same idea**: the choice is
the picture rather than a line of text in a dropdown. `Ref2VA` greys out while
nothing is switched on, and choosing it in that state stops the node before any
weights move rather than handing the writer an empty reference block.

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
> on each square is the way to switch a reference off. The same is true of the
> checkboxes on Multi Reference Caption.
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
5 s for a video.** It runs through the same llama.cpp binaries as everything else
— `llama-mtmd-cli` ships in the archive the rewriter already fetches, so a
machine that has run one rewrite downloads no runtime at all.

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
- `context_size` — `0` means the model's own, which is what its projector was
  sized against; one 1024×1024 frame is already twenty-odd media chunks. Lower it
  only to cut the KV cache, and know that too small a value fails the run instead
  of truncating it.

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

**The checkbox on a slot's own row switches it off** without unplugging anything.
A caption costs a model load and seconds to minutes, which makes "everything
except this one" the ordinary thing to want, and pulling the wire out to get it
throws away the wiring you meant to keep. The state is saved with the workflow
and travels through the API like any other value.

**A `videos` slot takes a `VIDEO` or an `IMAGE` batch**, whichever your loader
hands out — VideoHelperSuite's `Load Video (Upload)` wires straight in. Frames
are sampled evenly up to `max_frames` either way, so the cost of a clip stays
independent of its length.

**The block is written in the guide's order** — subjects, pictures, videos, audio
— rather than in wiring order, and each label is numbered within its own
category, continuing from whatever arrives on `previous`. So this node still sits
in a chain with single caption nodes on either side.

`model`, `length`, `seed`, `max_frames`, `context_size` and `bypass` are shared
by every asset in the node. `role`, `description` and `instruction` are gone on
purpose: the group is the role, and text you write yourself belongs to one asset
at a time. Keep [Reference Caption](#minimax-h3-reference-caption) for an asset
you want to describe by hand or ask a different question about.

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

It holds four lists with the same fields. **`models`** feeds the LoRA rewriter
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

A list of its own rather than more entries in `models`, because an entry from
either would fail to load in the other's node — different architecture,
different adapter. The two adapters live apart for the same reason, under
`adapters` and `adapters_8b`.

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

- all directories ComfyUI registers for `LLM`, `transformers` and `diffusers`
  — including anything mapped in through `extra_model_paths.yaml`, and
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
happens to be in ComfyUI's environment the node uses it; if it is not, the node
fetches the official llama.cpp binaries (~34 MB) into
`ComfyUI/user/minimax_h3_rewriter/runtime/` and runs `llama-cli` as a
subprocess. Same download switch as the weights: `auto_download`.

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
| `auto` → `vulkan` | 34 MB | NVIDIA, AMD and Intel alike; about half the CUDA throughput |
| `cuda` | 511 MB | ~2× faster on NVIDIA; **Windows only** — upstream publishes no Linux CUDA build |
| `cpu` | 17 MB | no GPU at all |

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

The GGUF route uses a **converted** adapter, not the PEFT one. It is fetched
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

## Licence

MIT — see [LICENSE](LICENSE). This covers the ComfyUI integration code only; the
model weights carry their own licences.
