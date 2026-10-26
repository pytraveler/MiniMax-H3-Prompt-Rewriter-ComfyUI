"""One row per model in a dropdown, and a saved label that keeps finding it.

Three things used to go wrong between the list, the disk and a saved workflow:

- An entry downloaded its file into ``models/LLM``, the disk scan found that
  file, and the dropdown offered the same model twice -- once by its name and
  once as ``on disk:``.
- Editing an entry's size, VRAM note or note changed its label, and every
  workflow saved with the old one stopped at ComfyUI's "Value not in list".
- A GGUF with no chat template passed Check it and stopped on the first run.

Nothing here needs ComfyUI: ``models_root`` is a temporary folder, the scans
are replaced with what they would have found, and the list is a temporary copy
of the packaged one.
"""

import ast
import importlib
import pathlib
import shutil
import struct
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    _package = types.ModuleType(_PKG)
    _package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = _package

catalog = importlib.import_module(f"{_PKG}.catalog")
discovery = importlib.import_module(f"{_PKG}.discovery")
paths = importlib.import_module(f"{_PKG}.paths")
sections = importlib.import_module(f"{_PKG}.model_sections")
nodes = importlib.import_module(f"{_PKG}.nodes")
ollama_store = importlib.import_module(f"{_PKG}.ollama_store")
routes = importlib.import_module(f"{_PKG}.routes")

MINE = {
    "name": "Mine",
    "format": "gguf",
    "repo": "someone/mine-GGUF",
    "file": "mine.gguf",
    "download_gb": 2.5,
    "vram": "~4 GB",
    "note": "small",
}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A fresh install's list, and a models folder with nothing in it yet."""
    listed = tmp_path / "models.json"
    shutil.copyfile(catalog.SEED_FILE, listed)
    monkeypatch.setattr(catalog, "user_file", lambda: str(listed))
    catalog._DATA_CACHE.clear()

    models = tmp_path / "LLM"
    models.mkdir()
    monkeypatch.setattr(paths, "models_root", lambda: str(models))
    monkeypatch.setattr(nodes, "models_root", lambda: str(models))
    yield models
    catalog._DATA_CACHE.clear()


@pytest.fixture
def scanned(monkeypatch):
    """What the writer scan finds: set ``rows`` to ``[(label, path), ...]``."""
    found = types.SimpleNamespace(rows=[])
    monkeypatch.setattr(discovery, "scan_writer_gguf", lambda: list(found.rows))
    monkeypatch.setattr(ollama_store, "scan_writers", lambda: [])
    return found


def put(folder: pathlib.Path, name: str, data: bytes = b"GGUF" + b"\0" * 60) -> pathlib.Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_a_label_from_before_a_note_was_edited_finds_its_entry(live):
    catalog.add("writers", MINE)
    before = catalog.entry_label(MINE)
    catalog.update("writers", "Mine", dict(MINE, note="smaller", vram="~3 GB"))
    now = catalog.current_label("writers", before)
    assert now == catalog.entry_label(dict(MINE, note="smaller", vram="~3 GB"))
    assert now != before


def test_a_name_that_is_the_start_of_another_does_not_answer_for_it(live):
    """'Mine' is not 'Mine Q8', however the label of the second one begins."""
    catalog.add("writers", MINE)
    remembered = "Mine Q8" + catalog.LABEL_SEPARATOR + "3 GB download"
    assert catalog.current_label("writers", remembered) == ""


def test_the_longest_name_wins(live):
    """Only a name holding a separator can be the start of another's label."""
    catalog.add("writers", MINE)
    longer = dict(MINE, name="Mine" + catalog.LABEL_SEPARATOR + "Q8", file="q8.gguf", note="big")
    catalog.add("writers", longer)
    remembered = longer["name"] + catalog.NOTE_SEPARATOR + "old"
    assert catalog.current_label("writers", remembered) == catalog.entry_label(longer)


def test_a_renamed_entry_is_not_found(live):
    """The name is the one thing that says it is the same entry."""
    catalog.add("writers", MINE)
    before = catalog.entry_label(MINE)
    catalog.update("writers", "Mine", dict(MINE, name="Yours"))
    assert catalog.current_label("writers", before) == ""


