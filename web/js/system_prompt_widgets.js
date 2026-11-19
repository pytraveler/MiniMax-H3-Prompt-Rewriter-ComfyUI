import { app } from "../../scripts/app.js";
import {
    installBaseStyle, installStyle, onRefresh, refresh, repaintOn, replaceWithDom,
    setWidgetValue, widgetNamed,
} from "./mmx_controls.js";
import { ask, confirmed, element, footNote, frame, mayClose } from "./mmx_window.js";

const FIELD = "system_prompt_file";
const ROUTE = "/minimax_h3_rewriter/system_prompts";
const QWEN = "MiniMaxH3QwenRewriterOptions";
const API = "MiniMaxH3ApiWriterOptions";
const OPTIONS = new Set(["MiniMaxH3RewriterOptions", QWEN, API]);
const TASKS = ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"];
const MAX_BYTES = 256 * 1024;
const activeNodes = new Set();
const editors = new WeakMap();
let names = [];
let reading;
let listed = false;
let revision = 0;

async function request(path = "", body, timeout = 15000) {
    const { ok, status, payload } = await ask(ROUTE + path, body, { signal: AbortSignal.timeout(timeout) });
    if (!ok) throw new Error(payload.error || `Request failed (HTTP ${status}).`);
    return payload;
}

function updateNames(next) {
    revision++;
    names = [...new Set(next)].sort((left, right) => left.localeCompare(right));
    for (const node of activeNodes) refresh(node);
}

function listFiles() {
    const requestedAt = revision;
    reading ??= request().then(({ files }) => {
        listed = true;
        if (requestedAt === revision) updateNames(files);
    }).finally(() => { reading = undefined; });
    return reading;
}

function selectNames(select, current, blank) {
    const missing = current && !names.includes(current) ? current : "";
    const signature = JSON.stringify([blank, names, missing, listed]);
    if (select.dataset.items !== signature) {
        const options = [new Option(blank, ""), ...names.map((name) => new Option(name, name))];
        if (missing) options.push(new Option(listed ? missing + " (missing)" : missing, missing));
        select.replaceChildren(...options);
        select.dataset.items = signature;
    }
    select.value = current;
}

function linked(node) {
    const index = node.inputs?.findIndex((slot) => slot.name === FIELD) ?? -1;
    return index >= 0 && (node.isInputConnected?.(index) ?? node.inputs[index].link != null);
}

