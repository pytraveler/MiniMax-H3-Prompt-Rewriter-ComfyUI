import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    buttonRow,
    installStyle,
    setWidgetValue,
    showWidget,
    told,
    widgetNamed,
} from "./mmx_controls.js";
import { recordFor } from "./repeat_last.js";
import { toast } from "./self_check.js";

const NODES = [
    "MiniMaxH3PromptRewriter",
    "MiniMaxH3PromptWriter8B",
    "MiniMaxH3PromptWriterOmni",
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3UniversalWriter",
    "MiniMaxH3UniversalRewriter",
];
const OPTIONS_NODE = "MiniMaxH3RewriterOptions";
const CHECK_NODE = "MiniMaxH3PromptCheck";

const PICK = "library_pick";
const REPEAT = "repeat_last";
const FILE_WIDGET = "prompt_file";
const DEFAULT_FILE = "global";

const SAVE_LABEL = "Save the last prompt";
const EDIT_LABEL = "Edit the last prompt";
const BROWSE_LABEL = "Prompt library";
const NEW_FILE_LABEL = "New prompt file";

const SAVE_TOOLTIP =
    "Keep the answer this node last produced in the prompt library, under a name, a " +
    "description and any number of groups. The library is a JSON file in the ComfyUI " +
    "user directory, so a prompt saved here is available to every workflow and survives " +
    "a restart -- unlike 'repeat_last', which only holds one answer per node for this " +
    "session.\n\nRun the node once first: what gets saved is what it wrote.";
const EDIT_TOOLTIP =
    "Open the answer this node last produced and change it, with the self-check reading "  +
    "what you type as you type it. The fields are split out of the text again when you "  +
    "save, so the section outputs stay in step with the prose.\n\nIt edits what the node "  +
    "is holding for this session, which is what 'repeat_last' hands on and what 'Save the "  +
    "last prompt' would keep. Nothing reaches disk until you save it to the library.";
const BROWSE_TOOLTIP =
    "Choose which kept prompt 'repeat_last' hands to this node's output. The list starts " +
    "with the node's own last answer, then everything in the library, filtered by group " +
    "and by any text in a name, a description, a prompt or a reference.\n\nChoosing one " +
    "switches 'repeat_last' on, and turning that switch off is all it takes to give the " +
    "node back to the model -- the choice stays, waiting. 'Write a new one' does both: it " +
    "forgets the choice and switches off.\n\nThe choice is saved with the workflow, so a " +
    "graph reopened tomorrow returns the same prompt.";
const NEW_FILE_TOOLTIP =
    "Make another set of saved prompts and switch to it. One file is one working set: the " +
    "nodes connected to this Options node save into it and list it. The file appears in " +
    "'prompt_file' straight away, and after a browser refresh for everyone else.";

