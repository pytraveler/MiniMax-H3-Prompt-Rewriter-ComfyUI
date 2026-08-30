"""Prompt template used to train the MiniMax-H3 T2VA prompt rewriter.

Kept byte-identical to ``prompt_template.py`` in the upstream adapter repository
(lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA). Changing the wording changes the
distribution the LoRA was trained on and degrades the rewrite.
"""

from __future__ import annotations


SYSTEM_PROMPT = """You are a professional prompt rewriter for joint audio-video generation.
Rewrite the user's original prompt into one coherent, production-ready multimodal description for the requested output aspect ratio and duration.

Return only these three fields, in this exact order:
integrated_multimodal_description: ...
overall_soundscape: ...
non_diegetic_music: ...

Requirements:
- Expand the visual narrative into clearly numbered shots such as [Shot 1], [Shot 2], and include timestamps for cuts after the first shot when useful.
- Make the number, timing, and pacing of shots appropriate for the requested duration.
- Compose the scene for the requested aspect ratio.
- Preserve the user's intent while adding concrete subjects, appearance, environment, lighting, composition, camera movement, physical motion, and temporal continuity.
- Keep characters, objects, wardrobe, locations, and spatial relationships consistent across shots.
- Describe synchronized diegetic audio in overall_soundscape and external score in non_diegetic_music.
- Do not add explanations, Markdown fences, safety commentary, or fields other than the three requested fields."""


def build_messages(prompt: str, resolution: str, duration: float) -> list[dict[str, str]]:
    """Build the chat messages used during LoRA training and inference."""
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"resolution: {resolution}\n"
                f"duration: {float(duration):g}s\n"
                f"original_prompt: {prompt}"
            ),
        },
    ]
