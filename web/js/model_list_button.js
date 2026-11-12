import { app } from "../../scripts/app.js";
import { buttonRow, installStyle, setWidgetValue, told, widgetNamed } from "./mmx_controls.js";
import { ask, confirmed, element, footNote, frame, mayClose, warned } from "./mmx_window.js";

const NODE_SECTIONS = {
    MiniMaxH3QwenRewriter: [["model", "qwen_rewriters"]],
    MiniMaxH3PromptRewriter: [["model", "models"]],
    MiniMaxH3PromptWriter8B: [["model", "models_8b"]],
    MiniMaxH3PromptWriterOmni: [["model", "models_omni"]],
    MiniMaxH3GuidedWriter: [["model", "writers"]],
    MiniMaxH3GuidedWriterRef: [["model", "writers"]],
    MiniMaxH3PromptReducer: [["model", "writers"]],
    MiniMaxH3ReferenceCaption: [["model", "captioners"]],
    MiniMaxH3MultiReferenceCaption: [["model", "captioners"]],
    MiniMaxH3UniversalWriter: [
        ["caption_model", "captioners"],
        ["writer_model", "writers"],
    ],
    MiniMaxH3UniversalRewriter: [
        ["model_27b", "models"],
        ["model_8b", "models_8b"],
        ["model_omni", "models_omni"],
    ],
};

const GUIDE_NODES = [
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3GuidePrompt",
    "MiniMaxH3UniversalWriter",
];

const ROOT = "/minimax_h3_rewriter/model_list";
const LIST_FILE = {
    url: "/minimax_h3_rewriter/open_model_list",
    what: "the model list",
    fallback: "ComfyUI/user/minimax_h3_rewriter/models.json",
};

const LIST_LABEL = "Model list";
const LIST_TOOLTIP =
    "Add, edit and delete the models this node offers, and check one before you " +
    "download it: a file already on this machine is read outright, and a Hugging " +
    "Face repository is asked whether the named files are actually in it.\n\n" +
    "Each node edits only the lists its own dropdowns are fed from, because the " +
    "adapters take different architectures and an entry from one list will not " +
    "load in another's node.";

const GUIDE_FOLDER = {
    url: "/minimax_h3_rewriter/open_guide_folder",
    label: "Open guide folder",
    what: "the guide folder",
    fallback: "ComfyUI/user/minimax_h3_rewriter/guides",
    tooltip:
        "Opens the folder holding MiniMax's prompt-writing guides, fetched on first " +
        "use. Editing one changes the system prompt; trimming it is the cheapest way " +
        "to fit a small model's context.",
};

const KEEP_WIDGET = "keep_model_loaded";
const UNLOAD = {
    url: "/minimax_h3_rewriter/unload",
    label: "Unload model",
    tooltip:
        "Unloads the model this pack is keeping in memory, without running the node " +
        "again. It is what to press when 'keep_model_loaded' is on, the text is right " +
        "and the card is wanted for something else: turning that switch off instead " +
        "would write the same text a second time first.\n\n" +
        "Pressed while a node is still writing, it waits for that run and unloads " +
        "after it. ComfyUI's own 'Unload Models' does the same thing to this pack's " +
        "model. Nothing happens for a GGUF run by the llama.cpp binaries, which " +
        "never keep a model.",
};

