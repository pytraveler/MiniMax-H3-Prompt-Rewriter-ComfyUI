"""Reading saved references -- RefMods -- off the disk, and handing them on.

The format is somebody else's, and the files are whatever people downloaded,
so most of what is pinned down here is the reading: a header parsed by hand, a
pair of files that is one entry, a bundle whose members are numbered by where
they sit, and everything that is not a RefMod costing nothing. The files are
built here byte by byte, the same layout a safetensors writer produces, so the
listing is tested without torch.

What needs torch -- loading, the blocks the DiT reads, the pictures the text
encoder is shown -- is tested with it when it is installed.
"""

import importlib
import io
import json
import pathlib
import struct
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

refmods = importlib.import_module(f"{_PKG}.refmods")
references = importlib.import_module(f"{_PKG}.references")

SIZES = {"F32": 4, "F16": 2, "U8": 1}


def write(path, tensors, meta=None, raw_meta=None):
    """A safetensors file: ``{name: (dtype, shape, bytes or None)}``, zeros when None."""
    header, blobs, offset = {}, [], 0
    for name, (dtype, shape, data) in tensors.items():
        count = 1
        for n in shape:
            count *= n
        data = data if data is not None else bytes(count * SIZES[dtype])
        header[name] = {"dtype": dtype, "shape": list(shape), "data_offsets": [offset, offset + len(data)]}
        blobs.append(data)
        offset += len(data)
    if raw_meta is not None:
        header["__metadata__"] = raw_meta
    elif meta is not None:
        header["__metadata__"] = {"refmod_meta": json.dumps(meta)}
    encoded = json.dumps(header).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + b"".join(blobs))
    return path


def look(t=3, h=8, w=6, dtype="F16"):
    return (dtype, [1, 24, t, h, w], None)


def voice(t=40):
    return ("F32", [1, 32, 2, t], None)


def visual_file(path, kind="video", source="stack", t=3, **extra):
    meta = {"kind": kind, "latent_t": t, "latent_h": 8, "latent_w": 6, "source": source,
            "concept_type": "identity", **extra}
    return write(path, {"latent": look(t)}, meta)


def audio_file(path, t=40, **extra):
    return write(path, {"latent": voice(t)}, {"kind": "audio", "latent_t": t, "concept_type": "voice", **extra})


def bundle_file(path, members=None, tensors=None):
    members = members or [
        {"kind": "video", "latent_t": 3, "source": "stack", "concept_type": "identity"},
        {"kind": "audio", "latent_t": 40, "concept_type": "voice"},
    ]
    tensors = tensors or {"ref_0": look(), "ref_1": voice()}
    return write(path, tensors, {"_format_version": 5, "kind": "bundle", "name": path.stem, "members": members})


def names(items):
    return [item.name for item in items]


def test_the_metadata_is_read_from_the_header(tmp_path):
    path = visual_file(tmp_path / "hero.safetensors", subject_name="Bob")
    meta, tensors = refmods.read_header(str(path))
    assert meta["subject_name"] == "Bob"
    assert tensors["latent"]["shape"] == [1, 24, 3, 8, 6]


def test_a_voice_file_may_keep_its_metadata_under_its_own_key(tmp_path):
    path = write(tmp_path / "v.safetensors", {"latent": voice()},
                 raw_meta={"audio_refmod_meta": json.dumps({"kind": "audio"})})
    assert refmods.read_header(str(path))[0] == {"kind": "audio"}


def test_an_older_file_keeps_it_beside_itself(tmp_path):
    path = write(tmp_path / "old.safetensors", {"latent": look()})
    (tmp_path / "old.json").write_text(json.dumps({"kind": "video", "source": "stack"}))
    assert refmods.read_header(str(path))[0]["source"] == "stack"


def test_a_file_that_is_not_safetensors_is_nothing(tmp_path):
    path = tmp_path / "broken.safetensors"
    path.write_bytes(b"\xff" * 64)
    assert refmods.read_header(str(path)) == (None, {})


