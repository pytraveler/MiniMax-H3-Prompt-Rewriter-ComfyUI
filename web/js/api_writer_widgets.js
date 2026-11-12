import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { addDurationLimit, applyDurationCeiling } from "./duration_limit.js";
import { addApiWidgetMigration, moveApiControls } from "./api_writer_migration.js";
import { apiInputSwitches } from "./api_writer_slots.js";
import { modelControls } from "./api_writer_models.js";
import { previewFor, releasePreviews } from "./reference_previews.js";
import { addSlotSwitches } from "./slot_switches.js";
import { chipElement, installStripStyle, numberEntries, placeDragged, readInstructions,
    slotNumber, slots, stripHeight, writeInstruction } from "./reference_strip.js";
import { MARGIN, graphChanged, installBaseStyle, installStyle, onRefresh, ratiosHeight, refresh,
    renderRatios, renderSegments, repaintOn, replaceWithDom, setWidgetValue,
    showWidget, widgetNamed, widgetValues } from "./mmx_controls.js";

const NODE = "MiniMaxH3ApiUniversalWriter";
const STATE = "__minimaxH3ApiWriter";
const LAYOUT = "reference_layout";
const INSTRUCTIONS = "reference_instructions";
const ROLES = ["Picture", "Subject", "Video"];
const REF_TYPES = "IMAGE,VIDEO,AUDIO,H3_REWRITER_REFMOD";
const DESCRIPTIONS = "reference_descriptions";
const OPTION_WIDGETS = ["max_tokens", "response_format", "temperature", "timeout_seconds", "auto_download_guide",
    "video_sample_seconds", "video_max_seconds", "batch_video_fps", "audio_sample_rate", "repair_once"];
const profiles = new Map();
const activeNodes = new Set();
let profileProblem = "Connection details are loading.";
let refreshInputSwitches;

function layoutOf(node) {
    try {
        const parsed = JSON.parse(widgetNamed(node, LAYOUT)?.value || "{}");
        return { order: Array.isArray(parsed.order) ? parsed.order : [],
            off: Array.isArray(parsed.off) ? parsed.off : [], roles: parsed.roles || {},
            sound: Array.isArray(parsed.sound) ? parsed.sound : [] };
    } catch {
        return { order: [], off: [], roles: {}, sound: [] };
    }
}

function entriesOf(node) {
    const layout = layoutOf(node);
    const connected = new Map(slots(node, "ref_").filter((one) => one.connected).map((one) => [one.name, one]));
    const order = [...new Set(layout.order.filter((name) => connected.has(name)))];
    order.push(...[...connected.keys()].filter((name) => !order.includes(name)).sort((a, b) => slotNumber(a) - slotNumber(b)));
    return numberEntries(order.map((name) => {
        const slot = connected.get(name);
        if (!slot.refmod) {
            const input = node.inputs.find((one) => one.name === name);
            const links = node.graph?.links;
            const link = links?.get ? links.get(input?.link) : links?.[input?.link];
            const source = node.graph?.getNodeById?.(link?.origin_id);
            const type = String(source?.outputs?.[link?.origin_slot]?.type || "").toUpperCase();
            if (["IMAGE", "VIDEO", "AUDIO"].includes(type)) slot.kind = type.toLowerCase();
        }
        const choices = slot.refmod ? ROLES.slice(0, 2) : ROLES;
        const role = slot.kind === "audio" ? "Audio" : slot.kind === "video" ? "Video"
            : choices.includes(layout.roles[name]) ? layout.roles[name] : "Picture";
        return { name, kind: slot.kind, refmod: slot.refmod, on: !layout.off.includes(name), role,
            sound: slot.kind === "video" && !slot.refmod && layout.sound.includes(name) };
    }), widgetNamed(node, "task")?.value === "Ref2VA");
}

