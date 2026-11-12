"""Convert a complete LightX2V SFT checkpoint with the official llama.cpp tools.

No model or converter is downloaded. Existing outputs are never overwritten.
The projector is always extracted from the same source as the language model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

SUPPORTED = {"Qwen3_5ForConditionalGeneration"}
QUANTS = ("Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_source(source):
    config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    if not set(config.get("architectures", ())) & SUPPORTED or not config.get("vision_config"):
        raise ValueError("Use the full multimodal Qwen SFT checkpoint, including its original vision weights.")
    text = config.get("text_config") or {}
    if (text.get("hidden_size"), text.get("num_hidden_layers")) not in ((4096, 32), (5120, 64)):
        raise ValueError("Only the full 9B and 27B LightX2V Qwen rewriters are supported.")
    index = source / "model.safetensors.index.json"
    if index.is_file():
        raw = json.loads(index.read_text(encoding="utf-8"))
        names = sorted(set(raw["weight_map"].values()))
    else:
        names = ["model.safetensors"]
    for name in names:
        path = (source / name).resolve()
        if not path.is_relative_to(source) or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Source checkpoint is incomplete: " + name)
    for name in ("tokenizer.json", "tokenizer_config.json", "preprocessor_config.json"):
        if not (source / name).is_file():
            raise ValueError("Missing original processor/tokenizer file: " + name)
    return names


def run(command, log):
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
        try:
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
    if code:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
        raise RuntimeError(f"Conversion stopped with exit {code}. See {log}\n{tail}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Complete official SFT directory")
    parser.add_argument("--llama-cpp", required=True, type=Path, help="Official llama.cpp checkout (tested: b10310)")
    parser.add_argument("--quantize", required=True, type=Path, help="llama-quantize executable")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, help="Original lightx2v/model repository")
    parser.add_argument("--source-revision", required=True, help="Pinned original Hugging Face commit SHA")
    parser.add_argument("--name", help="Output file stem; defaults to the source directory name")
    parser.add_argument("--quants", nargs="+", choices=QUANTS, default=["Q5_K_M", "Q4_K_M"])
    parser.add_argument("--keep-bf16", action="store_true", help="Keep the control GGUF alongside the quantized models")
    args = parser.parse_args(argv)
    source, output = args.source.resolve(), args.output.resolve()
    names = inspect_source(source)
    converter = args.llama_cpp.resolve() / "convert_hf_to_gguf.py"
    quantizer = args.quantize.resolve()
    if not converter.is_file() or not quantizer.is_file():
        parser.error("Both the official converter and llama-quantize must exist locally.")
    stem = args.name or source.name
    if not stem or Path(stem).name != stem or not any(task in stem for task in ("FL2VA", "Ref2VA")):
        parser.error("The output name must be a file stem containing FL2VA or Ref2VA.")
    if len(set(args.quants)) != len(args.quants):
        parser.error("Do not repeat a quantization.")
    output.mkdir(parents=True, exist_ok=True)
    destinations = [output / f"{stem}-{quant}.gguf" for quant in args.quants]
    destinations += [output / f"mmproj-{stem}-F16.gguf", output / "conversion.json"]
    if args.keep_bf16:
        destinations.append(output / f"{stem}-BF16.gguf")
    if any(path.exists() for path in destinations):
        parser.error("An output already exists. Use a new output directory; conversion never overwrites artifacts.")
    started = time.monotonic()
    revision = subprocess.run(["git", "-C", str(args.llama_cpp), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    required = sum((source / name).stat().st_size for name in names) * 2.5
    if shutil.disk_usage(output).free < required:
        parser.error("Insufficient output space for the BF16 control, projector and quantized files.")
    records = []
    with tempfile.TemporaryDirectory(prefix=".qwen-convert-", dir=output) as staging:
        staging = Path(staging)
        bf16 = staging / f"{stem}-BF16.gguf"
        print("Converting BF16 control", flush=True)
        run([sys.executable, str(converter), str(source), "--outtype", "bf16", "--no-nextn", "--outfile", str(bf16)],
            output / "convert-bf16.log")
        projector = staging / f"mmproj-{stem}-F16.gguf"
        print("Extracting the matching vision projector", flush=True)
        run([sys.executable, str(converter), str(source), "--mmproj", "--outtype", "f16", "--outfile", str(projector)],
            output / "convert-mmproj.log")
        for quant in args.quants:
            print("Quantizing " + quant, flush=True)
            target = staging / f"{stem}-{quant}.gguf"
            run([str(quantizer), str(bf16), str(target), quant], output / f"quantize-{quant}.log")
            final = output / target.name
            target.replace(final)
            records.append({"file": final.name, "bytes": final.stat().st_size, "sha256": sha256(final), "quantization": quant})
        projected = output / projector.name
        projector.replace(projected)
        records.append({"file": projected.name, "bytes": projected.stat().st_size, "sha256": sha256(projected), "quantization": "F16", "role": "vision_projector"})
        if args.keep_bf16:
            final = output / bf16.name
            bf16.replace(final)
            records.append({"file": final.name, "bytes": final.stat().st_size, "sha256": sha256(final), "quantization": "BF16", "role": "control"})
    print("Hashing source shards", flush=True)
    manifest = {"source_repo": args.source_repo, "source_revision": args.source_revision,
                "source_files": [{"file": name, "bytes": (source / name).stat().st_size, "sha256": sha256(source / name)} for name in names],
                "llama_cpp_revision": revision, "converter_sha256": sha256(converter),
                "quantizer_sha256": sha256(quantizer), "projector": "Extracted from the same checkpoint; not quantized with the language model.",
                "mtp": "Omitted from the language GGUF with --no-nextn.", "outputs": records,
                "elapsed_seconds": round(time.monotonic() - started, 3), "inference_validation": "Not performed by this conversion command."}
    (output / "conversion.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Converted files and provenance: " + str(output), flush=True)


if __name__ == "__main__":
    main()
