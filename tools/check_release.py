"""Inspect a Registry or GitHub ZIP before publishing, without extracting it.

Usage: python tools/check_release.py node.zip --kind registry
       python tools/check_release.py release.zip --kind release

Compare runtime files with this checkout, including new files not yet committed.
The command is read-only: it never stages, publishes or opens a credential store.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = {"docs/node_options.png", "docs/api_options_node.png", "docs/api_universal_writer_node.png",
               "docs/qwen_rewriter_workflow.png"}
REQUIRED = {
    "__init__.py", "pyproject.toml", "requirements.txt", "LICENSE",
    "README.md", "README_RU.md", "README_ZH.md", "CHANGELOG.md", "CHANGELOG_RU.md",
    "minimax_h3_rewriter/models.json", "minimax_h3_rewriter/api_profiles.example.json",
    "docs/api_writer.md", "docs/api_writer_RU.md", "docs/api_writer_contract.md", "docs/api_writer_security.md",
    "locales/ru/nodeDefs.json", "locales/ru/main.json",
    "example_workflows/11 - Write through an API.json",
    "example_workflows/11 - Write through an API.jpg",
    "docs/qwen_rewriter.md", "docs/qwen_rewriter_RU.md", "docs/qwen_rewriter_ZH.md",
    "docs/badges/hf-sft-9b-ref2va.svg", "docs/badges/hf-sft-9b-fl2va.svg",
    "docs/badges/hf-sft-27b-ref2va.svg", "docs/badges/hf-sft-27b-fl2va.svg",
    "example_workflows/12 - Write with the full Qwen model.json",
    "example_workflows/12 - Write with the full Qwen model.jpg",
} | SCREENSHOTS
PRIVATE_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".codex", ".agents", "_user"}
PRIVATE_NAMES = {"api_profiles.json", "api_secrets.enc", "auth.json", "credentials.json",
                 "comfyui-path.txt", ".mcp.json", "api_universal_writer_plan.md"}
PRIVATE_SUFFIXES = {".pyc", ".pyo", ".exe", ".dll", ".pyd", ".so", ".gguf", ".safetensors",
                    ".pem", ".key", ".log", ".har", ".db", ".sqlite", ".sqlite3", ".bak"}
MAX_MEMBER = 32 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


class ArchiveError(ValueError):
    pass


def _fail(message):
    raise ArchiveError(message)


def _runtime_files(source):
    files = {"__init__.py"}
    for folder, suffix in (("minimax_h3_rewriter", "*.py"), ("web/js", "*.js")):
        files.update(path.relative_to(source).as_posix() for path in (source / folder).rglob(suffix)
                     if not PRIVATE_DIRS.intersection(path.relative_to(source).parts))
    return files


def inspect_archive(archive, *, kind, source=ROOT):
    """Reject missing/changed runtime code, private state and unsafe ZIP members."""
    if kind not in {"registry", "release"}:
        _fail("Choose registry or release.")
    source = Path(source)
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if sum(item.file_size for item in entries) > MAX_TOTAL:
            _fail("Archive exceeds the uncompressed size limit.")
        roots = [item.filename[:-len("pyproject.toml")] for item in entries
                 if item.filename == "pyproject.toml" or item.filename.endswith("/pyproject.toml")]
        if len(roots) != 1:
            _fail("Expected one package pyproject.toml.")
        prefix = roots[0]
        if (kind == "registry" and prefix) or (kind == "release" and (not prefix or prefix.count("/") != 1)):
            _fail("Unexpected package root for this archive kind.")
        seen, members = set(), {}
        for item in entries:
            name = item.filename
            parts = name.rstrip("/").split("/")
            if (name != item.orig_filename or "\\" in name or ":" in name
                    or name.startswith("/") or any(p in {"", ".", ".."} for p in parts)):
                _fail("Unsafe ZIP member path.")
            folded = name.casefold().rstrip("/")
            if folded in seen:
                _fail("Duplicate ZIP member path.")
            seen.add(folded)
            mode = stat.S_IFMT(item.external_attr >> 16)
            if mode not in {0, stat.S_IFREG, stat.S_IFDIR} or item.flag_bits & 1:
                _fail("Links, special files and encrypted ZIP members are not allowed.")
            if item.file_size > MAX_MEMBER:
                _fail("ZIP member exceeds the size limit.")
            if not name.startswith(prefix):
                _fail("File outside the package root.")
            if item.is_dir():
                continue
            relative = name[len(prefix):]
            path = PurePosixPath(relative.casefold())
            if (PRIVATE_DIRS.intersection(path.parts) or path.name in PRIVATE_NAMES
                    or path.suffix in PRIVATE_SUFFIXES or path.name.startswith(".env")
                    or path.name.startswith(("api_secrets.enc.", "api_profiles.json."))
                    or path.parts[0] in {"user", "dist", "downloads", "dev-junction", ".github"}):
                _fail("Private or development state in archive: " + relative)
            if kind == "registry" and path.parts[0] in {"tools", "tests"}:
                _fail("Maintainer-only files in Registry archive: " + relative)
            if kind == "release" and path.parts[0] in {".gitignore", ".gitattributes"}:
                _fail("Git-only metadata in release archive: " + relative)
            members[relative] = item
        runtime = _runtime_files(source)
        missing = sorted((REQUIRED | runtime) - members.keys())
        if missing:
            _fail("Missing files: " + ", ".join(missing))
        for relative in sorted(runtime | SCREENSHOTS | {"pyproject.toml", "requirements.txt"}):
            data = bundle.read(members[relative])
            if data != (source / relative).read_bytes():
                _fail("Archive differs from checkout: " + relative)
            if relative.endswith(".py"):
                tree = ast.parse(data, filename=relative, feature_version=(3, 10))
                if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id in {"eval", "exec"} for node in ast.walk(tree)):
                    _fail("Built-in eval/exec call in runtime code: " + relative)
        for relative, item in members.items():
            if relative.endswith(".json"):
                json.loads(bundle.read(item))
        example = json.loads(bundle.read(members["minimax_h3_rewriter/api_profiles.example.json"]))
        for profile in example.get("profiles", []):
            secret = profile.get("secret", {})
            if set(secret) - {"kind", "name"} or {"api_key", "headers", "token"}.intersection(profile):
                _fail("Credential material in example profiles.")
        return {"kind": kind, "files": len(members), "runtime_files": len(runtime),
                "sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(), "prefix": prefix}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--kind", choices=("registry", "release"), required=True)
    parser.add_argument("--source", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = inspect_archive(args.archive, kind=args.kind, source=args.source)
    except (ArchiveError, OSError, ValueError, SyntaxError, zipfile.BadZipFile) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
