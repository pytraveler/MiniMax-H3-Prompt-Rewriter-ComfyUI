import { installStyle, refresh, widgetNamed } from "./mmx_controls.js";


export const CHIP_W = 52;
export const CHIP_ROLE_H = 18;
export const CHIP_SLOT_H = 16;
export const CHIP_INSTR_H = 17;
export const CHIP_H = 78;
export const CHIP_H_PLAIN = CHIP_H - CHIP_INSTR_H;
export const CHIP_GAP = 4;

export const COLOUR = {
    Picture: "#3B7DD8",
    Subject: "#C98A2E",
    Video: "#3F9E5A",
    Audio: "#8A54C8",
};

export const SHORT = { Picture: "pic", Subject: "subj", Video: "vid", Audio: "aud" };

const STYLE_ID = "minimax-h3-strip-style";

const STYLE = `
.mmx-strip { flex: 1 1 auto; display: flex; flex-wrap: wrap;
    align-content: flex-start; gap: ${CHIP_GAP}px; overflow: hidden; }
.mmx-chip { flex: 0 0 auto; box-sizing: border-box;
    width: ${CHIP_W}px; height: ${CHIP_H}px;
    border-radius: 5px; display: flex; flex-direction: column; overflow: hidden;
    cursor: grab; user-select: none; touch-action: none;
    border: 1px solid rgba(0, 0, 0, 0.45);
    transition: opacity 0.12s, transform 0.12s, box-shadow 0.12s; }
.mmx-chip.mmx-still { cursor: pointer; }
.mmx-chip-role { flex: 0 0 ${CHIP_ROLE_H}px; font-size: 11px;
    line-height: ${CHIP_ROLE_H}px; text-align: center; letter-spacing: 0.03em;
    color: #fff; background: rgba(0, 0, 0, 0.3); }
.mmx-chip-num { flex: 1 1 auto; font-size: 15px; text-align: center;
    line-height: ${CHIP_H - CHIP_ROLE_H - CHIP_SLOT_H - CHIP_INSTR_H - 2}px;
    font-weight: 600; color: #fff; }
/* The number says where a square sits in the block, so it renumbers the moment
   anything moves -- which would make a reorder of two squares of the same kind
   invisible. The slot it is plugged into is what stays with it. */
.mmx-chip-slot { flex: 0 0 ${CHIP_SLOT_H}px; font-size: 9px;
    line-height: ${CHIP_SLOT_H}px; text-align: center; letter-spacing: 0.02em;
    color: rgba(255, 255, 255, 0.75); background: rgba(0, 0, 0, 0.22); }
.mmx-chip.mmx-plain { height: ${CHIP_H_PLAIN}px; }
.mmx-chip.mmx-plain .mmx-chip-num {
    line-height: ${CHIP_H_PLAIN - CHIP_ROLE_H - CHIP_SLOT_H - 2}px; }
.mmx-chip.mmx-off { opacity: 0.35; }
.mmx-chip.mmx-dragging { cursor: grabbing; transform: scale(1.1);
    box-shadow: 0 3px 10px rgba(0, 0, 0, 0.55); }
.mmx-strip .mmx-note { line-height: ${CHIP_H}px; }

/* Same colour as the square above it, and unlit until it carries something:
   an empty band should read as an offer rather than as a setting.
   border-box, and a line-height a pixel short of the band: the border is part
   of the height here, and a line exactly as tall as its box loses its descenders
   to the chip's overflow. */
.mmx-instr { flex: 0 0 ${CHIP_INSTR_H}px; box-sizing: border-box; font-size: 9px;
    line-height: ${CHIP_INSTR_H - 3}px; text-align: center; letter-spacing: 0.04em;
    cursor: pointer; color: rgba(255, 255, 255, 0.5);
    background: rgba(0, 0, 0, 0.42);
    border-top: 1px solid rgba(0, 0, 0, 0.35); }
.mmx-instr:hover { color: #fff; background: rgba(255, 255, 255, 0.16); }
/* Two lit states, because the two modes do opposite things and the operator in
   front of the word is easy to miss on a 52px square. Added is the softer of
   the two; instead-of gets the solid band. */
.mmx-instr.mmx-set { color: #fff; font-weight: 600;
    background: rgba(255, 255, 255, 0.28); }
.mmx-instr.mmx-set.mmx-swap { color: #1b1b1b;
    background: rgba(255, 255, 255, 0.82); }

.mmx-ask { position: fixed; z-index: 3000; width: 320px; padding: 8px;
    display: flex; flex-direction: column; gap: 6px; border-radius: 8px;
    font-family: system-ui, sans-serif;
    background: var(--comfy-menu-bg, #353535);
    border: 1px solid var(--border-color, #4e4e4e);
    box-shadow: 0 6px 22px rgba(0, 0, 0, 0.6); }
.mmx-ask-title { font-size: 11px; color: var(--descrip-text, #999); }
.mmx-ask textarea { width: 100%; height: 96px; resize: vertical; padding: 6px;
    box-sizing: border-box; font: inherit; font-size: 12px; border-radius: 5px;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmx-ask-mode { display: flex; align-items: center; gap: 6px; font-size: 11px;
    color: var(--input-text, #ddd); cursor: pointer; user-select: none; }
.mmx-ask-mode input { margin: 0; cursor: pointer; }
.mmx-ask-note { font-size: 10px; color: var(--descrip-text, #999); }
.mmx-ask-row { display: flex; justify-content: flex-end; gap: 6px; }
.mmx-ask button { font: inherit; font-size: 11px; padding: 4px 10px;
    border-radius: 5px; cursor: pointer; color: var(--input-text, #ddd);
    background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmx-ask button.mmx-ask-go { background: #3B7DD8; border-color: #3B7DD8;
    color: #fff; font-weight: 600; }
`;