const STYLE_ID = "minimax-h3-library-style";
const STYLE = `
.mmxlib-back { position: fixed; inset: 0; z-index: 1300; display: flex;
    align-items: center; justify-content: center; background: rgba(0, 0, 0, 0.55);
    font-family: system-ui, sans-serif; }
.mmxlib-panel { width: min(560px, 92vw); max-height: 88vh; overflow: auto;
    background: var(--comfy-menu-bg, #353535); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #4e4e4e); border-radius: 10px;
    padding: 18px 20px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); }
.mmxlib-panel.mmxlib-wide { width: min(760px, 94vw); display: flex;
    flex-direction: column; overflow: hidden; }
.mmxlib-title { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.mmxlib-sub { font-size: 11px; color: var(--descrip-text, #999); margin: 0 0 14px; }
.mmxlib-field { display: block; font-size: 11px; color: var(--descrip-text, #999);
    margin: 12px 0 4px; }
.mmxlib-panel input[type="text"], .mmxlib-panel textarea, .mmxlib-panel select {
    width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
    padding: 6px 8px; border-radius: 6px; color: var(--input-text, #ddd);
    background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-panel textarea { resize: vertical; min-height: 58px; }
.mmxlib-groups { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.mmxlib-chip { font-size: 11px; padding: 3px 9px; border-radius: 999px;
    cursor: pointer; user-select: none; background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); color: var(--descrip-text, #999); }
.mmxlib-chip.mmxlib-on { background: #3B7DD8; border-color: #3B7DD8; color: #fff; }
.mmxlib-about { margin-top: 16px; padding-top: 12px; font-size: 11px;
    color: var(--descrip-text, #999); border-top: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-refs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.mmxlib-ref { display: flex; flex-direction: column; align-items: center; gap: 3px;
    font-size: 9px; line-height: 11px; text-align: center; width: 62px; }
.mmxlib-ref img { width: 50px; height: 50px; border-radius: 4px;
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-ref .mmxlib-blank { width: 50px; height: 50px; border-radius: 4px;
    display: flex; align-items: center; justify-content: center; font-size: 16px;
    background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-preview { margin-top: 8px; max-height: 96px; overflow: auto;
    white-space: pre-wrap; font-size: 11px; line-height: 15px;
    color: var(--input-text, #ddd); opacity: 0.8; }
.mmxlib-row { display: flex; gap: 8px; justify-content: flex-end;
    align-items: center; margin-top: 18px; }
.mmxlib-row button, .mmxlib-card button { font: inherit; font-size: 13px;
    padding: 6px 16px; border-radius: 6px; cursor: pointer;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-row button.mmxlib-go, .mmxlib-card button.mmxlib-go {
    background: #3B7DD8; border-color: #3B7DD8; color: #fff; }
.mmxlib-row button[disabled] { opacity: 0.5; cursor: not-allowed; }
.mmxlib-problem { flex: 1 1 auto; font-size: 11px; color: #E08A8A; text-align: left; }

.mmxlib-head { display: flex; gap: 8px; align-items: center; }
.mmxlib-head input[type="text"] { flex: 1 1 auto; }
.mmxlib-head select { flex: 0 0 150px; }
.mmxlib-list { flex: 1 1 auto; overflow: auto; margin-top: 12px;
    border-top: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-card { display: flex; gap: 10px; padding: 10px 2px;
    border-bottom: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-card.mmxlib-on { background: rgba(59, 125, 216, 0.12); }
.mmxlib-shots { display: flex; flex-direction: column; gap: 4px; flex: 0 0 auto; }
.mmxlib-shots img { width: 50px; height: 50px; border-radius: 4px;
    border: 1px solid var(--border-color, #4e4e4e); display: block; }
.mmxlib-body { flex: 1 1 auto; min-width: 0; }
.mmxlib-name { font-size: 13px; font-weight: 600; }
.mmxlib-meta { font-size: 10px; color: var(--descrip-text, #999); margin-top: 2px; }
.mmxlib-desc { font-size: 11px; margin-top: 4px; }
.mmxlib-text { font-size: 11px; color: var(--descrip-text, #999); margin-top: 4px;
    overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2;
    -webkit-box-orient: vertical; }
.mmxlib-acts { display: flex; flex-direction: column; gap: 6px; flex: 0 0 auto;
    align-items: stretch; }
.mmxlib-acts button { padding: 4px 12px; font-size: 12px; }
.mmxlib-acts button.mmxlib-drop { color: #E08A8A; }
.mmxlib-none { padding: 24px 4px; text-align: center; font-size: 12px;
    color: var(--descrip-text, #999); }

.mmxlib-scroll { flex: 1 1 auto; overflow: auto; padding-right: 6px; }
.mmxlib-panel textarea.mmxlib-prompt { min-height: 220px; line-height: 1.5;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
.mmxlib-found { margin-top: 8px; font-size: 11px; line-height: 1.5; }
.mmxlib-found .mmxlib-warn, .mmxlib-found .mmxlib-note {
    text-indent: -1.05em; padding-left: 1.05em; }
.mmxlib-warn { color: #E0A45A; }
.mmxlib-note { color: var(--descrip-text, #999); }
.mmxlib-clean { color: #7FB77F; }
.mmxlib-kept { margin-top: 10px; font-size: 11px; color: var(--descrip-text, #999); }
.mmxlib-caution { margin: 0 0 14px; padding: 8px 11px; font-size: 11px;
    line-height: 1.5; color: #E0A45A; border-radius: 4px;
    border: 1px solid rgba(224, 164, 90, 0.4);
    background: rgba(224, 164, 90, 0.08); }
`;

const KIND_MARK = { image: "IMG", video: "VID", audio: "SND" };

