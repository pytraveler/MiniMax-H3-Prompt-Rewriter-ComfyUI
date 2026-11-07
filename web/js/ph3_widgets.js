import { app } from "../../scripts/app.js";
import { previewFor, releasePreviews } from "./reference_previews.js";
import { addSlotSwitches } from "./slot_switches.js";
import { firstSelectedFrame, frameInfo, openFramePicker, selectedCount } from "./ph3_frame_picker.js";
import {
    CHIP_H, chipElement, installStripStyle, placeDragged, readInstructions,
    slotNumber, slots, stripHeight, writeInstruction,
} from "./reference_strip.js";
import {
    MARGIN, drivenByWire, installBaseStyle, installStyle, onRefresh, ratiosHeight,
    renderRatios, renderSegments, repaintOn, replaceWithDom, setWidgetValue,
    showWidget, widgetNamed,
} from "./mmx_controls.js";

const NODE = "MiniMaxH3PH3Writer";
const STATE = "__minimaxH3PH3";
const LAYOUT = "reference_layout";
const INSTRUCTIONS = "reference_instructions";
const STYLE = `
.mmx-ph3-header { display: flex; flex-direction: column; gap: ${MARGIN}px; height: 100%; }
.mmx-ph3-header > .mmx-seg-row { flex: 0 0 26px; height: 26px; box-sizing: border-box; }
.mmx-ph3-refs { display: flex; flex-direction: column; gap: 3px; flex: 1 1 0; min-height: 0; }
.mmx-ph3-refs .mmx-chip { height: ${CHIP_H + 19}px; }
.mmx-ph3-refs .mmx-chip-role { white-space: nowrap; overflow: hidden; min-height: 18px; }
.mmx-ph3-refs .mmx-chip-media { flex: 0 0 46px; }
.mmx-ph3-refs .mmx-ph3-source .mmx-chip-media .mmx-chip-num {
    inset: 2px 2px auto auto; min-width: 0; height: 12px; padding: 0 3px;
    border-radius: 3px; font: 8px/12px system-ui; background: #000a; }
.mmx-ph3-refs .mmx-ph3-unused { opacity: .4; }
.mmx-ph3-frame-button { flex: 0 0 17px; height: 17px; padding: 0; border: 0;
    border-top: 1px solid #ffffff26; border-radius: 0; cursor: pointer;
    font: 10px/16px system-ui; color: #fff; background: #0003;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mmx-ph3-frame-button:hover { background: #ffffff30; }
.mmx-ph3-hint { flex: 0 0 14px; height: 14px; font: 10px/14px system-ui; color: var(--descrip-text, #999);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mmx-ph3-dialog { width: 350px; max-width: 85vw; padding: 16px; border-radius: 8px;
    background: var(--comfy-menu-bg, #353535); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #555); font: 12px system-ui; }
.mmx-ph3-dialog textarea { width: 100%; min-height: 100px; box-sizing: border-box;
    margin: 10px 0; background: var(--comfy-input-bg, #222); color: inherit; }
.mmx-ph3-dialog form > div { display: flex; justify-content: flex-end; gap: 8px; }
`;

function remountControls() {
    if (!app.extensionManager?.setting?.get("Comfy.VueNodes.Enabled")) return;
    const graph = app.canvas?.graph ?? app.graph;
    for (const shown of document.querySelectorAll("[data-mmx-ph3-widget]")) {
        if (!shown.parentElement?.classList.contains("*:flex-1")) continue;
        const node = graph?.getNodeById?.(shown.dataset.mmxPh3Node);
        if (node?.type !== NODE) continue;
        const current = node[STATE]?.[shown.dataset.mmxPh3Widget];
        if (current && current !== shown) shown.replaceWith(current);
    }
}

function layoutOf(node) {
    let parsed;
    try { parsed = JSON.parse(widgetNamed(node, LAYOUT)?.value || "{}"); }
    catch { parsed = {}; }
    return {
        order: Array.isArray(parsed?.order) ? parsed.order.filter((n) => typeof n === "string") : [],
        off: Array.isArray(parsed?.off) ? parsed.off.filter((n) => typeof n === "string") : [],
    };
}

function entriesOf(node) {
    const layout = layoutOf(node);
    const connected = new Map(slots(node, "ref_").filter((s) => s.connected).map((s) => [s.name, s]));
    const names = [...new Set([...layout.order, ...[...connected.keys()].sort((a, b) => slotNumber(a) - slotNumber(b))])];
    let next = 1;
    return names.filter((name) => connected.has(name)).map((name) => {
        const slot = connected.get(name);
        const on = !layout.off.includes(name);
        const kind = slot.kind === "audio" ? "audio" : "image";
        const count = selectedCount(node, name);
        const number = on ? next : 0;
        if (on) next = next === null || count === null ? null : next + count;
        return { ...slot, sourceKind: slot.kind, kind, role: "Picture", on, number, count };
    });
}

