# Changelog

[Русская версия](CHANGELOG_RU.md)

The version in `pyproject.toml`, the git tag and the release on GitHub always say
the same thing; the release workflow refuses a tag that disagrees with
`pyproject.toml`, or one that neither changelog has a section for.

## 0.18.2 - 2026-08-30

### Added

- **The answer is checked.** Every fresh answer is read back against the rules
  of MiniMax's own writing guides before it leaves the node: shot numbering and
  cut times against the requested duration, dialogue markup and its language
  tags, reference tags against both what the task can carry and what is
  actually connected, retention entries for every defined subject, the
  350-500-word guidance, and the fixed alignment line of the frame tasks. What
  it finds is said in three places -- a toast in the ComfyUI window with the
  first few findings, the node's caption with the full list above the answer,
  and the console -- and nothing is ever blocked: the model is sometimes right
  to bend a rule, and only you know whether this is that time. A clean answer
  adds not a single line. Prompts handed on from the library are not
  re-checked; they were checked when they were written.

  The rules live in one pure module, `minimax_h3_rewriter/checks.py`, with a
  test suite under `tests/` that runs without ComfyUI:
  `cd tests && ../.venv/Scripts/python.exe -m pytest`.

- **The `bypass` and `repeat_last` badges are always on the title bar**, not
  only when the node is collapsed or the switch is on. On the tall writers the
  widget itself is a screenful away, and a badge that appears and disappears
  moves its neighbour around; two badges in a stable row are a switch you can
  hit without scrolling, expanded or not.

- **The nodes' own warnings became toasts too**, under "Heads-up" instead of
  "Self-check": a reference the chosen task will not read (the 8B's spare
  frame, the 27B tab's unread frames, the Universal Writer's ignored T2VA
  references), a saved prompt whose references no longer match what the node
  is shown, and a captioner that came back with nothing for a connected asset.
  All of these were already said on the node and in the console; now they are
  also impossible to miss.

## 0.18.1 - 2026-08-30

### Added