async function ask(url, body) {
    const response = await api.fetchApi(url, {
        method: body ? "POST" : "GET",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    let payload = {};
    try {
        payload = await response.json();
    } catch (error) {
        payload = {};
    }
    return { ok: response.ok, status: response.status, payload };
}

function element(tag, className, text) {
    const made = document.createElement(tag);
    if (className) made.className = className;
    if (text !== undefined) made.textContent = text;
    return made;
}

const OPEN = [];

function frame(wide, { onClose, sticky } = {}) {
    installStyle(STYLE_ID, STYLE);
    const back = element("div", "mmxlib-back");
    const panel = element("div", "mmxlib-panel" + (wide ? " mmxlib-wide" : ""));
    back.appendChild(panel);
    const me = { close };
    OPEN.push(me);

    function close() {
        const at = OPEN.indexOf(me);
        if (at >= 0) OPEN.splice(at, 1);
        document.removeEventListener("keydown", onKey, true);
        back.remove();
        onClose?.();
    }

    function onKey(event) {
        if (event.key !== "Escape" || OPEN[OPEN.length - 1] !== me) return;
        event.stopPropagation();
        close();
    }

    panel.addEventListener("keydown", (event) => event.stopPropagation());
    document.addEventListener("keydown", onKey, true);
    back.addEventListener("pointerdown", (event) => {
        if (event.target === back && !sticky) close();
    });
    document.body.appendChild(back);
    return { back, panel, close };
}

function fileFor(node) {
    const link = node.inputs?.find((input) => input.name === "options")?.link;
    if (link === null || link === undefined) return DEFAULT_FILE;
    const source = app.graph?.getNodeById?.(app.graph?.links?.[link]?.origin_id);
    return widgetNamed(source, FILE_WIDGET)?.value || DEFAULT_FILE;
}

function pickOf(node) {
    const raw = widgetNamed(node, PICK)?.value;
    if (!raw) return null;
    try {
        const wanted = JSON.parse(raw);
        return wanted?.id ? wanted : null;
    } catch (error) {
        return null;
    }
}

function labelFor(node) {
    const pick = pickOf(node);
    if (!pick) return BROWSE_LABEL;
    const on = widgetNamed(node, REPEAT)?.value;
    return `Library: ${pick.name || pick.id}${on ? "" : " (repeat_last is off)"}`;
}

function relabel(node) {
    const button = node.widgets?.find((entry) => entry.mmxlibBrowse)?.mmxlibBrowse;
    if (!button) return;
    button.textContent = labelFor(node);
}

function notify(severity, summary, detail) {
    toast(severity, summary, detail, "mmx-selfcheck-toast");
}

const KIND_ORDER = ["image", "video", "audio"];

function spellShape(references) {
    const counts = {};
    for (const reference of references || []) {
        if (reference.kind) counts[reference.kind] = (counts[reference.kind] || 0) + 1;
    }
    const named = KIND_ORDER.filter((kind) => counts[kind]);
    const rest = Object.keys(counts)
        .filter((kind) => !KIND_ORDER.includes(kind))
        .sort();
    return [...named, ...rest]
        .map((kind) => `${counts[kind]} ${kind}${counts[kind] > 1 ? "s" : ""}`)
        .join(" + ");
}

async function copy(text, button) {
    const before = button.textContent;
    try {
        await navigator.clipboard.writeText(text);
    } catch (error) {
        const box = document.createElement("textarea");
        box.value = text;
        box.style.position = "fixed";
        box.style.opacity = "0";
        document.body.appendChild(box);
        box.select();
        try {
            document.execCommand("copy");
        } catch (failed) {
            button.textContent = "Cannot copy";
            setTimeout(() => (button.textContent = before), 1600);
            box.remove();
            return;
        }
        box.remove();
    }
    button.textContent = "Copied";
    setTimeout(() => (button.textContent = before), 1600);
}

function describeRun(record) {
    if (!record?.stored) {
        return "Nothing kept yet. Run this node once and its answer is what gets saved.";
    }
    const parts = [record.node_class, record.task, `${record.chars} characters`];
    return `Kept at ${record.clock} - ` + parts.filter(Boolean).join(" - ");
}

function measured(reference) {
    const size = reference.width ? `${reference.width}x${reference.height}` : "";
    const time = reference.seconds ? `${reference.seconds}s` : "";
    return [size, time, reference.fps ? `${reference.fps} fps` : ""].filter(Boolean).join(" ");
}

function referenceCards(references) {
    const holder = element("div", "mmxlib-refs");
    for (const reference of references) {
        const card = element("div", "mmxlib-ref");
        if (reference.thumb) {
            const picture = document.createElement("img");
            picture.src = reference.thumb;
            picture.alt = reference.label;
            card.appendChild(picture);
        } else {
            card.appendChild(element("div", "mmxlib-blank", KIND_MARK[reference.kind] || "?"));
        }
        card.appendChild(element("span", "", reference.label));
        const said = measured(reference);
        if (said) card.appendChild(element("span", "", said));
        holder.appendChild(card);
    }
    return holder;
}

function askName(title, note, placeholder) {
    return new Promise((resolve) => {
        const { panel, close } = frame(false, { onClose: () => resolve(null) });
        panel.appendChild(element("h3", "mmxlib-title", title));
        panel.appendChild(element("p", "mmxlib-sub", note));

        const box = document.createElement("input");
        box.type = "text";
        box.placeholder = placeholder;
        panel.appendChild(box);

        const row = element("div", "mmxlib-row");
        const cancel = element("button", "", "Cancel");
        const go = element("button", "mmxlib-go", "Make it");
        row.append(cancel, go);
        panel.appendChild(row);

        function done(value) {
            resolve(value);
            close();
        }

        cancel.addEventListener("click", () => done(null));
        go.addEventListener("click", () => done(box.value.trim() || null));
        box.addEventListener("keydown", (event) => {
            if (event.key === "Enter") done(box.value.trim() || null);
        });
        box.focus();
    });
}

function groupEditor(chosen) {
    const chips = element("div", "mmxlib-groups");
    const known = new Set();

    function addChip(label) {
        known.add(label);
        const chip = element("span", "mmxlib-chip", label);
        chip.classList.toggle("mmxlib-on", chosen.has(label));
        chip.addEventListener("click", () => {
            if (chosen.has(label)) chosen.delete(label);
            else chosen.add(label);
            chip.classList.toggle("mmxlib-on", chosen.has(label));
        });
        chips.appendChild(chip);
        return chip;
    }

    const box = document.createElement("input");
    box.type = "text";
    box.placeholder = "New group, then Enter";
    box.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        const label = box.value.trim();
        box.value = "";
        if (!label || known.has(label)) return;
        chosen.add(label);
        addChip(label);
    });

    for (const label of chosen) addChip(label);

    return {
        chips,
        box,
        offer(labels) {
            for (const label of labels || []) if (!known.has(label)) addChip(label);
        },
    };
}

