import { app } from "../../scripts/app.js";
import { addSlotSwitches } from "./slot_switches.js";


const NODE = "MiniMaxH3UniversalWriter";

const LAYOUT = "reference_layout";
const TASK = "task";
const RESOLUTION = "resolution";
const DURATION = "duration";

const PREFIX = "ref_";
const REF_TASK = "Ref2VA";

const DURATION_PROPERTY = "max_duration";
const DURATION_PROPERTY_DEFAULT = 30;
const DURATION_MIN = 0.1;
const DURATION_CEILING = 600;

const IMAGE_ROLES = ["Picture", "Subject", "Video"];

const SHORT = { Picture: "pic", Subject: "subj", Video: "vid", Audio: "aud" };
const COLOUR = {
    Picture: "#3B7DD8",
    Subject: "#C98A2E",
    Video: "#3F9E5A",
    Audio: "#8A54C8",
};

const CHIP_W = 52;
const CHIP_H = 58;
const CHIP_ROLE_H = 18;
const CHIP_SLOT_H = 16;
const CHIP_GAP = 4;

const HINT_H = 12;
const HINT_GAP = 3;
const HINT_IMAGES = "drag to reorder - click the label to change it - click below to switch off";
const HINT_PLAIN = "drag to reorder - click a square below its label to switch it off";

const TASKS_H = 26;
const RATIOS_H = 38;
const RATIO_BOX_W = 28;
const RATIO_BOX_H = 22;

const DRAG_SLOP = 6;

const MARGIN = 6;

function boxed(content) {
    return content + 2 * MARGIN;
}

const STYLE_ID = "minimax-h3-universal-style";
const STATE = "__minimaxH3Universal";

const STYLE = `
.mmx-refs { display: flex; flex-direction: column; gap: ${HINT_GAP}px;
    width: 100%; height: 100%; overflow: hidden;
    font-family: system-ui, sans-serif; }
.mmx-strip { flex: 1 1 auto; display: flex; flex-wrap: wrap;
    align-content: flex-start; gap: ${CHIP_GAP}px; overflow: hidden; }
.mmx-hint { flex: 0 0 ${HINT_H}px; font-size: 9px; line-height: ${HINT_H}px;
    color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.mmx-chip { flex: 0 0 auto; width: ${CHIP_W}px; height: ${CHIP_H}px;
    border-radius: 5px; display: flex; flex-direction: column; overflow: hidden;
    cursor: grab; user-select: none; touch-action: none;
    border: 1px solid rgba(0, 0, 0, 0.45);
    transition: opacity 0.12s, transform 0.12s, box-shadow 0.12s; }
.mmx-chip-role { flex: 0 0 ${CHIP_ROLE_H}px; font-size: 11px;
    line-height: ${CHIP_ROLE_H}px; text-align: center; letter-spacing: 0.03em;
    color: #fff; background: rgba(0, 0, 0, 0.3); }
.mmx-chip-num { flex: 1 1 auto; font-size: 15px;
    line-height: ${CHIP_H - CHIP_ROLE_H - CHIP_SLOT_H}px; text-align: center;
    font-weight: 600; color: #fff; }
/* The number says where a square sits in the block, so it renumbers the moment
   anything moves -- which would make a reorder of two squares of the same kind
   invisible. The slot it is plugged into is what stays with it. */
.mmx-chip-slot { flex: 0 0 ${CHIP_SLOT_H}px; font-size: 9px;
    line-height: ${CHIP_SLOT_H}px; text-align: center; letter-spacing: 0.02em;
    color: rgba(255, 255, 255, 0.75); background: rgba(0, 0, 0, 0.22); }
.mmx-chip.mmx-off { opacity: 0.35; }
.mmx-chip.mmx-dragging { cursor: grabbing; transform: scale(1.1);
    box-shadow: 0 3px 10px rgba(0, 0, 0, 0.55); }
.mmx-note { font-size: 11px; line-height: ${CHIP_H}px;
    color: var(--descrip-text, #999); font-family: system-ui, sans-serif; }

.mmx-tasks { display: flex; width: 100%; height: 100%; overflow: hidden;
    border: 1px solid var(--border-color, #4e4e4e); border-radius: 6px;
    font-family: system-ui, sans-serif; }
.mmx-task { flex: 1 1 0; min-width: 0; display: flex; align-items: center;
    justify-content: center; font-size: 10px; letter-spacing: 0.02em;
    cursor: pointer; user-select: none; touch-action: none;
    color: var(--input-text, #ddd); background: var(--comfy-menu-bg, #353535);
    border-left: 1px solid var(--border-color, #4e4e4e); }
.mmx-task:first-child { border-left: 0; }
.mmx-task.mmx-on { background: #3B7DD8; color: #fff; font-weight: 600; }
.mmx-task.mmx-unavailable { opacity: 0.4; cursor: not-allowed; }
.mmx-task.mmx-on.mmx-unavailable { background: #8A3B3B; opacity: 1; }

.mmx-ratios { display: flex; gap: ${CHIP_GAP}px; width: 100%; height: 100%;
    overflow: hidden; font-family: system-ui, sans-serif; }
.mmx-ratio { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 3px; cursor: pointer;
    user-select: none; touch-action: none; border-radius: 5px;
    border: 1px solid transparent; }
.mmx-ratio.mmx-on { border-color: #3B7DD8; background: rgba(59, 125, 216, 0.16); }
/* Without flex: 0 0 auto these are shrunk to fit and every ratio draws the
   same rectangle, which is the one thing the control exists to show. */
.mmx-box { flex: 0 0 auto; box-sizing: border-box;
    border: 1.5px solid var(--descrip-text, #999); border-radius: 2px; }
.mmx-ratio.mmx-on .mmx-box { border-color: #7FB2F5;
    background: rgba(127, 178, 245, 0.28); }
.mmx-ratio-label { flex: 0 0 auto; font-size: 9px; line-height: 11px;
    color: var(--descrip-text, #999); }
.mmx-ratio.mmx-on .mmx-ratio-label { color: var(--input-text, #ddd); }
`;

function installStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = STYLE;
    document.head.appendChild(style);
}


function widgetNamed(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function readLayout(node) {
    let parsed = {};
    try {
        parsed = JSON.parse(widgetNamed(node, LAYOUT)?.value || "{}") || {};
    } catch (error) {
        parsed = {};
    }
    const names = (key) => {
        const found = parsed[key];
        return Array.isArray(found) ? found.filter((n) => typeof n === "string") : [];
    };
    return {
        order: names("order"),
        off: names("off"),
        roles: parsed.roles && typeof parsed.roles === "object" ? { ...parsed.roles } : {},
    };
}

function writeLayout(node, layout) {
    const kept = {};
    if (layout.order.length) kept.order = layout.order;
    if (layout.off.length) kept.off = layout.off;
    const settled = new Set(
        slots(node).filter((s) => s.connected && s.kind !== "image").map((s) => s.name)
    );
    const roles = {};
    for (const [name, role] of Object.entries(layout.roles || {})) {
        if (settled.has(name)) continue;
        if (IMAGE_ROLES.includes(role) && role !== IMAGE_ROLES[0]) roles[name] = role;
    }
    if (Object.keys(roles).length) kept.roles = roles;

    const widget = widgetNamed(node, LAYOUT);
    if (widget) widget.value = JSON.stringify(kept);
    refresh(node);
}


function linkKind(node, link) {
    const links = node.graph?.links;
    const info = links?.get ? links.get(link) : links?.[link];
    const type = String(info?.type || "").toUpperCase();
    if (type.includes("AUDIO")) return "audio";
    if (type.includes("VIDEO")) return "video";
    return "image";
}

function slots(node) {
    const found = [];
    for (const input of node.inputs || []) {
        const name = String(input.name || "");
        const tail = name.slice(name.lastIndexOf(".") + 1);
        if (!tail.startsWith(PREFIX)) continue;
        const link = input.link;
        const connected = link !== null && link !== undefined;
        found.push({ name: tail, connected, kind: connected ? linkKind(node, link) : "image" });
    }
    return found;
}

function slotNumber(name) {
    const tail = name.slice(name.lastIndexOf("_") + 1);
    return /^\d+$/.test(tail) ? Number(tail) : 0;
}

function arranged(node) {
    const layout = readLayout(node);
    const connected = new Map();
    for (const slot of slots(node)) {
        if (slot.connected) connected.set(slot.name, slot);
    }

    const names = layout.order.filter((name) => connected.has(name));
    const known = new Set(names);
    names.push(
        ...[...connected.keys()]
            .filter((name) => !known.has(name))
            .sort((a, b) => slotNumber(a) - slotNumber(b))
    );

    const counts = {};
    return names.map((name) => {
        const slot = connected.get(name);
        let role = "Audio";
        if (slot.kind === "video") role = "Video";
        else if (slot.kind === "image") {
            role = IMAGE_ROLES.includes(layout.roles[name]) ? layout.roles[name] : IMAGE_ROLES[0];
        }
        const on = !layout.off.includes(name);
        if (on) counts[role] = (counts[role] || 0) + 1;
        return { name, kind: slot.kind, role, on, number: on ? counts[role] : null };
    });
}

function anyEnabled(node) {
    return arranged(node).some((entry) => entry.on);
}


function replaceWithDom(node, name, type, element, height) {
    const index = node.widgets?.findIndex((w) => w.name === name) ?? -1;
    if (index < 0) return null;

    const original = node.widgets[index];
    const held = { value: original.value };

    const widget = node.addDOMWidget(name, type, element, {
        hideOnZoom: false,
        margin: MARGIN,
        hideInPanel: true,
        getValue: () => held.value,
        setValue: (next) => {
            held.value = next;
            refresh(node);
        },
        getMinHeight: () => boxed(height()),
        getMaxHeight: () => boxed(height()),
    });
    widget.tooltip = original.tooltip;
    widget.options.values = original.options?.values ?? [];

    const appended = node.widgets.indexOf(widget);
    if (appended >= 0) node.widgets.splice(appended, 1);
    node.widgets.splice(index, 1, widget);
    return widget;
}

function setWidgetValue(node, name, value) {
    const widget = widgetNamed(node, name);
    if (!widget || widget.value === value) return;
    widget.value = value;
    widget.callback?.(value, app.canvas, node);
    refresh(node);
}


function stripHeight(node) {
    const count = node[STATE]?.chipCount ?? 0;
    const usable = Math.max(node.size?.[0] ?? 300, 120) - 2 * MARGIN;
    const perRow = Math.max(1, Math.floor((usable + CHIP_GAP) / (CHIP_W + CHIP_GAP)));
    const rows = Math.max(1, Math.ceil(count / perRow));
    const hint = count ? HINT_H + HINT_GAP : 0;
    return rows * CHIP_H + (rows - 1) * CHIP_GAP + hint;
}

function placeDragged(strip, chip, x, y) {
    let before = null;
    for (const other of strip.children) {
        if (other === chip || !other.dataset?.slot) continue;
        const rect = other.getBoundingClientRect();
        if (y < rect.top || (y <= rect.bottom && x < rect.left + rect.width / 2)) {
            before = other;
            break;
        }
    }
    if (before) {
        if (before.previousElementSibling !== chip) strip.insertBefore(chip, before);
    } else if (strip.lastElementChild !== chip) {
        strip.appendChild(chip);
    }
}

function commitOrder(node, strip) {
    const layout = readLayout(node);
    layout.order = [...strip.children]
        .filter((child) => child.dataset?.slot)
        .map((child) => child.dataset.slot);
    writeLayout(node, layout);
}

function cycleRole(node, name) {
    const layout = readLayout(node);
    const current = Math.max(0, IMAGE_ROLES.indexOf(layout.roles[name]));
    layout.roles[name] = IMAGE_ROLES[(current + 1) % IMAGE_ROLES.length];
    writeLayout(node, layout);
}

function toggleSlot(node, name) {
    const layout = readLayout(node);
    const at = layout.off.indexOf(name);
    if (at >= 0) layout.off.splice(at, 1);
    else layout.off.push(name);
    writeLayout(node, layout);
}

function isSlotEnabled(node, name) {
    return !readLayout(node).off.includes(name);
}

function beginGesture(node, chip, role, entry, event) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();

    const strip = chip.parentElement;
    const startX = event.clientX;
    const startY = event.clientY;
    const onLabel = event.clientY <= role.getBoundingClientRect().bottom;
    let dragging = false;

    const move = (moved) => {
        if (!dragging) {
            if (Math.hypot(moved.clientX - startX, moved.clientY - startY) < DRAG_SLOP) return;
            dragging = true;
            if (node[STATE]) node[STATE].dragging = true;
            chip.classList.add("mmx-dragging");
        }
        placeDragged(strip, chip, moved.clientX, moved.clientY);
    };

    const finish = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", finish);
        chip.classList.remove("mmx-dragging");
        if (node[STATE]) node[STATE].dragging = false;

        if (dragging) commitOrder(node, strip);
        else if (onLabel && entry.kind === "image") cycleRole(node, entry.name);
        else toggleSlot(node, entry.name);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
}

