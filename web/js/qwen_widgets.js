import { app } from "../../scripts/app.js";
import { addDurationLimit, applyDurationCeiling } from "./duration_limit.js";
import { entriesOf, layoutOf, toggleReference, editInstruction, startGesture, syncSlots } from "./api_writer_widgets.js";
import { apiInputSwitches } from "./api_writer_slots.js";
import { addSlotSwitches } from "./slot_switches.js";
import { previewFor, releasePreviews } from "./reference_previews.js";
import { chipElement, installStripStyle, readInstructions, stripHeight } from "./reference_strip.js";
import { MARGIN, installBaseStyle, installStyle, onRefresh, ratiosHeight, refresh, renderRatios,
    renderSegments, repaintOn, replaceWithDom, setWidgetValue, showWidget, widgetNamed, widgetValues } from "./mmx_controls.js";

const NODE = "MiniMaxH3QwenRewriter";
const OPTIONS = "MiniMaxH3QwenRewriterOptions";
const STATE = "__minimaxH3ApiWriter";
const ORDER = "minimax_h3_qwen_widget_order";
const active = new Set();
let refreshSwitches;

export function checkpointFamily(label) {
    const name = String(label || "").split(/ [\u00b7\u2014] /)[0];
    return /Ref2VA/i.test(name) ? "Ref2VA" : /FL2VA/i.test(name) ? "FL2VA" : "";
}

function frameRole(task, index) {
    if (task === "I2VA" || task === "FL2VA" && index === 0) return "first";
    if (task === "L2VA" || task === "FL2VA" && index === 1) return "last";
    return "";
}