function held(node, cannot, note) {
    const summary = recordFor(node.id);
    if (summary?.stored) return summary;
    notify(
        "info",
        "Nothing kept yet",
        `This node has not written anything this session, so there is nothing to ${cannot}. ` +
            "Run it once: what it writes is what you get.\n\n" +
            note
    );
    return null;
}

function openSave(node, button) {
    const summary = held(
        node,
        "save",
        "A prompt handed on from the library is not kept here either -- that one is " +
            "already in the library, under its own name."
    );
    if (!summary) return;

    const { panel, close } = frame(false);

    panel.appendChild(element("h3", "mmxlib-title", "Save the last prompt"));
    panel.appendChild(
        element(
            "p",
            "mmxlib-sub",
            "It goes to a JSON file in the ComfyUI user directory, available to every workflow."
        )
    );

    panel.appendChild(element("label", "mmxlib-field", "Name"));
    const name = document.createElement("input");
    name.type = "text";
    name.placeholder = "What this prompt is";
    panel.appendChild(name);

    panel.appendChild(element("label", "mmxlib-field", "Description"));
    const description = document.createElement("textarea");
    description.placeholder = "What it is for, what to watch out for -- searched later";
    panel.appendChild(description);

    panel.appendChild(element("label", "mmxlib-field", "Groups"));
    const chosen = new Set();
    const groups = groupEditor(chosen);
    panel.append(groups.chips, groups.box);

    panel.appendChild(element("label", "mmxlib-field", "Save in"));
    const file = document.createElement("select");
    const wanted = fileFor(node);
    file.appendChild(new Option(wanted, wanted));
    panel.appendChild(file);

    const about = element("div", "mmxlib-about");
    about.appendChild(element("div", "", describeRun(summary)));
    if (summary?.preview) about.appendChild(element("div", "mmxlib-preview", summary.preview));
    panel.appendChild(about);

    const row = element("div", "mmxlib-row");
    const problem = element("div", "mmxlib-problem");
    const take = element("button", "", "Copy prompt");
    const cancel = element("button", "", "Cancel");
    const save = element("button", "mmxlib-go", "Save");
    take.addEventListener("click", async () => {
        const { payload } = await ask(
            `/minimax_h3_rewriter/memory/text?node=${encodeURIComponent(node.id)}`
        );
        copy(payload.text || "", take);
    });
    row.append(problem, take, cancel, save);
    panel.appendChild(row);

    cancel.addEventListener("click", close);
    save.addEventListener("click", async () => {
        save.disabled = true;
        problem.textContent = "";
        const result = await ask("/minimax_h3_rewriter/library/save", {
            node: String(node.id),
            file: file.value,
            name: name.value,
            description: description.value,
            groups: [...chosen],
        });
        if (!result.ok || !result.payload.ok) {
            problem.textContent = result.payload.error || `HTTP ${result.status}`;
            save.disabled = false;
            return;
        }
        console.log(
            `[MiniMax-H3 Prompt Rewriter] '${result.payload.record.name}' saved to ${file.value}`
        );
        told(button, `Saved to ${file.value}`);
        close();
    });

    name.focus();

    ask("/minimax_h3_rewriter/library/files").then(({ payload }) => {
        const files = payload.files || [];
        if (!files.length) return;
        file.replaceChildren();
        for (const entry of files) file.appendChild(new Option(entry, entry));
        file.value = files.includes(wanted) ? wanted : files[0];
    });
    ask(`/minimax_h3_rewriter/library?file=${encodeURIComponent(wanted)}`).then(({ payload }) =>
        groups.offer(payload.groups)
    );

    if (summary.references) {
        ask(`/minimax_h3_rewriter/references?node=${encodeURIComponent(node.id)}`).then(
            ({ payload }) => {
                const references = payload.references || [];
                if (references.length) about.appendChild(referenceCards(references));
            }
        );
    }
}