const STYLE_ID = "minimax-h3-modellist-style";
const STYLE = `
.mmxlib-back .mmxlib-panel.mmxmod-panel { width: min(920px, 88vw); }

.mmxmod-tabs { display: flex; gap: 4px; margin: 12px 0 0; flex-wrap: wrap; }
.mmxmod-tab { font-size: 12px; padding: 5px 12px; border-radius: 6px 6px 0 0;
    cursor: pointer; user-select: none; color: var(--input-text, #ddd);
    background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); border-bottom: 0; }
.mmxmod-tab.mmxmod-on { background: #3B7DD8; border-color: #3B7DD8; color: #fff;
    font-weight: 600; }
.mmxmod-about { border-top: 2px solid #3B7DD8; padding: 10px 0 0; }
.mmxmod-blurb { font-size: 11px; color: var(--input-text, #ddd); opacity: 0.85; }
.mmxmod-needs { margin: 6px 0 0; padding: 0 0 0 16px; font-size: 11px; line-height: 1.55;
    color: var(--descrip-text, #999); }

.mmxmod-card { display: flex; gap: 10px; align-items: flex-start; padding: 9px 2px;
    border-bottom: 1px solid var(--border-color, #4e4e4e); }
.mmxmod-card.mmxmod-scanned { opacity: 0.72; }
.mmxmod-body { flex: 1 1 auto; min-width: 0; }
.mmxmod-name { font-size: 13px; font-weight: 600; word-break: break-word; }
.mmxmod-where { font-size: 11px; color: var(--descrip-text, #999); margin-top: 3px;
    word-break: break-all; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.mmxmod-tag { display: inline-block; font-size: 9px; letter-spacing: 0.05em;
    text-transform: uppercase; padding: 1px 6px; border-radius: 999px; margin-right: 6px;
    vertical-align: 1px; color: var(--descrip-text, #999);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxmod-acts { display: flex; flex-direction: column; gap: 6px; flex: 0 0 auto; }
.mmxmod-acts button { padding: 4px 12px; font-size: 12px; }
.mmxmod-acts button.mmxmod-drop { color: #E08A8A; }
.mmxmod-heading { font-size: 11px; color: var(--descrip-text, #999); margin: 14px 0 2px;
    padding-top: 10px; border-top: 1px solid var(--border-color, #4e4e4e); }

.mmxmod-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 10px; }
.mmxmod-shows { margin-top: 14px; padding: 8px 11px; border-radius: 4px; font-size: 12px;
    background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); word-break: break-word; }
.mmxmod-shows span { font-size: 10px; color: var(--descrip-text, #999); display: block;
    margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.04em; }
.mmxmod-found { margin-top: 10px; font-size: 11px; line-height: 1.55; }
.mmxmod-found div { text-indent: -1.05em; padding-left: 1.05em; }
.mmxmod-bad { color: #E08A8A; }
.mmxmod-warn { color: #E0A45A; }
.mmxmod-good { color: #7FB77F; }
.mmxmod-busy, .mmxmod-note { color: var(--descrip-text, #999); }
.mmxmod-tag.mmxmod-here { color: #7FB77F; border-color: #7FB77F; }

.mmxmod-picks-head { font-size: 10px; color: var(--descrip-text, #999); margin: 10px 0 4px;
    text-transform: uppercase; letter-spacing: 0.04em; }
.mmxmod-chips { display: flex; flex-wrap: wrap; gap: 4px; max-height: 150px; overflow: auto; }
.mmxmod-chip { font: inherit; font-size: 11px; padding: 3px 9px; border-radius: 999px;
    cursor: pointer; text-align: left; word-break: break-all;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxmod-chip:hover { border-color: #3B7DD8; }
.mmxmod-chip span { margin-left: 6px; opacity: 0.65; white-space: nowrap; }
.mmxmod-chip.mmxmod-on { background: #3B7DD8; border-color: #3B7DD8; color: #fff; }
`;

function eachGraphNode(graph, visit) {
    for (const item of graph?._nodes || []) {
        if (item.isSubgraphNode?.() && item.subgraph) eachGraphNode(item.subgraph, visit);
        visit(item);
    }
}

function applyChoices(choices) {
    if (!choices) return;
    eachGraphNode(app.graph, (item) => {
        let touched = false;
        for (const [widget, section] of NODE_SECTIONS[item.type] || []) {
            const values = choices[section];
            const combo = widgetNamed(item, widget);
            if (!values || !combo) continue;
            combo.options = combo.options || {};
            combo.options.values = values;
            touched = true;
        }
        if (touched) item.setDirtyCanvas?.(true, true);
    });
}