- **A bundled example workflow** (issue #10). `example_workflows/` ships a
  ready graph that ComfyUI's template browser lists under this pack's name:
  the stock MiniMax-H3 Ref2VA and FL2VA templates with a Universal Rewriter
  and a Universal Writer wired in ahead of each branch. The checkpoints it
  names are the stock ones, offered for download when missing.

- **Qwen3.8-27B in the writer catalog.** `unsloth/Qwen3.8-27B-GGUF` joins the
  guided writers' list in four of unsloth's dynamic quants - Q2_K_XL (9.2 GB,
  a 16 GB card), Q3_K_XL (12.2 GB), Q4_K_M (15.3 GB, a 24 GB card) and the
  near-lossless Q6_K_XL (23.6 GB, a 32 GB card) - the strongest prose the list
  offers at every one of those sizes. The writers are where it belongs:
  Qwen3.8-27B has 65 layers where the 27B rewriter's LoRA was trained on
  Qwen3.6-27B's 64, so the adapter cannot ride it - as a rewriter base it
  would run as a plain model. The entries are merged into existing
  installations' `models.json` the usual way; nothing you edited is touched.

- **The model scans look in `text_encoders` and `clip` now, and the adapter
  scan in `loras`.** ComfyUI's own GGUF loaders file Qwen-VL encoders under
  `text_encoders` and `clip`, so that is where people already keep the files -
  and a scan that skipped those folders read as "can't find any models"
  (issue #11 and more than one report since). `models/LLM` stays the
  recommended home; the wider scan is for the files already living elsewhere.
  The architecture and shape filters still apply, so the new folders add the
  models that fit rather than everything they hold - and the guided writers,
  which accept any architecture, now skip the encoder halves of image
  pipelines and embedding models (`t5`, `t5encoder`, `bert`, `nomic-bert`,
  `nomic-bert-moe`, `clip`): no chat template, nothing to say, and every
  `text_encoders` folder has one.

### Fixed

- **Workflows saved before 0.17.2 load with the right Options values again**
  (issue #11). ComfyUI restores a node's widget values by position, and 0.17.2
  put `merge_lora` into the middle of the list, between `use_lora` and
  `auto_download`. Every Options node saved before that came back one slot
  askew: `merge_lora` showed a boolean, `gpu_layers` got `n_ctx`'s 8192,
  `n_ctx` read `auto` as NaN -- and validation refused the whole graph with
  four errors that named none of this.

  The node now recognizes the old layout on load -- a boolean in
  `merge_lora`'s slot, which a combo never saves -- and deals the values back
  onto the widgets they were written from, with `merge_lora` at its `auto`
  default. Nothing to redo by hand; reloading the workflow is enough.

- **The Universal Rewriter's `duration` is a `FLOAT` now, like the Universal
  Writer's.** As an `INT` it refused the wire from any float seconds source
  that the writer accepted. The value also stays fractional all the way down:
  the 8B path used to truncate it on the way into the request, and now `7.5`
  arrives as `duration: 7.5s` while whole numbers keep rendering exactly as
  before -- `10s`, not `10.0s`. The Omni tab already took fractions and snaps
  them to the frame grid; the standalone rewriter nodes keep their integer
  widget.

## 0.18.0 - 2026-08-29

### Added

- **A prompt library.** `repeat_last`, added in 0.17.3, holds one answer per node
  and forgets it when ComfyUI restarts. This is the other half: prompts you name
  and keep, in a JSON file under the ComfyUI user directory, available to every
  workflow and every node.

  **Save the last prompt** on any writer or rewriter opens a box asking for a
  name, a description and any number of groups -- the groups already in the file
  come back as chips, a new one is a word and Enter. What is saved is the run
  itself: the text, the sections after it, the task, the model, the ratio, the
  duration, the seed, and a 50x50 thumbnail of every reference the node was
  shown.

  Those thumbnails are taken **during the run**, which is the only moment they
  can be: by the time a save box opens the node has returned strings and the
  tensors are gone. So the session record and the library record are one shape,
  and saving is a matter of naming something already complete. Only what
  genuinely exists is measured -- an image has a size; a clip that arrived as a
  real `VIDEO` has frames, seconds and a frame rate, while one that arrived as a
  batch of images has a frame count and nothing else, having no container to
  ask; a sound has a duration, a rate and a channel count. References are
  labelled by position, `ref1-image` and `ref2-audio`, rather than by a file name
  a node cannot see.

  **Prompt library** opens the list: the node's own last answer first, then the
  file, newest first. Filter by group with the chips, or search across names,
  descriptions, prompts, groups, references and settings at once. **Use** points
  the node at a record, **Delete** removes it for good.

  A chosen prompt reaches `rewritten_prompt` and the section outputs exactly as a
  fresh one would -- no model is loaded, nothing is generated, and the run takes
  about a tenth of a second against the fifty a real rewrite costs. A record
  written by another kind of node works too: matching classes hand the outputs
  back verbatim, and otherwise the text is split into sections by the node
  reading it, the same way it splits an answer it wrote itself.

  The choice lives in a hidden widget, so it is saved with the workflow and
  reaches an API run -- a graph reopened tomorrow returns the same prompt. A
  record deleted since stops the run and says so rather than quietly writing a
  new one: the graph asked for a particular prompt.

- **A saved prompt is checked against the references the node is actually being
  shown.** A T2VA prompt is self-contained, but a prompt for a frame task or for
  Ref2VA names its references and describes them inside the text, so reusing one
  over a different set of assets describes something that is not there. When the
  kinds and counts no longer match what the record was written for, the node says
  so -- on the node, in the console -- and hands the prompt on anyway, because
  reusing a description as a template is a legitimate thing to do. The thumbnails
  on the card cover what no check can: one picture swapped for another of the same
  kind.

- **Copy buttons in the library window**, on every record and on the Last Prompt
  row, and beside Save in the save box. They put the prompt on the clipboard
  without pointing the node at anything.

- **`prompt_file` on the Options node, and a `New prompt file` button beside
  it.** One file is one working set, `global` unless a workflow says otherwise,
  and the nodes wired to that Options node save into it and list it. The files
  are plain JSON in `ComfyUI/user/minimax_h3_rewriter/prompts/`, so they can be
  edited by hand, copied between machines or deleted. A record with one thumbnail
  and a full-length prompt is about 11 KB.

- **A badge for `repeat_last`, next to the one `bypass` already had.** It reads
  `REPEAT` when the node is handing back its own last answer and `LIBRARY` when a
  saved prompt is driving it, shows the dim switch name on a collapsed node, and
  toggles the switch when clicked -- the same behaviour as the bypass badge, which
  it now shares its implementation with. It does not recolour the node: bypass
  does that, the two appear together often, and one colour cannot mean both.

### Changed

- **`repeat_last` is the one switch, and the library window only chooses what it
  hands back.** Picking a saved prompt switches it on; switching it off gives the
  node back to the model and leaves the choice waiting, and `Write a new one` in
  the window does both. Before this, a saved prompt applied whatever the switch
  said, which left the visible control doing nothing while an invisible one
  decided -- the button now reads `Library: <name>`, with `(repeat_last is off)`
  after it when that is the case.

## 0.17.3 - 2026-08-29

### Added

- **`repeat_last`, on every writer, rewriter and captioner.** A switch that hands
  back the answer the node produced last instead of running the model again. With
  nothing kept yet the node runs once, keeps what it wrote and says so; from then
  on it returns that same text for as long as the switch is on, whatever else
  changes. That is the point of it: a fifty-second rewrite is not something to pay
  for twice while wiring up everything downstream of it.

  ComfyUI's own cache cannot do this. A node's cache key is its class, its
  `IS_CHANGED` value and every input it received, so editing the prompt is exactly
  what drops the entry, and `IS_CHANGED` can only add invalidation, never mask it.
  So the answers are kept in a store of the pack's own, one per node.

  The store lives in memory for the ComfyUI session and nowhere else: it is not
  written to disk, it does not travel with the workflow, and a restart empties it.
  Every run says where its answer came from in three places -- the caption under
  the node, the ComfyUI console, and the switch's own tooltip, which carries the
  time it was kept, its length and the opening of the text. When the inputs no
  longer match the ones that produced it, all three say so rather than pretending
  nothing changed. `bypass` still wins over it.

  On the two caption nodes it is the caption that is kept, not the assembled
  block: the numbering belongs to the chain, so the line is written again around
  whatever arrives on `previous`, and an asset connected since is described for
  real.

### Fixed

- **The `bypass` badge and tint were missing from three nodes.** The purple
  colour and the BYPASSED chip that mark a switched-off node come from an
  extension with a list of node types in it, and that list was never extended
  when the 8B rewriter, the Omni rewriter and the Universal Rewriter were added.
  All nine nodes with a `bypass` switch now look alike, collapsed or not, and the
  chip on a collapsed node toggles the switch as it does everywhere else.

## 0.17.2 - 2026-08-29

### Added

- **`merge_lora`, and about twice the tokens a second on the safetensors
  route.** PEFT keeps an attached adapter beside the base model and computes it
  on every token, on top of the base weights; folding it into those weights once
  at load does the same arithmetic ahead of time. Measured on Qwen3-VL-8B with
  the 8B adapter, 128 tokens on a 5090: **14.3 tokens a second attached against
  25.0 folded in** on `bfloat16`, and 13.6 against 25.1 on `nf4`. The merge
  itself costs 0.07 s on an unquantized base and 4.6 s on a bitsandbytes one,
  which has to be dequantized and quantized back a layer at a time.

  `auto`, the default, takes the free half: it folds the adapter in on an
  unquantized base and leaves it attached on `nf4`/`int8`, where the cost is
  real and so is the extra rounding. `on` folds it in there as well; `off` is
  the behaviour up to now. The GGUF route is untouched -- llama.cpp applies an
  adapter its own way, and `merge_lora` says nothing about it.

  **A folded run is not word-for-word the attached one.** `W + BA` computed once
  is not bit-for-bit `Wx + B(Ax)` computed per token, so a token here and there
  falls differently and the sentence follows it: two differences in 128 tokens
  on `bfloat16` at the same seed, more on `nf4`, where PEFT prints a warning of
  its own about the requantization. Neither answer is the better one. A workflow
  tuned to the token is a reason to set `off`.

  Worth recording what turned out not to be the problem, since the measuring was
  the work: reusing a loaded model costs 0.00 s, the tokenizer 0.28 s and the
  processor 0.48 s against a 6-15 s load, and `device_map` pinned to one card
  came out level with `"auto"`. The adapter was the whole of it.

## 0.17.1 - 2026-08-28

### Added

- **32:9 and 48:9**, for the frame that spans more than one monitor -- 32:9 is
  two 16:9 screens side by side, 48:9 three. The value travels as text, one line
  of the task message, and neither this pack nor MiniMax's guides parse it, so
  the list was free to grow. Worth knowing before you reach for them: no adapter
  was trained on those strings, so the model composes wider by reading the
  request rather than by recognising a shape it was taught.

  The picker had to grow with it. Every rectangle is drawn to its own proportion
  inside one budget, so the wide end reads as how little height is left, and at
  the old budget 32:9 and 48:9 came out 8 and 6 pixels tall -- the second one
  clamped, both of them the same line. The budget is wider now and the floor
  lower, which steps 21:9, 32:9 and 48:9 apart at roughly 15, 10 and 6 pixels. A
  node too narrow to hold eight of them wraps the row onto a second line and
  grows by exactly that much, rather than cutting the last rectangles off.

  Requested by [@Geese586](https://github.com/Geese586) in
  [#9](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/9).

- **`aspect_ratio`, an input on every writer and rewriter**, because the shape of
  the frame is usually decided elsewhere in the graph and spelled differently
  there. ComfyUI's own Resolution Selector calls 16:9 `16:9 (Widescreen)`; a size
  node says `3840x1080`; a divider says `1.78`. All three are read, and a label
  around the pair is read through -- the number pair is what counts, wherever it
  sits. A frame size within 2% of a listed ratio is called by that ratio's name,
  which is what turns `1376x768` -- the Resolution Selector's own answer for 16:9
  at 1 MP -- into `16:9` rather than into `43:24`. A ratio that is on no list
  passes through as itself, which is how `2.39:1` and `5:4` get in, and something
  that is not a ratio at all is refused by name rather than composed for.

  The socket takes a `STRING` or a `COMBO` link, so the primitive that already
  drives the Resolution Selector drives this from the same wire. Draw that one
  from the selector first: a primitive adopts the type of whatever it is plugged
  into first, and a primitive that has become a STRING has nothing a COMBO widget
  will take.

  It outranks the picker while it is connected, and the interface says so: the
  squares dim, nothing stays lit, clicks are refused. Unplugging clears the
  field, because the upstream node writes its value into the widget -- that is
  how a wire feeds a widget input at all -- and a value left sitting there would
  go on outranking a picker that has just lit up again.

- **The Open model list button on the Prompt Rewriter Omni node**, which was the
  one node in the pack without it. Its tooltip now names `models_8b` and
  `models_omni` beside `models`, since three rewriters read three sections and
  the button opens the file for all of them.

### Changed

- **The ratio picker has no socket any more.** `resolution` is `socketless`, on
  every node that has it: a ratio arriving from the graph belongs on
  `aspect_ratio`, which reads the spellings other nodes actually use, and two
  doors into one setting meant the parser could be walked around. A workflow that
  had `resolution` converted to an input loses that link when it loads -- the
  input it pointed at no longer exists -- and the value it was feeding is worth
  checking on the picker afterwards.

### Fixed

- **The buttons stop appearing in the prompt.** `Open model list` and `Open guide
  folder` are canvas buttons with no value, but they still reached the API-format
  export as `"Open model list": null` -- an input the node never declared, in
  every workflow using this pack. `serialize = false` only keeps a widget out of
  the saved `widgets_values`; the prompt needs `serializeValue` as well. ComfyUI
  drops unknown keys, so this was noise rather than a failure.

- **Four things the README said that the code did not do.** The captioner was
  described as costing no runtime on a machine that had run one rewrite, which is
  true only when that rewrite went through the binaries: `gguf_runtime` defaults
  to `llama-cpp-python` wherever the wheel imports, and a safetensors base never
  touches llama.cpp at all, so for most installations the first caption is what
  pays for the archive. `llama_backend = auto` was documented as Vulkan at 34 MB
  when it takes CUDA -- 511 MB -- on Windows with an NVIDIA card of compute
  capability 8.6, 8.9, 12.0 or 12.1. The unpacked runtime was described as "its
  own `runtime/` folder" when the lookup is per backend, so a Vulkan build
  unpacked by hand does not answer for a run that resolved to CUDA. And the
  requirements table listed `llama-cpp-python` as what GGUF needs, against a
  section three pages down headed "nothing to install".

## 0.17.0 - 2026-08-26

### Added

- **MiniMax-H3 Prompt Rewriter Omni**, for LightX2V's third adapter - the first
  that hears. It is trained on Qwen2.5-Omni-7B, the same model this pack's
  captioners already use, so a reference reaches it as the asset itself rather
  than as a sentence somebody wrote about it: the picture, the clip, or the
  sound. It is also the only one of the three trained on **Ref2AV**, the
  full-reference task, which answers with six fields instead of three.

  One growing socket takes an IMAGE, a VIDEO or an AUDIO, so there is no wrong
  socket to plug into: what a reference is called follows from what it is, and
  pictures are numbered among pictures, sounds among sounds. The strip below
  shows what is connected, in what order, and what each will be called - and
  that order is the ordering: drag the second picture to the front and it
  becomes `<Picture 1>`. There is deliberately no relabelling here, unlike the
  Universal Writer's strip: the socket settles what a reference is, and a
  *subject* is something this adapter produces in `subject_definitions`, not
  something the request supplies.

  `duration` is snapped before it is written into the turn. MiniMax-H3 generates
  on a 17n+5 frame grid at 24 fps, so most lengths do not exist: ask for 10
  seconds and it is 243 frames, 10.13 s, and that is the number the alignment
  line quotes back. The widget stays what you meant.

- **The Omni adapter as GGUF**, converted from LightX2V's safetensors with
  llama.cpp's `convert_lora_to_gguf.py` and published at
  [pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-Omni-GGUF](https://huggingface.co/pytraveler/MiniMax-H3-Prompt-Rewriter-LoRA-Omni-GGUF)
  in `F16` (0.65 GB) and `Q8_0` (0.34 GB, same rewrite behaviour). Which means
  the Omni rewriter runs on the llama.cpp route with nothing installed, at a 6.2
  GB download rather than the 22.4 GB the safetensors base costs.

- **`models_omni` and `adapters_omni` in `models.json`**, holding the
  Qwen2.5-Omni-7B bases in both shapes the adapter is published for. Two kinds
  of near-miss are marked in the list rather than hidden: a Qwen2.5-Omni-**3B**
  is `(wrong size for the adapter)`, and a **Qwen2.5-VL-7B** - the same
  architecture string, the same 28 blocks, the same width, so the adapter *would*
  attach - is `(vision only, not an Omni build)`, told apart by its projector
  having no audio encoder.

- **A third tab on the Universal Rewriter, and Ref2VA with it.** Same prompt,
  same two frames, third adapter, one click. `model_omni` and `quantization_omni`
  belong to the tab; everything above them is shared, as before.

  The tab also brings the fifth task, and two sockets to feed it:
  `reference_video` and `reference_audio`, read by `Ref2VA` and by nothing else -
  the other two adapters have no ear, and the four frame tasks take pictures
  alone, so a sound connected to `FL2VA` is refused by name rather than quietly
  dropped. On `Ref2VA` everything connected is a reference in socket order:
  `first_frame`, `last_frame`, the clip, the sound.

  Its four extra outputs are appended **after** the three every task fills rather
  than interleaved with them, because ComfyUI addresses an output link by its
  slot index: putting `subject_definitions` in the middle would have moved
  `overall_soundscape` in every workflow already built on this node. The same
  trap as widget positions, one layer down.

  Four references and no strip, where the Prompt Rewriter Omni node takes twelve
  and lets you drag them: order is the whole labelling rule, and with four
  sockets the order is the order of the sockets.

### Fixed

- **A section deleted from the model list stays deleted.** Removing entries one
  at a time already stuck -- `seed_offered` records every name the packaged list
  has ever put in front of you, so anything you take out is not put back. But
  removing a whole section did not: the merge read an absent section as "this
  installation predates it" and copied the packaged one in wholesale, every
  start, forever. Which made a section the one edit the file would not keep,
  in the one file whose whole point is that it is yours to edit.

  The two situations are told apart by the same record the entries use. An
  installation that predates a section has never been offered its entries and
  gets all of them; somebody who deleted the section has been offered every one
  and gets none. Found while checking that `models_omni` and `adapters_omni`
  reach an existing user file - they do, and this was underneath.

- **A reference frame no longer arrives at the vision tower full size.** Qwen
  spends a token per 28x28 block, so a 1616x1616 picture - which is what a
  ComfyUI workflow hands over without thinking about it - is 3249 tokens. Two of
  them overflowed an 8k context before a word of the prompt was counted, and
  `FL2AV` died at `decode: failed to find a memory slot for batch of size 1316`
  with both pictures already encoded. `I2AV` with one picture survived, which is
  what made it look like a broken task rather than a budget.

  The size is not the only thing wrong with that. LightX2V's own inference
  scripts cap a picture on the processor - 301056 pixels for the Omni adapter,
  1024x1024 for the 8B - so a full-size frame is also a shape neither model saw
  in training. Both nodes now scale to their adapter's own ceiling, and a frame
  taken from a clip is capped harder still, at 100352 pixels, for the reason a
  clip is many pictures.

  The context is then sized to what the turn actually costs, measured off the
  written files rather than guessed, so a `Ref2AV` with eight references widens
  it instead of failing.

- **The Omni safetensors base is no longer refused as the wrong model.** The
  check that reads `config.json` looked one level in, at `text_config`.
  Qwen2.5-Omni keeps its language model two levels down, under
  `thinker_config.text_config`, because the checkpoint holds a talker and a
  vocoder as well - so the top level has no `hidden_size` at all and the base the
  adapter was trained on was reported as `not Qwen2.5-Omni-7B`. It now walks
  down.

- **And it now loads.** `AutoModelForImageTextToText` has no entry for
  `qwen2_5_omni`, only for the thinker inside it, which is the half the adapter
  was cut for and the only half that writes anything - so even past the check
  above the load would have failed with "unrecognized configuration class". The
  loader descends the same nested configs and takes the class from Transformers'
  own mapping.

- **A full KV cache no longer reports itself as an unreadable projector.** `find
  a memory slot` was not among the strings the failure hint recognised, so it
  fell through to the note about projector formats and sent the reader off to
  change models when the fix was one number.

## 0.16.6 - 2026-08-25

### Added

- **Each reference can be asked its own question.** Under every square on the
  strip there is now a narrow band in the same colour: dark while the reference
  is asked its role's usual question, lit once it is asked something else. Click
  it to write that question, right-click to take it back, hover to read it. It
  is per reference rather than per node because one node describes a picture, a
  clip and a sound at once, and a single box would have had to ask all three the
  same thing.

  **Two modes, and the band says which.** `+ instr` adds the line to the role's
  question; `= instr`, on a solid band, asks it *instead*. Both are needed and
  neither can be the only one: "never mention the window" is a rule, and asked
  on its own it leaves nothing wanting a description - the model answers the
  rule, with "No". "Always answer 'blah blah blah'" is a whole question, and
  leaving the role's question in front of it makes a small model settle the
  contradiction by describing anyway. A replacement drops the length preset too,
  since whoever writes the question owns the shape of its answer.

  The text rides in a hidden JSON widget, so it travels with the workflow and
  through the API like every other setting. A bare string there is read as an
  addition, which is the safe half for anything hand-written.

  Requested by [@808charlie](https://github.com/808charlie) in
  [#8](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/8).

- **`system_prompt` on the writers**, which is what aims them at something other
  than MiniMax-H3. The assembled guide is replaced wholesale by what you write
  there, and the guide is then not even fetched - so a system prompt written for
  LTX, Krea or Wan turns these into writers for those, offline, with no document
  downloaded to be ignored. The shortest way in is the
  `MiniMax-H3 Guide Prompt (any LLM)` node, which hands you the stock prompt on
  an output: take it, edit it, connect it back.

  The task message is never replaced - it carries the prompt, the aspect ratio
  and the duration, which any guide needs. And the answer is still split into
  the H3 sections, so a guide that replies with a paragraph fills
  `rewritten_prompt` and leaves the section outputs empty.

  Requested by [@808charlie](https://github.com/808charlie) in
  [#8](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/8).

### Fixed

- **A captioner's reasoning no longer ends up in the reference block.** The CLIP
  captioner has always cut the `<think>` block out of an answer; the GGUF one
  never did, so an empty `<think> </think>` opened every caption and a thinking
  model wrote its whole deliberation into `reference_assets` - from where it
  travelled on into the writer's prompt. Both paths cut it now.

  The cut alone is not enough, and cannot be: the writers render the chat
  template themselves and pass `enable_thinking=False`, while a caption cannot,
  because `llama-mtmd-cli` applies the template itself - that is what puts the
  media tokens in the right place - and publishes no switch for the thought
  channel. Reasoning is therefore still charged against `--predict`, which is
  how a thinking model produces a page of deliberation and half a sentence of
  caption. So every caption question now ends with a line asking for the
  description alone, which is the only instrument this path has.

  Reported by [@808charlie](https://github.com/808charlie) in
  [#8](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/8).

### Changed

- **Multi Reference Caption draws the same strip as the Universal Writer.** Its
  switches were drawn straight onto the canvas, which the Nodes 2.0 renderer
  does not draw at all; the squares are HTML, which both renderers do. So they
  are one mechanism now, and the node that had checkboxes has coloured squares
  instead: click one to silence its reference, read the label to see what it
  will be called and in what order. Dragging is not among them - this node
  writes the block in the guide's own order and the group an asset is plugged
  into decides its label.

- **New widgets go at the end of a node, always.** ComfyUI restores a saved
  node's widget values by position, so a widget added anywhere else hands every
  widget below it its neighbour's value: a workflow saved before the upgrade
  came back with `max_frames` holding what `context_size` had. Nothing shipped
  in that state, but it is worth writing down, because the obvious place to put
  a new setting is next to the one it belongs with.

## 0.16.5 - 2026-08-24

### Fixed

- **A captioner no longer reserves the whole context the model was trained on.**
  `--ctx-size 0` reads as "let llama.cpp decide" and means "the context this
  model was trained for", and the packaged captioners hid the difference:
  Qwen2.5-Omni asks for 32k and fits anywhere. Qwen3-VL asks for 262144 tokens,
  and its cache is 36 layers of 8 KV heads at 128 dimensions, K and V, in f16 -
  144 KiB a token, so 36 GiB of KV cache allocated before a single pixel has
  been read. On a 32 GB card the run died at `failed to allocate buffer for kv
  cache`, having never looked at the picture, and llama.cpp's own auto-fit could
  not rescue it because the node pins `--n-gpu-layers`.

  0 now means "what this run needs, and never more than the card can hold". The
  context length and the shape of the cache are read from the GGUF header the
  model scan already parses, and sized against the number of references and the
  memory of the device the run is bound for. A number typed into `context_size`
  is still honoured exactly as typed, and a header too thin to size against
  falls back to letting llama.cpp decide, as before.

  Reported by [@808charlie](https://github.com/808charlie) in
  [#7](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/7).

### Changed

- **A failed caption says which way it failed.** The note about projector
  formats was printed on every non-zero exit from `llama-mtmd-cli`, so an
  out-of-memory came back dressed as a model mtmd cannot read - and sent the
  reader to study the model while the child had already said `cudaMalloc
  failed`. An allocation that did not fit and a context too small for the frames
  now each get their own answer, naming the widget that moves them, and the
  projector note is left for the exits that are actually about the projector.

- **The captioner scan stops narrating.** A model sharing a folder with an
  mmproj it could not be paired with printed a line naming every projector in
  that folder, at INFO. A flat `models/LLM` holding a dozen unrelated quants and
  five projectors therefore announced twelve models by five names each - four
  times over, once for every node that offers a captioner dropdown, every time
  ComfyUI asked for the node definitions. One line per folder now, at DEBUG.

  Reported by [@808charlie](https://github.com/808charlie) in
  [#7](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/7).

## 0.16.4 - 2026-08-24

### Changed

- **An installed `llama-cpp-python` no longer looks like something that should
  have spared a caption run its download.** It cannot: a reference asset goes
  through `llama-mtmd-cli`, a program, and the wheel is a set of shared
  libraries loaded by ctypes with no executables in it at all - a CUDA one
  compiled from source included, since that build produces `libllama.so` and its
  neighbours and no CLI targets. So a machine with a perfectly good wheel still
  fetches the release archive to caption an image, and the only account of it
  was 32 MB going past.

  The captioner now says so before the download rather than after, in the log,
  and again in the refusal when there is no download to be had. `gguf_runtime`
  is a writer setting; the caption nodes have no wheel path at all.

- **`llama_backend = cuda` on Linux says what a build of your own is, and what
  is not one.** The refusal already said that upstream publishes no Linux CUDA
  archive and that a build you compiled is run as it is. What it left out is
  that `llama-cpp-python` is not such a build, whatever it was compiled with -
  the reasonable next thought for anyone who has just built the wheel with
  `-DGGML_CUDA=on` and expected the caption nodes to follow. It now names the
  two cmake targets that produce the missing programs, and `llama_bin.txt`
  beside `MINIMAX_H3_LLAMA_BIN` and `PATH`. On a caption run it carries the
  search report too, and that report names `llama-mtmd-cli` rather than the
  completion binary nobody was looking for.

- **A runtime download that cannot reach GitHub prints the way to do it by
  hand.** The asset URLs, the folder to unpack them into, and the one thing that
  goes wrong quietly when a file manager does the unpacking: the shared
  libraries are symlinks and have to stay symlinks. A stack of identical
  connection errors is not advice on a machine that is deliberately offline.

  Reported by [@808charlie](https://github.com/808charlie) in
  [#7](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/7).

## 0.16.3 - 2026-08-22

### Changed

- **A GGUF that will not load now says why.** llama-cpp-python raises one
  message for every load failure - `Failed to load model from file:` and the
  path - and llama.cpp's own explanation goes to the C-level stderr this backend
  suppresses, so the commonest failure of all arrived with no reason attached:
  a model whose architecture is newer than the llama.cpp compiled into the
  installed wheel. Qwen3-VL needs a build from 2025-10-30 onwards and Qwen3.5
  one from 2026-02-10, while llama-cpp-python 0.3.16 carries llama.cpp from
  2025-08-14 - so plain Qwen3 loads there and every newer family fails
  identically, which reads as the node being broken.

  The refusal now names the architecture, read out of the file's own header, and
  says whether this build has a loader for it, read out of the architecture
  table compiled into the library. It has to be read that way: the C API
  publishes neither that table nor the build it came from, and the package
  version answers nothing either, since forks number themselves and any version
  can be built against any llama.cpp. A stripped or packed library, where the
  search would prove nothing, falls back to the general message rather than
  accusing the wrong thing.

  Reported by [@808charlie](https://github.com/808charlie) in
  [#4](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/4).

## 0.16.2 - 2026-08-22

### Fixed

- **The llama.cpp runtime unpacks on Linux and macOS.** The guard around tar
  extraction refused every symlink outright, and the official Linux and macOS
  archives ship their shared libraries as SONAME symlinks -
  `libllama.so.0 -> libllama.so.0.0.10310`, ten of them in each Linux build and
  eighteen in the macOS one - so the very first download died on
  `refusing archive member outside the target: 'llama-b10310/libllama.so.0'`
  and no machine outside Windows ever got a runtime at all. Windows was never
  affected: its archives are zip, and that branch has a check of its own.

  Those links are not decoration. `ldd llama-completion` resolves
  `libllama.so.0`, `libggml.so.0` and `libggml-base.so.0` - the links
  themselves - so keeping the files and dropping the links would have unpacked
  a runtime the loader still refuses to start.

  A link is now refused only when its target really does leave the destination,
  which is the zip-slip risk the guard is there for. The two kinds are resolved
  from the places `tarfile` resolves them from: a symlink's target relative to
  the link's own directory, a hardlink's relative to the extraction root. One
  base for both would count the link's own depth twice and wave through
  `build/bin/x -> ../../secret`, two levels above the destination, as though it
  were `secret` inside it. An absolute target is refused outright.

  Reported and fixed by [@ViolinKaine](https://github.com/ViolinKaine) in
  [#3](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/pull/3),
  against release b10310 on Linux.

## 0.16.1 - 2026-08-22

### Added

- **A path in a file, for a server whose environment is not yours to set.** One
  line in `ComfyUI/user/minimax_h3_rewriter/llama_bin.txt` — the folder your
  llama.cpp was built in, or the binary itself — and the node runs that build.
  It is read after `MINIMAX_H3_LLAMA_BIN` and before everything else.

  This is what `MINIMAX_H3_LLAMA_BIN` cannot do on a server. An export reaches a
  server started from that same shell and nothing else: a systemd unit, a
  container entrypoint or a launcher script hands the process an environment of
  its own and never reads your `~/.bashrc`, so the variable is simply absent
  where it matters — and so is anything you added to `PATH` the same way. The
  file is read by the node itself, and does not care who started ComfyUI.

### Changed

- **When no llama.cpp is found anywhere, the refusal prints what this process
  actually had.** The variable and whether it is set here, the file and whether
  it exists, the unpacked runtime folder, and `PATH` with its entry count — plus,
  on Linux, the one command that shows the running server's real environment.
  "Put it on PATH" is useless advice to someone who did exactly that in a shell
  the server never saw, so the message reports the search rather than repeating
  the instruction.

## 0.16.0 - 2026-08-21

### Added

- **An llama.cpp the machine already has is run as it is, and nothing is
  downloaded.** Before fetching the official binaries the node now looks in three
  places, in order: the path in `MINIMAX_H3_LLAMA_BIN` — the executable itself or
  the folder holding it — then its own `runtime/` folder, then `PATH`.

  This is what a build you compiled yourself was missing. `llama_backend` names
  an archive to fetch from upstream, and upstream publishes no CUDA archive for
  Linux at all, so `llama_backend = cuda` there was a dead end whatever the
  machine had on it — a CUDA llama.cpp sitting on `PATH` included, because
  nothing ever looked at `PATH`. Now it is found; once it is, `llama_backend`
  stops mattering, since it chooses a download rather than what an existing
  binary was compiled against; and `device = cuda:0` does what it says.

  The caption nodes take `llama-mtmd-cli` from the same place, because every
  build puts it beside the completion binary. A build carrying one and not the
  other — a distribution package, usually — sends the node back to the archive
  for that one job instead of refusing it.

  A variable pointing at nothing raises rather than falling through to a
  download: naming a build is an instruction, and a typo should not cost half a
  gigabyte of archive.

### Changed

- The `llama_backend` tooltip and both READMEs say where the runtime is looked
  for, and the refusal for `llama_backend = cuda` on Linux now ends with what to
  do about it rather than only with what upstream does not publish.

## 0.15.0 - 2026-08-21

### Added

- **"MiniMax-H3 Universal Rewriter" - both prompt-rewriter LoRAs in one node,
  with a tab choosing which one runs.** The two existing rewriter nodes are
  unchanged and still registered.

  What the tab owns is `model` and `quantization`, one pair per adapter, and
  nothing else. The prompt, the task, the aspect ratio, the duration, the seed
  and both frame inputs are shared, because they mean the same thing to both -
  so trying the other adapter is one click rather than a second node kept in
  step by hand. The widget the other tab uses is hidden rather than reset, and
  survives a save and load, so it is still set to whatever you last chose.

  The task switch is shared too, and the 27B tab does not touch it: it shows
  T2VA lit with the three frame tasks greyed out, and clicking does nothing, so
  the value the 8B tab had is still there when you switch back. Run the 27B tab
  with frames connected and the node says on itself that it is not reading them,
  and where to put them instead.

  Two IMAGE inputs, `first_frame` and `last_frame`, each with a checkbox on its
  own row, and a switched-off row counts as unplugged.

  There is no captioner on the 27B tab, and that was tested rather than assumed.
  A description folded into the prompt does reach the adapter and survives its
  trained shape - but the picture is absorbed into the scene rather than pinned
  to 0.00 seconds, which is what the LoRA's own page says: T2VA finished, FL2VA
  not. A widget for it would have promised the frame task the 27B cannot do.

- The two frame rows' checkboxes, the tab strip, the task switch and the
  aspect-ratio picker are drawn by the same code the Universal Writer uses,
  which moved into `web/js/mmx_controls.js` rather than being copied.

### Changed

- **The Universal Writer's task switch greys out every task the strip cannot
  supply, not only Ref2VA.** It counts the squares badged `pic`, which is the
  count the node already refuses on: nothing connected lights `T2VA` alone, one
  picture lights `I2VA` and `L2VA`, two light `FL2VA`, and three grey all of
  them - three is as impossible for `I2VA` as none is. The greyed button carries
  the sentence the run would have raised, so the fix reads the same before and
  after. Badges are counted rather than sockets, so turning a picture into a
  subject lights the task it was blocking.

- Each rewriter's engine plumbing is now a function rather than a method, and
  the node calls it. Which format the base model is in, which adapter goes with
  it, which of the three engines runs: one copy, called from two nodes.

### Fixed

- A widget hidden by this pack's own scripts is now hidden in **both** node
  renderers. The classic canvas reads `widget.hidden` and the Nodes 2.0 renderer
  reads `options.hidden`, and only the first was being set - so the checkbox map
  on Multi Reference Caption was drawn as a text field under *Modern Node
  Design*, which is exactly the widget the checkboxes exist to replace.

## 0.14.1 - 2026-08-21

### Added

- **The 8B rewriter runs the safetensors build as well as the GGUF one.** The
  adapter is published as `adapter_model.safetensors` on
  `lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B`, trained on the official
  `Qwen/Qwen3-VL-8B-Instruct` folder, and until now the node could only reach
  the GGUF conversion of both. Now the base model list offers either shape and
  the node picks the engine from it.

  What the route buys is residency. A GGUF base with frames runs through
  `llama-mtmd-cli`, a fresh process each time, so `keep_model_loaded` could only
  ever work for T2VA; safetensors loads in ComfyUI's own process through
  Transformers and PEFT and stays there for every task. What it costs is the
  download: 17.5 GB of base and 2.8 GB of adapter against 4.7 + 0.7 + 0.7.

- **`quantization` on the 8B rewriter**, the same widget the 27B has and for the
  same reason: `nf4` needs about 8 GB of VRAM, `int8` about 13, `bfloat16` about
  20. Ignored for a GGUF base, which carries its own.

- **The "Open model list" button on two nodes that were missing it** - the 8B
  rewriter and Multi Reference Caption. Both pick their model from `models.json`
  like every other node, so both had every reason to offer the button and no
  reason not to.

### Changed

- **The base-model check knows which adapter is about to be applied.** It
  compared four numbers from `config.json` against constants that always
  described Qwen3.6-27B, so it could only ever answer for the 27B; the four now
  travel together as a `Shape` and the caller says which one it means. A
  Qwen3-VL-4B is refused for the 8B adapter by name and number -
  `hidden_size is 2560, the adapter needs 4096` - rather than after the
  download. Nothing changes for the 27B, which passes its own shape.

- The transformers engine splits generation into a step that prepares inputs and
  a step that runs them, so the multimodal path could be added without a second
  copy of the sampling, streaming and interrupt handling. A checkpoint's
  processor is loaded and cached beside its model, and a text-only checkpoint
  simply has none.

- **The adapter sections reach your `models.json` at last.** `adapters` and
  `adapters_8b` are dicts, and the merge that keeps a live list current is set
  algebra over named entries in a *list*, so it had never walked them: no
  adapter entry has ever been written into anybody's copy. Reading still worked,
  because an unconfigured entry falls back to the packaged value, but there was
  no line to point at a conversion of your own - which is the whole reason the
  file is yours to edit. They are now merged one format at a time and recorded
  in `seed_offered` under the section name, so a format published later arrives
  and one you delete on purpose stays deleted.

- A chat template found on the tokenizer rather than on the processor is used
  rather than refused. Qwen3-VL-4B is one such checkpoint, and its tokenizer's
  template writes the same image placeholders.

## 0.14.0 - 2026-08-21

### Added

- **MiniMax-H3 Universal Writer** - one node for a whole shot: the references,
  the order they are in, and the rewrite. It covers what Multi Reference Caption
  and both writer nodes cover, for all five tasks at once. Those three are
  unchanged and stay in the menu, so nothing already built stops working.

  The reason to fold them together is order. `Picture 1` and `Picture 2` are not
  interchangeable in FL2VA - one opens the video and the other closes it - and
  until now the only thing deciding which was which was the order the caption
  node happened to write them in, which came in turn from which slot each was
  plugged into. Real, load-bearing, and nowhere on screen.

  One socket takes an image, a clip or a sound, and slots grow as they fill. The
  strip under the inputs is where a reference gets its label and its number: drag
  a square to move it, click its label to say what an image is for (`pic`,
  `subj`, `vid`), click its number to switch it off without unplugging it. So one
  socket still produces the four labels Ref2VA allows, and the distinction Multi
  Reference Caption makes with four groups of inputs is made here on the square.

  Verified in ComfyUI 0.30 on every task. FL2VA and I2VA with two images; Ref2VA
  with an image, a second image relabelled a subject, and an audio clip on the
  same growing socket, which came back as `Subject 1`, `Video 1` and `Audio 1` in
  strip order; T2VA with references connected and correctly ignored without
  loading a captioner at all. Reordering the strip reorders the block.
  Relabelling a square changes both the instruction the captioner is given and
  the label the answer is written under. A task the strip does not match is
  refused before anything is downloaded or loaded, and so is Ref2VA with every
  reference switched off. A workflow saved and reloaded came back with every
  widget value in place.

- **The reference strip, the task switch and the aspect-ratio picker are drawn
  controls**, in HTML through `addDOMWidget` rather than on the canvas. That is
  the one widget mechanism ComfyUI renders in both the classic canvas and the
  Nodes 2.0 renderer, and it needs no Vue: the frontend is a Vue application but
  does not export Vue, and its own registry of Vue widget types is a closed list.

  Each control takes over an ordinary widget the node declares and keeps that
  widget's name and its place in the node. A workflow stores widget values by
  position, so a control sitting *beside* the widget it replaces rather than in
  place of it would shift every value after it; and a browser that never loads
  the script falls back to a text field and two dropdowns, with the node still
  running.

  They are kept off the right-side Parameters panel with `hideInPanel`, the flag
  ComfyUI's own Load3D and text-preview widgets use. That panel picks a component
  with `getComponent(widget.type) || WidgetLegacy` and has no branch for a DOM
  widget, so all it can draw is an empty labelled row - and it could not do better
  anyway, since showing one HTML element in the panel would mean taking it off the
  node.

- **`duration` is a slider on the new node**, in tenths of a second. Its upper
  end is the node's own `max_duration` property - right-click, Properties Panel -
  and 30 seconds until changed. A widget's range is fixed when the node is
  declared and one number cannot suit every graph, so the server accepts up to
  ten minutes while the slider spans whatever the graph actually uses. The other
  nodes keep their whole-second `duration` exactly as it was.

### Changed

- `guide_prompt` accepts a fractional duration. The task line is written as `10s`
  and `7.5s` rather than `10.0s`, so a whole number of seconds still reads like
  one. Every existing node passes whole seconds and is unaffected.

- The checkboxes drawn on input rows are shared code. `web/js/slot_switches.js`
  now holds the drawing, the hit test and the column arithmetic that
  `multi_caption_switch.js` used to carry alone, and both nodes configure it with
  their own idea of which slots take a checkbox and where the state is kept.
  No behaviour change on Multi Reference Caption.

  Worth knowing while that code is on the move, and true before this release as
  well: those checkboxes are canvas drawing, and ComfyUI's experimental "Modern
  Node Design (Nodes 2.0)" returns from `LGraphCanvas.drawNode` before any
  per-node callback runs. On that setting they are neither drawn nor clickable,
  on either node. The new node's squares reach the same state, which is one
  reason each of them carries the on/off toggle as well.

- An empty caption is reported rather than passed on quietly. The label still
  goes into the block, because the strip promised that number to that square and
  closing the gap would make it lie - but a bare `Subject 1:` tells the writer an
  asset exists without saying what it is, so the node now names the slot on
  screen and in the log. Seen on a thinking-model text encoder through the `clip`
  route, where the answer is a reasoning block that never closes.

## 0.13.0 - 2026-08-19

### Added

- **MiniMax-H3 Prompt Rewriter 8B (sees frames)** - a second rewriter node, for
  LightX2V's new adapter on Qwen3-VL-8B-Instruct. It is multimodal, so where the
  27B has to be told in words what a reference frame contains, this one is shown
  the frame and writes the alignment line from what it sees. Four tasks -
  T2VA, I2VA, FL2VA, L2VA - against the 27B node's one, on ~6.1 GB of download
  and ~9 GB of VRAM.

  Two engines, picked by the task. T2VA has no pictures in it and takes the
  ordinary text path, so no projector is loaded and `keep_model_loaded` works.
  The other three carry frames and go through `llama-mtmd-cli`, which is a fresh
  process per run and therefore cannot keep anything resident; the node says so
  in the log rather than ignoring the switch.

  Verified on all four tasks against Qwen3-VL-8B-Instruct Q4_K_M with the F16
  adapter. T2VA opens straight on the three fields; the other three open with
  the alignment sentence, verbatim in the trained wording. With `use_lora` off
  the same model still fills the three fields - the contract is in the system
  prompt, not only in the LoRA - but stops writing `[Shot 2]` cut markers and
  comes back about a third as long, which is the adapter's visible work.

- **`adapter` is a dropdown.** It lists both LoRAs at both published precisions,
  plus every `.gguf` adapter already in your ComfyUI model folders, labelled with
  its architecture so `qwen35` and `qwen3vl` are told apart. Picking a
  quantisation used to mean hand-editing `models.json`, which is a power-user
  path rather than an interface. The first entry is the old default string,
  character for character, and means "whichever build matches the base model you
  picked" - so saved workflows keep their choice and pick up the right adapter on
  either node.

  What is lost with it: the field can no longer be typed into, so a converted
  LoRA is pointed at by putting the file in `models/LLM` rather than by writing a
  path. A path that a saved workflow already carries still resolves.

- **`models_8b` and `adapters_8b` in the model list**, holding the 8B base models
  (Q4_K_M and Q8_0, each with its projector) and the 8B adapter. Lists of their
  own because an entry from either family would fail to load in the other's node.
  Both are merged into an existing `models.json` on update, and the previous copy
  is kept as `.bak` as always.

### Fixed

- **A folder holding several models and one projector paired all of them with
  it.** `models/LLM` with a dozen unrelated GGUFs in it and a single `mmproj`
  satisfied "only one projector, so it must be the right one", and the captioner
  list offered every model in the folder paired with that projector - a 27B text
  model handed a projector built for an 8B, which loads and then writes gibberish
  rather than failing. The shortcut now applies only when there is exactly one
  model in the folder too; otherwise the names have to match, as they already did
  when there was more than one projector.

- **The Q8_0 build of the 27B adapter was invisible to anyone who installed
  before it existed.** `adapters` is a dictionary, and the merge that keeps model
  lists current is set algebra over named entries in a *list*, so it never
  reached it. The packaged alternatives are now folded into a live list that
  points at the same repository and names none of its own - which includes a list
  written before the publisher's account was renamed, since the old id is
  recognised as the same publication.

### Changed

- The packaged list names `pytraveler/...` for the converted GGUF adapters, the
  account having been renamed from `ivanfromm`. Hugging Face redirects the old
  id, so nothing has to be edited and existing lists keep working.

- `llama-cli` is now called with `-st`. It is the fallback binary, used only when
  `llama-completion` is missing, and on Qwen3-VL it enters chat mode despite
  `-no-cnv` and then waits for a second turn - so the node hangs rather than
  fails. On `llama-completion`, which is what actually runs, it changes nothing:
  byte-identical output on Qwen2.5-Omni-3B and on Qwen3-VL-8B.

## 0.12.1 - 2026-08-17

### Fixed

- **The LoRA was silently not applied on llama-cpp-python builds that have moved
  to the newer adapter API.** The backend passed the adapter as
  `Llama(lora_path=...)`, which is how upstream has always spelled it. Builds
  that replaced it with a registry - `load_lora(name, path)` at load time, then
  `active_loras=[{"name": ..., "scale": ...}]` per call - no longer have that
  parameter, and `Llama.__init__` takes a `**kwargs` it never reads, so the
  argument was accepted and thrown away rather than rejected. Nothing raised,
  the log still announced the adapter, and the node answered from the plain base
  model: `use_lora` on and off produced byte-identical text, with cut points in
  the base model's `00:00-00:05` ranges instead of the trained `00:08.500`.

  Which API a build offers is now decided by reading the signature, because the
  way this fails is silence and there is no exception to catch. The `TypeError`
  guard it replaces could never have fired - the `**kwargs` is in every build,
  upstream's included, and there it simply did not matter. A build with neither
  API now refuses and points at `gguf_runtime: llama.cpp`, whose `--lora` was
  never affected, instead of quietly running an unadapted model.

  Registration is read back with `list_loras()` rather than assumed.
  `Llama.eval` looks adapters up by name and skips a miss with a warning it
  prints only when `verbose`, which this backend turns off, so an unchecked
  `load_lora` would have rebuilt exactly the same silence one layer down. A
  successful registration now writes one `INFO` line naming the adapter and the
  count of loaded ones.

  Reported, diagnosed down to the line and verified by
  [@ioritree](https://github.com/ioritree) in
  [#2](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI/issues/2),
  against JamePeng's CUDA fork at 0.3.47.

- **Access violation on unload, once the adapter really is attached.** That same
  registry is walked by `LlamaModel.close` *after* it has freed the base model,
  so `llama_adapter_lora_free` runs against memory that is already gone and the
  ComfyUI worker thread dies with `access violation reading 0x...` on every
  unload. The adapters are now released while the model they point into is still
  alive, which hands `close` an empty registry and the bad branch is never
  entered. Nothing is freed twice - the adapter's own `free` guards on its
  pointer - and builds without the newer API do not have the method at all, so
  they are untouched.

## 0.12.0 - 2026-08-16

### Added

- **A `clip` input on both caption nodes: describe with a model ComfyUI already
  has loaded.** The GGUF route runs `llama-mtmd-cli`, one process per asset, so a
  shot with five references reads the weights off disk five times. Connect a
  multimodal text encoder from `CLIPLoader` instead and the same work happens
  inside ComfyUI, on a model its own allocator is holding: read once, reused for
  every asset, and reused again on the next run. Measured on Gemma-4 12B: three
  assets - an image, a clip and its soundtrack - in 24 s, with one
  `Requested to load` in the log covering all of them.

  This settles the question of running `llama-mtmd-cli` in its chat mode to avoid
  reloading. That would have meant driving an interactive process over stdin and
  scraping its prompt out of stdout, against a runtime whose own rule is that a
  child's stdin is closed. The encoder route gets the same saving with a
  documented API and no subprocess at all, so the chat-mode idea is dropped.

  Nothing is replaced. `model` stays exactly as it was and is simply not
  consulted while `clip` is connected; leave the input empty and both nodes
  behave as before, down to the wording of their errors.

- **Encoder capability is checked before anything runs**, the same courtesy the
  GGUF route pays with its projector header. What is looked for is the audio
  *projector* rather than the audio *encoder*, and the difference is not
  cosmetic: Gemma-4 E2B and E4B carry a real audio encoder, while the 12B is the
  encoder-free "unified" build that projects audio frames straight into the
  language model and has no encoder to find. Probing for one would have reported
  a model deaf while it was listening perfectly well. Gemma-4 31B, which
  genuinely cannot hear, is refused; so is Qwen3-VL.

- **Reasoning is cut off the caption.** Asking a model not to think is not the
  same as it not thinking: Gemma-4's decoder rewrites its thought channel into
  `<think>`/`</think>`, and an fp8 E4B fills that channel with a page of analysis
  even though the prompt primes it closed. A caption is one line of a reference
  block, so everything up to the closing tag is dropped, and an unclosed block -
  what a truncated answer leaves - goes with it.

  Two related findings about Gemma-4 E2B/E4B, both reproduced with ComfyUI's own
  `Generate Text` node on the same checkpoint and therefore not this pack's to
  fix: it reasons out loud as above, and audio never reaches it - the caption
  comes back saying no clip was provided. The node warns in the log when it sees
  that shape of encoder rather than refusing, since another checkpoint may
  behave. Gemma-4 12B takes audio correctly, which is what the measurement above
  was made on.

### Changed

- **`media` decodes a VIDEO into an IMAGE batch as well as into PNGs.** Both
  sinks share the sampling, the seek strategy and `max_frames`, so a clip
  described through either route is described from exactly the same frames. The
  frames reach the encoder as `image=` rather than `video=` on purpose:
  Qwen3-VL's tokenizer has no video argument and would have ignored them in
  silence, and Gemma-4's video path re-subsamples to 1 fps, which would have
  quietly overruled `max_frames`.

## 0.11.0 - 2026-08-15

### Added

- **A third output on `Guide Prompt (any LLM)`: both prompts in one string.**
  Almost every LLM node takes a single prompt, so wiring this one up has always
  meant a string-concatenate node in the middle. The pairing that prompted this
  is ComfyUI's own `Generate Text`, new in 0.30: it runs a language model inside
  ComfyUI's process, off a checkpoint loaded by `CLIPLoader`, which makes
  `CLIPLoader` plus `Guide Prompt` plus `Generate Text` the shortest route to
  this pack's output with nothing downloaded at all - if a Qwen3-VL or Gemma-4
  text encoder is already on disk for an image model.

  `Generate Text` needs three settings to cooperate, and the README now names
  them: `max_length` well above its default of 512, which is an output budget
  that six Ref2VA sections do not fit into; `thinking` off; and
  `use_default_template` left alone.

- **`format` on the same node, `plain` or `chatml`.** `plain` joins the two
  prompts with a blank line and lets the LLM node apply the model's own chat
  template, which puts the guide inside the user turn - correct everywhere, and
  the default. `chatml` writes the turns out here instead: Qwen's tokenizer skips
  its own template as soon as the text starts with `<|im_start|>`, so the guide
  arrives as a real system message. That same branch skips Qwen's thinking
  suppression, so the empty think block is written along with it, for the reason
  the GGUF route renders templates with `enable_thinking=False`. It is
  Qwen-shaped by construction and says so.

  The new output is appended and the new widget is optional, so a saved workflow
  keeps every value and every wire it had.

## 0.10.0 - 2026-08-15

### Added

- **`MiniMax-H3 Multi Reference Caption`, a whole shot's references in one
  node.** A chain of caption nodes is exact but it grows: five references are
  five nodes, five wires and five chances to leave the wrong role on one of
  them. This node has no `role` widget at all - the group an asset is plugged
  into is its label. That is the guide's own vocabulary made structural: Ref2VA
  defines exactly four reference labels and forbids inventing more, so
  `subjects`, `pictures`, `videos` and `audios` cover the format completely, and
  describing an image as audio stops being possible rather than merely
  discouraged. Slots grow as they are filled, one spare always waiting, which is
  `io.Autogrow` from ComfyUI's v3 node API.

  The block comes out in the guide's order - subjects, pictures, videos, audio -
  rather than in wiring order, and each label is still numbered within its own
  category, continuing from whatever arrives on `previous`, so the node sits in
  a chain with single caption nodes on either side. `model`, `length`, `seed`,
  `max_frames`, `context_size` and `bypass` are shared by every asset in it.
  `description` and `instruction` are not carried over: text written by hand
  belongs to one asset at a time, and the single node still has them.

- **A checkbox on each reference slot, on the slot's own row.** A caption costs
  a model load and seconds to minutes, which makes "everything except this one"
  the ordinary thing to want, and pulling the wire out to get it throws away the
  wiring that was the point. A dropdown of names would have been the cheap
  answer and the wrong one: the whole value is being able to hit the switch
  belonging to the input you are looking at. ComfyUI will not lay a widget out
  there - the frontend sorts every plain socket above every widget, whatever
  order the schema asks for - so the box is drawn onto the node at the row's own
  height and the click is picked up from the canvas. The state itself is an
  ordinary hidden widget holding JSON, which is what makes it survive a save and
  reach the backend through the API like any other value; only switched-off
  slots are written down, so an untouched node stays empty in the saved
  workflow. A frontend that never runs the script leaves the JSON field visible
  and everything still works.

- **A video slot takes a `VIDEO` or an `IMAGE` batch.** Video loaders disagree
  about which they hand out - VideoHelperSuite's `Load Video (Upload)` gives
  frames, not a `VIDEO` - and both are the same reference, so the slot accepts
  either and the run sorts out which one arrived. Frames are sampled evenly up
  to `max_frames` in both cases, so the cost of a clip stays independent of its
  length.

### Changed

- **The pack no longer registers all or nothing.** The new node needs a recent
  ComfyUI for its growing inputs, so it is registered on its own and an install
  too old for the v3 node API loses that one node instead of every node in the
  pack to a single failed import.

## 0.9.5 - 2026-08-14

### Added

- **`bypass`, on the four nodes that run a model.** The LoRA rewriter, both
  guided writers and the reference caption node each grew a switch that skips
  the model outright: nothing is downloaded, nothing is loaded, no VRAM is
  touched. The writers hand `prompt` straight to `rewritten_prompt`, which is
  the cheap way to hold a written prompt against the raw one without unwiring
  anything; the caption node passes `previous` through unchanged, which drops a
  single asset from the chain while leaving the chain wired. Numbering survives
  it, because every node numbers what it receives, so the assets after the
  bypassed one close the gap. The section outputs come back empty.

  ComfyUI's own bypass, Ctrl+B, cannot do this here: it forwards a connected
  link and nothing else, and every input these nodes write from is a widget. Hit
  Ctrl+B on the rewriter and there is no link to forward, so the nodes
  downstream are handed nothing at all. On the caption node it half works - the
  `previous` link is forwarded - and then fails on the first node of a chain,
  which has no `previous` to forward. The switch is the last widget on each
  node, so a workflow saved before this release keeps every value it had.

- **A badge above the node title, and a violet node while bypassed.** Collapsed,
  a node draws its title bar and nothing else, which is exactly the state in
  which the switch is out of reach and someone wants to fold a workflow up and
  turn one heavy step off. LiteGraph draws badges above the title whatever the
  node's state, and its hit test walks the badges carrying a click handler
  before it gives up, so the badge is clickable with the node collapsed and no
  canvas handler had to be patched to get there. The colour is what reads at the
  zoom where a whole workflow is on screen; it is swapped underneath the node's
  own colour getter rather than written to the node, which is how ComfyUI's
  native bypass does it too, so it never reaches the saved workflow and a node
  coloured by hand keeps the colour it was given. A frontend too old for badges
  loses the badge and keeps the switch, which is where the feature lives.

## 0.9.4 - 2026-08-10

### Added

- **`trust_remote_code`, off.** A Transformers checkpoint can carry its own
  modelling code, named by `auto_map` in its `config.json`, and loading such a
  model imports and runs that Python with your user's rights. Nothing in the
  shipped list does this - every `transformers` entry is a Qwen3.6-27B variant,
  and the GGUF entries never reach Transformers at all - so the switch changes
  nothing for the models the node offers. It exists for a model you added to
  `models.json` yourself: the node stops and says so instead of running the
  code, and turning the switch on is you saying which model you trust.

### Changed

- **`adapter` refuses a network path.** Every other model is picked from a
  dropdown, so a saved workflow carries the *name* of an entry and never a path;
  `adapter` is a text field because pointing it at a `.gguf` LoRA you converted
  yourself is a thing people do, which also means a workflow you downloaded gets
  to fill it in. `\\host\share\...` and `//host/share/...` are rejected before
  anything reads them, because merely looking at one is an authentication
  attempt against whatever host is named. A share of your own is reachable by
  drive letter as usual, and a path in `models.json` is not restricted at all.
- **The adapter that was applied is logged**, every run, at warning level when
  it is not the configured one. A swapped LoRA is otherwise invisible: the node
  still runs and still fills every field, it just writes something else.

## 0.9.3 - 2026-08-09

### Added

- **An entry in `models.json` can point at a file you already have.** `repo` may
  be a folder on this machine instead of a Hugging Face id, or the whole path
  may go in `file` with `repo` left out - both forms work, in all three
  sections, and the file is read where it lies with nothing downloaded and
  nothing copied. A path that does not exist is named and refused rather than
  downloaded around.

### Fixed

- **A broken `models.json` was the quietest failure in the pack.** The parse
  threw, the packaged defaults were served instead, and the dropdown looked
  ordinary - an edit that never took was indistinguishable from an edit that did
  nothing. The first entry of every model dropdown now carries the parse error
  with its line and column; picking it and hitting Run repeats the message and
  names the file to fix. The rest of the list is still there and ComfyUI still
  runs.

## 0.9.2 - 2026-08-08

### Fixed

- **An audio track from a video loader was rejected.** ComfyUI's own AUDIO is a
  plain `dict`, but nothing enforces that and the common loaders do not oblige:
  VideoHelperSuite hands over a `LazyAudioMap`, a `Mapping` that runs ffmpeg the
  first time a key is read, and `isinstance(audio, dict)` says no to it. The two
  keys are asked for instead of the container's type - and reading them is what
  makes a lazy input decode, so it has to happen there rather than in a
  membership test.
- **A VIDEO could hang the run for good.** `llama-mtmd-cli --video` feeds the
  file to `ffprobe` through *stdin*, and when the MP4 carries its `moov` atom at
  the front - which is what "faststart" means, and what ComfyUI, phones and most
  of the web produce - ffprobe has what it needs after a few kilobytes and exits
  without reading the rest. llama.cpp is still writing the remaining megabytes
  into that pipe and blocks there for good: no output, no error, no end. The
  same clip with `moov` at the end runs in six seconds. Frames are now decoded
  in-process instead - by seeking straight to each one on long clips, and to the
  frame rather than to its keyframe, so eight samples over a 250-frame GOP do
  not collapse onto four.
- **`max_frames` now means something for a VIDEO**, which is what makes the cost
  of describing a clip independent of its length: two seconds at 25 fps is 56
  images through the vision tower, and thirty seconds is 750.

### Added

- Screenshots of the T2VA writer, the Ref2VA writer, the Reference Caption chain
  and the options node in both READMEs, with the measured timings beside them.

## 0.9.1 - 2026-08-08

### Added

- **`device` - `auto`, `cpu`, or one `cuda:N` per GPU ComfyUI can see**, in one
  spelling across all three backends (`--device CUDA1` for the llama.cpp
  binaries, `main_gpu` for llama-cpp-python, `device_map` for Transformers). The
  important part is not the placement: **on another card ComfyUI's own models
  are no longer evicted first.** Every backend unloaded them unconditionally,
  which is right when both want the same VRAM and pure waste when they do not -
  it cost a full reload of the diffusion model after every rewrite. Pick a
  second card and `keep_model_loaded` becomes worth switching on. A device the
  machine does not have is refused, not quietly demoted.

### Fixed

- **Listing your GGUF models cost 31 seconds for ten files.** Building the
  dropdown needs six values from the first few kilobytes of each file, but
  `gguf.GGUFReader` materialises the whole header the moment it opens one -
  including `tokenizer.ggml.tokens`, a quarter of a million strings - and the
  bill arrived the first time ComfyUI answered `/object_info`. The header is now
  walked directly and skipped past: the same six values in **0.4 s**. A
  half-downloaded file is still refused, by checking its tensor offsets against
  its size rather than by failing to map them.

## 0.8.1 - 2026-08-08

### Changed

- **Models added to the pack after you installed are merged into your list.**
  Your copy is still never overwritten, but "we will not touch your list"
  quietly became "you will never see anything new", with nothing anywhere to say
  the node knew about more. The rule is set algebra, not a version check: beside
  the lists your file records `seed_offered`, every name the packaged list has
  ever put in front of this installation, and an update adds only the names that
  are in the pack, not in your file, and not already offered. An entry you
  deleted stays deleted, one you renamed is not duplicated, a genuinely new one
  arrives. One exception, once: a file written before this existed has no record
  of what it was offered, so on the first update everything missing comes back,
  and the previous file is kept beside it as `models.json.bak`. A file the node
  cannot parse is left exactly as it is.

## 0.8.0 - 2026-08-08

### Added

- **MiniMax-H3 Reference Caption.** The writer nodes read text, not pixels; this
  is where the text comes from. Connect an image, an audio clip or a video and a
  small multimodal model describes it into one labelled line of
  `reference_assets` - 3 s for a frame, 2 s for an audio clip, 5 s for a video on
  a 3.4 GB Qwen2.5-Omni-3B. It runs through the same llama.cpp binaries as
  everything else, so a machine that has run one rewrite downloads no runtime at
  all.
- **Chaining by wiring**: `reference_assets` into the next node's `previous`.
  Each label is numbered within its own category, which is the guide's own rule,
  so four assets come out as `Picture 1`, `Picture 2`, `Video 1`, `Audio 1` -
  not 1 through 4.
- **`role` picks both the label and the question asked.** The `Audio` question is
  the one that matters: `<Audio N>` is usually a *timbre* reference and a
  transcript throws away exactly the part that is needed, so the instruction says
  "do not transcribe" outright and asks for voice, delivery, instrumentation and
  ambience instead.
- **A `captioners` list in `models.json`**, with `mmproj` beside `file` - a
  multimodal model is two files from the same conversion. Only pairs that have
  actually been run are listed: llama.cpp's `mtmd` has to understand the
  projector format, and Gemma 4's aborts the process outright while
  `llama-completion` runs the same model as text with no trouble.

## 0.7.0 - 2026-08-08

### Added

- **The writer nodes - T2VA/I2VA/FL2VA/L2VA and Ref2VA.** The same output fields
  without the LoRA and without the 27B: MiniMax's own prompt-writing guide goes
  into the system prompt and any instruction-following GGUF writes to it. The
  smallest working setup drops from ~10 GB and ~13 GB of VRAM to **2.6 GB and
  ~5 GB**, and the four tasks the LoRA cannot do come with it. Ref2VA writes six
  sections instead of three, subject definitions and retention analysis included.
- **MiniMax-H3 Guide Prompt**, which hands the same system prompt to any LLM you
  already have, for people who would rather run the model themselves.
- **The guides are fetched from MiniMax's repository rather than bundled**, so an
  update to the guide is an update to the output without a release here.

## 0.6.2 - 2026-08-08

### Added

- **The base model's shape is checked before anything is downloaded.**
  Qwen3.5-9B carries the same `general.architecture = qwen35` in its header, so
  it looks like a match and the model list showed it - but it has 32 blocks of
  width 4096 where the adapter needs 64 of 5120, and llama.cpp refuses to attach
  the LoRA. The node reads those two header numbers first and says so. A 9B
  producing a plausible-looking rewrite is running **without** the adapter: the
  format is coming from the system prompt, not from the LoRA.
- **`gguf_runtime` - `auto`, `llama-cpp-python`, `llama.cpp`** - separating *what
  runs the model* from `llama_backend`, which picks *which official build to
  fetch*. Forcing the wheel now fails with a clear message when it is absent
  rather than quietly using something else, and forcing the binaries is the way
  out when the installed wheel is broken. Only the wheel can honour
  `keep_model_loaded`; the binaries hand the model back to the operating system
  when the subprocess exits.

## 0.6.0 - 2026-08-07

First public release, shared without a tag.

- The rewriter node: a short prompt in any language in, and H3's three fields -
  `integrated_multimodal_description`, `overall_soundscape`,
  `non_diegetic_music` - out, entirely locally.
- The LightX2V Prompt Rewriter LoRA on Qwen3.6-27B, in `nf4`, `int8` or
  `bfloat16`, or on a GGUF quant through llama.cpp with the binaries fetched on
  first use.
- Weights, adapter and runtime downloaded on demand into ComfyUI's own model
  folders, with progress on the node.
- A `models.json` of your own, copied on first use and never overwritten by an
  update.