function clockOf(stamp) {
    return stamp ? new Date(stamp * 1000).toLocaleString() : "";
}

function provenance(record) {
    const about = record.about || {};
    return [
        record.node_class,
        record.task,
        about.resolution,
        about.duration ? `${about.duration}s` : "",
        spellShape(record.references),
        `saved ${clockOf(record.saved_at)}`,
        record.edited_at ? `edited ${clockOf(record.edited_at)}` : "",
    ]
        .filter(Boolean)
        .join("  ·  ");
}

function openEdit(spec) {
    return new Promise((resolve) => {
        let settled = false;

        function finish(answer) {
            if (settled) return;
            settled = true;
            clearTimeout(pending);
            close();
            resolve(answer);
        }

        const { panel, close } = frame(true, {
            sticky: true,
            onClose: () => finish(false),
        });
        panel.appendChild(element("h3", "mmxlib-title", spec.title));
        panel.appendChild(element("p", "mmxlib-sub", spec.note));
        if (spec.caution) {
            panel.appendChild(element("div", "mmxlib-caution", spec.caution));
        }

        const scroll = element("div", "mmxlib-scroll");
        panel.appendChild(scroll);

        const chosen = new Set(spec.groups || []);
        const groups = groupEditor(chosen);
        const name = document.createElement("input");
        const description = document.createElement("textarea");

        if (spec.naming) {
            scroll.appendChild(element("label", "mmxlib-field", "Name"));
            name.type = "text";
            name.value = spec.name || "";
            scroll.appendChild(name);

            scroll.appendChild(element("label", "mmxlib-field", "Description"));
            description.placeholder =
                "What it is for, what to watch out for -- searched later";
            description.value = spec.description || "";
            scroll.appendChild(description);

            scroll.appendChild(element("label", "mmxlib-field", "Groups"));
            scroll.append(groups.chips, groups.box);
        }

        scroll.appendChild(element("label", "mmxlib-field", "Prompt"));
        const text = document.createElement("textarea");
        text.className = "mmxlib-prompt";
        text.spellcheck = false;
        text.value = spec.text || "";
        scroll.appendChild(text);

        const found = element("div", "mmxlib-found");
        scroll.appendChild(found);

        if (spec.keptNote) {
            scroll.appendChild(element("div", "mmxlib-kept", spec.keptNote));
        }

        const about = element("div", "mmxlib-about");
        about.appendChild(element("div", "", spec.provenance));
        if ((spec.references || []).length) {
            about.appendChild(referenceCards(spec.references));
        }
        scroll.appendChild(about);

        const row = element("div", "mmxlib-row");
        const problem = element("div", "mmxlib-problem");
        const cancel = element("button", "", "Cancel");
        const save = element("button", "mmxlib-go", "Save changes");
        row.append(problem, cancel, save);
        panel.appendChild(row);

        let pending = null;

        async function look() {
            const issues = await spec.check(text.value);
            if (settled) return;
            found.replaceChildren();
            if (!issues.length) {
                found.appendChild(
                    element("div", "mmxlib-clean", "Self-check: nothing to report.")
                );
                return;
            }
            const warnings = issues.filter((issue) => issue.level === "warn").length;
            found.appendChild(
                element(
                    "div",
                    "",
                    `Self-check: ${warnings} warning(s), ${issues.length - warnings} note(s)`
                )
            );
            for (const issue of issues) {
                found.appendChild(
                    element(
                        "div",
                        issue.level === "warn" ? "mmxlib-warn" : "mmxlib-note",
                        (issue.level === "warn" ? "! " : "- ") + issue.message
                    )
                );
            }
        }

        text.addEventListener("input", () => {
            clearTimeout(pending);
            pending = setTimeout(look, 500);
        });

        cancel.addEventListener("click", () => finish(false));
        save.addEventListener("click", async () => {
            save.disabled = true;
            problem.textContent = "";
            const done = await spec.save({
                name: name.value,
                description: description.value,
                groups: [...chosen],
                text: text.value,
            });
            if (done?.error) {
                problem.textContent = done.error;
                save.disabled = false;
                return;
            }
            finish(done.value);
        });

        if (spec.naming && spec.offerGroups) {
            spec.offerGroups().then((labels) => groups.offer(labels));
        }
        look();
        (spec.naming ? name : text).focus();
    });
}