function repoint(section, before, after) {
    if (!before || before === after) return 0;
    let count = 0;
    eachGraphNode(app.graph, (item) => {
        for (const [widget, owns] of NODE_SECTIONS[item.type] || []) {
            if (owns !== section) continue;
            if (widgetNamed(item, widget)?.value !== before) continue;
            if (after) setWidgetValue(item, widget, after);
            count += 1;
        }
    });
    return count;
}

function nodeCount(count) {
    return count === 1 ? "1 node" : `${count} nodes`;
}

async function refreshDefinitions() {
    try {
        await app.refreshComboInNodes();
    } catch (error) {
        console.warn("[MiniMax-H3 Prompt Rewriter] could not reload node definitions", error);
    }
}

async function heal(graph = app.graph) {
    const stale = [];
    eachGraphNode(graph, (item) => {
        for (const [widget, section] of NODE_SECTIONS[item.type] || []) {
            const combo = widgetNamed(item, widget);
            const values = combo?.options?.values;
            if (!combo || typeof combo.value !== "string" || !combo.value) continue;
            if (!Array.isArray(values) || values.includes(combo.value)) continue;
            stale.push({ item, widget, section, value: combo.value });
        }
    });
    if (!stale.length) return 0;

    const labels = {};
    for (const one of stale) {
        labels[one.section] = labels[one.section] || [];
        if (!labels[one.section].includes(one.value)) labels[one.section].push(one.value);
    }
    let payload = {};
    try {
        ({ payload } = await ask(`${ROOT}/current`, { labels }));
    } catch (error) {
        console.debug("[MiniMax-H3 Prompt Rewriter] could not ask for the current labels", error);
        return 0;
    }

    let moved = 0;
    for (const one of stale) {
        const now = payload?.labels?.[one.section]?.[one.value];
        if (!now || widgetNamed(one.item, one.widget)?.value !== one.value) continue;
        setWidgetValue(one.item, one.widget, now);
        moved += 1;
    }
    if (moved) {
        console.log(
            `[MiniMax-H3 Prompt Rewriter] moved ${moved} model widget(s) to the label ` +
                "their list shows now"
        );
    }
    return moved;
}

const MIDDLE_DOT = "\u00b7";
const EM_DASH = "\u2014";

function previewLabel(values) {
    const parts = [values.name];
    const size = Number(values.download_gb);
    if (size) parts.push(`${size} GB download`);
    if (values.vram) parts.push(values.vram);
    let text = parts.filter(Boolean).join(" " + MIDDLE_DOT + " ");
    if (values.note) text += " " + EM_DASH + " " + values.note;
    return text;
}

function renderLines(holder, lines) {
    holder.replaceChildren();
    for (const line of lines || []) {
        const said = element("div", `mmxmod-${line.level}`, `- ${line.text}`);
        holder.appendChild(said);
    }
}

async function reveal(target) {
    const { ok, status, payload } = await ask(target.url, {});
    if (!ok || !payload.ok) {
        await warned(
            (payload.error || `HTTP ${status}`) +
                "\n\nOpen it by hand instead:\n" +
                (payload.path || target.fallback),
            { title: `Could not open ${target.what}` }
        );
        return false;
    }
    console.log(`[MiniMax-H3 Prompt Rewriter] opened ${payload.path}`);
    return true;
}

/** A captioned text box, wrapped so a grid lays out whole fields and not halves. */
function textField(holder, caption, value, hint) {
    const wrap = element("div", "mmxmod-field");
    wrap.appendChild(element("label", "mmxlib-field", caption));
    const box = document.createElement("input");
    box.type = "text";
    box.spellcheck = false;
    box.autocomplete = "off";
    box.value = value === undefined || value === null ? "" : String(value);
    if (hint) box.placeholder = hint;
    wrap.appendChild(box);
    holder.appendChild(wrap);
    return box;
}

