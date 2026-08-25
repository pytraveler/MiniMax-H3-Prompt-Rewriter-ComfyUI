"""Describing a reference asset with a model ComfyUI already has loaded.

``mtmd_engine`` shells out to ``llama-mtmd-cli``, which means one process and one
read of the weights off disk *per asset*: a shot with five references pays for
the model five times. Since 0.30 ComfyUI can do the same work itself --
``CLIPLoader`` loads a multimodal text encoder, ``clip.generate`` runs it -- and
the model then stays where ComfyUI's own allocator put it. Five references
become one load, and a second run while you tune the wording costs nothing at
all.

This is not a replacement. The GGUF route reaches models ComfyUI has no encoder
for and needs nothing loaded in advance; this one is what to reach for when a
suitable encoder is already on disk for an image model. The nodes pick between
them by whether ``clip`` is connected, and the two produce the same shape of
answer either way.

Which encoders qualify, checked against ComfyUI 0.30:

- **Qwen3-VL** sees, and does not hear. A batch of frames is split per frame by
  its tokenizer, so a clip still works.
- **Gemma-4 E4B/E2B** see and hear through a real audio encoder; **Gemma-4 12B**
  is the encoder-free "unified" build and hears through a projector alone;
  **Gemma-4 31B** has ``audio_config = None`` and does not hear.

That last row is why capability is probed by looking for ``audio_projector``
rather than the audio *encoder*: 12B has no ``audio_model`` at all, and asking
for one would report the model deaf while it is listening perfectly well.
"""

from __future__ import annotations

import logging

from . import media

from .constants import answer_only, normalize_seed

log = logging.getLogger(__name__)

MAX_CAPTION_TOKENS = 1024

VISION_MODULES = ("visual", "vision_model")
AUDIO_MODULES = ("audio_projector",)


def _module_names(clip) -> set[str]:
    """The last path element of every submodule of the loaded encoder."""
    model = getattr(clip, "cond_stage_model", None)
    if model is None or not hasattr(model, "named_modules"):
        return set()
    return {name.rsplit(".", 1)[-1] for name, _ in model.named_modules() if name}


def capabilities(clip) -> dict[str, bool]:
    """What the connected encoder can actually take in, by what it is built from."""
    names = _module_names(clip)
    return {
        "vision": any(name in names for name in VISION_MODULES),
        "audio": any(name in names for name in AUDIO_MODULES),
    }


def check(clip, kinds: set[str]) -> None:
    """Refuse before anything runs if the encoder cannot read what is connected."""
    names = _module_names(clip)
    able = capabilities(clip)
    if "audio" in kinds and able["audio"] and "audio_model" in names:
        log.warning(
            "[minimax_h3_rewriter.clip_caption] this encoder has a separate audio tower "
            "(Gemma-4 E2B/E4B shape). On ComfyUI 0.30 that path was not seen to deliver audio "
            "to the model, and the caption may come back saying no clip was provided. Gemma-4 "
            "12B works; so does leaving 'clip' unconnected and using the GGUF captioner."
        )
    if "audio" in kinds and not able["audio"]:
        raise RuntimeError(
            "The model on 'clip' has no audio path, so it cannot hear the clip you "
            "connected. Gemma-4 E2B, E4B and 12B can; Qwen3-VL and Gemma-4 31B cannot. "
            "Load one of those, or disconnect 'clip' and let the captioner in 'model' "
            "take the audio instead."
        )
    if kinds & {"image", "video"} and not able["vision"]:
        raise RuntimeError(
            "The model on 'clip' has no vision tower, so it cannot see. Connect a "
            "multimodal text encoder -- Qwen3-VL or Gemma-4 -- or disconnect 'clip'."
        )


def _frames(image=None, video=None, max_frames=media.DEFAULT_MAX_FRAMES):
    """The frames to show the model, as one IMAGE batch, or ``None``.

    A VIDEO is decoded here; an IMAGE batch is thinned to ``max_frames`` the same
    way the GGUF route thins it. Both end up as ``image=`` rather than
    ``video=``: Qwen3-VL's tokenizer has no video argument and would silently
    ignore the frames, and Gemma-4's video path re-subsamples to 1 fps, which
    would quietly overrule the very knob the node offers.
    """
    if video is not None:
        with media.Workspace() as workspace:
            batch, _total, _seconds = media.video_tensor(video, workspace, max_frames)
        return batch
    if image is None:
        return None
    if getattr(image, "ndim", 0) == 3:
        image = image[None, ...]
    wanted = media.frame_indices(image.shape[0], max_frames)
    return image[wanted] if len(wanted) < image.shape[0] else image


def describe(
    clip,
    instruction: str,
    image=None,
    audio=None,
    video=None,
    max_frames: int = media.DEFAULT_MAX_FRAMES,
    seed: int = 42,
    settings: dict | None = None,
    greedy: bool = True,
    progress=None,
) -> str:
    """One asset, one caption, through the encoder already loaded on ``clip``.

    Mirrors :func:`mtmd_engine.describe` closely enough that a node can pick
    between the two on one line.
    """
    settings = dict(settings or {})
    kinds = set()
    if image is not None:
        kinds.add("image")
    if video is not None:
        kinds.add("video")
    if audio is not None:
        kinds.add("audio")
    check(clip, kinds)

    frames = _frames(image, video, max_frames)
    tokens = clip.tokenize(
        instruction,
        image=frames,
        audio=audio,
        skip_template=False,
        thinking=False,
        min_length=1,
    )

    generated = clip.generate(
        tokens,
        do_sample=not greedy,
        max_length=min(int(settings.get("max_new_tokens", MAX_CAPTION_TOKENS)), MAX_CAPTION_TOKENS),
        temperature=float(settings.get("temperature", 0.7)),
        top_k=int(settings.get("top_k", 20)),
        top_p=float(settings.get("top_p", 0.8)),
        min_p=0.0,
        repetition_penalty=float(settings.get("repetition_penalty", 1.05)),
        seed=normalize_seed(seed),
    )
    return answer_only(clip.decode(generated))