function editSavedRecord(record, fileName) {
    return openEdit({
        title: "Edit a saved prompt",
        note:
            "The prompt itself, and what the card says about it. What produced this " +
            "record -- the writer, the settings, the references -- stays as it was.",
        naming: true,
        name: record.name,
        description: record.description,
        groups: record.groups,
        text: record.text,
        references: record.references,
        provenance: provenance(record),
        keptNote: (record.sections || []).length
            ? "This record also carries the writer's own split of that text into " +
              "fields. Changing the text drops it, and a node given this record " +
              "splits the text itself instead -- the same thing it already does " +
              "with a prompt from a different writer."
            : "",
        async offerGroups() {
            const { payload } = await ask(
                `/minimax_h3_rewriter/library?file=${encodeURIComponent(fileName)}`
            );
            return payload.groups || [];
        },
        async check(value) {
            const { payload } = await ask("/minimax_h3_rewriter/library/check", {
                file: fileName,
                id: record.id,
                text: value,
            });
            return payload.issues || [];
        },
        async save(edited) {
            const result = await ask("/minimax_h3_rewriter/library/update", {
                file: fileName,
                id: record.id,
                changes: edited,
            });
            if (!result.ok || !result.payload.ok) {
                return { error: result.payload.error || `HTTP ${result.status}` };
            }
            console.log(
                `[MiniMax-H3 Prompt Rewriter] '${result.payload.record.name}' edited in ` +
                    fileName
            );
            return { value: result.payload.record };
        },
    });
}

async function editLastPrompt(node, button) {
    const summary = held(
        node,
        "edit",
        "A prompt handed on from the library is not kept here either -- it was never " +
            "this node's own answer. Edit that one in the library window."
    );
    if (!summary) return;
    if (!summary.editable) {
        notify(
            "info",
            "Not a prompt",
            "This node keeps a caption about one asset rather than an answer with " +
                "fields, so there is nothing here to split or to edit."
        );
        return;
    }
    const node_id = encodeURIComponent(node.id);
    const [answer, shown] = await Promise.all([
        ask(`/minimax_h3_rewriter/memory/text?node=${node_id}`),
        ask(`/minimax_h3_rewriter/references?node=${node_id}`),
    ]);

    const pick = pickOf(node);
    const repeating = Boolean(widgetNamed(node, REPEAT)?.value);
    const caution = pick
        ? `This node is pointed at the saved prompt '${pick.name || pick.id}', and that ` +
          "is what it hands on. An edit here will not reach the output while the choice " +
          "stands -- clear it with 'Write a new one' in the library window, or edit that " +
          "record instead."
        : repeating
          ? ""
          : "'repeat_last' is off, so the next run writes a new answer over this one. " +
            "Saving switches it on, which is what makes this edit the node's output.";
    const saved = await openEdit({
        title: "Edit the last prompt",
        note:
            "The answer this node is holding for this session. Nothing is written to " +
            "disk -- to keep it, save it to the library afterwards.",
        naming: false,
        text: answer.payload.text || "",
        references: shown.payload.references || [],
        provenance: describeRun(summary),
        caution,
        keptNote:
            "The fields are split out of the text again when you save, so the section " +
            "outputs stay in step with what you wrote. Everything the node kept past " +
            "them belongs to the run rather than to the prose, and stays as it was.",
        async check(value) {
            const { payload } = await ask("/minimax_h3_rewriter/memory/check", {
                node: String(node.id),
                text: value,
            });
            return payload.issues || [];
        },
        async save({ text }) {
            const result = await ask("/minimax_h3_rewriter/memory/rewrite", {
                node: String(node.id),
                text,
            });
            if (!result.ok || !result.payload.ok) {
                return { error: result.payload.error || `HTTP ${result.status}` };
            }
            if (!pick && !repeating) {
                setWidgetValue(node, REPEAT, true);
                relabel(node);
            }
            return { value: result.payload.record };
        },
    });
    if (!saved) return;
    told(button, "Edited");
    notify(
        pick ? "warn" : "success",
        "The kept answer was edited",
        (pick
            ? `But this node is still pointed at '${pick.name || pick.id}', which is what ` +
              "it will hand on. The edit is waiting behind that choice."
            : repeating
              ? "It is what this node hands on now."
              : "'repeat_last' was switched on, so this is what the node hands on now.") +
            "\n\nNothing is on disk yet -- 'Save the last prompt' puts it in the library, " +
            "which survives a restart."
    );
}

function haystack(record) {
    return [
        record.name,
        record.description,
        record.task,
        record.text,
        (record.groups || []).join(" "),
        (record.references || []).map((reference) => reference.label).join(" "),
        Object.values(record.about || {}).join(" "),
    ]
        .join(" ")
        .toLowerCase();
}

