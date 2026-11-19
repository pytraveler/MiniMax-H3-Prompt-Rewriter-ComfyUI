import { app } from "../../scripts/app.js";
import { openApiProviders } from "./api_profiles_dialog.js";
import { modelControls } from "./api_writer_models.js";
import { apiInputSwitches } from "./api_writer_slots.js";
import { layoutOf, startGesture, toggleReference } from "./api_writer_widgets.js";
import { MARGIN, installBaseStyle, installStyle, onRefresh, repaintOn, replaceWithDom,
    renderSegments, setWidgetValue, showWidget, widgetNamed, widgetValues } from "./mmx_controls.js";
import { previewFor, releasePreviews } from "./reference_previews.js";
import { chipElement, installStripStyle, numberEntries, readInstructions, slotNumber,
    slots, stripHeight, writeInstruction } from "./reference_strip.js";
import { addSlotSwitches } from "./slot_switches.js";

const NODE = "MiniMaxH3ImagePromptWriter";
const API_WRITER = "API Writer";
const CONTEXT_IR = "MiniMax Context IR";
const LOCAL_GGUF = "Local GGUF";
const STATE = "__minimaxH3ApiWriter";
const LAYOUT = "reference_layout";
const INSTRUCTIONS = "reference_instructions";
const activeNodes = new Set();
let refreshSwitches;

function enabled(node, name) {
    return !layoutOf(node).off.includes(name);
}

function editInstructions(node, entry, current) {
    const state = node[STATE];
    if (state.dialog?.open) return;
    const dialog = document.createElement("dialog");
    dialog.className = "mmx-image-instruction";
    const form = document.createElement("form");
    form.method = "dialog";
    const label = document.createElement("label");
    label.textContent = entry.on ? `Picture ${entry.number} instructions` : `${entry.name} instructions`;
    const field = document.createElement("textarea");
    field.value = current || "";
    field.placeholder = "Use the fabric color from this picture.";
    label.append(field);
    const actions = document.createElement("div");
    for (const [value, labelText] of [["save", "Save instructions"], ["cancel", "Cancel"]]) {
        const button = document.createElement("button");
        button.value = value;
        button.textContent = labelText;
        actions.append(button);
    }
    form.append(label, actions);
    dialog.append(form);
    dialog.addEventListener("close", () => {
        if (dialog.returnValue === "save" && node.graph) writeInstruction(node, INSTRUCTIONS, entry.name, field.value, true);
        dialog.remove();
        delete state.dialog;
    }, { once: true });
    state.dialog = dialog;
    document.body.append(dialog);
    dialog.showModal();
    field.focus();
}

