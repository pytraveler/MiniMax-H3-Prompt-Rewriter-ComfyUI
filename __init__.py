"""ComfyUI entry point for the MiniMax-H3 T2VA prompt rewriter."""

import importlib
import logging

from .minimax_h3_rewriter import routes as _routes  # registers HTTP routes
from .minimax_h3_rewriter.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .minimax_h3_rewriter import writer_8b

NODE_CLASS_MAPPINGS.update(writer_8b.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(writer_8b.NODE_DISPLAY_NAME_MAPPINGS)

log = logging.getLogger(__name__)

for _module_name, _label in (
    ("multi_caption", "Multi Reference Caption"),
    ("universal", "Universal Writer"),
    ("prompt_check", "Prompt Check"),
    ("reference_adapter", "Reference Adapter"),
    ("writer_omni", "Prompt Rewriter Omni"),
    ("universal_rewriter", "Universal Rewriter"),
):
    try:
        _module = importlib.import_module(f".minimax_h3_rewriter.{_module_name}", __name__)
    except Exception:
        log.warning(
            "[minimax_h3_rewriter] '%s' needs a newer ComfyUI than this one, so it is not "
            "registered. Every other node in the pack is unaffected.",
            _label,
            exc_info=True,
        )
        continue
    NODE_CLASS_MAPPINGS.update(_module.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_module.NODE_DISPLAY_NAME_MAPPINGS)

WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