def test_the_separators_are_the_ones_the_label_writes():
    label = catalog.entry_label(MINE)
    assert label.startswith("Mine" + catalog.LABEL_SEPARATOR)
    assert label.endswith(catalog.NOTE_SEPARATOR + "small")


def test_a_file_flat_in_the_models_folder_is_present(live):
    path = put(live, "mine.gguf")
    assert paths.same_path(paths.present_file("someone/mine-GGUF", "mine.gguf"), str(path))


def test_an_empty_file_is_not_present(live):
    put(live, "mine.gguf", b"")
    assert paths.present_file("someone/mine-GGUF", "mine.gguf") == ""


def test_a_pair_is_present_only_whole(live):
    folder = live / "mine-GGUF"
    put(folder, "mine.gguf")
    assert paths.present_pair("someone/mine-GGUF", "mine.gguf", "mmproj.gguf") is None
    put(folder, "mmproj.gguf")
    found = paths.present_pair("someone/mine-GGUF", "mine.gguf", "mmproj.gguf")
    assert found and all(paths.same_path(one, str(folder / name))
                         for one, name in zip(found, ("mine.gguf", "mmproj.gguf")))


@pytest.fixture
def two_roots(tmp_path, monkeypatch):
    """``extra_model_paths.yaml`` naming an ``LLM`` folder of its own, ahead of ComfyUI's.

    The layout that downloaded a model a second time: the extra folder is first,
    so new downloads go there, and everything fetched before is in the second.
    """
    extra = tmp_path / "llama.cpp" / "models"
    comfy = tmp_path / "ComfyUI" / "models"
    for folder in (extra, comfy / "LLM"):
        folder.mkdir(parents=True)
    fake = types.ModuleType("folder_paths")
    fake.models_dir = str(comfy)
    fake.get_folder_paths = lambda name: [str(extra), str(comfy / "LLM")]
    monkeypatch.setitem(sys.modules, "folder_paths", fake)
    monkeypatch.setattr(paths, "models_root", lambda: str(extra))
    monkeypatch.setattr(nodes, "models_root", lambda: str(extra))
    return types.SimpleNamespace(extra=extra, comfy=comfy / "LLM")


def test_a_file_in_the_second_llm_folder_is_found(two_roots, monkeypatch):
    path = put(two_roots.comfy, "big.gguf")
    monkeypatch.setattr(nodes, "_fetch", lambda *a, **k: pytest.fail("downloaded again"))
    found = nodes._ensure_file("someone/big-GGUF", "big.gguf", "Base model", {}, None)
    assert paths.same_path(found, str(path))


def test_a_pair_in_the_second_llm_folder_is_found(two_roots, monkeypatch):
    folder = two_roots.comfy / "omni-GGUF"
    put(folder, "omni.gguf")
    put(folder, "mmproj.gguf")
    monkeypatch.setattr(nodes, "_fetch", lambda *a, **k: pytest.fail("downloaded again"))
    found = nodes._ensure_pair("someone/omni-GGUF", "omni.gguf", "mmproj.gguf", "Pair", {}, None)
    assert paths.same_path(found[0], str(folder / "omni.gguf"))


def test_a_checkpoint_folder_in_the_second_llm_folder_is_found(two_roots):
    folder = two_roots.comfy / "Qwen3.6-27B"
    put(folder, "config.json", b"{}")
    put(folder, "model.safetensors")
    assert paths.same_path(paths.present_folder("Qwen/Qwen3.6-27B"), str(folder))


def test_a_new_download_still_goes_to_the_first_llm_folder(two_roots, monkeypatch):
    landed = []

    def fetch(repo, directory, names, *rest):
        landed.append(directory)
        put(pathlib.Path(directory), names[0])

    monkeypatch.setattr(nodes, "_fetch", fetch)
    found = nodes._ensure_file(
        "someone/new-GGUF", "new.gguf", "Base model", {"auto_download": True}, None
    )
    assert paths.same_path(landed[0], str(two_roots.extra))
    assert paths.same_path(found, str(two_roots.extra / "new.gguf"))