function render(node) {
    const state = node[STATE];
    if (!state || state.dragging) return;
    refreshSwitches?.();
    const entries = entriesOf(node);
    const enabled = entries.filter((entry) => entry.on);
    state.count = entries.length;
    releasePreviews(state.strip);
    state.strip.replaceChildren();
    const task = widgetNamed(node, "task")?.value;
    for (const entry of entries) {
        const { chip, role } = chipElement(entry, false, previewFor(node, entry.name, entry.kind));
        const frame = entry.on && entry.role === "Picture" ? frameRole(task, enabled.indexOf(entry)) : "";
        if (frame) {
            role.textContent = frame;
            role.title = `Picture ${entry.number}: exact ${frame} frame`;
        }
        const instruction = document.createElement("button");
        instruction.className = "mmx-instr";
        instruction.type = "button";
        const text = readInstructions(node, "reference_instructions")[entry.name]?.text;
        instruction.classList.toggle("mmx-set", !!text);
        instruction.textContent = "+ instr";
        instruction.title = text || "Reference instructions; Audio and clip sound require an explicit description.";
        instruction.onclick = (event) => { event.stopPropagation(); editInstruction(node, entry, true); };
        chip.appendChild(instruction);
        if (task === "Ref2VA" && entry.on && (entry.kind === "audio" || entry.sound)) {
            const warning = document.createElement("button");
            warning.type = "button";
            warning.className = "mmx-qwen-audio-warning";
            warning.textContent = "!";
            const field = entry.sound ? "Clip sound description" : "Sound description";
            warning.title = `${entry.role} ${entry.number} (${entry.name}): Qwen cannot hear reference audio. `
                + `It reads your sound description. Click ! or + instr, fill '${field}', then 'Save instructions'. `
                + (entry.sound ? "To keep only the video, click the chip's top label to turn off sound."
                    : "To omit this audio, disable its input checkbox.");
            warning.setAttribute("aria-label", warning.title);
            warning.onclick = (event) => { event.stopPropagation(); editInstruction(node, entry, true); };
            role.classList.add("mmx-qwen-audio-role");
            role.appendChild(warning);
        }
        chip.addEventListener("pointerdown", (event) => startGesture(node, chip, role, entry, event, task === "Ref2VA"));
        state.strip.appendChild(chip);
    }
    if (!entries.length) state.strip.textContent = "Connect IMAGE, VIDEO, AUDIO or a decoded RefMod to ref_0.";
    const family = checkpointFamily(widgetNamed(node, "model")?.value);
    renderSegments(state.tasks, widgetValues(node, "task").map((name) => {
        const wanted = { I2VA: 1, L2VA: 1, FL2VA: 2 }[name];
        const pictures = enabled.filter((entry) => entry.role === "Picture" && entry.kind === "image").length;
        const why = family && (name === "Ref2VA") !== (family === "Ref2VA")
            ? `${family} checkpoint: select the other checkpoint family in Model list to use ${name}.`
            : name === "Ref2VA" && !enabled.length ? "Connect and enable a reference."
            : wanted !== undefined && (pictures !== wanted || enabled.length !== wanted)
                ? `${name} needs ${wanted} enabled Picture frame(s), in first/last order.` : "";
        return { label: name, on: name === task, shut: !!why, title: why || name,
            pick: () => setWidgetValue(node, "task", name) };
    }));
    renderRatios(node, state.ratios, "resolution");
    applyDurationCeiling(node);
    state.note.textContent = family === "Ref2VA"
        ? "Ref2VA: ordered images, timestamped video frames and explicit sound descriptions."
        : "FL2VA checkpoint: T2VA / I2VA / L2VA / FL2VA. Pictures are exact first/last frames.";
    if (enabled.some((entry) => entry.kind === "audio" || entry.sound)) {
        state.note.textContent += " Audio: text descriptions through + instr; this checkpoint has no audio encoder.";
    }
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle("minimax-h3-qwen-style", `
.mmx-qwen-refs { display: flex; flex-direction: column; gap: 3px; width: 100%; height: 100%;
    overflow: hidden; font: 11px system-ui, sans-serif; color: var(--input-text, #ddd); }
.mmx-qwen-refs .mmx-instr { padding: 0; border: 0; border-top: 1px solid rgba(0,0,0,.35);
    border-radius: 0; font-family: inherit; width: 100%; min-width: 0; white-space: nowrap; }
.mmx-qwen-audio-role { position: relative; box-sizing: border-box; padding-right: 12px; }
.mmx-qwen-audio-warning { position: absolute; right: 0; top: 1px; width: 12px; height: 15px;
    min-width: 0; min-height: 0; padding: 0; border: 0; border-radius: 2px;
    font: 900 15px/15px system-ui, sans-serif; color: #ff6666; background: #35161c;
    text-align: center; cursor: help; }
.mmx-qwen-audio-warning:hover, .mmx-qwen-audio-warning:focus-visible { color: #fff; background: #b22232; }
.mmx-qwen-hint { flex: 0 0 12px; font-size: 9px; line-height: 12px; color: var(--descrip-text, #aaa);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mmx-qwen-note { font: 10px system-ui, sans-serif; color: var(--descrip-text, #aaa); overflow: hidden; }
.mmx-api-instruction { max-width: 420px; background: var(--comfy-menu-bg, #353535);
    color: var(--input-text, #ddd); border: 1px solid #666; border-radius: 6px; }
.mmx-api-instruction textarea { display: block; width: min(380px, 75vw); height: 120px; margin: 10px 0; }
.mmx-api-instruction-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
`);
    const holder = document.createElement("div");
    holder.className = "mmx-qwen-refs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-qwen-hint";
    hint.textContent = "Drag: reorder; label: role/sound; below: on/off; + instr: instructions and descriptions.";
    holder.append(strip, hint);
    const tasks = document.createElement("div");
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";
    const note = document.createElement("div");
    note.className = "mmx-qwen-note";
    node[STATE] = { strip, tasks, ratios, note, count: 0, dragging: false, syncing: false };
    active.add(node);
    for (const name of ["reference_instructions", "reference_descriptions", "library_pick"]) showWidget(node, name, false);
    replaceWithDom(node, "reference_layout", "minimaxh3_qwen_references", holder,
        () => stripHeight(node, node[STATE].count, 12, 3, MARGIN));
    replaceWithDom(node, "task", "minimaxh3_qwen_task", tasks, () => 26);
    replaceWithDom(node, "resolution", "minimaxh3_qwen_ratio", ratios, () => ratiosHeight(node, "resolution"));
    node.addDOMWidget("qwen_note", "minimaxh3_qwen_note", note, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 42, getMaxHeight: () => 42,
    });
    for (const name of ["model", "bypass", "repeat_last"]) {
        const widget = widgetNamed(node, name);
        if (!widget) continue;
        const previous = widget.callback;
        widget.callback = function () {
            const result = previous?.apply(this, arguments);
            if (name === "model") {
                const family = checkpointFamily(widget.value);
                const task = widgetNamed(node, "task")?.value;
                if (family === "Ref2VA" && task !== "Ref2VA") setWidgetValue(node, "task", "Ref2VA");
                if (family === "FL2VA" && task === "Ref2VA") setWidgetValue(node, "task", "T2VA");
            }
            refresh(node);
            const input = node.inputs?.find((one) => one.name === "options");
            const link = node.graph?.links?.get?.(input?.link) || node.graph?.links?.[input?.link];
            const options = node.graph?.getNodeById?.(link?.origin_id);
            if (options?.type === OPTIONS) refresh(options);
            return result;
        };
    }
    onRefresh(node, () => { render(node); queueMicrotask(() => syncSlots(node)); });
    render(node);
    queueMicrotask(() => syncSlots(node));
}

