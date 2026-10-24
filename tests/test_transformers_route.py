"""What the safetensors route hands Transformers, on 4.57 as on 5.x.

Qwen2.5-Omni is the case that matters (issue #20). The wrapper has no auto
class; the thinker inside it does on 5.x and does not on 4.57, which still
ships the class itself. And 4.57's Omni processor reads the system turn as a
list of parts before rendering it. Only ``config.json`` is written; nothing is
loaded.
"""

import importlib
import json
import pathlib
import re
import sys
import types

import pytest

pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")
from transformers.models.auto import modeling_auto  # noqa: E402

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

engine = importlib.import_module(f"{_PKG}.engine")
template = importlib.import_module(f"{_PKG}.prompt_template_omni")

OMNI = {
    "model_type": "qwen2_5_omni",
    "talker_config": {"model_type": "qwen2_5_omni_talker"},
    "thinker_config": {
        "model_type": "qwen2_5_omni_thinker",
        "text_config": {"model_type": "qwen2_5_omni_text", "hidden_size": 3584},
    },
}


def checkpoint(tmp_path, config):
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return str(tmp_path)


def test_omni_loads_as_its_thinker(tmp_path):
    model_class = engine._model_class(checkpoint(tmp_path, OMNI))
    assert model_class is transformers.Qwen2_5OmniThinkerForConditionalGeneration


def test_omni_loads_as_its_thinker_without_a_mapping_entry(tmp_path, monkeypatch):
    """The 4.57 picture: no auto class names the thinker."""
    monkeypatch.delitem(
        modeling_auto.MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES,
        "qwen2_5_omni_thinker",
        raising=False,
    )
    model_class = engine._model_class(checkpoint(tmp_path, OMNI))
    assert model_class is transformers.Qwen2_5OmniThinkerForConditionalGeneration


def test_a_tower_that_cannot_generate_is_not_taken():
    assert engine._generating_class("qwen2_5_omni", "qwen2_5_omni_text") is None


def test_remote_code_falls_through_to_the_auto_class(tmp_path):
    config = {"model_type": "not_in_transformers", "text_config": {"model_type": "qwen2"}}
    model_class = engine._model_class(checkpoint(tmp_path, config))
    assert model_class is transformers.AutoModelForImageTextToText


def test_a_checkpoint_with_its_own_code_is_refused_before_anything_loads(tmp_path, monkeypatch):
    """No switch lets it through since 0.31.0 (issue #21): ``auto_map`` is a refusal."""
    path = checkpoint(
        tmp_path, {"model_type": "custom", "auto_map": {"AutoModel": "modeling_custom.Model"}}
    )
    monkeypatch.setattr(engine, "unload", lambda: pytest.fail("got as far as loading"))
    with pytest.raises(RuntimeError, match="does not run code that comes with a model"):
        engine.load(path, None, "bfloat16", "sdpa")


def test_nothing_in_the_pack_asks_transformers_to_run_model_code():
    """Every ``trust_remote_code`` the pack hands Transformers is a literal False."""
    given = re.compile(r"""["']?trust_remote_code["']?\s*[=:]\s*([^\s,)}]+)""")
    for path in (_ROOT / _PKG).glob("*.py"):
        for value in given.findall(path.read_text(encoding="utf-8")):
            assert value == "False", f"{path.name} passes trust_remote_code={value}"


def test_a_text_only_repack_is_a_causal_lm(tmp_path):
    model_class = engine._model_class(checkpoint(tmp_path, {"model_type": "qwen3_5_text"}))
    assert model_class is transformers.AutoModelForCausalLM


def test_the_processor_gets_the_system_turn_as_parts():
    """What 4.57's Omni processor does before it renders anything."""
    messages = template.build_messages("A fox in snow.", "i2av", kinds=("image",))
    system = engine._as_parts(messages)[0]
    assert system["role"] == "system"
    assert system["content"][0]["text"] == template.system_prompt_for("i2av")


def test_parts_leave_a_list_alone_and_the_original_untouched():
    messages = template.build_messages("A fox in snow.", "i2av", kinds=("image",))
    shaped = engine._as_parts(messages)
    assert shaped[1] is messages[1]
    assert isinstance(messages[0]["content"], str)