def test_the_node_fetches_nothing_for_a_present_file(live, monkeypatch):
    """``_ensure_file`` and the dedup ask the same function, so they cannot drift."""
    path = put(live, "mine.gguf")
    monkeypatch.setattr(nodes, "_fetch", lambda *a, **k: pytest.fail("downloaded"))
    found = nodes._ensure_file("someone/mine-GGUF", "mine.gguf", "Writer", {}, None)
    assert paths.same_path(found, str(path))


def row_for(path: pathlib.Path) -> str:
    return f"{path.name} [qwen3, 0.0 GB]"


def test_the_download_of_an_entry_is_not_offered_twice(live, scanned):
    catalog.add("writers", MINE)
    path = put(live, "mine.gguf")
    scanned.rows = [(row_for(path), str(path))]

    offered = nodes.writer_choices()
    assert catalog.entry_label(MINE) in offered
    assert nodes.LOCAL_PREFIX + row_for(path) not in offered


def test_a_file_no_entry_stands_for_is_still_offered(live, scanned):
    catalog.add("writers", MINE)
    other = put(live, "other.gguf")
    scanned.rows = [(row_for(other), str(other))]
    assert nodes.LOCAL_PREFIX + row_for(other) in nodes.writer_choices()


def test_a_hidden_row_still_resolves_to_its_file(live, scanned):
    """A workflow saved before the row was hidden runs the same file it always did."""
    catalog.add("writers", MINE)
    path = put(live, "mine.gguf")
    scanned.rows = [(row_for(path), str(path))]
    nodes.writer_choices()

    remembered = nodes.LOCAL_PREFIX + row_for(path)
    found = nodes._resolve_writer_choice(remembered)
    assert found.local and paths.same_path(found.reference, str(path))
    assert nodes.valid_writer(remembered) is True


def test_an_edited_label_passes_validation_and_runs_the_entry(live, scanned):
    catalog.add("writers", MINE)
    before = catalog.entry_label(MINE)
    catalog.update("writers", "Mine", dict(MINE, note="smaller"))
    nodes.writer_choices()

    assert nodes.valid_writer(before) is True
    assert nodes._resolve_writer_choice(before).file == "mine.gguf"


def test_a_label_that_names_nothing_gets_the_resolvers_message(live, scanned):
    nodes.writer_choices()
    verdict = nodes.valid_writer("Something long gone")
    assert isinstance(verdict, str) and "not in the writer model list" in verdict


def test_a_typed_path_does_not_pass_validation(live, scanned):
    """The resolver takes one from a wire; a widget value naming a path is refused,
    so an API prompt cannot have the server look at whatever it names."""
    path = put(live, "loose.gguf")
    nodes.writer_choices()
    assert nodes.valid_writer(str(path)) is not True


def test_a_wired_input_is_left_to_the_run(live, scanned):
    assert nodes.valid_writer(None) is True


def test_a_validator_of_several_fields_names_the_one_that_failed():
    """ComfyUI prints the answer under every field the validator names."""
    verdict = nodes.named_verdicts(("caption_model", True), ("writer_model", "gone"))
    assert verdict == "writer_model: gone"
    assert nodes.named_verdicts(("caption_model", True), ("writer_model", True)) is True


def test_a_broken_list_says_so_at_validation(live, scanned):
    verdict = nodes.valid_writer(nodes.PROBLEM_PREFIX + "models.json is not valid JSON")
    assert isinstance(verdict, str) and "models.json is not valid JSON" in verdict


def test_nothing_is_matched_by_name_while_the_list_is_broken(live, scanned, monkeypatch):
    """The packaged list stands in then, and its entry of that name may be another file."""
    catalog.add("writers", MINE)
    before = catalog.entry_label(MINE)
    catalog.update("writers", "Mine", dict(MINE, note="smaller"))
    mapping = nodes._build_writer_map()
    monkeypatch.setattr(catalog, "problem", lambda: "models.json is not valid JSON")
    assert sections.label_now(mapping, "writers", before) == ""


def test_the_browser_is_told_what_moved_and_nothing_else(live, scanned):
    catalog.add("writers", MINE)
    path = put(live, "mine.gguf")
    scanned.rows = [(row_for(path), str(path))]
    before = catalog.entry_label(MINE)
    catalog.update("writers", "Mine", dict(MINE, note="smaller"))
    now = catalog.entry_label(dict(MINE, note="smaller"))

    moved = sections.current_labels(
        "writers", [before, now, nodes.LOCAL_PREFIX + row_for(path), "Something long gone"]
    )
    assert moved == {before: now, nodes.LOCAL_PREFIX + row_for(path): now}


