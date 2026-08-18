"""ComfyUI entry point for the MiniMax-H3 T2VA prompt rewriter."""

import logging

from .minimax_h3_rewriter import routes as _routes  # registers HTTP routes
from .minimax_h3_rewriter.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .minimax_h3_rewriter import writer_8b

NODE_CLASS_MAPPINGS.update(writer_8b.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(writer_8b.NODE_DISPLAY_NAME_MAPPINGS)

log = logging.getLogger(__name__)

try:
    from .minimax_h3_rewriter import multi_caption

    NODE_CLASS_MAPPINGS.update(multi_caption.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(multi_caption.NODE_DISPLAY_NAME_MAPPINGS)
except Exception:
    log.warning(
        "[minimax_h3_rewriter] 'Multi Reference Caption' needs a newer ComfyUI than this one, "
        "so it is not registered. Every other node in the pack is unaffected.",
        exc_info=True,
    )

WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
