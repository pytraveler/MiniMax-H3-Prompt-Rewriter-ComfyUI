"""The effect embeddings: the header reader, the selection, and the insertion.

The package is registered by hand rather than imported: running its
``__init__.py`` would pull in nodes.py and with it ComfyUI, which a test run
does not have.

What is worth pinning here is what ComfyUI's tokenizer will and will not
accept, because every one of its refusals is silent -- a dropped token looks
exactly like a prompt that never asked for the effect. So the rules read out of
``comfy/sd1_clip.py`` are asserted directly: whitespace before the token, a
small ``e``, no full stop on the end of the name.
"""

import importlib
import json
import pathlib
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

embeddings = importlib.import_module(f"{_PKG}.embeddings")
fields = importlib.import_module(f"{_PKG}.fields")

PROMPT = (
    "integrated_multimodal_description: A cat walks along a fence at dusk.\n\n"
    "overall_soundscape: Wind in the grass.\n\n"
    "non_diegetic_music: N/A"
)

REF_PROMPT = (
    "subject_definitions: <Subject 1> is a young woman.\n\n"
    "summary: She walks through a market.\n\n"
    "retention_analysis: <Picture 1> retains the face.\n\n"
    "detailed_description: [Shot 1] The woman walks between the stalls.\n\n"
    "overall_soundscape: Market chatter."
)

ONE = "minimaxh3_bullet_time"
TWO = "minimaxh3_dark_magic"


def write_embedding(path, shape=(94, 5120), key=embeddings.KEY, body=True):
    """A safetensors file with a real header and, optionally, nothing else.

    Only the header is ever read, so a file with no tensor data in it is a
    perfectly good fixture -- and proves the reader stops where it says it does.
    """
    count = shape[0] * shape[1] * 2 if len(shape) > 1 else shape[0] * 2
    header = json.dumps({
        "__metadata__": {"format": "pt"},
        key: {"dtype": "BF16", "shape": list(shape), "data_offsets": [0, count]},
    }).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(len(header).to_bytes(8, "little"))
        handle.write(header)
        if body:
            handle.write(b"\0" * count)
    return path


def sized(path, size):
    """A file of exactly that many bytes, without writing that many bytes."""
    with open(path, "wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\0")
    return path


def tokens_in(text):
    """Every place the tokenizer would find a token, as (index, name)."""
    found = []
    start = 0
    while True:
        at = text.find(embeddings.IDENTIFIER, start)
        if at < 0:
            return found
        found.append((at, text[at:].split()[0]))
        start = at + 1


class TestTheTable:
    def test_there_are_ten_of_them_and_the_names_are_unique(self):
        assert len(embeddings.EFFECTS) == 10
        assert len(set(embeddings.NAMES)) == 10

    def test_each_one_knows_its_size_and_its_cost(self):
        for effect in embeddings.EFFECTS:
            assert effect.tokens > 0
            assert effect.size > 0

    def test_the_published_size_agrees_with_the_published_token_count(self):
        """bf16, 5120 wide, plus a header of a couple of hundred bytes.

        A typo in either column would be invisible until somebody downloaded
        the file, at which point the number on the node would quietly change.
        """
        for effect in embeddings.EFFECTS:
            body = effect.tokens * embeddings.WIDTH * 2
            assert 0 < effect.size - body < 1024

    def test_the_title_is_readable_and_the_token_is_not_decorated(self):
        one = embeddings.by_name(ONE)
        assert one.title == "Bullet time"
        assert one.token == "embedding:minimaxh3_bullet_time"
        assert one.repo_path == "embeddings/minimaxh3_bullet_time.safetensors"


class TestTheHeader:
    def test_a_real_header_is_read_without_the_body(self, tmp_path):
        path = write_embedding(tmp_path / "e.safetensors", body=False)
        assert embeddings.shape(str(path)) == (94, 5120)
        assert embeddings.token_count(str(path)) == 94

    def test_the_file_this_pack_downloads_reads_the_same_way(self, tmp_path):
        path = write_embedding(tmp_path / "e.safetensors", shape=(50, 5120))
        assert embeddings.token_count(str(path)) == 50

    def test_a_single_vector_is_one_position(self, tmp_path):
        path = write_embedding(tmp_path / "e.safetensors", shape=(5120,))
        assert embeddings.token_count(str(path)) == 1

    def test_a_tensor_of_another_width_is_not_counted(self, tmp_path):
        """It would load and then break the model, which is worth saying."""
        path = write_embedding(tmp_path / "e.safetensors", shape=(77, 768))
        assert embeddings.shape(str(path)) == (77, 768)
        assert embeddings.token_count(str(path)) is None

    def test_the_only_tensor_is_taken_when_the_key_is_another_one(self, tmp_path):
        """load_embed falls back the same way, so this agrees with what happens."""
        path = write_embedding(tmp_path / "e.safetensors", key="clip_l")
        assert embeddings.token_count(str(path)) == 94

    @pytest.mark.parametrize("content", [
        b"", b"short", b"\xff" * 64, b"\x08\x00\x00\x00\x00\x00\x00\x00not json",
    ])
    def test_nothing_readable_is_None_rather_than_an_exception(self, tmp_path, content):
        path = tmp_path / "e.safetensors"
        path.write_bytes(content)
        assert embeddings.header(str(path)) is None
        assert embeddings.token_count(str(path)) is None

    def test_a_header_longer_than_the_file_is_refused(self, tmp_path):
        """A truncated download must not be read as though it were whole."""
        path = tmp_path / "e.safetensors"
        write_embedding(path)
        whole = path.read_bytes()
        path.write_bytes(whole[:20])
        assert embeddings.header(str(path)) is None

    def test_a_missing_file_is_None(self, tmp_path):
        assert embeddings.token_count(str(tmp_path / "nothing.safetensors")) is None