def test_a_deleted_entry_hands_its_nodes_to_the_file(live, scanned):
    catalog.add("writers", MINE)
    path = put(live, "mine.gguf")
    scanned.rows = [(row_for(path), str(path))]

    answer = routes._delete_entry("writers", "Mine")
    assert answer["ok"]
    assert answer["label_before"] == catalog.entry_label(MINE)
    assert answer["label_after"] == nodes.LOCAL_PREFIX + row_for(path)
    assert answer["label_after"] in answer["choices"]["writers"]


def test_a_deleted_entry_with_nothing_on_disk_hands_its_nodes_nowhere(live, scanned):
    """Picking some other model for them could start a download nobody asked for."""
    catalog.add("writers", MINE)
    answer = routes._delete_entry("writers", "Mine")
    assert answer["ok"] and answer["label_after"] == ""


def test_the_window_marks_an_entry_that_is_already_here(live, scanned):
    catalog.add("writers", MINE)
    path = put(live, "mine.gguf")
    scanned.rows = [(row_for(path), str(path))]

    shown = sections.listing("writers")
    mine = next(one for one in shown["entries"] if one["name"] == "Mine")
    assert mine["on_disk"] and mine["scanned"]
    assert not any("mine.gguf" in one for one in shown["found"])


UINT32, STRING = 4, 8


def gguf(path: pathlib.Path, template: str | None) -> pathlib.Path:
    """The smallest GGUF the header reader accepts: metadata, no tensors, some padding."""
    values = {
        "general.architecture": "qwen3",
        "qwen3.block_count": 36,
        "qwen3.embedding_length": 4096,
    }
    if template is not None:
        values["tokenizer.chat_template"] = template

    out = bytearray(b"GGUF" + struct.pack("<IQQ", 3, 0, len(values)))
    for key, value in values.items():
        name = key.encode()
        out += struct.pack("<Q", len(name)) + name
        if isinstance(value, str):
            text = value.encode()
            out += struct.pack("<IQ", STRING, len(text)) + text
        else:
            out += struct.pack("<II", UINT32, value)
    out += b"\0" * 64
    path.write_bytes(bytes(out))
    return path


def check_writer(tmp_path, template):
    gguf(tmp_path / "model.gguf", template)
    entry = {"name": "A", "format": "gguf", "repo": str(tmp_path), "file": "model.gguf"}
    return sections.check("writers", entry)


def test_a_model_without_a_chat_template_is_refused_where_the_node_renders_one(tmp_path):
    found = check_writer(tmp_path, None)
    assert found["verdict"] == "bad"
    assert any("no chat template" in line["text"] for line in found["lines"])


def test_a_template_that_renders_is_good(tmp_path):
    found = check_writer(tmp_path, "{% for m in messages %}{{ m['content'] }}{% endfor %}")
    assert found["verdict"] == "good"
    assert any("renders" in line["text"] for line in found["lines"])


def test_a_template_that_does_not_render_is_refused(tmp_path):
    found = check_writer(tmp_path, "{{ raise_exception('this template wants tools') }}")
    assert found["verdict"] == "bad"
    assert any("does not render" in line["text"] for line in found["lines"])


def test_where_the_binary_renders_it_a_missing_template_is_only_a_warning(tmp_path):
    lines = []
    sections._check_template("captioners", str(gguf(tmp_path / "model.gguf", None)), lines)
    assert [line["level"] for line in lines] == ["warn"]


CHOICE_FUNCTIONS = {"model_choices", "writer_choices", "captioner_choices"}
VALIDATORS = {"VALIDATE_INPUTS", "validate_inputs"}


