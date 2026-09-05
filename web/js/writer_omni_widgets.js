import { app } from "../../scripts/app.js";
import { addSlotSwitches } from "./slot_switches.js";
import {
    CHIP_H_PLAIN,
    chipElement,
    installStripStyle,
    placeDragged,
    slotNumber,
    slots,
    stripHeight as stripHeightFor,
} from "./reference_strip.js";
import {
    MARGIN,
    installBaseStyle,
    installStyle,
    onRefresh,
    ratiosHeight,
    renderRatios,
    renderSegments,
    replaceWithDom,
    repaintOn,
    setWidgetValue,
    widgetNamed,
    widgetValues,
} from "./mmx_controls.js";

const NODE = "MiniMaxH3PromptWriterOmni";

const LAYOUT = "reference_layout";
const TASK = "task";
const RESOLUTION = "resolution";
const DURATION = "duration";

const PREFIX = "ref_";
const REF_TASK = "REF2AV";
const TEXT_TASK = "T2AV";

const PICTURES_FOR_TASK = { I2AV: 1, L2AV: 1, FL2AV: 2 };

const ROLE_FOR_KIND = { image: "Picture", video: "Video", audio: "Audio" };

const HINT_H = 12;
const HINT_GAP = 3;
const HINT =
    "drag to reorder - that order numbers the labels - click a square to switch it off";

const TASKS_H = 26;

const DRAG_SLOP = 6;

const STYLE_ID = "minimax-h3-omni-style";
const STATE = "__minimaxH3Omni";

const STYLE = `
.mmx-omni-refs { display: flex; flex-direction: column; gap: ${HINT_GAP}px;
    width: 100%; height: 100%; overflow: hidden;
    font-family: system-ui, sans-serif; }
.mmx-omni-hint { flex: 0 0 ${HINT_H}px; font-size: 9px; line-height: ${HINT_H}px;
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
    return { order: names("order"), off: names("off") };
}

function writeLayout(node, layout) {
    const kept = {};
    if (layout.order.length) kept.order = layout.order;
    if (layout.off.length) kept.off = layout.off;

    const widget = widgetNamed(node, LAYOUT);
    if (widget) widget.value = JSON.stringify(kept);
    redraw(node);
}


function arranged(node) {
    const layout = readLayout(node);
    const connected = new Map();
    for (const slot of slots(node, PREFIX)) {
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
        const role = ROLE_FOR_KIND[slot.kind] || ROLE_FOR_KIND.image;
        const on = !layout.off.includes(name);
        if (on) counts[role] = (counts[role] || 0) + 1;
        return { name, kind: slot.kind, role, on, number: on ? counts[role] : null };
    });
}

function counted(node) {
    const on = arranged(node).filter((entry) => entry.on);
    return {
        total: on.length,
        pictures: on.filter((entry) => entry.role === "Picture").length,
        heard: on.filter((entry) => entry.role !== "Picture").map((entry) => entry.role),
    };
}

function whyShut(node, task) {
    const { total, pictures, heard } = counted(node);

    if (task === TEXT_TASK) {
        return total
            ? "T2AV is written from text alone, and the strip is not empty. Switch every " +
              "square off, or pick a task that reads them."
            : "";
    }
    if (task === REF_TASK) {
        return total
            ? ""
            : "Ref2AV describes how a target video reuses reference assets, so it needs at " +
              "least one. Connect a picture, a clip or a sound and switch its square on.";
    }
    if (heard.length) {
        const kinds = [...new Set(heard)].map((role) => role.toLowerCase()).join(" and ");
        return (
            `${task} is written from pictures alone, and ${kinds} is connected. Switch those ` +
            "squares off, or pick Ref2AV, which is the task that takes clips and sound."
        );
    }
    const wanted = PICTURES_FOR_TASK[task] ?? 0;
    if (pictures === wanted) return "";
    const short = `${task} is written from ${wanted} picture(s), and the strip has ${pictures}.`;
    return pictures > wanted
        ? `${short} Switch the extra squares off, or pick Ref2AV.`
        : `${short} Connect the missing picture, or switch its square back on.`;
}


function stripHeight(node) {
    return stripHeightFor(
        node, node[STATE]?.chipCount ?? 0, HINT_H, HINT_GAP, MARGIN, CHIP_H_PLAIN
    );
}

function commitOrder(node, strip) {
    const layout = readLayout(node);
    layout.order = [...strip.children]
        .filter((child) => child.dataset?.slot)
        .map((child) => child.dataset.slot);
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

function beginGesture(node, chip, entry, event) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();

    const strip = chip.parentElement;
    const startX = event.clientX;
    const startY = event.clientY;
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
        else toggleSlot(node, entry.name);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
}

function buildChip(node, entry) {
    const { chip } = chipElement(entry, true);
    chip.title =
        `${entry.name}: <${entry.role} ${entry.on ? entry.number : "-"}>` +
        (entry.on ? "" : ", switched off") +
        "\nDrag to reorder -- the order is what numbers the labels. Click to switch off.";

    chip.addEventListener("pointerdown", (event) => beginGesture(node, chip, entry, event));
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
    if (hint) hint.textContent = HINT;
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

const FRAME_RATE = 24;
const FRAME_STEP = 17;
const FRAME_BASE = 5;

function snapped(seconds) {
    const steps = Math.ceil((FRAME_RATE * Number(seconds) - FRAME_BASE) / FRAME_STEP);
    const frames = steps * FRAME_STEP + FRAME_BASE;
    return { frames, seconds: frames / FRAME_RATE };
}

function renderDuration(node) {
    const widget = widgetNamed(node, DURATION);
    if (!widget) return;
    const value = Number(widget.value);
    if (!isFinite(value) || value <= 0) return;
    const fit = snapped(value);
    widget.tooltip =
        `${fit.frames} frames at ${FRAME_RATE} fps, ${fit.seconds.toFixed(2)} s -- the ` +
        "nearest length MiniMax-H3 can actually produce, and the one the rewrite quotes." +
        "\n\nRight-click the node for 'duration' to move how far this reaches.";
}


function redraw(node) {
    if (!node[STATE]) return;
    renderStrip(node);
    renderTasks(node);
    renderRatios(node, node[STATE].ratios, RESOLUTION);
    renderDuration(node);
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle(STYLE_ID, STYLE);

    const references = document.createElement("div");
    references.className = "mmx-omni-refs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-omni-hint";
    references.appendChild(strip);
    references.appendChild(hint);

    const tasks = document.createElement("div");
    tasks.className = "mmx-seg-row";
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";

    node[STATE] = { strip, hint, tasks, ratios, chipCount: 0, dragging: false };
    onRefresh(node, () => redraw(node));

    replaceWithDom(node, LAYOUT, "minimaxh3_omni_references", references, () => stripHeight(node));
    replaceWithDom(node, TASK, "minimaxh3_omni_task", tasks, () => TASKS_H);
    replaceWithDom(node, RESOLUTION, "minimaxh3_omni_ratio", ratios, () => ratiosHeight(node, RESOLUTION));

    redraw(node);
}

function addControls(nodeType) {
    repaintOn(nodeType, build);

    addSlotSwitches(nodeType, {
        prefixes: [PREFIX],
        enabled: isSlotEnabled,
        toggle: (node, name) => toggleSlot(node, name),
    });
}

app.registerExtension({
    name: "minimax_h3_rewriter.writer_omni_widgets",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) addControls(nodeType);
    },
});
