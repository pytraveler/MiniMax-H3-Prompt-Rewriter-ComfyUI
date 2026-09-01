"""HTTP routes backing the node's buttons.

Registered on import; a failure here must never stop the nodes from loading, so
everything is guarded and logged rather than raised.
"""

from __future__ import annotations

import logging

from . import catalog, guides, library, memory

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

    @routes.get(f"{PREFIX}/memory/text")
    async def memory_text(request):
        """The whole answer a node is holding -- the summary carries only its opening."""
        record = memory.recall(request.query.get("node") or "")
        return web.json_response({"text": record.text if record else ""})

    @routes.get(f"{PREFIX}/references")
    async def node_references(request):
        """The thumbnails one node is holding -- what a save would put in the file."""
        record = memory.recall(request.query.get("node") or "")
        return web.json_response({"references": record.references if record else []})

    @routes.get(f"{PREFIX}/library/files")
    async def library_files(request):
        return web.json_response({"files": library.files(), "path": library.root()})

    @routes.get(f"{PREFIX}/library")
    async def library_records(request):
        name = request.query.get("file") or library.DEFAULT_FILE
        data = library.load(name)
        return web.json_response(
            {
                "file": library.clean(name),
                "records": data["records"],
                "groups": library.groups(data["records"]),
                "problem": data.get("problem", ""),
            }
        )

    @routes.post(f"{PREFIX}/library/create")
    async def library_create(request):
        body = await request.json()
        try:
            made = library.create(body.get("file") or "")
        except OSError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=500)
        return web.json_response({"ok": True, "file": made, "files": library.files()})

    @routes.post(f"{PREFIX}/library/save")
    async def library_save(request):
        """Keep the session record this node is holding, under a name."""
        body = await request.json()
        record = memory.recall(body.get("node"))
        if record is None:
            return web.json_response(
                {
                    "ok": False,
                    "error": (
                        "this node has nothing to save yet - run it once, and the answer it "
                        "writes is what gets kept"
                    ),
                },
                status=404,
            )
        try:
            saved = library.add(
                body.get("file") or library.DEFAULT_FILE,
                library.from_record(
                    record,
                    body.get("name") or "",
                    body.get("description") or "",
                    body.get("groups") or [],
                ),
            )
        except OSError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=500)
        return web.json_response({"ok": True, "record": saved})

    @routes.post(f"{PREFIX}/library/update")
    async def library_update(request):
        """Change a saved record's own text, name, description or groups."""
        body = await request.json()
        try:
            saved = library.edit(
                body.get("file") or library.DEFAULT_FILE,
                body.get("id") or "",
                body.get("changes") or {},
            )
        except OSError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=500)
        if saved is None:
            return web.json_response(
                {"ok": False, "error": "that record is not in this set any more"}, status=404
            )
        return web.json_response({"ok": True, "record": saved})

    @routes.post(f"{PREFIX}/library/check")
    async def library_check(request):
        """The self-check over text being edited, read against its own record.

        The record supplies the task, the duration and the references it was
        written for, so an edit is judged by the same rules the run was. An id
        that no longer resolves still gets the rules that need no context.
        """
        body = await request.json()
        record = library.find(body.get("file") or library.DEFAULT_FILE, body.get("id") or "")
        about = (record or {}).get("about") or {}
        having = None
        if record is not None:
            having = [
                reference.get("kind")
                for reference in record.get("references") or ()
                if isinstance(reference, dict)
            ]
        return web.json_response(
            {
                "issues": library.inspect(
                    body.get("text") or "",
                    task=(record or {}).get("task") or "",
                    duration=about.get("duration"),
                    having=having,
                )
            }
        )

    @routes.post(f"{PREFIX}/library/delete")
    async def library_delete(request):
        body = await request.json()
        try:
            gone = library.remove(body.get("file") or library.DEFAULT_FILE, body.get("id") or "")
        except OSError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=500)
        return web.json_response({"ok": gone})

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