function openEntryForm(section, held, onSaved) {
    const { panel, close, handle } = frame(false, { sticky: true });
    const editing = Boolean(held?.name);

    panel.appendChild(
        element("h3", "mmxlib-title", editing ? "Edit an entry" : `Add a model to ${section.title}`)
    );
    panel.appendChild(element("p", "mmxlib-sub", section.blurb));

    const name = textField(panel, "Name -- what the dropdown shows", held?.name, "Qwen3.5 4B Q4_K_M");

    let format = held?.format || "";
    if (section.formats.length > 1) {
        panel.appendChild(element("label", "mmxlib-field", "Format"));
        const picked = document.createElement("select");
        for (const one of section.formats) picked.appendChild(new Option(one, one));
        picked.value = format || section.default_format;
        format = picked.value;
        panel.appendChild(picked);
        picked.addEventListener("change", () => {
            format = picked.value;
            redraw();
        });
    } else {
        format = section.formats[0];
    }

    const repo = textField(
        panel,
        "Repository id, or a folder on this machine",
        held?.repo,
        "unsloth/Qwen3.5-4B-GGUF"
    );
    const fileRow = element("div", "");
    const file = textField(
        fileRow,
        "File inside the repository -- or leave it empty and Check it, to pick one",
        held?.file,
        "Qwen3.5-4B-Q4_K_M.gguf"
    );
    panel.appendChild(fileRow);

    const projRow = element("div", "");
    const mmproj = textField(projRow, "mmproj -- the projector beside it", held?.mmproj, "mmproj-F16.gguf");
    panel.appendChild(projRow);

    const grid = element("div", "mmxmod-grid");
    const size = textField(grid, "Download, GB", held?.download_gb, "2.6");
    const vram = textField(grid, "VRAM note", held?.vram, "~8 GB in context");
    const note = textField(grid, "Note", held?.note, "smallest that holds the format");
    panel.appendChild(grid);

    const shows = element("div", "mmxmod-shows");
    shows.appendChild(element("span", "", "Shows in the dropdown as"));
    const shown = element("div", "", "");
    shows.appendChild(shown);
    panel.appendChild(shows);

    const moved = element("div", "mmxmod-found");
    panel.appendChild(moved);

    const said = element("div", "mmxmod-found");
    panel.appendChild(said);

    const picks = element("div", "mmxmod-picks");
    panel.appendChild(picks);

    const row = element("div", "mmxlib-row");
    const trouble = element("div", "mmxlib-problem");
    const probe = element("button", "", "Check it");
    probe.title =
        "Reads the files if they are on this machine, or asks Hugging Face whether the " +
        "repository has them and how big they are. With a repository and no file yet, " +
        "lists the GGUF files in it to pick from.";
    const cancel = element("button", "", "Cancel");
    const keep = element("button", "mmxlib-go", "Save");
    row.append(trouble, probe, cancel, keep);
    panel.appendChild(row);

    function values() {
        const gguf = format === "gguf";
        return {
            name: name.value.trim(),
            format,
            repo: repo.value.trim(),
            file: gguf ? file.value.trim() : "",
            mmproj: gguf && section.mmproj !== "unused" ? mmproj.value.trim() : "",
            download_gb: size.value.trim(),
            vram: vram.value.trim(),
            note: note.value.trim(),
        };
    }

    function redraw() {
        const gguf = format === "gguf";
        fileRow.hidden = !gguf;
        projRow.hidden = !gguf || section.mmproj === "unused";
        picks.hidden = !gguf;
        shown.textContent = previewLabel(values()) || "(nothing yet)";

        moved.replaceChildren();
        if (editing && previewLabel(values()) !== held.label) {
            const renamed = values().name !== held.name;
            moved.appendChild(
                element(
                    "div",
                    renamed ? "mmxmod-warn" : "mmxmod-note",
                    renamed
                        ? `- The dropdown reads '${held.label}' today. Saving this renames the ` +
                              "entry, and workflows holding the old name lose their choice -- " +
                              "the graph open here is moved across for you, others are not."
                        : `- The dropdown reads '${held.label}' today. Workflows holding that ` +
                              "still find the entry by its name, and move to the new label " +
                              "when they are opened."
                )
            );
        }
    }

    for (const box of [name, repo, file, mmproj, size, vram, note]) {
        box.addEventListener("input", redraw);
    }
    redraw();

    const start = JSON.stringify(values());
    async function leave() {
        if (keep.disabled) return;
        if (await mayClose(JSON.stringify(values()) !== start)) close();
    }
    handle.onEscape = leave;

    let named = "";
    let sized = "";
    let listed = "";

    function ours(box, filled) {
        return !box.value.trim() || box.value === filled;
    }

    function chipGroup(caption, offers, box, take) {
        if (!offers?.length) return;
        picks.appendChild(element("div", "mmxmod-picks-head", caption));
        const chips = element("div", "mmxmod-chips");
        for (const one of offers) {
            const chip = element("button", "mmxmod-chip", one.file);
            chip.type = "button";
            chip.dataset.file = one.file;
            chip.appendChild(element("span", "", `${one.gb} GB`));
            chip.title = box === file ? `Use this model -- '${one.name}'` : "Use this projector";
            chip.addEventListener("click", () => take(one));
            chips.appendChild(chip);
        }
        chips.mmxField = box;
        picks.appendChild(chips);
    }

    function markPicked() {
        for (const chips of picks.querySelectorAll(".mmxmod-chips")) {
            for (const chip of chips.children) {
                chip.classList.toggle("mmxmod-on", chip.dataset.file === chips.mmxField.value.trim());
            }
        }
    }

    function offer(payload) {
        if (!payload.files && !payload.projectors) return;
        listed = repo.value.trim();
        picks.replaceChildren();
        chipGroup("Models in it", payload.files, file, (one) => {
            file.value = one.file;
            if (ours(name, named)) name.value = named = one.name;
            const sole = payload.projectors?.length === 1 ? payload.projectors[0].file : "";
            if (sole && section.mmproj !== "unused" && !mmproj.value.trim()) mmproj.value = sole;
            picked();
        });
        if (section.mmproj !== "unused") {
            chipGroup("Projectors -- for mmproj", payload.projectors, mmproj, (one) => {
                mmproj.value = one.file;
                picked();
            });
        }
        markPicked();
    }

    function picked() {
        if (ours(size, sized)) size.value = sized = "";
        redraw();
        markPicked();
        const needs = section.mmproj === "required";
        if (!file.value.trim()) {
            renderLines(said, [{ level: "note", text: "Now pick the model." }]);
        } else if (needs && !mmproj.value.trim()) {
            renderLines(said, [{ level: "note", text: "Now pick its projector." }]);
        } else {
            runCheck();
        }
    }

    async function runCheck() {
        probe.disabled = true;
        renderLines(said, [{ level: "busy", text: "Looking..." }]);
        const { payload } = await ask("/minimax_h3_rewriter/model_list/check", {
            section: section.key,
            entry: values(),
        });
        probe.disabled = false;
        if (!payload.ok) {
            renderLines(said, [{ level: "bad", text: payload.error || "The check failed." }]);
            return;
        }
        renderLines(said, payload.lines);
        offer(payload);
        if (!payload.download_gb) return;
        if (ours(size, sized)) {
            size.value = sized = String(payload.download_gb);
            redraw();
        } else if (Math.abs(Number(size.value.trim()) - payload.download_gb) > 0.01) {
            said.appendChild(
                element(
                    "div",
                    "mmxmod-warn",
                    `- The files come to ${payload.download_gb} GB and the form says ` +
                        `${size.value.trim()}. Saving keeps what the form says.`
                )
            );
        }
    }

    probe.addEventListener("click", runCheck);
    repo.addEventListener("input", () => {
        if (listed && repo.value.trim() !== listed) {
            listed = "";
            picks.replaceChildren();
        }
    });
    for (const box of [file, mmproj]) box.addEventListener("input", markPicked);

    cancel.addEventListener("click", leave);
    keep.addEventListener("click", async () => {
        keep.disabled = true;
        trouble.textContent = "";
        const { payload } = await ask("/minimax_h3_rewriter/model_list/save", {
            section: section.key,
            name: held?.name || "",
            entry: values(),
        });
        if (!payload.ok) {
            trouble.textContent = payload.error || "That could not be saved.";
            keep.disabled = false;
            return;
        }
        applyChoices(payload.choices);
        const carried = repoint(section.key, payload.label_before, payload.label_after);
        await heal();
        close();
        onSaved(
            `${editing ? "Saved" : "Added"} '${payload.label_after}'.` +
                (carried ? ` ${nodeCount(carried)} in this graph moved to the new label.` : "")
        );
        refreshDefinitions();
    });

    name.focus();
}

