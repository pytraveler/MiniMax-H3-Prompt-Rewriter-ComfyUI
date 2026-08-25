import { app } from "../../scripts/app.js";
import { addSlotSwitches } from "./slot_switches.js";
import {
    MARGIN,
    installBaseStyle,
    installStyle,
    onRefresh,
    replaceWithDom,
    repaintOn,
    showWidget,
    widgetNamed,
} from "./mmx_controls.js";
import {
    chipElement,
    installStripStyle,
    instructionBand,
    readInstructions,
    stripHeight as stripHeightFor,
    writeInstruction,
} from "./reference_strip.js";


const NODE = "MiniMaxH3MultiReferenceCaption";

const MASK = "enabled_mask";
const INSTRUCTIONS = "reference_instructions";

const GROUPS = [
    { prefix: "subject_", role: "Subject" },
    { prefix: "picture_", role: "Picture" },
    { prefix: "video_", role: "Video" },
    { prefix: "audio_", role: "Audio" },
];

const PREFIXES = GROUPS.map((group) => group.prefix);

const HINT_H = 12;
const HINT_GAP = 3;
const HINT = "click a square to switch it off - 'instr' asks it something else, right-click clears it";

const STYLE_ID = "minimax-h3-multi-caption-style";
const STATE = "__minimaxH3MultiCaption";

const STYLE = `
.mmx-mrefs { display: flex; flex-direction: column; gap: ${HINT_GAP}px;
    width: 100%; height: 100%; overflow: hidden;
    font-family: system-ui, sans-serif; }
.mmx-mhint { flex: 0 0 ${HINT_H}px; font-size: 9px; line-height: ${HINT_H}px;
    color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
`;


function readMask(node) {
    try {
        const parsed = JSON.parse(widgetNamed(node, MASK)?.value ?? "{}");
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
        return {};
    }
}

function isEnabled(node, name) {
    const mask = readMask(node);
    return mask[name] === undefined ? true : !!mask[name];
}

function toggle(node, name) {
    const mask = readMask(node);
    mask[name] = !isEnabled(node, name);
    for (const key of Object.keys(mask)) {
        if (mask[key] !== false) delete mask[key];
    }
    const widget = widgetNamed(node, MASK);
    if (widget) widget.value = JSON.stringify(mask);
    redraw(node);
}

function slotOf(input, prefix) {
    const name = String(input?.name || "");
    const tail = name.slice(name.lastIndexOf(".") + 1);
    return tail.startsWith(prefix) ? tail : null;
}

function slotNumber(name) {
    const tail = name.slice(name.lastIndexOf("_") + 1);
    return /^\d+$/.test(tail) ? Number(tail) : 0;
}

function arranged(node) {
    const entries = [];
    const counts = {};
    for (const group of GROUPS) {
        const found = [];
        for (const input of node.inputs || []) {
            const slot = slotOf(input, group.prefix);
            if (!slot) continue;
            const link = input.link;
            if (link === null || link === undefined) continue;
            found.push(slot);
        }
        found.sort((a, b) => slotNumber(a) - slotNumber(b));
        for (const name of found) {
            const on = isEnabled(node, name);
            if (on) counts[group.role] = (counts[group.role] || 0) + 1;
            entries.push({ name, role: group.role, on, number: on ? counts[group.role] : null });
        }
    }
    return entries;
}

const instructions = (node) => ({
    read: (slot) => readInstructions(node, INSTRUCTIONS)[slot] || null,
    write: (slot, text, add) => writeInstruction(node, INSTRUCTIONS, slot, text, add),
});

function buildChip(node, entry) {
    const { chip } = chipElement(entry);
    chip.classList.add("mmx-still");
    chip.appendChild(instructionBand(entry.name, entry.role, instructions(node)));
    chip.title =
        `${entry.name}: ${entry.role.toLowerCase()}` +
        (entry.on ? ` ${entry.number}` : ", switched off") +
        `\nClick to switch it ${entry.on ? "off" : "on"}.`;

    chip.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        toggle(node, entry.name);
    });
    return chip;
}

function stripHeight(node) {
    return stripHeightFor(node, node[STATE]?.chipCount ?? 0, HINT_H, HINT_GAP, MARGIN);
}

function redraw(node) {
    const state = node[STATE];
    if (!state) return;

    const entries = arranged(node);
    state.chipCount = entries.length;

    state.strip.replaceChildren();
    if (!entries.length) {
        const note = document.createElement("span");
        note.className = "mmx-note";
        note.textContent = "no references connected";
        state.strip.appendChild(note);
        state.hint.textContent = "";
    } else {
        for (const entry of entries) state.strip.appendChild(buildChip(node, entry));
        state.hint.textContent = HINT;
    }
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();
    installStripStyle();
    installStyle(STYLE_ID, STYLE);

    const holder = document.createElement("div");
    holder.className = "mmx-mrefs";
    const strip = document.createElement("div");
    strip.className = "mmx-strip";
    const hint = document.createElement("div");
    hint.className = "mmx-mhint";
    holder.appendChild(strip);
    holder.appendChild(hint);

    node[STATE] = { strip, hint, chipCount: 0 };
    onRefresh(node, () => redraw(node));

    replaceWithDom(node, MASK, "minimaxh3_multi_references", holder, () => stripHeight(node));
    showWidget(node, INSTRUCTIONS, false);
    redraw(node);
}

function addControls(nodeType) {
    repaintOn(nodeType, build);

    addSlotSwitches(nodeType, { prefixes: PREFIXES, enabled: isEnabled, toggle });
}

app.registerExtension({
    name: "minimax_h3_rewriter.multi_caption_widgets",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) addControls(nodeType);
    },
});