class TestWhatIsOnDisk:
    def test_a_file_of_the_published_size_is_present(self, tmp_path):
        effect = embeddings.by_name(ONE)
        assert embeddings.present(str(tmp_path), effect) is False
        sized(embeddings.path_for(str(tmp_path), ONE), effect.size)
        assert embeddings.present(str(tmp_path), effect) is True

    def test_a_half_written_file_is_not(self, tmp_path):
        effect = embeddings.by_name(ONE)
        pathlib.Path(embeddings.path_for(str(tmp_path), ONE)).write_bytes(b"x" * 40)
        assert embeddings.present(str(tmp_path), effect) is False
        assert effect in embeddings.missing(str(tmp_path))

    def test_the_files_land_flat(self, tmp_path):
        """load_embed joins the folder and the name from the prompt, and no more."""
        assert embeddings.path_for(str(tmp_path), ONE) == str(
            tmp_path / f"{ONE}.safetensors"
        )

    def test_the_grid_says_what_it_knows_and_marks_what_it_cannot_use(self, tmp_path):
        write_embedding(embeddings.path_for(str(tmp_path), ONE), shape=(77, 768))
        rows = {row["name"]: row for row in embeddings.state(str(tmp_path))}
        assert len(rows) == 10
        assert rows[ONE]["foreign"] is True
        assert rows[ONE]["present"] is False
        assert rows[TWO]["foreign"] is False
        assert rows[TWO]["tokens"] == embeddings.by_name(TWO).tokens

    def test_a_downloaded_file_reports_its_own_count(self, tmp_path):
        write_embedding(embeddings.path_for(str(tmp_path), ONE), shape=(7, 5120))
        rows = {row["name"]: row for row in embeddings.state(str(tmp_path))}
        assert rows[ONE]["tokens"] == 7


class TestTheSelection:
    def test_only_the_ticked_ones_count(self):
        assert embeddings.chosen(json.dumps({ONE: True, TWO: False})) == (ONE,)

    def test_an_empty_widget_means_none_of_them(self):
        """The inverse of the pack's two other JSON widgets, on purpose."""
        assert embeddings.chosen("{}") == ()
        assert embeddings.chosen("") == ()
        assert embeddings.chosen(None) == ()

    def test_the_order_is_this_module_s_and_not_the_order_of_the_clicks(self):
        assert embeddings.chosen(json.dumps({TWO: True, ONE: True})) == (ONE, TWO)

    def test_a_list_of_names_is_read_too(self):
        assert embeddings.chosen(json.dumps([TWO, "not_an_effect"])) == (TWO,)
        assert embeddings.chosen([ONE]) == (ONE,)

    def test_nonsense_selects_nothing_rather_than_raising(self):
        assert embeddings.chosen("{{{") == ()
        assert embeddings.chosen("7") == ()
        assert embeddings.chosen(json.dumps({ONE: "yes"})) == ()


