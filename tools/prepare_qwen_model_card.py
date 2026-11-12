"""Prepare a Hugging Face model card beside verified Qwen GGUF artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def render(manifest, repo_id, licence, validation=""):
    source = manifest["source_repo"]
    revision = manifest["source_revision"]
    title = repo_id.split("/")[-1]
    task = "Ref2VA" if "Ref2VA" in title else "FL2VA"
    files = [one for one in manifest["outputs"] if one.get("role") != "control"]
    model = next(one["file"] for one in files if one.get("quantization") == "Q5_K_M")
    projector = next(one["file"] for one in files if one.get("role") == "vision_projector")
    rows = "\n".join(f"| [{one['file']}]({one['file']}) | {one['bytes'] / 1e9:.3f} GB | "
                     f"{'Vision projector, F16' if one.get('role') == 'vision_projector' else one['quantization']} |" for one in files)
    tasks = ("Ref2VA with ordered image references, sampled video frames and explicit audio descriptions."
             if task == "Ref2VA" else "T2VA, I2VA, L2VA and FL2VA. Use first/last frames in the order required by the task.")
    fields = ("`subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`"
              if task == "Ref2VA" else "`integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music`, plus frame alignment for image tasks")
    license_yaml = "license: apache-2.0" if licence == "apache-2.0" else (
        "license: other\nlicense_name: upstream-terms\nlicense_link: https://huggingface.co/" + source + "/tree/" + revision)
    license_text = ("The original checkpoint includes an Apache 2.0 licence, copied here as `LICENSE`. "
                    "Credit for the fine-tuned model belongs to LightX2V and for its base model to Qwen."
                    if licence == "apache-2.0" else
                    "The pinned LightX2V 27B release does not declare a fine-tuning licence or include a standalone licence file. "
                    "The base [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE) is Apache 2.0. "
                    "The base licence is included as `BASE_MODEL_LICENSE` when supplied with the artifacts. This card records the upstream terms without assigning a new licence to LightX2V's fine-tuning. "
                    "Credit for the fine-tuned model belongs to LightX2V and for its base model to Qwen.")
    return f"""---
{license_yaml}
base_model: {source}
base_model_relation: quantized
library_name: gguf
pipeline_tag: image-text-to-text
language:
  - en
tags:
  - gguf
  - minimax-h3
  - prompt-rewriting
  - multimodal
  - {task.lower()}
---

# {title}

GGUF conversions of [{source}](https://huggingface.co/{source}).
The model rewrites user requests into structured MiniMax-H3 video and audio prompts.
These are complete model weights with a separate vision projector. Load the language
file with `-m` and the projector with `--mmproj`.

**Tasks:** {tasks}

**Output:** {fields}.

## Files

| File | Download size | Contents |
|---|---:|---|
{rows}

Sizes are decimal GB of files on disk. Runtime memory also includes the context,
vision processing and backend allocations. Both quantizations use the same F16
projector extracted from this checkpoint.

`conversion.json` records source/output SHA-256 hashes and the conversion tools.
The BF16 control used for validation is not required to run these files.

## ComfyUI

Install [MiniMax-H3 Prompt Rewriter](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI)
and add **MiniMax-H3 Qwen Rewriter** with **Qwen Rewriter Options**. Choose this
checkpoint in **Model list**, or put the model and projector together under
`ComfyUI/models/LLM/{title}/` for local discovery.

The node provides input checkboxes, the ordered reference strip, **+ instr**,
aspect ratio, duration, bypass/repeat and the prompt library. Its `references`
output carries the original enabled conditioning to Reference Slots or Reference
Encode. Select a task supported by this checkpoint.

## llama.cpp

Use a build that supports `qwen35` and its Qwen3-VL vision projector. Conversion
used llama.cpp commit `{manifest['llama_cpp_revision']}` (`b10310`).

```bash
hf download {repo_id} --include '*Q5_K_M.gguf' 'mmproj-*.gguf' --local-dir .
llama-server -m {model} \\
  --mmproj {projector} --jinja \\
  --ctx-size 32768 --n-gpu-layers 999 --host 127.0.0.1 --port 8081
```

Supply the matching official writing guide and task request, as in the
[upstream inference example](https://huggingface.co/{source}/blob/{revision}/post_vllm.py).
Image parts must stay interleaved with their labels. Video references are sampled
images with source timestamps. The ComfyUI node builds this request automatically.

The upstream defaults are `temperature=0.7`, `top_p=0.8`, `top_k=20` and
`max_tokens=16384`, with thinking enabled. A smaller budget can be used with
thinking disabled through `chat_template_kwargs.enable_thinking=false`.

**Audio:** this checkpoint has no audio encoder. Provide an explicit description
of the reference sound. The original Ref2VA helper labels audio but does not send
its waveform; these conversions do not add a hearing capability.

## Validation

{validation or 'See validation.json for the checks performed on these exact files.'}

Quantization changes the weights and can change the wording. A successful smoke
test checks loading, media input and output structure; it is not a quality score.

## Conversion and source

Source: [{source} at {revision}](https://huggingface.co/{source}/tree/{revision}).
The official `convert_hf_to_gguf.py` produced a BF16 language-model control with
`--no-nextn` and an F16 vision projector with `--mmproj` from the same SFT
directory. `llama-quantize` produced Q5_K_M and Q4_K_M language files. The vision
projector was kept at F16; no additional LoRA is needed.

The writing guides are fetched from their original source by the node and are
not redistributed in this repository.

## Licence and credits

{license_text}

Prompt format and video generation: [MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3).
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="GGUF directory containing conversion.json")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--licence", required=True, choices=("apache-2.0", "upstream"))
    parser.add_argument("--licence-file", type=Path)
    parser.add_argument("--validation-text", default="")
    args = parser.parse_args(argv)
    root = args.directory.resolve()
    manifest = json.loads((root / "conversion.json").read_text(encoding="utf-8"))
    for item in manifest["outputs"]:
        path = root / item["file"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            parser.error("An artifact is missing or its size changed: " + item["file"])
    card = root / "README.md"
    if card.exists():
        parser.error("README.md already exists; review edits directly instead of overwriting it.")
    if args.licence == "apache-2.0":
        if args.licence_file is None or not args.licence_file.is_file():
            parser.error("Supply the original upstream licence file for an Apache 2.0 card.")
        if args.licence_file.resolve() != root / "LICENSE":
            shutil.copyfile(args.licence_file, root / "LICENSE")
    card.write_text(render(manifest, args.repo_id, args.licence, args.validation_text), encoding="utf-8")
    print(card)


if __name__ == "__main__":
    main()