function saveLayout(node, layout) {
    setWidgetValue(node, LAYOUT, JSON.stringify(layout));
}

function toggleReference(node, name) {
    const layout = layoutOf(node);
    layout.off = layout.off.includes(name) ? layout.off.filter((value) => value !== name) : [...layout.off, name];
    saveLayout(node, layout);
}

function editInstruction(node, entry) {
    const panel = document.createElement("dialog");
    panel.className = "mmx-ph3-dialog";
    const form = document.createElement("form");
    form.method = "dialog";
    const label = document.createElement("label");
    label.textContent = `${entry.name}: what to take from this reference`;
    const box = document.createElement("textarea");
    box.value = readInstructions(node, INSTRUCTIONS)[entry.name]?.text || "";
    const editing = widgetNamed(node, "task")?.value === "Image2Image";
    box.placeholder = editing ? "Use the clothing; preserve the face in Picture 1."
        : "Describe the clothing and appearance; ignore the background.";
    label.appendChild(box);
    const note = document.createElement("p");
    note.textContent = editing ? "For a RefMod or image batch, this applies to every expanded picture. Empty clears it."
        : "With use_ref_description, this guides the first active reference's selected frame. The user request takes priority. Empty clears it.";
    const row = document.createElement("div");
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    cancel.value = "cancel";
    const save = document.createElement("button");
    save.textContent = "Save";
    save.value = "save";
    row.append(cancel, save);
    form.append(label, note, row);
    panel.appendChild(form);
    panel.addEventListener("close", () => {
        if (panel.returnValue === "save") writeInstruction(node, INSTRUCTIONS, entry.name, box.value, true);
        panel.remove();
    }, { once: true });
    document.body.appendChild(panel);
    panel.showModal();
    box.focus();
}