function descriptionsOf(node) {
    try {
        const value = JSON.parse(widgetNamed(node, DESCRIPTIONS)?.value || "{}");
        return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch { return {}; }
}

function changeLayout(node, change) {
    const layout = layoutOf(node);
    change(layout);
    widgetNamed(node, LAYOUT).value = JSON.stringify(layout);
    refresh(node);
    graphChanged();
}

function toggleReference(node, name) {
    changeLayout(node, (layout) => {
        if (layout.off.includes(name)) layout.off = layout.off.filter((slot) => slot !== name);
        else layout.off.push(name);
    });
}

function editInstruction(node, entry, withClear = false) {
    const dialog = document.createElement("dialog");
    dialog.className = "mmx-api-instruction";
    const title = document.createElement("div");
    title.textContent = `${entry.role} ${entry.number}: instructions and description`;
    const box = document.createElement("textarea");
    box.value = readInstructions(node, INSTRUCTIONS)[entry.name]?.text || "";
    box.placeholder = "Keep this character's clothes, use only the lighting from this image...";
    const label = document.createElement("div");
    label.textContent = withClear
        ? entry.kind === "audio" ? "Sound description (required; this checkpoint receives text for sound)"
            : "Optional description of this reference; saved RefMod descriptions are included."
        : "Description of this reference (required in descriptions mode; RefMods can use their saved description)";
    const description = document.createElement("textarea");
    const current = descriptionsOf(node)[entry.name];
    description.value = typeof current === "string" ? current : current?.text || "";
    description.placeholder = "Describe the visible scene, motion, or the sound actually present.";
    const soundLabel = document.createElement("div");
    soundLabel.textContent = withClear ? "Clip sound description (required when sound is enabled)"
        : "Clip sound description (required when sound is included in descriptions mode)";
    const sound = document.createElement("textarea");
    sound.value = typeof current === "object" ? current?.sound || "" : "";
    const hasSound = entry.kind === "video" && !entry.refmod;
    const save = document.createElement("button");
    save.textContent = "Save instructions";
    save.onclick = () => {
        writeInstruction(node, INSTRUCTIONS, entry.name, box.value, true);
        const map = descriptionsOf(node);
        if (description.value.trim() || sound.value.trim()) map[entry.name] = { text: description.value, sound: sound.value };
        else delete map[entry.name];
        widgetNamed(node, DESCRIPTIONS).value = JSON.stringify(map);
        refresh(node);
        graphChanged();
        dialog.close();
    };
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    cancel.onclick = () => dialog.close();
    dialog.append(title, box, label, description);
    if (hasSound) dialog.append(soundLabel, sound);
    const actions = document.createElement("div");
    actions.className = "mmx-api-instruction-actions";
    if (withClear) {
        const clear = document.createElement("button");
        clear.textContent = "Clear";
        clear.onclick = () => { box.value = ""; description.value = ""; sound.value = ""; box.focus(); };
        actions.appendChild(clear);
    }
    actions.append(save, cancel);
    dialog.appendChild(actions);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    document.body.appendChild(dialog);
    dialog.showModal();
    box.focus();
}

function startGesture(node, chip, role, entry, event, allowRoleChange = true) {
    if (event.button !== 0 || event.target.closest("button")) return;
    event.preventDefault();
    event.stopPropagation();
    const strip = chip.parentElement;
    const startX = event.clientX, startY = event.clientY;
    const onLabel = event.clientY <= role.getBoundingClientRect().bottom;
    let dragged = false;
    const move = (moved) => {
        if (!dragged && Math.hypot(moved.clientX - startX, moved.clientY - startY) < 6) return;
        dragged = true;
        node[STATE].dragging = true;
        chip.classList.add("mmx-dragging");
        placeDragged(strip, chip, moved.clientX, moved.clientY);
    };
    const release = () => {
        window.removeEventListener("pointermove", move, true);
        window.removeEventListener("pointerup", finish, true);
        window.removeEventListener("pointercancel", finish, true);
        node[STATE].dragging = false;
        chip.classList.remove("mmx-dragging");
        delete node[STATE].releaseGesture;
    };
    const finish = (finished) => {
        release();
        if (!node.graph || finished.type === "pointercancel") return refresh(node);
        changeLayout(node, (layout) => {
            if (dragged) layout.order = [...strip.children].map((child) => child.dataset.slot).filter(Boolean);
            else if (onLabel && entry.kind === "video" && !entry.refmod) {
                if (layout.sound.includes(entry.name)) layout.sound = layout.sound.filter((name) => name !== entry.name);
                else layout.sound.push(entry.name);
            } else if (onLabel && entry.kind === "image" && allowRoleChange) {
                const choices = entry.refmod ? ROLES.slice(0, 2) : ROLES;
                layout.roles[entry.name] = choices[(choices.indexOf(entry.role) + 1) % choices.length];
            }
            else if (onLabel) return;
            else if (layout.off.includes(entry.name)) layout.off = layout.off.filter((name) => name !== entry.name);
            else layout.off.push(entry.name);
        });
    };
    node[STATE].releaseGesture = release;
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", finish, true);
    window.addEventListener("pointercancel", finish, true);
}

function syncSlots(node) {
    const state = node[STATE];
    if (!state || state.syncing) return;
    state.syncing = true;
    try {
        const refs = (node.inputs || []).filter((one) => /^ref_(?:[0-9]|1[01])$/.test(one.name));
        for (const input of refs) input.type = REF_TYPES;
        const free = refs.filter((one) => one.link == null);
        const keep = free[0];
        for (let index = (node.inputs?.length || 0) - 1; index >= 0; index--) {
            const input = node.inputs[index];
            if (free.includes(input) && input !== keep) node.removeInput(index);
        }
        if (!keep && refs.length < 12) {
            const names = new Set(refs.map((one) => one.name));
            const index = Array.from({ length: 12 }, (_, position) => position).find((position) => !names.has(`ref_${position}`));
            node.addInput(`ref_${index}`, REF_TYPES);
        }
    } finally {
        state.syncing = false;
    }
}

function render(node) {
    const state = node[STATE];
    if (!state || state.dragging) return;
    refreshInputSwitches?.();
    state.models.update();
    const entries = entriesOf(node);
    state.count = entries.length;
    releasePreviews(state.strip);
    state.strip.replaceChildren();
    for (const entry of entries) {
        const { chip, role } = chipElement(entry, false, previewFor(node, entry.name, entry.kind));
        const instruction = document.createElement("button");
        instruction.className = "mmx-instr";
        instruction.type = "button";
        const current = readInstructions(node, INSTRUCTIONS)[entry.name]?.text;
        instruction.classList.toggle("mmx-set", !!current);
        instruction.textContent = "+ instr";
        instruction.title = current || "Instructions and an optional text description for this reference";
        instruction.onclick = (event) => { event.stopPropagation(); editInstruction(node, entry); };
        chip.appendChild(instruction);
        chip.addEventListener("pointerdown", (event) => startGesture(node, chip, role, entry, event));
        state.strip.appendChild(chip);
    }
    if (!entries.length) state.strip.textContent = "Connect images, video, audio or RefMods to ref_0 and the next free slot.";
    renderSegments(state.tasks, widgetValues(node, "task").map((name) => {
        const pictures = entries.filter((entry) => entry.on && entry.role === "Picture").length;
        const wanted = { I2VA: 1, FL2VA: 2, L2VA: 1 }[name];
        const why = name === "Ref2VA" && !entries.some((entry) => entry.on) ? "Connect and enable at least one reference."
            : wanted !== undefined && pictures !== wanted ? `${name} needs ${wanted} enabled Picture reference(s); the strip has ${pictures}.` : "";
        return { label: name, on: widgetNamed(node, "task")?.value === name, shut: !!why,
            title: why || name, pick: () => setWidgetValue(node, "task", name) };
    }));
    renderRatios(node, state.ratios, "resolution");
    applyDurationCeiling(node);
    const profile = profiles.get(widgetNamed(node, "profile_id")?.value);
    const model = state.models.selected();
    const modelId = widgetNamed(node, "model")?.value || profile?.model;
    const task = widgetNamed(node, "task")?.value;
    const enabled = entries.filter((entry) => entry.on);
    const described = widgetNamed(node, "media_mode")?.value === "descriptions";
    const repeating = widgetNamed(node, "repeat_last")?.value;
    const bypassing = widgetNamed(node, "bypass")?.value;
    const profileInput = node.inputs?.find((input) => input.name === "profile_id");
    const wiredProfile = profileInput?.link != null;
    const recipient = wiredProfile ? "Connection ID comes from a wire; its profile is selected during execution."
        : profile ? `${profile.name} / ${modelId}\n${profile.base_url}` : profileProblem;
    const contents = task === "T2VA" ? "System/guide + raw prompt. Connected references are ignored in T2VA."
        : described ? `System/guide + raw prompt + descriptions of ${enabled.length} reference(s). No images or audio are sent. Missing descriptions stop execution.`
        : `System/guide + raw prompt + ${enabled.length} reference(s). JPEG max side ${widgetNamed(node, "image_max_side")?.value || 1024}px. Video: sampled frames, max ${widgetNamed(node, "video_max_frames")?.value || 8} per clip with timestamps. Audio: mono WAV; clip sound only when its label is purple.`;
    const needed = new Set(enabled.map((entry) => entry.role === "Audio" ? "audio" : "image"));
    if (enabled.some((entry) => entry.sound)) needed.add("audio");
    const missing = [...needed].filter((kind) => profile && !(model?.modalities || (widgetNamed(node, "model")?.value ? ["text"] : profile.modalities)).includes(kind));
    const warning = !described && !wiredProfile && profile && task !== "T2VA" && missing.length
        ? `\nThis profile has no ${missing.join("/")} input. Select a suitable profile or explicitly use descriptions.` : "";
    state.recipient.textContent = bypassing ? "Bypass: pass the original prompt and enabled original references through. No API request or media preparation."
        : `${recipient}\n${repeating ? "Repeat: return a kept prompt; if none exists yet, make one API request." : contents}${warning}`;
    state.recipient.title = state.recipient.textContent;
    node.setDirtyCanvas?.(true, true);
}

async function pullProfiles() {
    try {
        const response = await api.fetchApi("/minimax_h3_rewriter/api/profiles");
        const listing = response.ok ? await response.json() : null;
        profiles.clear();
        for (const profile of listing?.profiles || []) profiles.set(profile.id, profile);
        profileProblem = "Connection details unavailable. Open API providers to configure this connection.";
    } catch {
        profileProblem = "Connection details unavailable. The node will check the profile before sending.";
    }
    for (const node of activeNodes) {
        updateProfileChoices(node);
        node[STATE]?.models.update(true);
        render(node);
    }
}

function updateProfileChoices(node) {
    const widget = widgetNamed(node, "profile_id");
    if (widget?.options) widget.options.values = [...new Set([...profiles.keys(), widget.value].filter(Boolean))];
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle("minimax-h3-api-writer-style", `
.mmx-api-refs { display: flex; flex-direction: column; gap: 3px; width: 100%; height: 100%;
    font: 11px system-ui, sans-serif; color: var(--input-text, #ddd); overflow: hidden; }
.mmx-api-refs .mmx-instr { padding: 0; border: 0; border-top: 1px solid rgba(0, 0, 0, 0.35);
    border-radius: 0; font-family: inherit; width: 100%; min-width: 0; white-space: nowrap; }
.mmx-api-hint { flex: 0 0 12px; font-size: 9px; line-height: 12px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; color: var(--descrip-text, #aaa); }
.mmx-api-recipient { white-space: pre-line; font: 11px system-ui, sans-serif; overflow: hidden; color: var(--input-text, #ddd); }
.mmx-api-instruction { max-width: 420px; background: var(--comfy-menu-bg, #353535); color: var(--input-text, #ddd); border: 1px solid #666; border-radius: 6px; }
.mmx-api-instruction textarea { display: block; width: 380px; height: 120px; margin: 10px 0; }
.mmx-api-instruction-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.mmx-api-models { display: flex; flex-direction: column; gap: 3px; width: 100%; height: 100%; font: 11px system-ui, sans-serif; }
.mmx-api-model-row { display: flex; gap: 6px; min-height: 25px; }
.mmx-api-model-row select { flex: 1 1 0; min-width: 0; }
.mmx-api-model-row select, .mmx-api-model-row button { color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b); border: 1px solid var(--border-color, #555); border-radius: 5px; font: inherit; }
.mmx-api-model-row button { padding: 0 8px; cursor: pointer; }
.mmx-api-model-note { color: var(--descrip-text, #aaa); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`);
    const holder = document.createElement("div");
    holder.className = "mmx-api-refs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-api-hint";
    hint.textContent = "Drag to reorder; image label: Picture/Subject/Video; video label: sound; below: on/off.";
    holder.append(strip, hint);
    const tasks = document.createElement("div");
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";
    const recipient = document.createElement("div");
    recipient.className = "mmx-api-recipient";
    const models = modelControls(node);
    node[STATE] = { strip, recipient, tasks, ratios, models, count: 0, dragging: false, syncing: false };
    activeNodes.add(node);
    updateProfileChoices(node);
    for (const name of ["profile_id", "task", "image_max_side", "repeat_last", "bypass", "media_mode", "video_max_frames"]) {
        const widget = widgetNamed(node, name);
        if (!widget) continue;
        const previous = widget.callback;
        widget.callback = function () {
            const result = previous?.apply(this, arguments);
            refresh(node);
            return result;
        };
    }
    showWidget(node, INSTRUCTIONS, false);
    showWidget(node, DESCRIPTIONS, false);
    for (const name of OPTION_WIDGETS) showWidget(node, name, false);
    replaceWithDom(node, LAYOUT, "minimaxh3_api_references", holder,
        () => stripHeight(node, node[STATE].count, 12, 3, MARGIN));
    replaceWithDom(node, "task", "minimaxh3_api_task", tasks, () => 26);
    replaceWithDom(node, "resolution", "minimaxh3_api_ratio", ratios, () => ratiosHeight(node, "resolution"));
    replaceWithDom(node, "model", "minimaxh3_api_model", models.holder, () => 43);
    node.addDOMWidget("api_recipient", "minimaxh3_api_recipient", recipient, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 100, getMaxHeight: () => 100,
    });
    moveApiControls(node);
    onRefresh(node, () => {
        render(node);
        queueMicrotask(() => syncSlots(node));
    });
    render(node);
    queueMicrotask(() => syncSlots(node));
}

export { layoutOf, entriesOf, toggleReference, editInstruction, startGesture, syncSlots };

app.registerExtension({
    name: "minimax_h3_rewriter.api_writer_widgets",
    setup() {
        refreshInputSwitches = apiInputSwitches(activeNodes,
            (node, name) => !layoutOf(node).off.includes(name), toggleReference);
        pullProfiles();
        window.addEventListener("minimax-h3-api-profiles-changed", pullProfiles);
    },
    afterConfigureGraph() { pullProfiles(); },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        addDurationLimit(nodeType, nodeData);
        repaintOn(nodeType, build);
        addSlotSwitches(nodeType, {
            prefixes: ["ref_"],
            enabled: (node, name) => !layoutOf(node).off.includes(name),
            toggle: toggleReference,
        });
        addApiWidgetMigration(nodeType);
        const previous = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            activeNodes.delete(this);
            return previous?.apply(this, arguments);
        };
    },
});
