"""HTTP routes backing the node's buttons.

Registered on import; a failure here must never stop the nodes from loading, so
everything is guarded and logged rather than raised.
"""

from __future__ import annotations

import asyncio
import logging

from . import catalog, guides, library, memory, model_sections, presets

log = logging.getLogger(__name__)

PREFIX = "/minimax_h3_rewriter"


def _model_list(node: str) -> dict:
    """Everything the model-list window needs to draw itself for one node."""
    wanted = model_sections.sections_of(node) or tuple(model_sections.SECTIONS)
    writable, refusal = True, ""
    try:
        catalog.writable()
    except catalog.CatalogWriteError as error:
        writable, refusal = False, str(error)
    return {
        "ok": True,
        "path": catalog.user_file(),
        "writable": writable,
        "problem": refusal,
        "widgets": [
            {"widget": one.widget, "section": one.section}
            for one in model_sections.for_node(node)
        ],
        "sections": [model_sections.listing(one) for one in wanted],
    }


def _current(section: str, name: str) -> dict | None:
    return next(
        (one for one in catalog.raw_entries(section) if str(one.get("name") or "") == name),
        None,
    )


def _save_entry(section: str, name: str, raw: dict) -> dict:
    """Add or replace one entry, and say what the dropdown used to call it.

    The label is what a saved workflow remembers, and it is built from four of
    these fields rather than just the name -- so an edit that only touches the
    VRAM note still moves it. Reporting both spellings is what lets the window
    put the graph in front of the person back in step instead of leaving a
    dangling choice for them to find later.
    """
    entry = model_sections.clean_entry(section, raw)
    before = ""
    if name:
        held = _current(section, name)
        if held is None:
            raise catalog.CatalogWriteError(
                f"'{name}' is not in this list any more -- something else changed the file. "
                f"Reopen this window to see what is there now."
            )
        before = catalog.entry_label(held)
        catalog.update(section, name, entry)
    else:
        catalog.add(section, entry)

    after = catalog.entry_label(entry)
    return {
        "ok": True,
        "entry": dict(entry, label=after),
        "label_before": before,
        "label_after": after,
        "choices": {section: model_sections.choices(section)},
    }


