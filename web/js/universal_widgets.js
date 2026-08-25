import { app } from "../../scripts/app.js";
import { addSlotSwitches } from "./slot_switches.js";
import {
    CHIP_GAP,
    CHIP_H,
    CHIP_W,
    chipElement,
    installStripStyle,
    instructionBand,
    readInstructions,
    stripHeight as stripHeightFor,
    writeInstruction,
} from "./reference_strip.js";
import {
    MARGIN,
    installBaseStyle,
    installStyle,
    onRefresh,
    renderRatios,
    renderSegments,
    replaceWithDom,
    repaintOn,
    setWidgetValue,
    showWidget,
    widgetNamed,
    widgetValues,
} from "./mmx_controls.js";


const NODE = "MiniMaxH3UniversalWriter";

const LAYOUT = "reference_layout";
const INSTRUCTIONS = "reference_instructions";
const TASK = "task";
const RESOLUTION = "resolution";
const DURATION = "duration";

const PREFIX = "ref_";
const REF_TASK = "Ref2VA";
const TEXT_TASK = "T2VA";

const PICTURES_FOR_TASK = { I2VA: 1, FL2VA: 2, L2VA: 1 };

const DURATION_PROPERTY = "max_duration";
const DURATION_PROPERTY_DEFAULT = 30;
const DURATION_MIN = 0.1;
const DURATION_CEILING = 600;

const IMAGE_ROLES = ["Picture", "Subject", "Video"];

const HINT_H = 12;
const HINT_GAP = 3;
const HINT_IMAGES =
    "drag to reorder - click the label to change it - click below to switch off - 'instr' asks, right-click clears";
const HINT_PLAIN =
    "drag to reorder - click a square below its label to switch it off - 'instr' asks, right-click clears";

const TASKS_H = 26;
const RATIOS_H = 38;

const DRAG_SLOP = 6;

const STYLE_ID = "minimax-h3-universal-style";
const STATE = "__minimaxH3Universal";

const STYLE = `
.mmx-refs { display: flex; flex-direction: column; gap: ${HINT_GAP}px;
    width: 100%; height: 100%; overflow: hidden;
    font-family: system-ui, sans-serif; }
.mmx-hint { flex: 0 0 ${HINT_H}px; font-size: 9px; line-height: ${HINT_H}px;
    color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
`;


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
    redraw(node);
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

function pictureCount(node) {
    return arranged(node).filter((entry) => entry.on && entry.role === IMAGE_ROLES[0]).length;
}

function whyShut(node, task) {
    if (task === TEXT_TASK) return "";
    if (task === REF_TASK) {
        return anyEnabled(node)
            ? ""
            : "Ref2VA is written from at least one reference, and the strip is empty. " +
              "Connect an image, a clip or a sound and switch its square on.";
    }
    const wanted = PICTURES_FOR_TASK[task] ?? 0;
    const have = pictureCount(node);
    if (have === wanted) return "";
    const short = `${task} is written from ${wanted} picture(s), and the strip has ${have}.`;
    return have > wanted
        ? `${short} Switch the extra squares off, or click a badge to call one a subject ` +
          `or a clip -- those are not counted here.`
        : `${short} Connect the missing image, switch its square back on, or click a badge ` +
          `to turn a subject back into a picture.`;
}


function stripHeight(node) {
    return stripHeightFor(node, node[STATE]?.chipCount ?? 0, HINT_H, HINT_GAP, MARGIN);
}

const instructions = (node) => ({
    read: (slot) => readInstructions(node, INSTRUCTIONS)[slot] || null,
    write: (slot, text, add) => writeInstruction(node, INSTRUCTIONS, slot, text, add),
});

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

    // Bound to the window, with no pointer capture: moving the chip in the DOM
    // is what a live reorder does, and that releases capture mid-gesture.
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
}

function buildChip(node, entry) {
    const { chip, role } = chipElement(entry);
    chip.appendChild(instructionBand(entry.name, entry.role, instructions(node)));

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
    const chosen = widgetNamed(node, TASK)?.value;

    renderSegments(
        holder,
        widgetValues(node, TASK).map((name) => {
            const why = whyShut(node, name);
            return {
                label: name,
                on: name === chosen,
                shut: !!why,
                title: why || name,
                pick: () => setWidgetValue(node, TASK, name),
            };
        })
    );
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


function redraw(node) {
    if (!node[STATE]) return;
    renderStrip(node);
    renderTasks(node);
    renderRatios(node, node[STATE].ratios, RESOLUTION);
    applyDurationCeiling(node);
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle(STYLE_ID, STYLE);
    showWidget(node, INSTRUCTIONS, false);

    const references = document.createElement("div");
    references.className = "mmx-refs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-hint";
    references.appendChild(strip);
    references.appendChild(hint);

    const tasks = document.createElement("div");
    tasks.className = "mmx-seg-row";
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";

    node[STATE] = { strip, hint, tasks, ratios, chipCount: 0, dragging: false };
    onRefresh(node, () => redraw(node));

    replaceWithDom(node, LAYOUT, "minimaxh3_references", references, () => stripHeight(node));
    replaceWithDom(node, TASK, "minimaxh3_task", tasks, () => TASKS_H);
    replaceWithDom(node, RESOLUTION, "minimaxh3_ratio", ratios, () => RATIOS_H);

    if (node.properties?.[DURATION_PROPERTY] === undefined) {
        node.addProperty?.(DURATION_PROPERTY, DURATION_PROPERTY_DEFAULT, "number");
    }
    redraw(node);
}

function addControls(nodeType) {
    repaintOn(nodeType, build);

    const onPropertyChanged = nodeType.prototype.onPropertyChanged;
    nodeType.prototype.onPropertyChanged = function (name) {
        const result = onPropertyChanged?.apply(this, arguments);
        if (name === DURATION_PROPERTY) {
            applyDurationCeiling(this);
            this.setDirtyCanvas?.(true, true);
        }
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
