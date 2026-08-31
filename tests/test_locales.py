"""The translation files, checked for the mistakes that fail silently.

ComfyUI shows the node's own English for any key a locale does not carry, so a
wrong key here is invisible: nothing breaks, the translation simply never
appears. These tests catch the four ways that happens -- a node name that is
not ours, a field ComfyUI does not read, an empty string that would render as
a blank label, and a file that is not valid JSON.

What they deliberately do not check is coverage. A partial translation is the
supported state, and `python tools/locales.py report ru` is where that is
reported, against a running ComfyUI rather than a second copy of the nodes.
"""

import importlib
import json
import pathlib
import sys
import types

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

sys.path.insert(0, str(_ROOT / "tools"))
locales = importlib.import_module("locales")

NODE_FIELDS = {"display_name", "description", "inputs", "outputs"}
ITEM_FIELDS = {"name", "tooltip"}

LANGS = sorted(p.name for p in (_ROOT / "locales").iterdir() if p.is_dir())


def node_defs(lang):
    path = _ROOT / "locales" / lang / "nodeDefs.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_is_at_least_one_language():
    assert LANGS, "locales/ has no language directories"


def test_every_file_is_valid_json():
    for lang in LANGS:
        for path in (_ROOT / "locales" / lang).glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))


def test_only_our_own_nodes_are_translated():
    ours = locales.own_nodes()
    for lang in LANGS:
        for name in node_defs(lang):
            assert name in ours, f"{lang}: {name} is not a node this pack registers"


def test_only_fields_comfyui_reads():
    for lang in LANGS:
        for name, entry in node_defs(lang).items():
            extra = set(entry) - NODE_FIELDS
            assert not extra, f"{lang}: {name} has unknown field(s) {sorted(extra)}"
            for group in ("inputs", "outputs"):
                for item, fields in (entry.get(group) or {}).items():
                    unknown = set(fields) - ITEM_FIELDS
                    assert not unknown, f"{lang}: {name}.{group}.{item} -> {sorted(unknown)}"


def test_outputs_are_keyed_by_index():
    for lang in LANGS:
        for name, entry in node_defs(lang).items():
            for key in (entry.get("outputs") or {}):
                assert key.isdigit(), f"{lang}: {name}.outputs.{key} is not an index"


def test_no_empty_translations():
    for lang in LANGS:
        for name, entry in node_defs(lang).items():
            for field in ("display_name", "description"):
                if field in entry:
                    assert entry[field].strip(), f"{lang}: {name}.{field} is empty"
            for group in ("inputs", "outputs"):
                for item, fields in (entry.get(group) or {}).items():
                    for field, value in fields.items():
                        assert value.strip(), f"{lang}: {name}.{group}.{item}.{field} is empty"


def test_russian_is_actually_russian():
    lo, hi = chr(0x0400), chr(0x04FF)      # the Cyrillic block
    cyrillic = 0
    for name, entry in node_defs("ru").items():
        for field in ("display_name", "description"):
            text = entry.get(field, "")
            if any(lo <= ch <= hi for ch in text):
                cyrillic += 1
    assert cyrillic, "locales/ru carries no Cyrillic at all"