def _check_entry(section: str, raw: dict) -> dict:
    """Probe one entry. Cleaned first, so a network path is refused before it is read."""
    entry = model_sections.clean_entry(section, raw)
    found = model_sections.check(section, entry)
    return {"ok": True, "label": catalog.entry_label(entry), **found}


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

    @routes.post(f"{PREFIX}/memory/rewrite")
    async def memory_rewrite(request):
        """Replace the answer a node is holding with an edited one."""
        body = await request.json()
        record = memory.rewrite(body.get("node") or "", body.get("text") or "")
        if record is None:
            return web.json_response(
                {
                    "ok": False,
                    "error": (
                        "this node is not holding an answer to edit - run it once, and what "
                        "it writes is what can be edited"
                    ),
                },
                status=404,
            )
        return web.json_response({"ok": True, "record": memory.summary(record)})

    @routes.post(f"{PREFIX}/memory/check")
    async def memory_check(request):
        """The self-check over text being edited, read against the run that made it.

        The record supplies the task, the duration and the references the answer
        was written for, so an edit is judged by the rules the run was. The twin
        of ``library/check``, for the half of the memory that lives in RAM.
        """
        body = await request.json()
        record = memory.recall(body.get("node") or "")
        return web.json_response(
            {
                "issues": library.inspect(
                    body.get("text") or "",
                    task=record.task if record else "",
                    duration=(record.about if record else {}).get("duration"),
                    having=(
                        [item.get("kind") for item in record.references]
                        if record is not None
                        else None
                    ),
                )
            }
        )

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

    @routes.get(f"{PREFIX}/presets")
    async def preset_catalogue(request):
        """The bundled prompts, their tags and who they are owed to.

        Empty when the pack was installed without the files: the picker says so
        rather than reporting a failure the person cannot act on.

        Sent as bytes serialised once, not built per request: it is a megabyte
        and a third, and it is the same every time.
        """
        return web.Response(
            body=presets.payload(),
            content_type="application/json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @routes.get(PREFIX + "/presets/record/{preset}")
    async def preset_record(request):
        """One preset: its label, its text and where its clip can be watched.

        What a node draws itself from after a page reload. The catalogue would
        answer the same question and cost a megabyte to do it, and a graph with
        five of these nodes in it would pull it five times.
        """
        found = presets.one(request.match_info["preset"])
        if found is None:
            return web.json_response({"ok": False}, status=404)
        return web.json_response({"ok": True, "preset": found})

    @routes.get(PREFIX + "/presets/thumb/{preset}")
    async def preset_thumb(request):
        """The frame of one preset, as the WebP that is on disk.

        A route rather than data URLs in the catalogue: this way the browser
        asks for the two dozen frames a screenful needs, caches them by URL, and
        never carries six megabytes it is not showing.
        """
        frame = presets.thumb(request.match_info["preset"])
        if frame is None:
            return web.Response(status=404)
        return web.Response(
            body=frame,
            content_type="image/webp",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @routes.post(f"{PREFIX}/presets/check")
    async def preset_check(request):
        """The self-check over a preset being edited on its way to the library.

        The twin of ``library/check``, for text that has no record yet. The task
        and the duration come from the preset rather than from the caller, since
        those are facts about the clip it was written for.
        """
        body = await request.json()
        found = presets.find(body.get("preset") or "")
        seconds = (found or {}).get("seconds")
        return web.json_response(
            {
                "issues": library.inspect(
                    body.get("text") or "",
                    task=presets.TASK,
                    duration=round(float(seconds)) if seconds else None,
                    having=[],
                )
            }
        )

    @routes.post(f"{PREFIX}/library/save_preset")
    async def library_save_preset(request):
        """Put a copy of a bundled preset into one of the person's own sets.

        The one place a record enters the library from somewhere other than a
        node's session memory. It arrives with the credit in its description and
        the collection in its ``source``, so a prompt that travels on from here
        still says whose it was.
        """
        body = await request.json()
        found = presets.find(body.get("preset") or "")
        if found is None:
            return web.json_response(
                {"ok": False, "error": "that preset is not in this pack"}, status=404
            )
        record = presets.as_record(
            found,
            name=body.get("name") or "",
            description=body.get("description") or "",
            tags=body.get("groups") if isinstance(body.get("groups"), list) else None,
            prompt=body.get("text") or "",
        )
        name = body.get("file") or library.DEFAULT_FILE
        try:
            saved = library.add(name, record)
        except OSError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=500)
        return web.json_response(
            {"ok": True, "file": library.clean(name), "record": saved}
        )

    async def off_loop(work, *args):
        """Run blocking work on a thread, so the event loop keeps serving.

        Every other route here answers from memory. These read GGUF headers, walk
        the model folders and talk to the Hub: a cold header read costs seconds
        per file, and the Hub calls carry 30- and 60-second timeouts. Holding the
        loop for that stops the whole interface, the progress socket and the
        queue along with it.
        """
        return await asyncio.to_thread(work, *args)

    def refused(error, status=400):
        return web.json_response({"ok": False, "error": str(error)}, status=status)

    async def answered(work, *args):
        """Run one model-list operation, telling a refusal apart from a failure.

        A refusal is the ordinary case here -- a duplicate name, a network path,
        a file that does not parse -- and the window prints it beside the field.
        Anything else is a bug and is logged as one.
        """
        try:
            return web.json_response(await off_loop(work, *args))
        except (RuntimeError, KeyError) as error:
            return refused(error)
        except Exception as error:  # noqa: BLE001 - the window has to say something
            log.error("[minimax_h3_rewriter.routes] model list: %s", error, exc_info=True)
            return refused(error, status=500)

    @routes.get(f"{PREFIX}/model_list")
    async def model_list(request):
        """The lists one node reads, what may go in them, and what is in them now."""
        return await answered(_model_list, request.query.get("node") or "")

    @routes.post(f"{PREFIX}/model_list/save")
    async def model_list_save(request):
        """Add an entry, or replace the one named by ``name``."""
        body = await request.json()
        return await answered(
            _save_entry,
            body.get("section") or "",
            str(body.get("name") or ""),
            body.get("entry") or {},
        )

    @routes.post(f"{PREFIX}/model_list/delete")
    async def model_list_delete(request):
        body = await request.json()
        section = body.get("section") or ""

        def drop():
            gone = catalog.remove(section, str(body.get("name") or ""))
            return {"ok": gone, "choices": {section: model_sections.choices(section)}}

        return await answered(drop)

    @routes.post(f"{PREFIX}/model_list/restore")
    async def model_list_restore(request):
        """Offer this list's packaged entries again."""
        body = await request.json()
        section = body.get("section") or ""

        def bring_back():
            restored = catalog.restore_packaged(section)
            return {
                "ok": True,
                "restored": restored,
                "choices": {section: model_sections.choices(section)},
            }

        return await answered(bring_back)

    @routes.post(f"{PREFIX}/model_list/check")
    async def model_list_check(request):
        """What this entry actually is, as far as that can be known without weights."""
        body = await request.json()
        return await answered(_check_entry, body.get("section") or "", body.get("entry") or {})


try:
    register()
    log.info("[minimax_h3_rewriter] routes registered")
except Exception as error:  # noqa: BLE001 - the nodes must still load
    log.warning("[minimax_h3_rewriter] routes not registered: %s", error)