/** One entry of a list. `done(text)` is told what an edit or a delete did. */
function entryCard(section, held, done, writable) {
    const card = element("div", "mmxmod-card");
    const body = element("div", "mmxmod-body");

    const heading = element("div", "mmxmod-name");
    if (held.seeded) heading.appendChild(element("span", "mmxmod-tag", "from the pack"));
    if (held.on_disk) {
        const here = element("span", "mmxmod-tag mmxmod-here", "on disk");
        here.title =
            "Already downloaded, so choosing it fetches nothing." +
            (held.scanned
                ? " Its file is not listed a second time under 'Found in your model folders'."
                : "");
        heading.appendChild(here);
    }
    heading.appendChild(document.createTextNode(held.label || held.name));
    body.appendChild(heading);

    const where = [held.format, held.repo, held.file, held.mmproj].filter(Boolean).join("  ");
    body.appendChild(element("div", "mmxmod-where", where));
    card.appendChild(body);

    const acts = element("div", "mmxmod-acts");
    const change = element("button", "", "Edit");
    change.addEventListener("click", () => openEntryForm(section, held, done));
    const drop = element("button", "mmxmod-drop", "Delete");
    drop.addEventListener("click", async () => {
        const coming = held.seeded
            ? "It is one of the pack's own, so 'Restore the packaged entries' can bring it back."
            : "It is yours, so nothing will bring it back -- you would type it again.";
        const after = held.scanned
            ? "Its file stays on disk and is offered as an 'on disk:' row, and nodes in this " +
              "graph that use the entry move to that row."
            : "Nodes that use it keep it until another model is picked, and a run before " +
              "then stops and says so rather than picking one for you.";
        const sure = await confirmed(`${coming}\n\n${after}`, {
            title: `Delete '${held.label}' from ${section.title}?`,
            ok: "Delete",
            danger: true,
        });
        if (!sure) return;
        drop.disabled = true;
        const { payload } = await ask("/minimax_h3_rewriter/model_list/delete", {
            section: section.key,
            name: held.name,
        });
        if (!payload.ok) {
            drop.disabled = false;
            await warned(payload.error || "That entry could not be deleted.");
            return;
        }
        applyChoices(payload.choices);
        const carried = repoint(section.key, payload.label_before, payload.label_after);
        const fate = !carried
            ? ""
            : payload.label_after
              ? ` ${nodeCount(carried)} in this graph moved to '${payload.label_after}'.`
              : ` ${nodeCount(carried)} in this graph ${carried === 1 ? "names" : "name"} ` +
                "it still, and a run there stops until another model is picked.";
        done(`Deleted '${held.label || held.name}'.${fate}`);
        refreshDefinitions();
    });
    if (!writable) {
        const why = "The file cannot be written right now -- see the message in the footer.";
        for (const button of [change, drop]) {
            button.disabled = true;
            button.title = why;
        }
    }
    acts.append(change, drop);
    card.appendChild(acts);
    return card;
}