def _node_classes():
    """``{node id: ClassDef}`` for every node class in the package."""
    found = {}
    for path in sorted((ROOT / _PKG).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mapped = {}
        for statement in tree.body:
            if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Dict):
                continue
            if not any(getattr(one, "id", "") == "NODE_CLASS_MAPPINGS" for one in statement.targets):
                continue
            for key, value in zip(statement.value.keys, statement.value.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Name):
                    mapped[value.id] = key.value
        for cls in (one for one in tree.body if isinstance(one, ast.ClassDef)):
            named = next(
                (
                    keyword.value.value
                    for call in ast.walk(cls) if isinstance(call, ast.Call)
                    for keyword in call.keywords
                    if keyword.arg == "node_id" and isinstance(keyword.value, ast.Constant)
                ),
                None,
            )
            node_id = named or mapped.get(cls.name)
            if node_id:
                found[node_id] = cls
    return found


def _called(cls) -> set[str]:
    names = set()
    for call in ast.walk(cls):
        if isinstance(call, ast.Call):
            if isinstance(call.func, ast.Name):
                names.add(call.func.id)
            elif isinstance(call.func, ast.Attribute):
                names.add(call.func.attr)
    return names


def test_every_node_that_reads_a_list_is_in_the_table():
    """The Reducer read the writer list and was left out: no button, no refresh."""
    readers = {node for node, cls in _node_classes().items() if _called(cls) & CHOICE_FUNCTIONS}
    assert readers, "no node reads a model list -- this test would pass vacuously"
    assert readers <= set(sections.NODE_SECTIONS), readers - set(sections.NODE_SECTIONS)


def _autogrow_groups(cls, module: ast.Module) -> set[str]:
    """The Autogrow group names a node declares.

    A literal first argument is the name. A computed one is Multi Reference
    Caption's ``group.id`` over ``GROUPS``, whose names are the first argument
    of every ``Group(...)`` in that module-level tuple.
    """
    names = set()
    for call in ast.walk(cls):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        owner = call.func.value
        if call.func.attr != "Input" or getattr(owner, "attr", "") != "Autogrow":
            continue
        first = call.args[0] if call.args else None
        if isinstance(first, ast.Constant):
            names.add(first.value)
            continue
        for statement in module.body:
            if isinstance(statement, ast.Assign) and any(
                getattr(one, "id", "") == "GROUPS" for one in statement.targets
            ):
                for item in ast.walk(statement.value):
                    if isinstance(item, ast.Call) and item.args and isinstance(item.args[0], ast.Constant):
                        names.add(item.args[0].value)
    return names