function openLibrary(node, button) {
    const { panel, close } = frame(true);
    panel.appendChild(element("h3", "mmxlib-title", "Prompt library"));
    panel.appendChild(
        element(
            "p",
            "mmxlib-sub",
            "Hand a saved prompt straight to this node's output. No model is loaded."
        )
    );

    const head = element("div", "mmxlib-head");
    const search = document.createElement("input");
    search.type = "text";
    search.placeholder = "Search names, descriptions, prompts, references";
    const file = document.createElement("select");
    const wanted = fileFor(node);
    file.appendChild(new Option(wanted, wanted));
    head.append(search, file);
    panel.appendChild(head);

    const chips = element("div", "mmxlib-groups");
    chips.style.marginTop = "10px";
    panel.appendChild(chips);

    const list = element("div", "mmxlib-list");
    panel.appendChild(list);

    const row = element("div", "mmxlib-row");
    const problem = element("div", "mmxlib-problem");
    const clear = element("button", "", "Write a new one");
    const shut = element("button", "", "Close");
    row.append(problem, clear, shut);
    panel.appendChild(row);

    let records = [];
    const filters = new Set();

    function use(pick, repeat) {
        setWidgetValue(node, PICK, pick ? JSON.stringify(pick) : "");
        setWidgetValue(node, REPEAT, repeat === undefined ? Boolean(pick) : repeat);
        relabel(node);
        close();
    }

    function card(record) {
        const holder = element("div", "mmxlib-card");
        const shots = element("div", "mmxlib-shots");
        for (const reference of (record.references || []).slice(0, 3)) {
            if (!reference.thumb) continue;
            const picture = document.createElement("img");
            picture.src = reference.thumb;
            picture.title = `${reference.label} ${measured(reference)}`;
            shots.appendChild(picture);
        }
        if (shots.children.length) holder.appendChild(shots);

        const body = element("div", "mmxlib-body");
        body.appendChild(element("div", "mmxlib-name", record.name || record.id));
        const about = record.about || {};
        const meta = [
            record.task,
            about.resolution,
            about.duration ? `${about.duration}s` : "",
            spellShape(record.references),
            (record.groups || []).join(", "),
        ].filter(Boolean);
        body.appendChild(element("div", "mmxlib-meta", meta.join("  ·  ")));
        if (record.description) body.appendChild(element("div", "mmxlib-desc", record.description));
        body.appendChild(element("div", "mmxlib-text", record.text || ""));
        holder.appendChild(body);

        const acts = element("div", "mmxlib-acts");
        const take = element("button", "mmxlib-go", "Use");
        take.addEventListener("click", () =>
            use({ file: file.value, id: record.id, name: record.name })
        );
        const change = element("button", "", "Edit");
        change.title = "Change the prompt itself, and what this card says about it";
        change.addEventListener("click", async () => {
            const saved = await editSavedRecord(record, file.value);
            if (!saved) return;
            const pick = pickOf(node);
            if (pick?.id === record.id && pick.name !== saved.name) {
                setWidgetValue(node, PICK, JSON.stringify({ ...pick, name: saved.name }));
                relabel(node);
            }
            load();
        });
        const hand = element("button", "", "Copy");
        hand.title = "Put the prompt on the clipboard, without pointing the node at it";
        hand.addEventListener("click", () => copy(record.text || "", hand));
        const drop = element("button", "mmxlib-drop", "Delete");
        drop.addEventListener("click", async () => {
            drop.disabled = true;
            const result = await ask("/minimax_h3_rewriter/library/delete", {
                file: file.value,
                id: record.id,
            });
            if (!result.payload.ok) {
                problem.textContent = "that record could not be deleted";
                drop.disabled = false;
                return;
            }
            const pick = pickOf(node);
            if (pick?.id === record.id) {
                setWidgetValue(node, PICK, "");
                relabel(node);
            }
            load();
        });
        acts.append(take, change, hand, drop);
        holder.appendChild(acts);

        if (pickOf(node)?.id === record.id) holder.classList.add("mmxlib-on");
        return holder;
    }

    function lastPromptCard() {
        const summary = recordFor(node.id);
        const holder = element("div", "mmxlib-card");
        const body = element("div", "mmxlib-body");
        body.appendChild(element("div", "mmxlib-name", "Last Prompt"));
        body.appendChild(element("div", "mmxlib-meta", describeRun(summary)));
        if (summary?.preview) body.appendChild(element("div", "mmxlib-text", summary.preview));
        holder.appendChild(body);

        const acts = element("div", "mmxlib-acts");
        const take = element("button", "", "Use");
        const hand = element("button", "", "Copy");
        take.disabled = !summary?.stored;
        hand.disabled = !summary?.stored;
        hand.title = "Put this answer on the clipboard, without switching anything on";
        hand.addEventListener("click", async () => {
            const { payload } = await ask(
                `/minimax_h3_rewriter/memory/text?node=${encodeURIComponent(node.id)}`
            );
            copy(payload.text || "", hand);
        });
        take.title =
            "Switches 'repeat_last' on, which is where this answer lives. It is held for " +
            "this session only and is not in the library.";
        take.addEventListener("click", () => use(null, true));
        acts.append(take, hand);
        holder.appendChild(acts);
        return holder;
    }

    function draw() {
        const text = search.value.trim().toLowerCase();
        list.replaceChildren(lastPromptCard());
        const shown = records.filter((record) => {
            if (filters.size) {
                const groups = record.groups || [];
                if (![...filters].some((group) => groups.includes(group))) return false;
            }
            return !text || haystack(record).includes(text);
        });
        for (const record of shown.slice().reverse()) list.appendChild(card(record));
        if (!shown.length) {
            list.appendChild(
                element(
                    "div",
                    "mmxlib-none",
                    records.length
                        ? "Nothing in this set matches."
                        : "This set is empty. 'Save the last prompt' on a node puts one here."
                )
            );
        }
    }

    async function load() {
        const { payload } = await ask(
            `/minimax_h3_rewriter/library?file=${encodeURIComponent(file.value)}`
        );
        records = payload.records || [];
        problem.textContent = payload.problem || "";
        chips.replaceChildren();
        for (const label of payload.groups || []) {
            const chip = element("span", "mmxlib-chip", label);
            if (filters.has(label)) chip.classList.add("mmxlib-on");
            chip.addEventListener("click", () => {
                if (filters.has(label)) filters.delete(label);
                else filters.add(label);
                chip.classList.toggle("mmxlib-on", filters.has(label));
                draw();
            });
            chips.appendChild(chip);
        }
        draw();
    }

    search.addEventListener("input", draw);
    file.addEventListener("change", () => {
        filters.clear();
        load();
    });
    clear.addEventListener("click", () => use(null, false));
    shut.addEventListener("click", close);

    load();
    ask("/minimax_h3_rewriter/library/files").then(({ payload }) => {
        const files = payload.files || [];
        if (!files.length) return;
        const holding = file.value;
        file.replaceChildren();
        for (const entry of files) file.appendChild(new Option(entry, entry));
        file.value = files.includes(holding) ? holding : files[0];
    });
    search.focus();
}