function scannedCard(text) {
    const card = element("div", "mmxmod-card mmxmod-scanned");
    const body = element("div", "mmxmod-body");
    body.appendChild(element("div", "mmxmod-name", text));
    card.appendChild(body);
    return card;
}

function openModelList(node) {
    installStyle(STYLE_ID, STYLE);
    const { panel, close } = frame(true, { size: "models" });
    panel.classList.add("mmxmod-panel");

    panel.appendChild(element("h3", "mmxlib-title", "Model list"));
    panel.appendChild(
        element(
            "p",
            "mmxlib-sub",
            "The models this node offers. Entries are kept in models.json in the ComfyUI " +
                "user directory, so they outlive an update of the pack and are shared by " +
                "every workflow."
        )
    );

    const tabs = element("div", "mmxmod-tabs");
    panel.appendChild(tabs);
    const about = element("div", "mmxmod-about");
    panel.appendChild(about);
    const cards = element("div", "mmxlib-list");
    panel.appendChild(cards);

    let payload = null;
    let at = 0;

    const row = element("div", "mmxlib-row");
    const said = footNote(() => {
        const section = current();
        if (!section) return "";
        const entries = section.entries.length;
        const found = section.found.length;
        return (
            (entries === 1 ? "1 entry" : `${entries} entries`) +
            (found ? ` ${MIDDLE_DOT} ${found} more found on disk` : "")
        );
    });
    const adding = element("button", "mmxlib-go", "Add a model");
    const restoring = element("button", "", "Restore the packaged entries");
    const opening = element("button", "", "Open models.json");
    opening.title =
        "Opens the file itself, for anything this window does not cover -- the adapter " +
        "sections, or a path to a network share, which is refused here because this " +
        "window is reachable over the ComfyUI API and a hand-edited file is not.";
    const reloading = element("button", "", "Reload definitions");
    reloading.title =
        "Ask ComfyUI to re-read every node definition. The dropdowns are already kept in " +
        "step as you edit, so this is only needed if one looks stale -- after a model was " +
        "copied into a folder by hand, or the list was edited from another browser tab.";
    const shutting = element("button", "", "Close");
    row.append(said.holder, adding, restoring, opening, reloading, shutting);
    panel.appendChild(row);

    function current() {
        return payload?.sections?.[at] || null;
    }

    function after(text) {
        said.say(text);
        load();
    }

    function draw() {
        const section = current();
        tabs.replaceChildren();
        if ((payload?.sections?.length || 0) > 1) {
            payload.sections.forEach((one, index) => {
                const tab = element("div", "mmxmod-tab" + (index === at ? " mmxmod-on" : ""), one.title);
                tab.addEventListener("click", () => {
                    at = index;
                    said.say("");
                    draw();
                });
                tabs.appendChild(tab);
            });
        }

        about.replaceChildren();
        cards.replaceChildren();
        if (!section) return;

        about.appendChild(element("div", "mmxmod-blurb", section.blurb));
        const needs = element("ul", "mmxmod-needs");
        for (const line of section.requirements) needs.appendChild(element("li", "", line));
        about.appendChild(needs);

        for (const held of section.entries) {
            cards.appendChild(entryCard(section, held, after, payload.writable));
        }
        if (!section.entries.length) {
            cards.appendChild(
                element("div", "mmxlib-none", "Nothing in this list. 'Add a model' puts one here.")
            );
        }
        if (section.found.length) {
            cards.appendChild(
                element(
                    "div",
                    "mmxmod-heading",
                    "Found in your model folders. These are offered too, and there is nothing " +
                        "to edit: they are files on disk, not entries in the file. A file an " +
                        "entry above already downloaded is not repeated here."
                )
            );
            for (const text of section.found) cards.appendChild(scannedCard(text));
        }

        const coming = section.restorable.length;
        restoring.disabled = !coming || !payload.writable;
        restoring.title = coming
            ? `Offer these again: ${section.restorable.join(", ")}`
            : "Every entry the pack ships with is already in this list.";
        adding.disabled = !payload.writable;
        said.paint();
    }

    async function load() {
        const answer = await ask(`${ROOT}?node=${encodeURIComponent(node.type)}`);
        if (!answer.payload?.ok) {
            said.stand(answer.payload?.error || "The model list could not be read.");
            return;
        }
        payload = answer.payload;
        said.stand(payload.problem || "");
        if (at >= payload.sections.length) at = 0;
        draw();
    }

    adding.addEventListener("click", () => {
        const section = current();
        if (section) openEntryForm(section, null, after);
    });
    restoring.addEventListener("click", async () => {
        const section = current();
        if (!section) return;
        restoring.disabled = true;
        const answer = await ask("/minimax_h3_rewriter/model_list/restore", {
            section: section.key,
        });
        if (!answer.payload.ok) {
            said.say(answer.payload.error || "Those could not be restored.", true);
            restoring.disabled = false;
            return;
        }
        applyChoices(answer.payload.choices);
        await heal();
        const back = answer.payload.restored || [];
        after(back.length ? `Put back: ${back.join(", ")}.` : "Nothing was missing.");
        refreshDefinitions();
    });
    opening.addEventListener("click", async () => {
        if (await reveal(LIST_FILE)) told(opening, "Opened");
    });
    reloading.addEventListener("click", async () => {
        reloading.disabled = true;
        try {
            await app.refreshComboInNodes();
            told(reloading, "Reloaded");
        } catch (error) {
            said.say(`Could not reload the node definitions: ${error}`, true);
        }
        reloading.disabled = false;
    });
    shutting.addEventListener("click", close);

    load();
}