class TestInsertion:
    @pytest.mark.parametrize("placement", embeddings.PLACEMENTS)
    def test_every_token_is_where_the_tokenizer_will_find_it(self, placement):
        """The split is (?<=\\s)embedding: -- whitespace before, or index zero.

        This is the whole of the difference between an effect and four words of
        prose that happen to contain a colon, and nothing reports it.
        """
        for prompt in (PROMPT, REF_PROMPT, "a bare line with no labels"):
            text, _note = embeddings.insert(prompt, (ONE, TWO), placement)
            found = tokens_in(text)
            assert len(found) == 2
            for at, _word in found:
                assert at == 0 or text[at - 1].isspace()

    @pytest.mark.parametrize("placement", embeddings.PLACEMENTS)
    def test_the_names_survive_exactly(self, placement):
        text, _note = embeddings.insert(PROMPT, (ONE,), placement)
        assert f"embedding:{ONE}" in text
        assert "Embedding:" not in text

    def test_the_description_placement_opens_the_description(self):
        text, note = embeddings.insert(PROMPT, (ONE,), embeddings.BODY)
        assert text.startswith(
            f"integrated_multimodal_description: embedding:{ONE} A cat walks"
        )
        assert note == ""

    def test_ref2va_puts_it_in_the_detailed_description(self):
        text, _note = embeddings.insert(REF_PROMPT, (ONE,), embeddings.BODY)
        assert f"detailed_description: embedding:{ONE} [Shot 1]" in text
        assert "summary: She walks" in text

    def test_a_label_with_no_space_after_it_still_gets_one(self):
        """Glued to the colon the token is not a token at all."""
        text, _note = embeddings.insert(
            "integrated_multimodal_description:A cat.", (ONE,), embeddings.BODY
        )
        assert f"description: embedding:{ONE} A cat." in text

    def test_a_prompt_with_no_labels_falls_back_to_the_top_and_says_so(self):
        text, note = embeddings.insert("a cat walks", (ONE,), embeddings.BODY)
        assert text.startswith(f"embedding:{ONE}")
        assert embeddings.TOP in note

    def test_the_end_placement_leaves_the_line_breaks_alone(self):
        """Everything after a token is joined onto one line by the tokenizer.

        At the end there is nothing after it, which is the only placement where
        the prompt reaches H3 with its field separators intact.
        """
        text, _note = embeddings.insert(PROMPT, (ONE,), embeddings.END)
        assert text.startswith(PROMPT)
        assert "\n" not in text[text.index(embeddings.IDENTIFIER):]

    def test_the_prompt_is_otherwise_passed_through_character_for_character(self):
        text, _note = embeddings.insert(PROMPT, (ONE,), embeddings.BODY)
        assert text.replace(f"embedding:{ONE} ", "") == PROMPT

    def test_choosing_nothing_changes_nothing(self):
        assert embeddings.insert(PROMPT, (), embeddings.BODY) == (PROMPT, "")

    def test_an_empty_prompt_is_the_tokens_alone(self):
        text, note = embeddings.insert("   ", (ONE,), embeddings.BODY)
        assert text == f"embedding:{ONE}"
        assert note == ""


class TestClearing:
    def test_running_twice_does_not_stack_them(self):
        once, _ = embeddings.insert(PROMPT, (ONE, TWO), embeddings.BODY)
        twice, _ = embeddings.insert(once, (ONE, TWO), embeddings.BODY)
        assert once == twice

    def test_unticking_an_effect_takes_it_back_out(self):
        both, _ = embeddings.insert(PROMPT, (ONE, TWO), embeddings.BODY)
        one, _ = embeddings.insert(both, (ONE,), embeddings.BODY)
        assert f"embedding:{TWO}" not in one
        assert f"embedding:{ONE}" in one
        assert embeddings.clear(one) == PROMPT

    def test_somebody_else_s_embedding_is_not_this_node_s_to_remove(self):
        text = "A cat. embedding:my_own_inversion walks."
        assert embeddings.clear(text) == text

    def test_a_broken_capital_one_of_ours_goes_too(self):
        """It reads like an effect and is not one, so leaving it would mislead."""
        assert "Embedding" not in embeddings.clear(f"A cat. Embedding:{ONE} walks.")

    def test_a_prompt_with_no_tokens_is_returned_untouched(self):
        odd = "  leading and trailing spaces  \n\n\n and blank runs  "
        assert embeddings.clear(odd) == odd


class TestTheBodyOffset:
    def test_it_points_at_the_first_character_of_the_description(self):
        at = fields.body_offset(PROMPT)
        assert PROMPT[at:].startswith("A cat walks")

    def test_ref2va_is_the_detailed_description_and_not_the_summary(self):
        at = fields.body_offset(REF_PROMPT)
        assert REF_PROMPT[at:].startswith("[Shot 1]")

    def test_an_unlabelled_prompt_has_none(self):
        assert fields.body_offset("just a line of prose") is None


class TestFetching:
    def test_a_complete_folder_downloads_nothing(self, tmp_path, monkeypatch):
        """Ten files that are already here must cost nothing to ask about."""
        download = importlib.import_module(f"{_PKG}.download")
        monkeypatch.setattr(
            download, "download_task", lambda *a, **kw: pytest.fail("fetched a file")
        )
        for effect in embeddings.EFFECTS:
            sized(embeddings.path_for(str(tmp_path), effect.name), effect.size)

        assert embeddings.fetch(str(tmp_path)) == {"files": 0, "bytes": 0}

    def test_what_is_missing_is_what_gets_fetched(self, tmp_path, monkeypatch):
        """And it lands flat, under the name the token will spell."""
        download = importlib.import_module(f"{_PKG}.download")
        for effect in embeddings.EFFECTS[1:]:
            sized(embeddings.path_for(str(tmp_path), effect.name), effect.size)

        asked = []

        def pretend(task, token, base, on_progress):
            asked.append(task)
            sized(task.dest, task.size)
            return task.size

        monkeypatch.setattr(download, "download_task", pretend)
        made = embeddings.fetch(str(tmp_path))

        wanted = embeddings.EFFECTS[0]
        assert made == {"files": 1, "bytes": wanted.size}
        assert len(asked) == 1
        assert asked[0].dest == embeddings.path_for(str(tmp_path), wanted.name)
        assert asked[0].url.endswith(f"/embeddings/{wanted.name}.safetensors")