function addButtons(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        const node = this;

        buttonRow(this, "mmx_prompt_actions", [
            {
                label: SAVE_LABEL,
                tooltip: SAVE_TOOLTIP,
                onClick: (button) => openSave(node, button),
            },
            {
                label: EDIT_LABEL,
                tooltip: EDIT_TOOLTIP,
                onClick: (button) => editLastPrompt(node, button),
            },
        ]);

        const { widget: browse, buttons } = buttonRow(this, "mmx_library", [
            {
                label: BROWSE_LABEL,
                tooltip: BROWSE_TOOLTIP,
                onClick: (button) => openLibrary(node, button),
            },
        ]);
        browse.mmxlibBrowse = buttons[0];

        const repeat = widgetNamed(this, REPEAT);
        if (repeat) {
            const original = repeat.callback;
            repeat.callback = function () {
                const answer = original?.apply(this, arguments);
                relabel(node);
                return answer;
            };
        }

        showWidget(this, PICK, false);
        relabel(this);
        return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const result = onConfigure?.apply(this, arguments);
        showWidget(this, PICK, false);
        relabel(this);
        return result;
    };
}

/** Only 'Save the last prompt'. For a node that keeps an answer but repeats nothing. */
function addSaveButton(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        const node = this;
        buttonRow(this, "mmx_prompt_actions", [
            {
                label: SAVE_LABEL,
                tooltip: SAVE_TOOLTIP,
                onClick: (button) => openSave(node, button),
            },
        ]);
        return result;
    };
}


function addNewFileButton(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        const node = this;
        buttonRow(this, "mmx_new_prompt_file", [
            {
                label: NEW_FILE_LABEL,
                tooltip: NEW_FILE_TOOLTIP,
                async onClick() {
                    const wanted = await askName(
                        "New prompt file",
                        "A separate set of saved prompts, for the nodes wired to this " +
                            "Options node.",
                        "storyboards"
                    );
                    if (!wanted) return;
                    const { payload } = await ask("/minimax_h3_rewriter/library/create", {
                        file: wanted,
                    });
                    if (!payload.ok) return;
                    const combo = widgetNamed(node, FILE_WIDGET);
                    if (combo) {
                        combo.options = combo.options || {};
                        combo.options.values = payload.files || [payload.file];
                        setWidgetValue(node, FILE_WIDGET, payload.file);
                    }
                    console.log(
                        `[MiniMax-H3 Prompt Rewriter] prompt set '${payload.file}' created`
                    );
                },
            },
        ]);
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.prompt_library",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (NODES.includes(nodeData.name)) addButtons(nodeType);
        if (nodeData.name === CHECK_NODE) addSaveButton(nodeType);
        if (nodeData.name === OPTIONS_NODE) addNewFileButton(nodeType);
    },
});
