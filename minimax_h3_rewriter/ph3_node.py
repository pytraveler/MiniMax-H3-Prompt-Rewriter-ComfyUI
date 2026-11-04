"""The native P-H3 upsampler with the pack's ordered references and library."""

from __future__ import annotations

from comfy_api.latest import io

from . import aspect, library, memory, ph3_prompt as ph3, previews, refmods, snapshot
from .checks import describe
from .nodes import CATEGORY
from .progress import NodeProgress, announce
from .references import SLOTS_TYPE, slot_bundle

NODE = "MiniMaxH3PH3Writer"


class MiniMaxH3PH3Writer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id=NODE, display_name="P-H3 Prompt Writer", category=CATEGORY,
            description=(
                "Writes Text2Image and Image2Image prompts with Pruna's P-H3 upsampler. "
                "Text2Image can describe the first active reference with use_ref_description; "
                "only text reaches the image generator. "
                "Load p-h3-image_prompt_upsampler with Load CLIP, type minimax. The first "
                "enabled picture is the image to edit; the others are references. VIDEO, "
                "IMAGE batches and visual RefMods support exact frame selection. RefMods "
                "are expanded to pictures and re-encoded by P-H3, not passed as video latents. "
                "Wire references through MiniMax-H3 Reference Slots to p-h3-image Conditioning, "
                "and connect width and height to keep its canvas in step with this writer."
            ),
            inputs=[
                io.Clip.Input("upsampler", tooltip="Load the P-H3 prompt upsampler, not its 4B text encoder; Load CLIP type minimax."),
                io.Combo.Input("task", options=list(ph3.TASKS), default=ph3.TASKS[0], tooltip="Text2Image uses text only; use_ref_description can describe the first active reference for its prompt. Image2Image uses the first enabled picture as the base."),
                io.Autogrow.Input("references", optional=True, template=io.Autogrow.TemplatePrefix(
                    input=io.MultiType.Input("ref", types=[io.Image, io.Video, io.Custom(refmods.TYPE)]),
                    prefix="ref_", min=0, max=ph3.MAX_IMAGES,
                )),
                io.String.Input("prompt", multiline=True, default="", tooltip="What to create or change. Name references as <Picture 1>, <Picture 2>, etc."),
                io.String.Input("keep", multiline=True, default="", tooltip="For edits: faces, poses, objects, composition or lighting that must stay unchanged."),
                io.Combo.Input("resolution", options=list(ph3.SIZES[8]), default="1:1"),
                io.Combo.Input("megapixels", options=[4, 8], default=8),
                io.Boolean.Input("match_image_ratio", default=True, tooltip="Image2Image uses the first picture's aspect ratio unless aspect_ratio is supplied. Otherwise use resolution."),
                io.Int.Input("refmod_images", default=3, min=1, max=ph3.MAX_IMAGES, tooltip="Automatic pictures per visual RefMod, evenly selected. The frames picker overrides this per socket. The total, including ordinary images, must fit within eight."),
                io.Int.Input("max_length", default=2048, min=256, max=32768, tooltip="Token budget per model call, including thinking. With use_ref_description it applies separately to the description and final prompt. Increase if it produces no final text."),
                io.Boolean.Input("thinking", default=True),
                io.Boolean.Input("bypass", default=False, tooltip="Pass prompt through as written; references and canvas are still prepared."),
                io.Boolean.Input("repeat_last", default=False, tooltip="Reuse the last prompt or the library choice. The current references and canvas still pass through."),
                io.String.Input("library_pick", default="", tooltip=library.PICK_TOOLTIP),
                io.String.Input("reference_layout", default="", tooltip="Reference strip order and disabled sockets, saved as JSON."),
                io.String.Input("reference_instructions", default="", tooltip="Instructions per input socket, saved as JSON. Applied to each picture expanded from that socket."),
                io.Vae.Input("refmod_vae", optional=True, tooltip="The original MiniMax-H3 video VAE, only for RefMods without stored or decoded pictures."),
                io.Int.Input("video_images", optional=True, default=3, min=1, max=ph3.MAX_IMAGES, tooltip="Automatic frame count for VIDEO and IMAGE batches larger than eight. Smaller IMAGE batches keep all frames. The frames picker overrides this per socket."),
                io.String.Input("frame_selection", optional=True, default="", tooltip="Selected zero-based frame indices per socket, saved as JSON by the frames picker. Missing sockets use automatic selection."),
                io.MultiType.Input(
                    io.String.Input("aspect_ratio", optional=True, default="",
                                    tooltip=aspect.TOOLTIP + " Also overrides match_image_ratio in Image2Image."),
                    types=[io.String, io.Combo],
                ),
                io.Boolean.Input("use_ref_description", optional=True, default=False,
                                 tooltip="Text2Image only: describe the first active reference's first selected frame, then write a standalone prompt from that description and the user request. Both steps use this upsampler. The user request takes priority; role says what to take from the picture. No reference images reach the image generator. Bypass passes the original prompt through."),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                io.String.Output(display_name="thinking"),
                io.String.Output(display_name="summary"),
                io.Int.Output(display_name="width"), io.Int.Output(display_name="height"),
                io.Custom(SLOTS_TYPE).Output(display_name="references", tooltip="Image2Image: expanded pictures in prompt order. Reference Slots picture_1 goes to P-H3 Conditioning image_1, and so on. Text2Image returns an empty bundle, including with use_ref_description."),
            ], hidden=[io.Hidden.unique_id, io.Hidden.prompt],
        )

    @classmethod
    def fingerprint_inputs(cls, library_pick="", repeat_last=False, **kwargs):
        return library.stamp(library_pick, repeat_last) + memory.stamp(cls.hidden.unique_id, repeat_last)

    @classmethod
    def execute(cls, upsampler, task, prompt, keep="", resolution="1:1", megapixels=8,
                match_image_ratio=True, refmod_images=3, max_length=2048, thinking=True,
                bypass=False, repeat_last=False, library_pick="", reference_layout="",
                reference_instructions="", refmod_vae=None, references=None,
                video_images=3, frame_selection="", aspect_ratio=None, use_ref_description=False):
        given = dict(locals())
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)
        progress.text("Preparing P-H3 pictures", force=True)
        graph = getattr(cls.hidden, "prompt", None) or {}
        inputs = graph.get(str(node_id), {}).get("inputs", {})
        links = {name.rsplit(".", 1)[-1]: value for name, value in inputs.items()
                 if name.startswith("references.") and isinstance(value, list)}
        shown = ph3.prepare(task, references, reference_layout, reference_instructions, refmod_vae,
                            refmod_images, video_images, frame_selection, node_id, links, use_ref_description)
        pictures = shown if task == "Image2Image" else []
        description_source = shown[0] if task == "Text2Image" and shown else None
        previews.announce(node_id, ph3.preview_items(shown, references))
        width, height, ratio = ph3.canvas(resolution, megapixels, pictures, match_image_ratio, aspect_ratio)
        handed_on = slot_bundle((p.slot, "Picture", p.image) for p in pictures)
        refs = snapshot.take((p.slot, "image", p.image) for p in pictures)
        summary = f"{task}: {width}x{height}, {len(pictures)} picture(s)"
        if task == ph3.TASKS[0]:
            connected = len(ph3.ordered(references, reference_layout))
            if description_source:
                given["description_source"] = description_source.slot
                summary += f"\nSource for prompt description: {description_source.slot}"
                if description_source.origin:
                    summary += f" ({description_source.origin})"
                summary += ". No reference pictures are passed to the image generator."
                if connected > 1:
                    summary += f" {connected - 1} other active reference(s) are ignored."
                if bypass:
                    summary += "\nBypass: source preview only; the original prompt passes through unchanged."
            elif use_ref_description:
                summary += "\nNo active reference for a description; writing from text alone."
            elif connected:
                note = f"Text2Image writes from text alone; {connected} connected reference(s) are ignored."
                summary += "\n" + note
                announce(node_id, [("warn", note)])
        for index, picture in enumerate(pictures, 1):
            summary += f"\n<Picture {index}> = {picture.slot}"
            if picture.origin:
                summary += f" ({picture.origin})"
            if index == 1:
                summary += " - base image"
        text, reasoning = prompt, ""
        generated = False
        if not bypass:
            _outputs, saved = library.picked(library_pick, repeat_last, NODE, 2, node_id, having=["image"] * len(pictures))
            repeated = None if saved is not None else memory.repeat(node_id, NODE, repeat_last, given)
            if saved is not None:
                text = saved
            elif repeated is not None:
                text, reasoning = repeated
            else:
                if description_source:
                    text, reasoning, description = ph3.generate_from_reference(
                        upsampler, prompt, ratio, description_source, max_length, thinking,
                        progress=lambda message: progress.text(message, force=True),
                    )
                    summary += "\n\nReference description:\n" + description
                else:
                    progress.text("Writing the P-H3 image prompt", force=True)
                    request = ph3.request(prompt, ratio, pictures, keep)
                    text, reasoning = ph3.generate(upsampler, request, pictures, max_length, thinking)
                generated = True
            if description_source and not generated:
                summary += "\nrepeat_last: reusing a stored prompt; no new visual description was generated."
        issues = ph3.review(text, task, ["image"] * len(pictures))
        if issues:
            announce(node_id, [(issue.level, issue.message) for issue in issues])
            summary += "\n\n" + describe(issues)
        if generated:
            memory.keep(node_id, NODE, (text, reasoning), given, references=refs, task=task, plain_text=True)
        progress.finish(summary + "\n\n" + text[-2000:])
        return io.NodeOutput(text, reasoning, summary, width, height, handed_on)


NODE_CLASS_MAPPINGS = {"MiniMaxH3PH3Writer": MiniMaxH3PH3Writer}
NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3PH3Writer": "P-H3 Prompt Writer"}