def test_a_model_file_without_metadata_is_not_a_refmod(tmp_path):
    write(tmp_path / "some_lora.safetensors", {"weight": ("F16", [4, 4], None)})
    assert refmods.scan([str(tmp_path)]) == []


def test_one_file_is_one_entry(tmp_path):
    visual_file(tmp_path / "hero.safetensors")
    [item] = refmods.scan([str(tmp_path)])
    assert item.name == "hero"
    assert item.visual.kind == "video" and item.visual.member is None and item.audio is None


def test_a_look_and_a_voice_side_by_side_are_one_entry(tmp_path):
    visual_file(tmp_path / "Yoda" / "Yoda_visual.safetensors")
    audio_file(tmp_path / "Yoda" / "Yoda_audio.safetensors")
    [item] = refmods.scan([str(tmp_path)])
    assert item.name == "Yoda/Yoda"
    assert item.visual.path.endswith("Yoda_visual.safetensors")
    assert item.audio.path.endswith("Yoda_audio.safetensors")


@pytest.mark.parametrize("pair", [("_visual", "_audio"), ("_Video", "_Audio")])
def test_both_spellings_of_a_pair_are_read(tmp_path, pair):
    visual_file(tmp_path / f"hero{pair[0]}.safetensors")
    audio_file(tmp_path / f"hero{pair[1]}.safetensors")
    assert names(refmods.scan([str(tmp_path)])) == ["hero"]


def test_a_suffix_that_disagrees_with_the_file_does_not_pair(tmp_path):
    visual_file(tmp_path / "hero_audio.safetensors")
    assert names(refmods.scan([str(tmp_path)])) == ["hero_audio"]


def test_a_bundle_offers_its_look_and_its_voice(tmp_path):
    bundle_file(tmp_path / "Aoi_V1.safetensors")
    [item] = refmods.scan([str(tmp_path)])
    assert (item.visual.member, item.audio.member) == (0, 1)
    assert item.visual.path == item.audio.path


def test_a_bundle_member_is_numbered_by_where_it_sits(tmp_path):
    members = [
        {"kind": "mask"},
        {"kind": "audio", "latent_t": 40},
        {"kind": "image", "latent_t": 1},
    ]
    tensors = {"ref_0": ("F16", [4], None), "ref_1": voice(), "ref_2": look(t=1)}
    bundle_file(tmp_path / "odd.safetensors", members, tensors)
    [item] = refmods.scan([str(tmp_path)])
    assert (item.visual.member, item.visual.kind, item.audio.member) == (2, "image", 1)


def test_a_bundle_member_without_its_tensor_is_left_out(tmp_path):
    bundle_file(tmp_path / "half.safetensors", tensors={"ref_0": look()})
    [item] = refmods.scan([str(tmp_path)])
    assert item.visual is not None and item.audio is None


def test_a_latent_that_is_not_the_shape_its_kind_says_is_not_listed(tmp_path):
    write(tmp_path / "bad.safetensors", {"latent": look(t=3)}, {"kind": "image"})
    write(tmp_path / "worse.safetensors", {"latent": ("F16", [1, 16, 3, 8, 8], None)}, {"kind": "video"})
    assert refmods.scan([str(tmp_path)]) == []


def test_subfolders_are_part_of_the_name_and_hidden_ones_are_skipped(tmp_path):
    visual_file(tmp_path / "characters" / "hero.safetensors")
    visual_file(tmp_path / ".trash" / "gone.safetensors")
    visual_file(tmp_path / "__pycache__" / "gone.safetensors")
    assert names(refmods.scan([str(tmp_path)])) == ["characters/hero"]


def test_the_first_folder_to_hold_a_name_keeps_it(tmp_path):
    first = visual_file(tmp_path / "a" / "hero.safetensors")
    visual_file(tmp_path / "b" / "hero.safetensors", kind="image", source="image", t=1)
    [item] = refmods.scan([str(tmp_path / "a"), str(tmp_path / "b")])
    assert item.visual.path == str(first)


