"""The table saying which node reads which model list, and its copy in JavaScript.

Two copies exist on purpose. Python needs it to answer the window's requests;
the browser needs it to put a freshly added model into every dropdown fed from
that list, including on nodes the window was not opened from. Neither can import
the other, so the only thing keeping them honest is this file.

The rest is the validator: what `models.json` will accept from the window, which
is the first time anything has judged an entry before it reached a download.
"""

import ast
import importlib
import json
import os
import pathlib
import re
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "web" / "js" / "model_list_button.js"

if _PKG not in sys.modules:
    _package = types.ModuleType(_PKG)
    _package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = _package

catalog = importlib.import_module(f"{_PKG}.catalog")
sections = importlib.import_module(f"{_PKG}.model_sections")


def _javascript_table() -> dict[str, list[list[str]]]:
    """`NODE_SECTIONS` out of the JavaScript, as data.

    The literal is JSON except for its unquoted keys and its trailing commas,
    both of which a regex can put right -- cheaper and steadier than asking for
    a JavaScript engine the test suite does not otherwise need.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    body = re.search(r"const NODE_SECTIONS = (\{.*?\n\});", source, re.DOTALL)
    assert body, "NODE_SECTIONS is not in model_list_button.js any more"
    text = re.sub(r"(\w+):", r'"\1":', body.group(1))
    text = re.sub(r",(\s*[\]}])", r"\1", text)
    return json.loads(text)


def _python_table() -> dict[str, list[list[str]]]:
    return {
        node: [[one.widget, one.section] for one in found]
        for node, found in sections.NODE_SECTIONS.items()
    }


def test_the_two_tables_are_the_same():
    """A node added to one and not the other loses its button or its refresh."""
    assert _javascript_table() == _python_table()


def test_every_node_reads_a_list_that_exists():
    for node, found in sections.NODE_SECTIONS.items():
        for one in found:
            assert one.section in sections.SECTIONS, f"{node} reads no such list"


def test_every_list_can_be_named_by_some_node():
    """A list nothing reads has no window to be edited from."""
    reachable = {one.section for found in sections.NODE_SECTIONS.values() for one in found}
    assert reachable == set(sections.SECTIONS)


def test_the_lists_are_the_ones_catalog_merges():
    """A section outside ``catalog.SECTIONS`` is never merged, so it silently rots."""
    assert set(sections.SECTIONS) == set(catalog.SECTIONS)


def test_every_list_can_build_its_dropdown():
    assert set(sections._CHOICE_SOURCE) == set(sections.SECTIONS)


def test_the_prefixes_match_the_nodes():
    """Copied rather than imported, because this module must not load the nodes."""
    source = (ROOT / _PKG / "nodes.py").read_text(encoding="utf-8")
    names = "LOCAL_PREFIX|OLLAMA_PREFIX|PROBLEM_PREFIX"
    found = dict(re.findall(rf'^({names}) = "([^"]*)"', source, re.MULTILINE))
    assert found["LOCAL_PREFIX"] == sections.LOCAL_PREFIX
    assert found["OLLAMA_PREFIX"] == sections.OLLAMA_PREFIX
    assert found["PROBLEM_PREFIX"] == sections.PROBLEM_PREFIX


def test_every_scanned_prefix_is_listed_as_one():
    """The window shows a scanned entry only if its prefix is in ``SCANNED_PREFIXES``.

    An entry the scan puts in the dropdown and the window leaves out reads as
    something the window has lost -- which is the whole reason it lists them.
    ``ollama:`` was exactly that until it was added here.
    """
    assert set(sections.SCANNED_PREFIXES) == {sections.LOCAL_PREFIX, sections.OLLAMA_PREFIX}


def test_the_module_does_not_import_the_nodes():
    """``routes`` imports this before the nodes exist, and the v3 nodes are optional.

    An import of ``nodes`` or ``writer_omni`` at the top of this module would run
    on load and take the whole pack down on a ComfyUI without ``comfy_api``.
    """
    tree = ast.parse((ROOT / _PKG / "model_sections.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.col_offset == 0:
            named = {alias.name for alias in node.names}
            assert not named & {"nodes", "writer_8b", "writer_omni"}, named


def test_every_list_says_what_it_wants():
    for section in sections.SECTIONS:
        lines = sections.requirements(section)
        assert lines and all(line.strip() for line in lines)


GGUF = {"name": "A Model", "repo": "someone/some-repo", "file": "model.gguf"}


def test_a_gguf_only_list_needs_no_format_spelled_out():
    """``_entries`` would read the absent key as transformers, which is wrong here."""
    assert sections.clean_entry("writers", GGUF)["format"] == "gguf"


def test_a_gguf_only_list_refuses_transformers():
    with pytest.raises(catalog.CatalogWriteError):
        sections.clean_entry("writers", dict(GGUF, format="transformers"))


def test_a_transformers_entry_refuses_a_file_name():
    """A folder of safetensors has no file inside it that anything reads, so a
    'file' there is the quietest kind of mistake: silently ignored."""
    with pytest.raises(catalog.CatalogWriteError):
        sections.clean_entry("models", dict(GGUF, format="transformers"))


def test_a_transformers_entry_refuses_a_projector():
    with pytest.raises(catalog.CatalogWriteError):
        sections.clean_entry(
            "models_8b", {"name": "A", "repo": "a/b", "format": "transformers",
                          "mmproj": "mmproj.gguf"}
        )


def test_a_transformers_folder_on_its_own_is_fine():
    written = sections.clean_entry("models_8b", {"name": "A", "repo": "Qwen/Qwen3-VL-8B-Instruct"})
    assert written == {"name": "A", "format": "transformers", "repo": "Qwen/Qwen3-VL-8B-Instruct"}


def test_empty_fields_are_left_out_rather_than_written_blank():
    written = sections.clean_entry("writers", dict(GGUF, vram="", note="", download_gb=""))
    assert set(written) == {"name", "format", "repo", "file"}


@pytest.mark.parametrize(
    "section, entry",
    [
        ("writers", dict(GGUF, name="   ")),
        ("writers", {"name": "A Model"}),
        ("writers", {"name": "A Model", "repo": "someone/some-repo"}),
        ("captioners", GGUF),
        ("models_8b", dict(GGUF, format="gguf")),
        ("models_omni", dict(GGUF, format="gguf")),
        ("writers", dict(GGUF, download_gb="a lot")),
        ("writers", dict(GGUF, download_gb=-3)),
    ],
    ids=[
        "no name",
        "neither repo nor file",
        "gguf without a file name",
        "captioner without a projector",
        "8B gguf without a projector",
        "omni gguf without a projector",
        "download size that is not a number",
        "negative download size",
    ],
)
def test_refused(section, entry):
    with pytest.raises(catalog.CatalogWriteError):
        sections.clean_entry(section, entry)


NETWORK = "\\\\attacker.example\\share\\evil.gguf"


@pytest.mark.parametrize("field", ["repo", "file", "mmproj"])
def test_a_network_path_is_refused_in_every_field(field):
    """These arrive over an API with no CSRF token, and reading one is an authentication.

    A path typed into ``models.json`` by hand is still unrestricted; that file
    does not leave the machine, and this window does not write to it blindly.
    """
    entry = dict(GGUF, mmproj="mmproj.gguf")
    entry[field] = NETWORK
    with pytest.raises(RuntimeError):
        sections.clean_entry("captioners", entry)


@pytest.mark.skipif(
    os.name != "nt",
    reason="'//host/share' names a UNC share on Windows only; elsewhere it is an ordinary path",
)
def test_forward_slashes_are_the_same_network_path():
    with pytest.raises(RuntimeError):
        sections.clean_entry("writers", dict(GGUF, repo="//attacker.example/share"))


def test_an_extended_local_path_is_not_a_network_path():
    entry = dict(GGUF, file="\\\\?\\C:\\models\\model.gguf")
    assert sections.clean_entry("writers", entry)["file"].endswith("model.gguf")