async function unloadModel(button) {
    button.disabled = true;
    const { status, payload } = await ask(UNLOAD.url, {});
    button.disabled = false;
    if (!payload.ok) {
        await warned(payload.error || `HTTP ${status}`, {
            title: "Could not unload the model",
        });
        return;
    }
    const gone = payload.released || [];
    if (gone.length) console.log(`[MiniMax-H3 Prompt Rewriter] unloaded ${gone.join(", ")}`);
    if (payload.waiting) told(button, "After this run");
    else told(button, gone.length ? "Unloaded" : "Nothing loaded");
}

function addButtons(nodeType, nodeData) {
    const owns = Boolean(NODE_SECTIONS[nodeData.name]);
    const guided = GUIDE_NODES.includes(nodeData.name);
    const keeps = Boolean(
        nodeData.input?.required?.[KEEP_WIDGET] ?? nodeData.input?.optional?.[KEEP_WIDGET]
    );
    if (!owns && !guided && !keeps) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        const node = this;
        const wanted = [];
        if (owns) {
            wanted.push({
                label: LIST_LABEL,
                tooltip: LIST_TOOLTIP,
                onClick: () => openModelList(node),
            });
        }
        if (guided) {
            wanted.push({
                label: GUIDE_FOLDER.label,
                tooltip: GUIDE_FOLDER.tooltip,
                onClick: async (button) => {
                    if (await reveal(GUIDE_FOLDER)) told(button, "Opened");
                },
            });
        }
        if (keeps) {
            wanted.push({
                label: UNLOAD.label,
                tooltip: UNLOAD.tooltip,
                onClick: unloadModel,
            });
        }
        buttonRow(this, "mmx_open", wanted);
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.model_list",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        addButtons(nodeType, nodeData);
    },
    afterConfigureGraph() {
        heal();
    },
});
