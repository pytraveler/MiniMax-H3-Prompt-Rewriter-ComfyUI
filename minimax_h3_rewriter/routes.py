"""HTTP routes backing the node's buttons.

Registered on import; a failure here must never stop the nodes from loading, so
everything is guarded and logged rather than raised.
"""

from __future__ import annotations

import logging

from . import catalog, guides, memory

log = logging.getLogger(__name__)

PREFIX = "/minimax_h3_rewriter"


def register() -> None:
    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.post(f"{PREFIX}/open_model_list")
    async def open_model_list(request):
        try:
            path = catalog.reveal()
        except Exception as error:
            log.error("[minimax_h3_rewriter.routes] could not open the model list: %s", error)
            return web.json_response({"ok": False, "error": str(error)}, status=500)
        return web.json_response({"ok": True, "path": path})

    @routes.post(f"{PREFIX}/open_guide_folder")
    async def open_guide_folder(request):
        try:
            path = guides.reveal()
        except Exception as error:
            log.error("[minimax_h3_rewriter.routes] could not open the guide folder: %s", error)
            return web.json_response(
                {"ok": False, "error": str(error), "path": guides.root()}, status=500
            )
        return web.json_response({"ok": True, "path": path})

    @routes.get(f"{PREFIX}/memory")
    async def prompt_memory(request):
        """What every node still holds, so a reloaded page can label the switch."""
        return web.json_response(
            {node: memory.summary(record) for node, record in memory.LAST.items()}
        )

    @routes.get(f"{PREFIX}/model_list")
    async def model_list(request):
        return web.json_response(
            {
                "path": catalog.user_file(),
                "models": [entry.label for entry in catalog.load()],
                "writers": [entry.label for entry in catalog.writers()],
            }
        )


try:
    register()
    log.info("[minimax_h3_rewriter] routes registered")
except Exception as error:  # noqa: BLE001 - the nodes must still load
    log.warning("[minimax_h3_rewriter] routes not registered: %s", error)
