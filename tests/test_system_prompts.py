"""A changed local guide must reach the writers without changing the workflow."""

import pytest

from minimax_h3_rewriter import system_prompts as prompts
from minimax_h3_rewriter.api_options import MiniMaxH3ApiWriterOptions
from minimax_h3_rewriter.qwen_options import MiniMaxH3QwenRewriterOptions


@pytest.fixture
def folder(tmp_path, monkeypatch):
    root = tmp_path / "system_prompts"
    monkeypatch.setattr(prompts, "root", lambda: root)
    return root


def test_system_prompt_priority_and_file_content_fingerprint(folder):
    name = "Nexora Ref2VA"
    assert prompts.files() == []
    first = prompts.save(name, "First version.")
    assert prompts.files() == [name]
    assert prompts.resolve("", name) == "First version."
    assert prompts.resolve("Inline instructions.", name) == "Inline instructions."
    assert prompts.resolve(" ", "") == ""
    for options in (MiniMaxH3ApiWriterOptions, MiniMaxH3QwenRewriterOptions):
        assert options.IS_CHANGED(name, auto_download=False) == first
        assert options().execute(system_prompt_file=name)[0].system_prompt_file == name
    second = prompts.save(name, "Edited prompt.", first)
    assert prompts.stamp(name) == second != first
    assert prompts.resolve(" ", name) == "Edited prompt."
    (folder / (name + ".md")).unlink()
    assert prompts.stamp(name) is None
    assert prompts.resolve("Inline instructions.", name) == "Inline instructions."
    with pytest.raises(prompts.PromptFileError, match="Nexora Ref2VA.*missing"):
        prompts.resolve("", name)


def test_save_requires_the_current_revision(folder):
    name = "Режиссёрская версия"
    assert prompts.files() == []
    first = prompts.save(name, "Первый вариант")
    second = prompts.save(name, "Второй вариант", first)
    for stale in ("", first):
        with pytest.raises(prompts.PromptFileError) as error:
            prompts.save(name, "Устаревший вариант", stale)
        assert error.value.status == 409
    assert prompts.read(name) == ("Второй вариант", second)
    assert [file.name for file in folder.iterdir()] == [name + ".md"]


@pytest.mark.parametrize("name", ["../outside", r"C:\outside", "a/b", "LPT1.log", "guide. "])
def test_unsafe_names_are_rejected_before_the_directory_is_created(folder, name):
    with pytest.raises(prompts.PromptFileError):
        prompts.save(name, "Instructions.")
    assert not folder.exists()


def test_default_template_uses_the_duration_from_the_request(monkeypatch):
    monkeypatch.setattr(prompts.guides, "text", lambda kind, download: "Small test guide.")
    copied = prompts.template("FL2VA", qwen=False, auto_download=False)
    assert "Small test guide." in copied and "Replace [duration] with the requested duration" in copied
    assert "8.25-second mark" in prompts.guide_prompt.system_prompt("Small test guide.", "FL2VA", 8.25)
    qwen = prompts.template("Ref2VA", qwen=True, auto_download=False)
    assert "reference-conditioned video" in qwen and qwen.endswith("Small test guide.")