function buildChip(node, entry) {
    const chip = document.createElement("div");
    chip.className = entry.on ? "mmx-chip" : "mmx-chip mmx-off";
    chip.dataset.slot = entry.name;
    chip.style.background = COLOUR[entry.role];

    const role = document.createElement("span");
    role.className = "mmx-chip-role";
    role.textContent = SHORT[entry.role];
    chip.appendChild(role);

    const number = document.createElement("span");
    number.className = "mmx-chip-num";
    number.textContent = entry.on ? String(entry.number) : "--";
    chip.appendChild(number);

    const slot = document.createElement("span");
    slot.className = "mmx-chip-slot";
    slot.textContent = entry.name;
    chip.appendChild(slot);

    const relabel =
        entry.kind === "image"
            ? "click the dark label band to call it a subject or a clip instead, "
            : "";
    chip.title =
        `${entry.name}: ${entry.role.toLowerCase()}` +
        (entry.on ? ` ${entry.number}` : ", switched off") +
        `\nDrag to reorder, ${relabel}click below the label to switch it off.`;

    chip.addEventListener("pointerdown", (event) => beginGesture(node, chip, role, entry, event));
    return chip;
}

function renderStrip(node) {
    const strip = node[STATE]?.strip;
    if (!strip || node[STATE].dragging) return;

    const entries = arranged(node);
    node[STATE].chipCount = entries.length;
    const hint = node[STATE].hint;

    strip.replaceChildren();
    if (!entries.length) {
        const note = document.createElement("span");
        note.className = "mmx-note";
        note.textContent = "no references connected";
        strip.appendChild(note);
        if (hint) hint.textContent = "";
        return;
    }
    for (const entry of entries) strip.appendChild(buildChip(node, entry));

    if (hint) {
        hint.textContent = entries.some((entry) => entry.kind === "image")
            ? HINT_IMAGES
            : HINT_PLAIN;
    }
}