def test_every_validator_takes_the_nodes_autogrow_groups():
    """ComfyUI hands an Autogrow group to ``validate_inputs`` whether it asked or not.

    ``build_nested_inputs`` adds every group of the node to the call, so a
    validator that did not name ``references`` stopped the whole prompt with
    "got an unexpected keyword argument" -- on every graph with a Universal
    Writer in it. ``**kwargs`` is not the way out; the test above says why.
    """
    checked = 0
    for path in sorted((ROOT / _PKG).glob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (one for one in module.body if isinstance(one, ast.ClassDef)):
            method = next(
                (one for one in cls.body
                 if isinstance(one, ast.FunctionDef) and one.name in VALIDATORS),
                None,
            )
            groups = _autogrow_groups(cls, module)
            if method is None or not groups:
                continue
            checked += 1
            arguments = {one.arg for one in method.args.args}
            assert groups <= arguments, f"{cls.name} does not take {sorted(groups - arguments)}"
    assert checked >= 3, "the three nodes with Autogrow groups were not found"


def test_every_listed_dropdown_is_validated_by_name():
    """By name, never ``**kwargs``: that switches off ComfyUI's checks of every input."""
    classes = _node_classes()
    for node, dropdowns in sections.NODE_SECTIONS.items():
        cls = classes[node]
        method = next(
            (one for one in cls.body
             if isinstance(one, ast.FunctionDef) and one.name in VALIDATORS),
            None,
        )
        assert method is not None, f"{node} has no validator"
        assert method.args.kwarg is None, f"{node} validates with **kwargs"
        arguments = {one.arg for one in method.args.args}
        for dropdown in dropdowns:
            assert dropdown.widget in arguments, f"{node} does not validate '{dropdown.widget}'"


def test_a_flat_pair_in_the_second_llm_folder_is_the_pair(two_roots, monkeypatch):
    """Qwen3-VL-8B, fetched flat by an older version, was downloaded again beside itself."""
    model = put(two_roots.comfy, "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
    projector = put(two_roots.comfy, "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf")
    monkeypatch.setattr(nodes, "_fetch", lambda *a, **k: pytest.fail("downloaded again"))
    found = nodes._ensure_pair(
        "Qwen/Qwen3-VL-8B-Instruct-GGUF", model.name, projector.name, "Captioner", {}, None
    )
    assert paths.same_path(found[0], str(model))
    assert paths.same_path(found[1], str(projector))


def test_the_repository_folder_is_asked_before_the_flat_layout(live):
    folder = live / "mine-GGUF"
    for where in (live, folder):
        put(where, "mine.gguf")
        put(where, "mmproj.gguf")
    found = paths.present_pair("someone/mine-GGUF", "mine.gguf", "mmproj.gguf")
    assert paths.same_path(found[0], str(folder / "mine.gguf"))


def test_a_pair_split_between_layouts_is_not_a_pair(live):
    """Both from one place: two halves found apart may be two different downloads."""
    put(live, "mine.gguf")
    put(live / "mine-GGUF", "mmproj.gguf")
    assert paths.present_pair("someone/mine-GGUF", "mine.gguf", "mmproj.gguf") is None


def test_a_file_in_its_repository_folder_is_present(live):
    path = put(live / "mine-GGUF", "mine.gguf")
    assert paths.same_path(paths.present_file("someone/mine-GGUF", "mine.gguf"), str(path))


def test_check_it_reads_the_pair_the_node_would_run(two_roots, monkeypatch):
    """A flat pair is judged from disk, not sent to the Hub as a download."""
    put(two_roots.comfy, "omni.gguf")
    put(two_roots.comfy, "mmproj.gguf")
    seen = []
    monkeypatch.setattr(
        sections, "_check_local_gguf",
        lambda section, entry, model, projector, lines: seen.append((model, projector)),
    )
    monkeypatch.setattr(
        sections, "_check_remote", lambda *a, **k: pytest.fail("asked the Hub")
    )
    entry = {"format": "gguf", "repo": "someone/omni-GGUF", "file": "omni.gguf",
             "mmproj": "mmproj.gguf"}
    sections.check("models_omni", entry)
    assert paths.same_path(seen[0][1], str(two_roots.comfy / "mmproj.gguf"))


def test_a_cancelled_download_leaves_a_line_in_the_console(tmp_path, monkeypatch, caplog):
    class Cancelled(Exception):
        pass

    def sync_repo(repo_id, dest_dir, on_progress=None, on_total=None, **_):
        on_total(5 * 1024 ** 3)
        on_progress(868 * 1024 ** 2, "big.gguf")
        raise Cancelled()

    progress = types.SimpleNamespace(
        set_total=lambda *_: None, update=lambda *_: None, text=lambda *a, **k: None
    )
    monkeypatch.setattr(nodes.download, "sync_repo", sync_repo)
    caplog.set_level("INFO")
    with pytest.raises(Cancelled):
        nodes._fetch("someone/big-GGUF", str(tmp_path), ("big.gguf",), (), progress)
    said = caplog.text
    assert "someone/big-GGUF: fetching big.gguf into" in said
    assert "stopped at 868" in said and "carries on from it" in said


def test_the_universal_writer_checks_what_its_block_says():
    """Not the sockets: a badge renames a reference, and 'previous' adds labels."""
    tree = ast.parse((ROOT / _PKG / "universal.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("_fix_once", "_report")
    ]
    assert len(calls) == 2
    for call in calls:
        having = next(keyword.value for keyword in call.keywords if keyword.arg == "having")
        assert isinstance(having, ast.Name) and having.id == "shown"


def test_the_ref2va_writer_checks_what_its_block_says():
    """Without it a <Video 1> nobody listed passed as any other Ref2VA tag (issue #22)."""
    tree = ast.parse((ROOT / _PKG / "nodes.py").read_text(encoding="utf-8"))
    writer = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MiniMaxH3GuidedWriterRef"
    )
    calls = [
        node for node in ast.walk(writer)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("_fix_once", "_report")
    ]
    assert len(calls) == 2
    for call in calls:
        having = next(keyword.value for keyword in call.keywords if keyword.arg == "having")
        assert isinstance(having, ast.Name) and having.id == "shown"