export function installStripStyle() {
    installStyle(STYLE_ID, STYLE);
}


export function readInstructions(node, name) {
    try {
        const parsed = JSON.parse(widgetNamed(node, name)?.value || "{}");
        if (!parsed || typeof parsed !== "object") return {};
        const kept = {};
        for (const [slot, value] of Object.entries(parsed)) {
            const text = typeof value === "string" ? value : value?.text;
            const add = typeof value === "string" ? true : value?.add !== false;
            if (typeof text === "string" && text.trim()) {
                kept[slot] = { text: text.trim(), add };
            }
        }
        return kept;
    } catch (error) {
        return {};
    }
}

export function writeInstruction(node, name, slot, text, add) {
    const map = readInstructions(node, name);
    const kept = (text || "").trim();
    if (kept) map[slot] = { text: kept, add: add !== false };
    else delete map[slot];

    const widget = widgetNamed(node, name);
    if (widget) widget.value = JSON.stringify(map);
    refresh(node);
    node.setDirtyCanvas?.(true, true);
}


let openEditor = null;

function closeEditor() {
    openEditor?.remove();
    openEditor = null;
}

export function askForInstruction(anchor, label, current, save) {
    closeEditor();

    const panel = document.createElement("div");
    panel.className = "mmx-ask";

    const title = document.createElement("div");
    title.className = "mmx-ask-title";
    title.textContent = `${label}: what to ask about this reference. Empty restores the usual question.`;
    panel.appendChild(title);

    const box = document.createElement("textarea");
    box.value = current?.text || "";
    box.placeholder = "Do not mention the window.";
    panel.appendChild(box);

    const mode = document.createElement("label");
    mode.className = "mmx-ask-mode";
    const swap = document.createElement("input");
    swap.type = "checkbox";
    swap.checked = current ? current.add === false : false;
    const modeText = document.createElement("span");
    modeText.textContent = "Ask this instead of the role's own question";
    mode.appendChild(swap);
    mode.appendChild(modeText);
    panel.appendChild(mode);

    const note = document.createElement("div");
    note.className = "mmx-ask-note";
    const describeMode = () => {
        note.textContent = swap.checked
            ? "The length preset is yours to state too: this text is the whole question."
            : "The role's question and the length preset stay; this goes after them.";
    };
    describeMode();
    swap.addEventListener("change", describeMode);
    panel.appendChild(note);

    const row = document.createElement("div");
    row.className = "mmx-ask-row";
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    const confirm = document.createElement("button");
    confirm.className = "mmx-ask-go";
    confirm.textContent = "Set";
    row.appendChild(cancel);
    row.appendChild(confirm);
    panel.appendChild(row);

    for (const kind of ["pointerdown", "pointerup", "click", "wheel", "contextmenu"]) {
        panel.addEventListener(kind, (event) => event.stopPropagation());
    }

    const done = () => {
        save(box.value, !swap.checked);
        closeEditor();
    };
    confirm.addEventListener("click", done);
    cancel.addEventListener("click", closeEditor);
    box.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeEditor();
        else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) done();
    });

    document.body.appendChild(panel);
    openEditor = panel;

    const at = anchor.getBoundingClientRect();
    const size = panel.getBoundingClientRect();
    const left = Math.min(Math.max(6, at.left), window.innerWidth - size.width - 6);
    const above = at.bottom + size.height + 8 > window.innerHeight;
    panel.style.left = `${left}px`;
    panel.style.top = `${above ? Math.max(6, at.top - size.height - 6) : at.bottom + 6}px`;

    box.focus();
    box.select();

    const away = (event) => {
        if (panel.contains(event.target)) return;
        window.removeEventListener("pointerdown", away, true);
        closeEditor();
    };
    setTimeout(() => window.addEventListener("pointerdown", away, true), 0);
}