function gesture(node, chip, entry, event) {
    if (event.button !== 0 || event.target.closest(".mmx-instr, .mmx-ph3-frame-button")) return;
    event.preventDefault();
    event.stopPropagation();
    const strip = chip.parentElement;
    const start = [event.clientX, event.clientY];
    let dragging = false;
    const move = (moved) => {
        if (!dragging && Math.hypot(moved.clientX - start[0], moved.clientY - start[1]) < 6) return;
        dragging = true;
        node[STATE].dragging = true;
        chip.classList.add("mmx-dragging");
        placeDragged(strip, chip, moved.clientX, moved.clientY);
    };
    const finish = (finished) => {
        window.removeEventListener("pointermove", move, true);
        window.removeEventListener("pointerup", finish, true);
        window.removeEventListener("pointercancel", finish, true);
        node[STATE].dragging = false;
        chip.classList.remove("mmx-dragging");
        if (finished.type === "pointercancel") { redraw(node); return; }
        if (dragging) {
            const layout = layoutOf(node);
            layout.order = [...strip.querySelectorAll(".mmx-chip")].map((item) => item.dataset.slot);
            saveLayout(node, layout);
        } else toggleReference(node, entry.name);
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", finish, true);
    window.addEventListener("pointercancel", finish, true);
}

function headerHeight(node) {
    return stripHeight(node, node[STATE]?.count ?? 0, 14, 3, MARGIN, CHIP_H + 19) + 26 + MARGIN;
}

function redraw(node) {
    const state = node[STATE];
    if (!state || state.dragging) return;
    const entries = entriesOf(node);
    const editing = widgetNamed(node, "task")?.value === "Image2Image";
    const describing = !editing && !!widgetNamed(node, "use_ref_description")?.value;
    const source = describing ? entries.find((entry) => entry.on)?.name : null;
    state.count = entries.length;
    state.header.style.minHeight = `${headerHeight(node)}px`;
    for (const name of ["header", "references", "tasks", "ratios"]) {
        state[name].dataset.mmxPh3Node = String(node.id);
        state[name].dataset.mmxPh3Widget = name;
    }
    releasePreviews(state.strip);
    state.strip.replaceChildren();
    const instructions = readInstructions(node, INSTRUCTIONS);
    for (const entry of entries) {
        const preview = previewFor(node, entry.name, entry.kind);
        const { chip, role } = chipElement(entry, false, preview?.data ? { ...preview, file: null } : preview);
        const base = editing && entry.on && entry.number === 1;
        role.textContent = entry.name === source ? "source" : base ? "base" : "picture";
        chip.classList.toggle("mmx-ph3-source", entry.name === source);
        chip.classList.toggle("mmx-ph3-unused", describing && entry.name !== source);
        chip.querySelector(".mmx-chip-num").textContent = describing ? entry.name === source ? "text" : "--"
            : !entry.on ? "--" : entry.number === null ? "*"
            : entry.count > 1 ? `${entry.number}-${entry.number + entry.count - 1}` : String(entry.number);
        chip.title = `${entry.name}: ${entry.refmod ? "expanded into pictures at run time" : "drag to reorder, click to switch off"}`;
        const instruction = document.createElement("span");
        instruction.className = "mmx-instr" + (instructions[entry.name] ? " mmx-set" : "");
        instruction.textContent = instructions[entry.name] ? "role set" : "role";
        instruction.title = instructions[entry.name]?.text || "What to take from this reference";
        instruction.addEventListener("pointerdown", (event) => event.stopPropagation());
        instruction.addEventListener("click", (event) => { event.stopPropagation(); editInstruction(node, entry); });
        chip.appendChild(instruction);
        const frames = document.createElement("button");
        frames.type = "button";
        frames.className = "mmx-ph3-frame-button";
        const info = frameInfo(node, entry.name);
        const first = firstSelectedFrame(node, entry.name);
        frames.textContent = describing
            ? `frame ${first === null ? "?" : first + 1}${info ? "/" + info.count : ""}`
            : info ? `frames ${entry.count}/${info.count}` : "frames";
        frames.title = describing ? "Choose the frame to describe. Move this reference first and run once with bypass to load its frames."
            : "Choose exact reference frames. Run once with bypass to load the received frames.";
        frames.addEventListener("pointerdown", (event) => event.stopPropagation());
        frames.addEventListener("click", (event) => { event.stopPropagation(); openFramePicker(node, entry); });
        chip.appendChild(frames);
        chip.addEventListener("pointerdown", (event) => gesture(node, chip, entry, event));
        state.strip.appendChild(chip);
    }
    state.hint.textContent = describing
        ? source ? `${source} supplies the text description - drag to change the source - no images reach the generator`
            : "connect a reference for its text description; without one, Text2Image uses text only"
        : !editing
        ? "Text2Image uses text only - connected references are ignored"
        : entries.length
            ? "drag to reorder - click to switch off - first enabled picture is the base"
            : "connect images, VIDEO or visual RefMods for Image2Image";
    renderSegments(state.tasks, ["Text2Image", "Image2Image"].map((task) => ({
        label: task, on: widgetNamed(node, "task")?.value === task,
        pick: () => setWidgetValue(node, "task", task),
    })));
    showWidget(node, "keep", editing);
    showWidget(node, "use_ref_description", !editing);
    showWidget(node, "match_image_ratio", editing);
    showWidget(node, "refmod_images", editing && entries.some((entry) => entry.refmod));
    showWidget(node, "video_images", editing && entries.some((entry) => (!entry.refmod && entry.sourceKind === "video")
        || (frameInfo(node, entry.name)?.kind === "image" && frameInfo(node, entry.name).count > 8)));
    renderRatios(node, state.ratios, "resolution");
    const wired = drivenByWire(node);
    const driven = wired || (editing && !!widgetNamed(node, "match_image_ratio")?.value);
    state.ratios.style.opacity = driven ? "0.4" : "";
    state.ratios.style.pointerEvents = driven ? "none" : "";
    state.ratios.title = wired ? "Using aspect_ratio from the connected node"
        : driven ? "Using the first picture's aspect ratio" : "";
    const match = widgetNamed(node, "match_image_ratio");
    if (match) {
        match.disabled = wired;
        match.options.disabled = wired;
    }
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle("minimax-h3-ph3-style", STYLE);
    showWidget(node, LAYOUT, false);
    showWidget(node, INSTRUCTIONS, false);
    showWidget(node, "frame_selection", false);
    const wrapper = document.createElement("div");
    wrapper.className = "mmx-ph3-refs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-ph3-hint";
    wrapper.append(strip, hint);
    const tasks = document.createElement("div");
    tasks.className = "mmx-seg-row";
    const header = document.createElement("div");
    header.className = "mmx-ph3-header";
    header.append(wrapper, tasks);
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";
    node[STATE] = { header, references: wrapper, strip, hint, tasks, ratios, count: 0, dragging: false };
    onRefresh(node, () => redraw(node));
    replaceWithDom(node, "task", "minimaxh3_ph3_task", header, () => headerHeight(node));
    replaceWithDom(node, "resolution", "minimaxh3_ph3_ratio", ratios, () => ratiosHeight(node, "resolution"));
    for (const name of ["match_image_ratio", "use_ref_description"]) {
        const widget = widgetNamed(node, name);
        const callback = widget?.callback;
        if (widget) widget.callback = function () { callback?.apply(this, arguments); redraw(node); };
    }
    redraw(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.ph3_widgets",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        repaintOn(nodeType, build);
        const resized = nodeType.prototype.onResize;
        nodeType.prototype.onResize = function () {
            const result = resized?.apply(this, arguments);
            redraw(this);
            return result;
        };
        addSlotSwitches(nodeType, {
            prefixes: ["ref_"],
            enabled: (node, name) => !layoutOf(node).off.includes(name),
            toggle: toggleReference,
        });
    },
    afterConfigureGraph() {
        setTimeout(remountControls, 0);
    },
});