function renderTasks(node) {
    const holder = node[STATE]?.tasks;
    if (!holder) return;
    const widget = widgetNamed(node, TASK);
    const options = widget?.options?.values ?? [];
    const chosen = widget?.value;
    const available = anyEnabled(node);

    holder.replaceChildren();
    for (const name of options) {
        const button = document.createElement("div");
        const unavailable = name === REF_TASK && !available;
        button.className =
            "mmx-task" +
            (name === chosen ? " mmx-on" : "") +
            (unavailable ? " mmx-unavailable" : "");
        button.textContent = name;
        button.title = unavailable
            ? "Ref2VA needs at least one reference switched on in the strip above."
            : name;
        if (!unavailable) {
            button.addEventListener("pointerdown", (event) => {
                event.preventDefault();
                event.stopPropagation();
                setWidgetValue(node, TASK, name);
            });
        }
        holder.appendChild(button);
    }
}


function ratioBox(ratio) {
    const [width, height] = ratio.split(":").map(Number);
    const box = document.createElement("div");
    box.className = "mmx-box";
    if (!width || !height) return box;
    const scale = Math.min(RATIO_BOX_W / width, RATIO_BOX_H / height);
    box.style.width = `${Math.max(6, Math.round(width * scale))}px`;
    box.style.height = `${Math.max(6, Math.round(height * scale))}px`;
    return box;
}

