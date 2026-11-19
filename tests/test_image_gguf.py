"""The same H3 Image instructions reach both local GGUF runners."""

import asyncio
from types import SimpleNamespace

import numpy as np

from minimax_h3_rewriter import image_gguf, image_prompt


def test_local_text_uses_selected_gguf_and_shared_expansion_contract(monkeypatch):
    monkeypatch.setattr(image_gguf, "valid_writer", lambda _label: True)

    def run(model, messages, greedy, seed, keep_loaded, settings, progress, label):
        assert model == "Local text model"
        assert messages[0]["content"] == image_prompt.SYSTEM_PROMPT
        assert "Red mug." in messages[1]["content"]
        assert greedy and seed == 44 and not keep_loaded
        assert settings["max_new_tokens"] == 768 and not settings["auto_download"]
        return '<think>Composition.</think>{"expanded_caption":"A red mug.","aspect_ratio":"3:2"}'

    monkeypatch.setattr(image_gguf, "run_messages", run)
    result = asyncio.run(image_gguf.expand("Red mug.", (), "auto", "Local text model", 1024,
                                         True, 44, False, {"max_new_tokens": 768, "temperature": 0}, 1024, None))
    assert result == ("A red mug.", "Composition.", "3:2", ["Local model: Local text model"])


def test_local_vision_uses_pair_in_chip_order(monkeypatch):
    choice = SimpleNamespace(local=True, reference="vision.gguf", mmproj="mmproj.gguf")
    monkeypatch.setattr(image_gguf, "_resolve_captioner_choice", lambda label: choice)
    monkeypatch.setattr(image_gguf, "check_encoders", lambda *_: None)

    def run(model, projector, messages, copies, runtime, seed, greedy, keep, progress, label):
        assert (model, projector) == (choice.reference, choice.mmproj)
        assert [one.slot for one in copies] == ["ref_1", "ref_0"]
        assert not runtime.thinking and runtime.context_size == 4096
        content = messages[1]["content"]
        labels = [content[index - 1]["text"].split(" has ")[0] for index, part in enumerate(content)
                  if part["type"] == "image"]
        assert labels == ["<Picture 1>", "<Picture 2>"]
        assert "target_aspect_ratio" in content[-1]["text"]
        return '{"expanded_caption":"Recolored <Picture 1>.","target_aspect_ratio":"4:3"}', "", {}

    monkeypatch.setattr(image_gguf.qwen_engine, "generate_gguf", run)
    blank = np.zeros((1, 128, 192, 3), dtype=np.float32)
    references = image_prompt.pictures({"ref_0": blank, "ref_1": blank}, '{"order":["ref_1","ref_0"]}', "{}")
    result = asyncio.run(image_gguf.expand("Recolor.", references, "auto", "Local vision model", 1024,
                                         False, 44, False, {"n_ctx": 4096}, 1024, None))
    assert result[:3] == ("Recolored <Picture 1>.", "", "4:3")