function namedWidgetOrder(nodeType) {
    const configure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info, ...rest) {
        const order = info?.properties?.[ORDER];
        if (!Array.isArray(order) || !Array.isArray(info.widgets_values)) return configure.call(this, info, ...rest);
        const saved = new Map(order.map((name, index) => [name, info.widgets_values[index]]));
        const values = (this.widgets || []).map((widget) => saved.has(widget.name) ? saved.get(widget.name) : widget.value);
        return configure.call(this, { ...info, widgets_values: values,
            widgets_values_named: Object.fromEntries((this.widgets || []).map((widget, index) => [widget.name, values[index]])) }, ...rest);
    };
    const serialize = nodeType.prototype.onSerialize;
    nodeType.prototype.onSerialize = function (info) {
        const result = serialize?.apply(this, arguments);
        info.properties = { ...info.properties, [ORDER]: (this.widgets || []).map((widget) => widget.name) };
        return result;
    };
}

function optionControls(node) {
    const note = document.createElement("div");
    note.className = "mmx-qwen-note";
    node.addDOMWidget("qwen_backend_note", "minimaxh3_qwen_note", note, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 42, getMaxHeight: () => 42,
    });
    onRefresh(node, () => {
        const graph = node.graph;
        const labels = (node.outputs?.[0]?.links || []).map((id) => {
            const link = graph?.links?.get?.(id) || graph?.links?.[id];
            const target = graph?.getNodeById?.(link?.target_id);
            return target ? widgetNamed(target, "model")?.value : null;
        }).filter(Boolean);
        const allSft = labels.length && labels.every((label) => /\bSFT\b/i.test(label));
        const allGguf = labels.length && labels.every((label) => /GGUF|\.gguf|Q[4568]_K|Q8_0/i.test(label));
        for (const name of ["quantization", "attn_implementation"]) showWidget(node, name, !allGguf);
        for (const name of ["gpu_layers", "llama_backend"]) showWidget(node, name, !allSft);
        note.textContent = allGguf ? "GGUF: file precision is fixed; llama-server applies GPU layers, backend and context."
            : allSft ? "SFT: Transformers applies quantization and attention. GGUF-only controls are hidden."
            : "Connect to a Qwen Rewriter. SFT uses quantization/attention; GGUF uses GPU layers and backend.";
    });
    refresh(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.qwen_widgets",
    setup() { refreshSwitches = apiInputSwitches(active, (node, name) => !layoutOf(node).off.includes(name), toggleReference); },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === OPTIONS) {
            repaintOn(nodeType, optionControls);
            return;
        }
        if (nodeData.name !== NODE) return;
        addDurationLimit(nodeType, nodeData);
        repaintOn(nodeType, build);
        addSlotSwitches(nodeType, { prefixes: ["ref_"], enabled: (node, name) => !layoutOf(node).off.includes(name), toggle: toggleReference });
        namedWidgetOrder(nodeType);
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            active.delete(this);
            const state = this[STATE];
            if (state?.strip) releasePreviews(state.strip);
            state?.releaseGesture?.();
            return removed?.apply(this, arguments);
        };
    },
});