def test_a_name_taken_already_lists_the_file_under_its_own(tmp_path):
    bundle_file(tmp_path / "hero.safetensors")
    visual_file(tmp_path / "hero_visual.safetensors")
    assert names(refmods.scan([str(tmp_path)])) == ["hero", "hero_visual"]


def test_entries_are_sorted_without_regard_to_case(tmp_path):
    for name in ("beta", "Alpha", "gamma"):
        visual_file(tmp_path / f"{name}.safetensors")
    assert names(refmods.scan([str(tmp_path)])) == ["Alpha", "beta", "gamma"]


def test_a_rewritten_file_is_read_again(tmp_path):
    path = visual_file(tmp_path / "hero.safetensors", t=3)
    assert refmods.scan([str(tmp_path)])[0].visual.tokens == 3 * 4 * 3
    visual_file(path, t=5)
    assert refmods.scan([str(tmp_path)])[0].visual.tokens == 5 * 4 * 3


def test_a_name_is_only_ever_found_in_the_listing(tmp_path, monkeypatch):
    visual_file(tmp_path / "hero.safetensors")
    monkeypatch.setattr(refmods, "roots", lambda: [str(tmp_path)])
    assert refmods.find("hero").name == "hero"
    assert refmods.find("../hero") is None
    assert refmods.find(str(tmp_path / "hero")) is None


def test_an_empty_folder_says_so_in_the_dropdown(tmp_path, monkeypatch):
    monkeypatch.setattr(refmods, "roots", lambda: [str(tmp_path)])
    assert refmods.choices() == [refmods.NONE_FOUND]


def test_a_look_costs_its_two_by_two_patches_rounded_up():
    assert refmods.tokens_of("video", [1, 24, 10, 114, 64]) == 10 * 57 * 32
    assert refmods.tokens_of("image", [1, 24, 1, 7, 5]) == 4 * 3


def test_a_voice_costs_two_rows_a_latent_frame():
    assert refmods.tokens_of("audio", [1, 32, 2, 236]) == 472