function render(node) {
    const state = node[STATE];
    if (!state || state.dragging) return;
    const layout = layoutOf(node);
    const connected = slots(node, "ref_").filter((one) => one.connected).sort((a, b) => slotNumber(a.name) - slotNumber(b.name));
    const byName = new Map(connected.map((one) => [one.name, one]));
    const order = [...new Set([...layout.order, ...byName.keys()])].filter((name) => byName.has(name));
    const entries = numberEntries(order.map((name) => ({
        name, role: "Picture", kind: "image", on: !layout.off.includes(name), sound: false,
    })), false);
    const instructions = readInstructions(node, INSTRUCTIONS);
    state.count = entries.length;
    releasePreviews(state.strip);
    state.strip.replaceChildren();
    state.strip.hidden = !entries.length;
    for (const entry of entries) {
        const { chip, role } = chipElement(entry, false, previewFor(node, entry.name, "image"));
        if (entry.number === 1) role.textContent = "base";
        chip.title = entry.on ? `Picture ${entry.number}` : "Disabled reference";
        const instruction = document.createElement("button");
        const current = instructions[entry.name]?.text;
        instruction.type = "button";
        instruction.className = "mmx-instr" + (current ? " mmx-set" : "");
        instruction.textContent = "+ instr";
        instruction.title = current || "Add editing instructions for this picture";
        instruction.onclick = () => editInstructions(node, entry, current);
        chip.append(instruction);
        chip.addEventListener("pointerdown", (event) => startGesture(node, chip, role, entry, event, false));
        state.strip.append(chip);
    }
    const backend = widgetNamed(node, "backend")?.value;
    renderSegments(state.backends, widgetValues(node, "backend").map((label) => ({
        label, on: backend === label,
        title: label === API_WRITER ? "Expand with the selected API profile and model"
            : label === LOCAL_GGUF ? "Expand with a local GGUF model"
            : label === CONTEXT_IR ? "Send the prompt and enabled pictures to MiniMax"
                : "Expand with the connected prompt model",
        pick: () => setWidgetValue(node, "backend", label),
    })));
    const cloud = backend === CONTEXT_IR;
    const usingApi = backend === API_WRITER;
    const usingGguf = backend === LOCAL_GGUF;
    const edits = entries.some((entry) => entry.on);
    state.providers.hidden = !cloud;
    state.models.holder.hidden = !usingApi;
    showWidget(node, "profile_id", cloud);
    showWidget(node, "image_max_side", usingApi || (usingGguf && edits));
    showWidget(node, "writer_model", usingGguf && !edits);
    showWidget(node, "caption_model", usingGguf && edits);
    showWidget(node, "keep_model_loaded", usingGguf);
    if (usingApi) state.models.update(state.backend !== backend);
    state.backend = backend;
    state.hint.textContent = edits
        ? "Image Edit. Drag to reorder, click a picture to switch it off."
        : "Text-to-Image. Connect images to edit.";
    state.hint.title = state.hint.textContent + (cloud ? " Context IR sends enabled pictures to MiniMax."
        : usingApi ? " API Writer sends enabled pictures to the selected provider." : "");
    refreshSwitches?.();
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle("minimax-h3-image-writer-style", `
.mmx-image-writer { display: flex; flex-direction: column; gap: 4px; height: 100%; font: 11px system-ui; }
.mmx-image-writer > * { flex: 0 0 auto; }
.mmx-image-writer .mmx-api-models { height: auto; }
.mmx-image-writer [hidden] { display: none !important; }
.mmx-image-writer .mmx-instr { padding: 0; width: 100%; border: 0; border-top: 1px solid #0005; border-radius: 0; }
.mmx-image-writer-row { display: flex; gap: 6px; min-height: 26px; }
.mmx-image-writer-row .mmx-seg-row { flex: 1; }
.mmx-image-writer button, .mmx-image-instruction button { cursor: pointer; }
.mmx-image-writer-hint { color: var(--descrip-text, #aaa); height: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mmx-image-instruction { width: min(380px, 85vw); border-radius: 6px; border: 1px solid var(--border-color, #555);
    color: var(--input-text, #ddd); background: var(--comfy-menu-bg, #353535); font: 12px system-ui; }
.mmx-image-instruction textarea { display: block; width: 100%; min-height: 110px; box-sizing: border-box;
    margin: 10px 0; background: var(--comfy-input-bg, #222); color: inherit; }
.mmx-image-instruction form > div { display: flex; justify-content: flex-end; gap: 8px; }
`);
    const panel = document.createElement("div");
    panel.className = "mmx-image-writer";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-image-writer-hint";
    const row = document.createElement("div");
    row.className = "mmx-image-writer-row";
    const backends = document.createElement("div");
    const providers = document.createElement("button");
    providers.type = "button";
    providers.textContent = "API providers";
    providers.onclick = () => openApiProviders(node);
    row.append(backends, providers);
    const models = modelControls(node, { chooseProfile: true });
    panel.append(strip, hint, row, models.holder);
    const state = { strip, hint, backends, providers, models, count: 0 };
    node[STATE] = state;
    activeNodes.add(node);
    showWidget(node, LAYOUT, false);
    showWidget(node, INSTRUCTIONS, false);
    showWidget(node, "model", false);
    onRefresh(node, () => render(node));
    replaceWithDom(node, "backend", "minimaxh3_image_writer", panel, () =>
        44 + (state.backend === API_WRITER ? 72 : 0)
        + (state.count ? stripHeight(node, state.count, 0, 0, MARGIN) + 4 : 0));
    render(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.image_writer",
    setup() {
        refreshSwitches = apiInputSwitches(activeNodes, enabled, toggleReference,
            (name) => /^references\.(ref_(?:[0-9]|1[0-5]))$/.exec(name)?.[1]);
        window.addEventListener("minimax-h3-api-profiles-changed", () => {
            for (const node of activeNodes) {
                if (node[STATE]?.backend === API_WRITER) node[STATE].models.update(true);
            }
        });
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        repaintOn(nodeType, build);
        addSlotSwitches(nodeType, { prefixes: ["ref_"], enabled, toggle: toggleReference });
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            const state = this[STATE];
            state?.releaseGesture?.();
            state?.dialog?.close();
            state?.dialog?.remove();
            releasePreviews(state?.strip);
            activeNodes.delete(this);
            return removed?.apply(this, arguments);
        };
    },
});