export function instructionBand(slot, label, api) {
    const band = document.createElement("div");
    const question = api.read(slot);
    const added = question ? question.add !== false : false;

    band.className = !question
        ? "mmx-instr"
        : added
          ? "mmx-instr mmx-set"
          : "mmx-instr mmx-set mmx-swap";
    band.textContent = !question || added ? "+ instr" : "= instr";
    band.title = question
        ? `${slot} is asked ${added ? "its role's question, and then" : "this instead of its role's question"}:` +
          `
${question.text}

Click to edit, right-click to clear.`
        : `${slot} is asked its role's usual question.
` +
          `Click to add a line to it, or to ask something else instead.`;

    band.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (event.button === 2) api.write(slot, "", true);
        else if (event.button === 0) {
            askForInstruction(band, label, question, (next, add) => api.write(slot, next, add));
        }
    });
    band.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
    });
    return band;
}

export function chipElement(entry, plain = false) {
    const chip = document.createElement("div");
    chip.className =
        (entry.on ? "mmx-chip" : "mmx-chip mmx-off") + (plain ? " mmx-plain" : "");
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

    return { chip, role };
}

export function stripHeight(node, count, hintHeight, hintGap, margin, chipHeight = CHIP_H) {
    const usable = Math.max(node.size?.[0] ?? 300, 120) - 2 * margin;
    const perRow = Math.max(1, Math.floor((usable + CHIP_GAP) / (CHIP_W + CHIP_GAP)));
    const rows = Math.max(1, Math.ceil(count / perRow));
    const hint = count ? hintHeight + hintGap : 0;
    return rows * chipHeight + (rows - 1) * CHIP_GAP + hint;
}


export function linkKind(node, link) {
    const links = node.graph?.links;
    const info = links?.get ? links.get(link) : links?.[link];
    const type = String(info?.type || "").toUpperCase();
    if (type.includes("AUDIO")) return "audio";
    if (type.includes("VIDEO")) return "video";
    return "image";
}

export function slotNumber(name) {
    const tail = name.slice(name.lastIndexOf("_") + 1);
    return /^\d+$/.test(tail) ? Number(tail) : 0;
}

export function slots(node, prefix) {
    const found = [];
    for (const input of node.inputs || []) {
        const name = String(input.name || "");
        const tail = name.slice(name.lastIndexOf(".") + 1);
        if (!tail.startsWith(prefix)) continue;
        const link = input.link;
        const connected = link !== null && link !== undefined;
        found.push({ name: tail, connected, kind: connected ? linkKind(node, link) : "image" });
    }
    return found;
}

export function placeDragged(strip, chip, x, y) {
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