def jpeg(colour=(200, 30, 30), size=(32, 16)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def stored_file(path, pictures, source="stack", times=None):
    pytest.importorskip("PIL")
    tensors = {"latent": look(t=len(pictures))}
    for index, data in enumerate(pictures):
        tensors[f"enc_{index}"] = ("U8", [len(data)], data)
    meta = {"kind": "video", "source": source, "enc_times": times or [float(i) for i in range(len(pictures))],
            "enc_fps": 24.0}
    return write(path, tensors, meta)


def test_pictures_stored_in_the_file_are_noticed(tmp_path):
    stored_file(tmp_path / "hero.safetensors", [jpeg(), jpeg()])
    assert refmods.scan([str(tmp_path)])[0].visual.stored is True


def test_pictures_promised_and_missing_are_not_counted_as_stored(tmp_path):
    write(tmp_path / "hero.safetensors", {"latent": look()},
          {"kind": "video", "enc_times": [0.0, 1.0]})
    assert refmods.scan([str(tmp_path)])[0].visual.stored is False


def test_the_square_is_drawn_with_the_first_stored_picture(tmp_path):
    first = jpeg((10, 200, 10))
    stored_file(tmp_path / "hero.safetensors", [first, jpeg()])
    [item] = refmods.scan([str(tmp_path)])
    assert refmods.preview_bytes(item) == (first, "image/jpeg")
    assert refmods.entry_record(item)["preview"] is True


def test_a_picture_saved_beside_a_pair_is_its_preview(tmp_path):
    visual_file(tmp_path / "hero_visual.safetensors")
    audio_file(tmp_path / "hero_audio.safetensors")
    (tmp_path / "hero.png").write_bytes(b"png")
    [item] = refmods.scan([str(tmp_path)])
    assert refmods.preview_bytes(item) == (b"png", "image/png")


def test_a_voice_alone_has_no_picture(tmp_path):
    audio_file(tmp_path / "hum.safetensors")
    [item] = refmods.scan([str(tmp_path)])
    assert refmods.preview_bytes(item) is None
    assert refmods.entry_record(item) == {"name": "hum", "audio": {"kind": "audio", "tokens": 80},
                                          "preview": False}


class Latent:
    def __init__(self, *shape):
        self.shape = shape


def mod(kind="video", source="stack", **fields):
    shape = (1, 32, 2, 40) if kind == "audio" else (1, 24, 3, 8, 6)
    return refmods.RefMod(name="hero", kind=kind, latent=Latent(*shape), source=source, **fields)


def test_a_stack_is_described_as_one_subject_not_as_events():
    said = refmods.hint(mod(concept="identity"))
    assert "separate photographs of one subject" in said
    assert "identity of a person or character" in said


def test_the_saved_name_and_look_are_offered_not_imposed():
    said = refmods.hint(mod(source="video", subject_name="Bob", appearance="a green wool coat"))
    assert "called Bob" in said
    assert "to keep to where it agrees: a green wool coat." in said
    assert "photographs" not in said


def test_a_voice_is_told_whose_it_is_and_how_it_was_described():
    said = refmods.hint(mod("audio", "audio", subject_name="Ann", voice="low and husky", appearance="red hair"))
    assert "voice of Ann" in said and "low and husky" in said and "red hair" not in said


def test_a_file_that_says_nothing_adds_nothing():
    assert refmods.hint(mod(source="video", concept="generic")) == ""


def test_a_voice_nothing_can_hear_is_written_from_its_saved_words():
    assert refmods.saved_caption(mod("audio", "audio", voice="low and husky")) == "low and husky."
    assert refmods.saved_caption(mod("audio", "audio", voice="x", subject_name="Ann")) == "The voice of Ann: x."
    assert refmods.saved_caption(mod("audio", "audio")) == ""


def test_the_writer_is_told_a_look_and_a_voice_are_one_entry():
    look_mod, voice_mod = mod(concept="identity"), mod("audio", "audio", concept="voice")
    assert refmods.writer_note(look_mod, voice_mod, "Audio 1") == (
        "(photos of one subject, not footage; saved as a reference for the identity of a person "
        "or character; Audio 1 is its voice) "
    )
    assert refmods.writer_note(voice_mod, look_mod, "Video 1") == (
        "(saved as a reference for a voice, the voice alone and not what plays behind it; "
        "the voice of the subject in Video 1) "
    )


def test_a_voice_only_a_description_calls_one_is_not_told_to_drop_its_backing():
    """The file has to say it was saved as a voice; a saved description could be of a song."""
    said = refmods.writer_note(mod("audio", "audio", voice="low and husky"), mod(), "Video 1")
    assert said == "(the voice of the subject in Video 1) "


def test_a_sound_that_is_no_voice_is_only_said_to_go_with_its_look():
    music = mod("audio", "audio", concept="music_style")
    assert refmods.writer_note(music, mod(source="video"), "Video 2") == (
        "(saved as a reference for a style of music; saved together with Video 2) "
    )
    assert refmods.writer_note(mod(source="video"), music, "Audio 1") == "(Audio 1 is its music) "


def test_a_refmod_that_says_nothing_of_itself_tells_the_writer_nothing():
    assert refmods.writer_note(mod(source="video")) == ""


def test_only_a_look_and_a_voice_of_one_entry_are_halves():
    look_mod, voice_mod = mod(), mod("audio", "audio")
    stranger = refmods.RefMod(name="villain", kind="audio", latent=Latent(1, 32, 2, 40), source="audio")
    found = [("ref_0", look_mod), ("ref_1", stranger), ("ref_2", voice_mod), ("ref_3", mod())]
    assert refmods.halves(found) == {"ref_0": "ref_2", "ref_2": "ref_0"}


def test_what_a_writer_cannot_read_is_said_with_its_fix():
    voice_only = mod("audio", "audio", voice="low")
    assert refmods.unseen("ref_1", voice_only, saved_ok=True) == ""
    assert "audio_vae" in refmods.unseen("ref_1", voice_only, saved_ok=False)
    assert "'vae'" in refmods.unseen("ref_0", mod(), saved_ok=True)
    assert refmods.unseen("ref_0", mod(view=object()), saved_ok=False) == ""


def test_a_refmod_never_passes_for_a_picture():
    """It carries a latent with a shape, and must not be taken for an IMAGE that does."""
    value = mod()
    assert not hasattr(value, "shape")
    assert references.kind_of(value) == ""
    assert refmods.seen(value) is None and refmods.seen("other") == "other"


def test_a_refmod_goes_on_in_its_own_group_as_it_is():
    look_mod, voice_mod = mod(), mod("audio", "audio")
    picture = types.SimpleNamespace(shape=(1, 64, 64, 3))
    bundle = references.slot_bundle([
        ("ref_0", "Picture", picture), ("ref_1", "Video", look_mod), ("ref_2", "Audio", voice_mod),
    ])
    asked = []
    slots, summary, warning = references.fan_out(bundle, False, lambda *args: asked.append(args))
    assert slots["pictures"][0] is picture
    assert slots["videos"][0] is look_mod and slots["video_audios"][0] is None
    assert slots["audios"][0] is voice_mod
    assert asked == [] and warning == ""
    assert "video_1 <- ref_1 (RefMod 'hero', 36 tokens)" in summary
    assert "audio_1 <- ref_2 (RefMod 'hero', 80 tokens)" in summary


def test_soundtracks_never_give_a_refmod_a_sound():
    bundle = references.slot_bundle([("ref_0", "Video", mod(), True)])
    slots, _summary, _warning = references.fan_out(bundle, True, None)
    assert slots["video_audios"] == [None] * references.MAX_VIDEOS


def test_the_slots_holding_refmods_are_named():
    bundle = references.slot_bundle([
        ("ref_0", "Picture", types.SimpleNamespace(shape=(1, 8, 8, 3))),
        ("ref_3", "Video", mod()), ("ref_5", "Audio", mod("audio", "audio")),
    ])
    assert references.refmods_in(bundle) == ["ref_3", "ref_5"]
    assert references.refmods_in({"pictures": []}) == []
    assert references.refmods_in(None) == []


@pytest.fixture
def torch():
    found = pytest.importorskip("torch")
    pytest.importorskip("safetensors")
    return found


def saved(path, tensors, meta):
    from safetensors.torch import save_file

    path.parent.mkdir(parents=True, exist_ok=True)
    save_file(tensors, str(path), metadata={"refmod_meta": json.dumps(meta)})
    return path


def test_a_look_is_loaded_as_float_with_what_the_file_says(tmp_path, torch):
    saved(tmp_path / "hero.safetensors", {"latent": torch.ones(1, 24, 3, 8, 6, dtype=torch.float16)},
          {"kind": "video", "source": "stack", "subject_name": "  Bob  ", "appearance": "a coat."})
    [item] = refmods.scan([str(tmp_path)])
    loaded = refmods.load(item, 0.5)["visual"]
    assert loaded.latent.dtype == torch.float32 and loaded.strength == 0.5
    assert (loaded.subject_name, loaded.appearance, loaded.stack) == ("Bob", "a coat", True)
    assert loaded.pictures is None and loaded.view is None


def test_a_bundle_member_is_loaded_from_its_own_tensor(tmp_path, torch):
    meta = {"kind": "bundle", "members": [{"kind": "video", "source": "stack"}, {"kind": "audio"}]}
    saved(tmp_path / "Aoi.safetensors",
          {"ref_0": torch.zeros(1, 24, 2, 4, 4), "ref_1": torch.full((1, 32, 2, 10), 3.0)}, meta)
    loaded = refmods.load(refmods.scan([str(tmp_path)])[0])
    assert loaded["visual"].latent_shape == [1, 24, 2, 4, 4]
    assert float(loaded["audio"].latent.mean()) == 3.0


def test_stored_pictures_come_back_as_one_batch(tmp_path, torch):
    tensors = {"latent": torch.zeros(1, 24, 2, 4, 4)}
    for index, colour in enumerate(((250, 0, 0), (0, 0, 250))):
        tensors[f"enc_{index}"] = torch.frombuffer(bytearray(jpeg(colour)), dtype=torch.uint8)
    saved(tmp_path / "hero.safetensors", tensors, {"kind": "video", "source": "video", "enc_times": [0.0, 0.5]})
    loaded = refmods.load(refmods.scan([str(tmp_path)])[0])["visual"]
    assert tuple(loaded.pictures.shape) == (2, 16, 32, 3)
    assert float(loaded.pictures[0, ..., 0].mean()) > 0.9 and float(loaded.pictures[1, ..., 2].mean()) > 0.9
    assert loaded.times == [0.0, 0.5] and loaded.decoded is False


def full(torch, kind="video", source="stack", frames=3, **fields):
    shape = (1, 32, 2, 40) if kind == "audio" else (1, 24, frames, 8, 6)
    latent = torch.linspace(-1, 1, int(torch.tensor(shape).prod())).reshape(shape)
    return refmods.RefMod(name="hero", kind=kind, latent=latent, source=source, **fields)


def test_the_block_is_the_shape_the_generator_builds(torch):
    video = refmods.block(full(torch))
    assert set(video) == {"kind", "latent_h", "latent_w", "latent", "latent_t", "ref_audio_t", "audio_latent"}
    assert (video["latent_t"], video["latent_h"], video["latent_w"], video["ref_audio_t"]) == (3, 8, 6, 0)
    picture = refmods.block(full(torch, "image", "image", frames=1))
    assert set(picture) == {"kind", "latent_h", "latent_w", "latent"}
    sound = refmods.block(full(torch, "audio", "audio"))
    assert set(sound) == {"kind", "ref_audio_t", "audio_latent"} and sound["ref_audio_t"] == 40


def test_full_strength_is_the_latent_as_saved_and_less_is_softer(torch):
    whole = full(torch)
    assert refmods.block(whole)["latent"] is whole.latent
    softer = refmods.block(full(torch, strength=0.25))["latent"]
    assert softer.shape == whole.latent.shape and not torch.equal(softer, whole.latent)
    weak = refmods.block(full(torch, "audio", "audio", strength=0.0))["audio_latent"]
    assert weak.shape == (1, 32, 2, 40)


def test_a_stack_shows_the_encoder_every_fourth_photo_two_to_a_block(torch):
    stack = full(torch, frames=10, pictures=torch.rand(10, 16, 16, 3), decoded=True)
    frames, times = refmods.encoder_frames(stack)
    assert frames.shape[0] == 3 and torch.equal(frames[1], stack.pictures[4])
    assert times == [0.0, 0.5, 1.0]


def test_a_clip_shows_the_encoder_the_pictures_it_has(torch):
    clip = full(torch, source="video", pictures=torch.rand(4, 16, 16, 3), times=[0.0, 0.5, 1.0, 1.5])
    frames, times = refmods.encoder_frames(clip)
    assert frames.shape[0] == 4 and times == [0.0, 0.5, 1.0, 1.5]


def test_two_a_second_is_timed_by_the_clock():
    picked, times = refmods.two_a_second(37)
    assert picked == [0, 12, 24, 36] and times == [0.0, 0.5, 1.0, 1.5]


def test_the_encoder_is_given_what_the_tokenizer_reads(torch):
    picture = refmods.encoder_item(full(torch, "image", "image", frames=1, pictures=torch.rand(1, 16, 16, 3)))
    assert picture["type"] == "image" and tuple(picture["data"].shape) == (1, 16, 16, 3)
    clip = refmods.encoder_item(full(torch, source="video", pictures=torch.rand(2, 16, 16, 3), times=[0.0, 0.5],
                                     strength=0.5))
    assert clip["type"] == "video" and clip["timestamps"] == [0.0, 0.5]
    assert tuple(clip["data"].shape) == (2, 16, 16, 3)
    assert refmods.encoder_item(full(torch, "audio", "audio")) == {"type": "audio"}
