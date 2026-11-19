"""Prompt expansion for MiniMax-H3-Image, wired to its native conditioning."""

from __future__ import annotations

from comfy_api.latest import io

from . import api_config, image_api_writer, image_context_ir, image_gguf, image_prompt as image
from . import library, memory, previews, snapshot
from .api_client import ApiError
from .api_contract import ContractError
from .api_options import OPTIONS_TYPE
from .nodes import (
    CATEGORY, OPTIONS_TYPE as LOCAL_OPTIONS_TYPE, captioner_choices, named_verdicts,
    valid_captioner, valid_writer, writer_choices,
)
from .ph3_prompt import PICTURE, object_of
from .progress import NodeProgress, announce
from .references import SLOTS_TYPE, slot_bundle

NODE = "MiniMaxH3ImagePromptWriter"
TITLE = "MiniMax-H3 Image Prompt Writer"


def _cancelled():
    from comfy import model_management

    return model_management.processing_interrupted()


class MiniMaxH3ImagePromptWriter(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id=NODE, display_name=TITLE, category=CATEGORY,
            description=(
                "Expands short requests for MiniMax-H3-Image. Connect up to 16 pictures for editing, "
                "or leave the image inputs empty for text-to-image. Local CLIP uses a connected "
                "prompt model, such as Qwen3.5 9B. MiniMax Context IR uses MiniMax's dedicated API. "
                "API Writer sends the H3 Image writing instructions to the selected Chat Completions "
                "model. Local GGUF loads a model from the pack's model list. Connect prompt, width and height to "
                "MiniMax H3 Image Conditioning."
            ),
            search_aliases=["h3 image prompt expander", "minimax context ir"],
            inputs=[
                io.Combo.Input("backend", options=list(image.BACKENDS), default=image.LOCAL),
                io.Autogrow.Input("references", optional=True, template=io.Autogrow.TemplatePrefix(
                    input=io.Image.Input("image", tooltip="Reference image. Uses the first frame of a batch."),
                    prefix="ref_", min=0, max=image.MAX_IMAGES,
                )),
                io.String.Input("prompt", multiline=True, default="", tooltip="What to create or edit. Use <Picture 1>, <Picture 2>, etc. to refer to the numbered chips."),
                io.String.Input("aspect_ratio", default="auto", tooltip="Auto lets the expander choose. Enter a fixed ratio from 1:3 to 3:1 to set the canvas yourself."),
                io.Combo.Input("area", options=list(image.AREA_SIDES), default="2K",
                               tooltip="Approximate canvas area. 2K means 2048 x 2048 at 1:1. For an exact canvas, set size."),
                io.Boolean.Input("bypass", default=False, tooltip="Pass the original prompt and ordered references through. Auto canvas follows the first picture or uses 1:1."),
                io.Boolean.Input("repeat_last", default=False, tooltip="Reuse the previous expansion or a library prompt with the current references."),
                io.Clip.Input("upsampler", optional=True, lazy=True, tooltip="For Local CLIP. Load the Qwen3.5 9B expander from the H3 Image ComfyUI template, using the same Load CLIP type."),
                io.String.Input("size", optional=True, default="auto", advanced=True, tooltip="Exact canvas WIDTHxHEIGHT, with edges divisible by 32. Set aspect_ratio to auto to use it."),
                io.String.Input("profile_id", optional=True, default="", advanced=True,
                                tooltip="For API Writer or MiniMax Context IR. In Context IR, leave empty to use MINIMAX_API_KEY from the server environment with api.minimax.io."),
                io.Int.Input("max_length", optional=True, default=4096, min=256, max=32768, advanced=True,
                             tooltip="Local model token budget, including thinking."),
                io.Boolean.Input("thinking", optional=True, default=True, advanced=True,
                                 tooltip="Thinking for Local CLIP and multimodal Local GGUF. Text GGUF uses its chat template."),
                io.Int.Input("seed", optional=True, default=0, min=0, max=0xffffffffffffffff, advanced=True, control_after_generate=True),
                io.String.Input("library_pick", optional=True, default="", tooltip=library.PICK_TOOLTIP),
                io.String.Input("reference_layout", optional=True, default="{}"),
                io.String.Input("reference_instructions", optional=True, default="{}"),
                io.String.Input("model", optional=True, default="", advanced=True,
                                tooltip="API Writer model ID from the selected profile's Allowed models. Empty uses the profile default."),
                io.Custom(OPTIONS_TYPE).Input("options", optional=True, lazy=True,
                    tooltip="API Writer uses max_tokens, temperature, response_format and timeout_seconds from API Writer Options."),
                io.Int.Input("image_max_side", optional=True, default=1024, min=64, max=4096, step=64, advanced=True,
                             tooltip="Maximum side of the image copies used by API Writer or Local GGUF. Preserves aspect ratio."),
                io.Combo.Input("writer_model", options=[image_gguf.SELECT_MODEL] + writer_choices(),
                               default=image_gguf.SELECT_MODEL, optional=True, tooltip="Local GGUF writer for Text-to-Image."),
                io.Combo.Input("caption_model", options=[image_gguf.SELECT_MODEL] + captioner_choices(),
                               default=image_gguf.SELECT_MODEL, optional=True, tooltip="Local GGUF model and its matching vision projector for Image Edit."),
                io.Custom(LOCAL_OPTIONS_TYPE).Input("local_options", optional=True, lazy=True,
                    tooltip="Connect MiniMax-H3 Rewriter Options for Local GGUF sampling, context size, runtime and GPU settings."),
                io.Boolean.Input("keep_model_loaded", default=False, optional=True, advanced=True,
                                 tooltip="Keep the Local GGUF model loaded after the rewrite."),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                io.Int.Output(display_name="width"),
                io.Int.Output(display_name="height"),
                io.String.Output(display_name="aspect_ratio"),
                io.Custom(SLOTS_TYPE).Output(display_name="references", tooltip="Enabled pictures in prompt order. Reference Slots exposes pictures 1 to 9. For more, wire the originals to Conditioning in the order shown on the chips."),
                io.String.Output(display_name="thinking"),
                io.String.Output(display_name="summary"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def validate_inputs(cls, backend=None, writer_model=None, caption_model=None, references=None,
                        bypass=False, repeat_last=False):
        if backend != image.LOCAL_GGUF or bypass or repeat_last:
            return True
        unused = (None, "", image_gguf.SELECT_MODEL)
        return named_verdicts(
            ("writer_model", True if writer_model in unused else valid_writer(writer_model)),
            ("caption_model", True if caption_model in unused else valid_captioner(caption_model)),
        )

    @classmethod
    def check_lazy_status(cls, backend, bypass=False, repeat_last=False, library_pick="", **inputs):
        requested = {image.LOCAL: "upsampler", image.API_WRITER: "options",
                     image.LOCAL_GGUF: "local_options"}.get(backend)
        if bypass or requested not in inputs or inputs[requested] is not None:
            return []
        if repeat_last:
            record = memory.recall(cls.hidden.unique_id)
            if object_of(library_pick).get("id") or (record is not None and record.node_class == NODE):
                return []
        return [requested]

    @classmethod
    def fingerprint_inputs(cls, backend, library_pick="", repeat_last=False, profile_id="", bypass=False, **_):
        config = ()
        if backend in (image.CONTEXT_IR, image.API_WRITER) and profile_id and not bypass:
            try:
                config = api_config.config_stamp()
            except api_config.ConfigError:
                return float("nan")
        return repr(config) + library.stamp(library_pick, repeat_last) + memory.stamp(cls.hidden.unique_id, repeat_last)

    @classmethod
    async def execute(cls, backend, prompt, aspect_ratio="auto", area="2K", bypass=False, repeat_last=False,
                      upsampler=None, size="auto", profile_id="", max_length=4096, thinking=True, seed=0,
                      library_pick="", reference_layout="{}", reference_instructions="{}", references=None,
                      model="", options=None, image_max_side=1024, writer_model="", caption_model="",
                      local_options=None, keep_model_loaded=False):
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)
        progress.text("Preparing H3 Image prompt", force=True)
        given = dict(backend=backend, prompt=prompt, aspect_ratio=aspect_ratio, area=area, size=size,
                     profile_id=profile_id, max_length=max_length, thinking=thinking, seed=seed,
                     reference_layout=reference_layout, reference_instructions=reference_instructions,
                     references=references, model=model, options=options, image_max_side=image_max_side,
                     writer_model=writer_model, caption_model=caption_model, local_options=local_options,
                     keep_model_loaded=keep_model_loaded if backend == image.LOCAL_GGUF else None)
        try:
            if backend not in image.BACKENDS:
                raise ContractError("Select Local CLIP, Local GGUF, MiniMax Context IR or API Writer.")
            ratio, dimensions = image.canvas_selection(aspect_ratio, size, area)
            pictures = image.pictures(references, reference_layout, reference_instructions)
            previews.announce(node_id, ((one.spec.slot, one.value) for one in pictures))
            task = "ImageEdit" if pictures else "Text2Image"
            source, text, reasoning, suggested = "Bypass", prompt, "", ""
            details = []
            if not bypass:
                saved, selected = library.picked(library_pick, repeat_last, NODE, 3, node_id,
                                                  having=["image"] * len(pictures))
                previous = memory.repeat(node_id, NODE, repeat_last, given) if selected is None else None
                if selected is not None:
                    source = "Library"
                    text, reasoning, suggested = saved or (selected, "", "")
                elif previous is not None:
                    source = "Repeat last"
                    text, reasoning, suggested = previous
                else:
                    source = backend
                    request = image.instruction(prompt, pictures)
                    progress.text(f"Expanding H3 Image prompt with {backend}", force=True)
                    if backend == image.LOCAL:
                        text, reasoning, suggested = image.expand_local(upsampler, request, pictures, ratio, max_length, thinking, seed)
                    elif backend == image.API_WRITER:
                        text, reasoning, suggested, details = await image_api_writer.expand(
                            request, pictures, ratio, profile_id.strip(), model, options, image_max_side,
                            cancelled=_cancelled,
                        )
                    elif backend == image.LOCAL_GGUF:
                        text, reasoning, suggested, details = await image_gguf.expand(
                            request, pictures, ratio, caption_model if pictures else writer_model,
                            max_length, thinking, seed, keep_model_loaded, local_options, image_max_side,
                            progress, cancelled=_cancelled,
                        )
                    else:
                        text, suggested, details = await image_context_ir.expand(
                            request, pictures, ratio, dimensions, profile_id.strip(), cancelled=_cancelled,
                        )
                    memory.keep(node_id, NODE, (text, reasoning, suggested), given, task=task, plain_text=True,
                                references=snapshot.take((one.spec.slot, "image", one.value) for one in pictures))
        except (ContractError, RuntimeError) as error:
            if isinstance(error, ApiError) and error.code == "cancelled":
                from comfy import model_management

                model_management.throw_exception_if_processing_interrupted()
            else:
                announce(node_id, [("warn", str(error))], kind="refusal")
            raise
        if ratio == "auto":
            ratio = suggested or image.fallback_ratio(pictures)
        width, height = image.canvas(ratio, dimensions, area)
        handed_on = slot_bundle((one.spec.slot, "Picture", one.value) for one in pictures)
        mismatched = sorted({int(value) for value in PICTURE.findall(text) if not 1 <= int(value) <= len(pictures)})
        if mismatched:
            note = "No image is wired for " + ", ".join(f"<Picture {index}>" for index in mismatched) + "."
            details.append(note)
            announce(node_id, [("warn", note)])
        summary = "\n".join([
            f"{task} | {source} | {width}x{height} ({ratio})",
            *(f"<Picture {index}> = {one.spec.slot}" for index, one in enumerate(pictures, 1)),
            *details,
        ])
        progress.finish(summary)
        return io.NodeOutput(text, width, height, ratio, handed_on, reasoning, summary)


NODE_CLASS_MAPPINGS = {"MiniMaxH3ImagePromptWriter": MiniMaxH3ImagePromptWriter}
NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3ImagePromptWriter": TITLE}