function renderRatios(node) {
    const holder = node[STATE]?.ratios;
    if (!holder) return;
    const widget = widgetNamed(node, RESOLUTION);
    const options = widget?.options?.values ?? [];
    const chosen = widget?.value;

    holder.replaceChildren();
    for (const ratio of options) {
        const item = document.createElement("div");
        item.className = "mmx-ratio" + (ratio === chosen ? " mmx-on" : "");
        item.title = `Compose for ${ratio}`;
        item.appendChild(ratioBox(ratio));

        const label = document.createElement("span");
        label.className = "mmx-ratio-label";
        label.textContent = ratio;
        item.appendChild(label);

        item.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            event.stopPropagation();
            setWidgetValue(node, RESOLUTION, ratio);
        });
        holder.appendChild(item);
    }
}


function applyDurationCeiling(node) {
    const widget = widgetNamed(node, DURATION);
    if (!widget?.options) return;

    let ceiling = Number(node.properties?.[DURATION_PROPERTY]);
    if (!isFinite(ceiling) || ceiling <= DURATION_MIN) ceiling = DURATION_PROPERTY_DEFAULT;
    ceiling = Math.min(Math.round(ceiling * 10) / 10, DURATION_CEILING);

    widget.options.max = ceiling;
    if (Number(widget.value) > ceiling) widget.value = ceiling;
}


function refresh(node) {
    if (!node[STATE]) return;
    renderStrip(node);
    renderTasks(node);
    renderRatios(node);
    applyDurationCeiling(node);
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installStyle();

    const references = document.createElement("div");
    references.className = "mmx-refs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-hint";
    references.appendChild(strip);
    references.appendChild(hint);

    const tasks = document.createElement("div");
    tasks.className = "mmx-tasks";
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";

    node[STATE] = { strip, hint, tasks, ratios, chipCount: 0, dragging: false };

    replaceWithDom(node, LAYOUT, "minimaxh3_references", references, () => stripHeight(node));
    replaceWithDom(node, TASK, "minimaxh3_task", tasks, () => TASKS_H);
    replaceWithDom(node, RESOLUTION, "minimaxh3_ratio", ratios, () => RATIOS_H);

    if (node.properties?.[DURATION_PROPERTY] === undefined) {
        node.addProperty?.(DURATION_PROPERTY, DURATION_PROPERTY_DEFAULT, "number");
    }
    refresh(node);
}

function addControls(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        build(this);
        return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const result = onConfigure?.apply(this, arguments);
        refresh(this);
        return result;
    };

    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function () {
        const result = onConnectionsChange?.apply(this, arguments);
        refresh(this);
        return result;
    };

    const onPropertyChanged = nodeType.prototype.onPropertyChanged;
    nodeType.prototype.onPropertyChanged = function (name) {
        const result = onPropertyChanged?.apply(this, arguments);
        if (name === DURATION_PROPERTY) {
            applyDurationCeiling(this);
            this.setDirtyCanvas?.(true, true);
        }
        return result;
    };

    const onResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function () {
        const result = onResize?.apply(this, arguments);
        this.setDirtyCanvas?.(true, true);
        return result;
    };

    addSlotSwitches(nodeType, {
        prefixes: [PREFIX],
        enabled: isSlotEnabled,
        toggle: (node, name) => toggleSlot(node, name),
    });
}

app.registerExtension({
    name: "minimax_h3_rewriter.universal_widgets",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) addControls(nodeType);
    },
});