function editor(node) {
    if (editors.has(node)) return;
    const handle = frame(true, {
        sticky: true, size: "system-prompts", onClose: () => editors.delete(node),
    });
    editors.set(node, handle);
    const { panel, close } = handle;
    panel.classList.add("mmx-system-editor");
    panel.setAttribute("aria-label", "System prompts");
    const saved = element("select");
    saved.setAttribute("aria-label", "Saved system prompts");
    const name = element("input");
    name.type = "text";
    name.maxLength = 80;
    name.setAttribute("aria-label", "System prompt name");
    const content = element("textarea");
    content.spellcheck = false;
    content.setAttribute("aria-label", "System prompt text");
    const file = element("input");
    file.type = "file";
    file.accept = ".md,.txt";
    file.hidden = true;
    const actions = element("div", "mmxlib-row mmx-system-tools");
    const importButton = element("button", "", "Import .md or .txt");
    const task = element("select");
    task.setAttribute("aria-label", "Default guide task");
    task.append(...TASKS.map((one) => new Option(one, one)));
    const copy = element("button", "", "Copy default guide");
    actions.append(importButton, task, copy);
    const note = footNote();
    note.holder.setAttribute("aria-live", "polite");
    const footer = element("div", "mmxlib-row");
    const leave = element("button", "", "Close");
    const save = element("button", "mmxlib-go", "Save and select");
    footer.append(note.holder, leave, save);
    panel.append(
        element("h3", "mmxlib-title", "System prompts"),
        element("p", "mmxlib-sub", "Saved on the ComfyUI server. The workflow keeps the selected name."),
        saved, actions, file, element("div", "mmxlib-field", "Name"), name,
        element("div", "mmxlib-field", "System prompt"), content, footer,
    );

    let opened = { name: "", text: "", revision: "" };
    let busy = false;
    const changed = () => name.value.trim() !== opened.name || content.value !== opened.text;
    const discard = () => !changed() || confirmed("Discard changes to this draft?", {
        title: "Unsaved system prompt", ok: "Discard",
    });

    function draw() {
        selectNames(saved, opened.name, "New system prompt");
        for (const control of [saved, importButton, task, copy, name, content, leave, save]) {
            control.disabled = busy;
        }
        save.disabled = busy || !name.value.trim() || !content.value.trim();
    }

    function show(entry) {
        name.value = entry.name;
        content.value = entry.text;
        opened = { ...entry, text: content.value };
        draw();
    }

    async function work(message, action) {
        busy = true;
        note.say(message);
        draw();
        try {
            await action();
        } catch (error) {
            note.say(error.message, true);
        } finally {
            busy = false;
            draw();
        }
    }

    async function open(name) {
        show({ name, text: "", revision: "" });
        if (name) show(await request("?name=" + encodeURIComponent(name)));
        note.say("");
    }

    async function tryClose() {
        if (!busy && await mayClose(changed())) close();
    }

    handle.handle.onEscape = tryClose;
    leave.addEventListener("click", tryClose);
    name.addEventListener("input", draw);
    content.addEventListener("input", draw);
    saved.addEventListener("change", async () => {
        const selected = saved.value;
        draw();
        if (await discard()) await work("Opening system prompt...", () => open(selected));
    });
    importButton.addEventListener("click", () => file.click());
    file.addEventListener("change", async () => {
        const selected = file.files[0];
        file.value = "";
        if (!selected || !await discard()) return;
        await work("Reading file...", async () => {
            if (!/\.(md|txt)$/i.test(selected.name) || selected.size > MAX_BYTES) {
                throw new Error("Choose a .md or .txt file up to 256 KiB.");
            }
            const text = new TextDecoder("utf-8", { fatal: true }).decode(await selected.arrayBuffer());
            show({ name: "", text: "", revision: "" });
            name.value = selected.name.replace(/\.(md|txt)$/i, "");
            content.value = text;
            note.say("Imported. Save to keep it in the library.");
        });
    });
    copy.addEventListener("click", async () => {
        if (!await discard()) return;
        const wanted = task.value;
        await work("Loading the default guide...", async () => {
            const flag = node.type === API ? "auto_download_guide" : "auto_download";
            const { text } = await request("/template", {
                task: wanted, qwen: node.type === QWEN, auto_download: widgetNamed(node, flag)?.value === true,
            }, 45000);
            const draftName = opened.name ? "" : name.value;
            show({ name: "", text: "", revision: "" });
            name.value = draftName || wanted + " guide";
            content.value = text;
            note.say("Copied. Edit and save under your own name.");
        });
    });
    save.addEventListener("click", async () => {
        const wanted = name.value.trim();
        const text = content.value;
        await work("Saving system prompt...", async () => {
            const entry = await request("", {
                name: wanted, text, revision: wanted === opened.name ? opened.revision : "",
            });
            updateNames([...names, entry.name]);
            show({ ...entry, text });
            if (!linked(node) && node.graph) setWidgetValue(node, FIELD, entry.name);
            note.say(linked(node) ? "Saved. The node uses the connected file name." : "Saved and selected.");
        });
    });

    work("Reading system prompts...", async () => {
        await listFiles();
        await open(widgetNamed(node, FIELD)?.value || "");
    });
}

function build(node) {
    installBaseStyle();
    installStyle("minimax-h3-system-prompts-style", `
.mmx-system-row select {
    flex: 1 1 0; min-width: 0; padding: 0 6px; font: inherit; font-size: 12px;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); border-radius: 6px;
}
.mmx-system-row button { flex: 0 0 auto; }
.mmx-system-editor { overflow-y: auto !important; }
.mmx-system-editor .mmx-system-tools { justify-content: flex-start; flex-wrap: wrap; margin-top: 10px; }
.mmx-system-editor .mmx-system-tools select { width: auto; }
.mmx-system-editor textarea { min-height: 180px; height: min(42vh, 420px); flex: 1 1 auto; }
`);
    const row = element("div", "mmx-buttons mmx-system-row");
    const select = element("select");
    select.setAttribute("aria-label", "System prompt file");
    const manage = element("button", "", "System prompts...");
    row.append(select, manage);
    if (!replaceWithDom(node, FIELD, "minimaxh3_system_prompt", row, () => 26)) return;
    onRefresh(node, () => {
        selectNames(select, widgetNamed(node, FIELD)?.value || "", "Default guide");
        select.disabled = Boolean(linked(node));
    });
    select.addEventListener("change", () => setWidgetValue(node, FIELD, select.value));
    select.addEventListener("focus", () => listFiles().catch(() => {}));
    manage.addEventListener("click", () => editor(node));
    refresh(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.system_prompts",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!OPTIONS.has(nodeData.name)) return;
        repaintOn(nodeType, build);
        const added = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function () {
            const result = added?.apply(this, arguments);
            activeNodes.add(this);
            refresh(this);
            if (!listed) listFiles().catch(() => {});
            return result;
        };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            activeNodes.delete(this);
            editors.get(this)?.close();
            return removed?.apply(this, arguments);
        };
    },
});
