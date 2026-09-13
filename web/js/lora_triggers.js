import { app } from "../../scripts/app.js";
import {
    buttonRow,
    installBaseStyle,
    installStyle,
    onRefresh,
    replaceWithDom,
    repaintOn,
    tickBox,
    widgetNamed,
} from "./mmx_controls.js";

const NODE = "MiniMaxH3LoraTriggers";
const WIDGET = "triggers";

const STATE = "__minimaxH3Triggers";
const STYLE_ID = "minimax-h3-triggers-style";
const PICKER_ID = "minimax-h3-triggers-picker";

const PLACEMENTS = [
    {
        value: "start of the description",
        short: "body",
        note: "Opens the description field itself, after its label. The field the writers put the scene in, and the position a caption-prefix trigger was trained in.",
    },
    {
        value: "top of the prompt",
        short: "top",
        note: "Above everything, before the field labels. Works on a prompt with no labels at all.",
    },
    {
        value: "start of the soundscape",
        short: "sound",
        note: "Opens overall_soundscape, after its label.",
    },
    {
        value: "start of the music",
        short: "music",
        note: "Opens non_diegetic_music, after its label.",
    },
    {
        value: "end of the prompt",
        short: "end",
        note: "Last, after everything. Works on a prompt with no labels at all.",
    },
];

const FALLBACK = PLACEMENTS[0].value;

const ROW_H = 26;
const INPUT_H = 22;
const HINT_H = 12;
const GAP = 3;
const GRIP_W = 12;
const WHERE_W = 48;
const KILL_W = 12;

const ADD_LABEL = "add a trigger";
const ADD_TOOLTIP =
    "One row is one adapter. Put its trigger words in the second field -- several " +
    "separated by commas if it answers to more than one -- and they go into the prompt " +
    "verbatim. The first field is a name for your own use and reaches nothing.";

const GRIP_TOOLTIP =
    "Drag to reorder. The order is not decoration: rows sharing a placement are written " +
    "into the prompt in list order, and that is the order they read in.";

const GRIP_SVG =
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 15 15'" +
    " fill='%23bbb'%3E%3Ccircle cx='5.5' cy='2.5' r='1.3'/%3E%3Ccircle cx='9.5' cy='2.5'" +
    " r='1.3'/%3E%3Ccircle cx='5.5' cy='7.5' r='1.3'/%3E%3Ccircle cx='9.5' cy='7.5'" +
    " r='1.3'/%3E%3Ccircle cx='5.5' cy='12.5' r='1.3'/%3E%3Ccircle cx='9.5' cy='12.5'" +
    " r='1.3'/%3E%3C/svg%3E";

const STYLE = `
.mmx-lt { display: flex; flex-direction: column; gap: ${GAP}px;
    width: 100%; height: 100%; overflow: hidden;
    font-family: system-ui, sans-serif; font-size: 11px; }
.mmx-lt-rows { display: flex; flex-direction: column; gap: ${GAP}px; }
.mmx-lt-row { display: flex; align-items: center; gap: 5px;
    height: ${ROW_H}px; padding: 0 2px; border-radius: 3px;
    color: var(--input-text, #ddd); }
.mmx-lt-grip { flex: 0 0 ${GRIP_W}px; height: 16px; cursor: grab; opacity: 0.5;
    background-image: url("${GRIP_SVG}"); background-repeat: no-repeat;
    background-position: center; background-size: ${GRIP_W}px 16px; }
.mmx-lt-grip:hover { opacity: 1; }
.mmx-lt-lifted { opacity: 0.75; background: var(--comfy-input-bg, #2b2b2b); }
.mmx-lt-lifted .mmx-lt-grip { cursor: grabbing; opacity: 1; }
.mmx-lt-row input { min-width: 0; font: inherit; font-size: 11px;
    height: ${INPUT_H}px; padding: 0 6px; border-radius: 4px; box-sizing: border-box;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmx-lt-row input:focus { outline: none; border-color: #3B7DD8; }
.mmx-lt-name { flex: 1 1 0; }
.mmx-lt-words { flex: 2 1 0; }
.mmx-lt-row.mmx-lt-off input { opacity: 0.45; }
.mmx-lt-where { flex: 0 0 ${WHERE_W}px; box-sizing: border-box; cursor: pointer;
    height: ${INPUT_H}px; line-height: ${INPUT_H - 2}px; text-align: center;
    font-size: 10px; border-radius: 4px; white-space: nowrap; overflow: hidden;
    color: var(--descrip-text, #999); background: var(--comfy-menu-bg, #353535);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmx-lt-where:hover { color: var(--input-text, #ddd); border-color: #3B7DD8; }
.mmx-lt-row.mmx-lt-off .mmx-lt-where { opacity: 0.45; }
.mmx-lt-kill { flex: 0 0 ${KILL_W}px; text-align: center; cursor: pointer;
    font-size: 11px; line-height: 1; color: var(--descrip-text, #999); }
.mmx-lt-kill:hover { color: #E08A8A; }
.mmx-lt-hint { flex: 0 0 ${HINT_H}px; font-size: 9px; line-height: ${HINT_H}px;
    color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }

.mmx-lt-menu { position: fixed; z-index: 1400; min-width: 170px; padding: 4px;
    border-radius: 8px; font-family: system-ui, sans-serif; font-size: 12px;
    color: var(--input-text, #ddd); background: var(--comfy-menu-bg, #353535);
    border: 1px solid var(--border-color, #4e4e4e);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5); }
.mmx-lt-item { padding: 6px 9px; border-radius: 5px; cursor: pointer;
    white-space: nowrap; }
.mmx-lt-item:hover { background: var(--comfy-input-bg, #2b2b2b); }
.mmx-lt-item.mmx-lt-now { background: #3B7DD8; color: #fff; }
`;

function shortFor(value) {
    const found = PLACEMENTS.find((spec) => spec.value === value);
    return found || PLACEMENTS[0];
}

function normalise(item) {
    if (typeof item === "string") {
        return { name: "", words: item, where: FALLBACK, on: true };
    }
    if (!item || typeof item !== "object") return null;
    const where = PLACEMENTS.some((spec) => spec.value === item.where)
        ? item.where
        : FALLBACK;
    return {
        name: String(item.name ?? ""),
        words: String(item.words ?? ""),
        where,
        on: item.on !== false,
    };
}

function readList(node) {
    const raw = widgetNamed(node, WIDGET)?.value ?? "[]";
    let parsed;
    try {
        parsed = JSON.parse(raw || "[]");
    } catch (error) {
        const text = String(raw).trim();
        return text ? [{ name: "", words: text, where: FALLBACK, on: true }] : [];
    }
    if (!Array.isArray(parsed)) parsed = parsed ? [parsed] : [];
    return parsed.map(normalise).filter((entry) => entry !== null);
}

function writeList(node, list, quiet) {
    const state = node[STATE];
    const widget = widgetNamed(node, WIDGET);
    if (!widget) return;
    if (state) state.quiet = true;
    widget.value = JSON.stringify(list);
    if (state) state.quiet = false;
    if (!quiet) redraw(node);
}

let dismiss = null;

function shutPicker() {
    if (dismiss) {
        dismiss();
        dismiss = null;
    }
    document.getElementById(PICKER_ID)?.remove();
}

function openPicker(node, index, anchor) {
    shutPicker();

    const panel = document.createElement("div");
    panel.className = "mmx-lt-menu";
    panel.id = PICKER_ID;

    const current = readList(node)[index]?.where;
    for (const spec of PLACEMENTS) {
        const item = document.createElement("div");
        item.className = "mmx-lt-item" + (spec.value === current ? " mmx-lt-now" : "");
        item.textContent = spec.value;
        item.title = spec.note;
        item.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            event.stopPropagation();
            setField(node, index, "where", spec.value);
            shutPicker();
        });
        panel.appendChild(item);
    }

    panel.addEventListener("pointerdown", (event) => event.stopPropagation());
    document.body.appendChild(panel);

    const box = anchor.getBoundingClientRect();
    const size = panel.getBoundingClientRect();
    const left = Math.max(4, Math.min(box.left, window.innerWidth - size.width - 4));
    const below = box.bottom + 4;
    const top = below + size.height > window.innerHeight - 4
        ? Math.max(4, box.top - size.height - 4)
        : below;
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;

    const away = (event) => {
        if (!panel.contains(event.target)) shutPicker();
    };
    const escaped = (event) => {
        if (event.key === "Escape") shutPicker();
    };
    document.addEventListener("pointerdown", away, true);
    document.addEventListener("keydown", escaped, true);
    dismiss = () => {
        document.removeEventListener("pointerdown", away, true);
        document.removeEventListener("keydown", escaped, true);
    };
}

function setField(node, index, key, value, quiet) {
    const list = readList(node);
    if (!list[index]) return;
    list[index][key] = value;
    writeList(node, list, quiet);
}

function addRow(node) {
    const list = readList(node);
    list.push({ name: "", words: "", where: FALLBACK, on: true });
    writeList(node, list);
    node[STATE]?.rows?.querySelector(".mmx-lt-row:last-child .mmx-lt-name")?.focus();
}

function dropRow(node, index) {
    const list = readList(node);
    if (index < 0 || index >= list.length) return;
    list.splice(index, 1);
    writeList(node, list);
}

function dropBefore(holder, y) {
    let closest = null;
    let nearest = Number.NEGATIVE_INFINITY;
    for (const child of holder.querySelectorAll(".mmx-lt-row:not(.mmx-lt-lifted)")) {
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        if (offset < 0 && offset > nearest) {
            nearest = offset;
            closest = child;
        }
    }
    return closest;
}

function commitOrder(node) {
    const state = node[STATE];
    if (!state) return;
    const list = readList(node);
    const order = [...state.rows.children]
        .map((row) => Number(row.dataset.mmxRow))
        .filter((index) => Number.isInteger(index) && list[index]);

    if (order.length !== list.length || order.every((index, at) => index === at)) {
        redraw(node);
        return;
    }
    writeList(node, order.map((index) => list[index]));
}

function beginDrag(node, row) {
    const holder = node[STATE]?.rows;
    if (!holder) return;
    row.classList.add("mmx-lt-lifted");

    const moved = (event) => {
        const before = dropBefore(holder, event.clientY);
        if (before) holder.insertBefore(row, before);
        else holder.appendChild(row);
    };
    const done = () => {
        window.removeEventListener("pointermove", moved);
        window.removeEventListener("pointerup", done);
        window.removeEventListener("pointercancel", done);
        row.classList.remove("mmx-lt-lifted");
        commitOrder(node);
    };

    window.addEventListener("pointermove", moved);
    window.addEventListener("pointerup", done);
    window.addEventListener("pointercancel", done);
}

function field(className, value, placeholder, tooltip, onType) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = className;
    input.value = value;
    input.placeholder = placeholder;
    input.title = tooltip;
    input.addEventListener("pointerdown", (event) => event.stopPropagation());
    input.addEventListener("keydown", (event) => event.stopPropagation());
    input.addEventListener("input", () => onType(input.value));
    return input;
}

function buildRow(node, entry, index) {
    const row = document.createElement("div");
    row.className = "mmx-lt-row" + (entry.on ? "" : " mmx-lt-off");
    row.dataset.mmxRow = String(index);

    const grip = document.createElement("div");
    grip.className = "mmx-lt-grip";
    grip.title = GRIP_TOOLTIP;
    grip.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        beginDrag(node, row);
    });
    row.appendChild(grip);

    const box = tickBox(entry.on);
    box.title = entry.on
        ? "On. Switching it off stops the words being added -- it does not take them out of a prompt that already has them."
        : "Off. The words are not added.";
    box.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        setField(node, index, "on", !entry.on);
    });
    row.appendChild(box);

    row.appendChild(
        field(
            "mmx-lt-name",
            entry.name,
            "name",
            "What you call this adapter. Yours to read; nothing is done with it.",
            (value) => setField(node, index, "name", value, true)
        )
    );
    row.appendChild(
        field(
            "mmx-lt-words",
            entry.words,
            "trigger words",
            "The words that go into the prompt, verbatim. Several separated by commas if the adapter answers to more than one; each is checked and added on its own.",
            (value) => setField(node, index, "words", value, true)
        )
    );

    const spec = shortFor(entry.where);
    const mark = document.createElement("div");
    mark.className = "mmx-lt-where";
    mark.textContent = spec.short;
    mark.title = `${spec.value}\n\n${spec.note}\n\nClick to change.`;
    mark.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        openPicker(node, index, mark);
    });
    row.appendChild(mark);

    const kill = document.createElement("div");
    kill.className = "mmx-lt-kill";
    kill.textContent = "x";
    kill.title = "Remove this row.";
    kill.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        dropRow(node, index);
    });
    row.appendChild(kill);

    return row;
}

function heightOf(node) {
    const count = readList(node).length;
    return count * (ROW_H + GAP) + HINT_H + GAP;
}

function redraw(node) {
    const state = node[STATE];
    if (!state || state.quiet) return;

    const list = readList(node);
    state.rows.replaceChildren();
    list.forEach((entry, index) => state.rows.appendChild(buildRow(node, entry, index)));

    const live = list.filter((entry) => entry.on && entry.words.trim()).length;
    const blank = list.filter((entry) => !entry.words.trim()).length;
    state.hint.textContent = !list.length
        ? "no triggers yet"
        : blank
          ? `${live} on, ${blank} with nothing in them yet`
          : `${live} on of ${list.length}`;

    const wanted = node.computeSize?.();
    if (wanted && node.size && node.size[1] < wanted[1]) {
        node.setSize([node.size[0], wanted[1]]);
    }

    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();
    installStyle(STYLE_ID, STYLE);

    const holder = document.createElement("div");
    holder.className = "mmx-lt";
    const rows = document.createElement("div");
    rows.className = "mmx-lt-rows";
    const hint = document.createElement("div");
    hint.className = "mmx-lt-hint";
    holder.appendChild(rows);
    holder.appendChild(hint);

    node[STATE] = { rows, hint, quiet: false };
    onRefresh(node, () => redraw(node));

    replaceWithDom(node, WIDGET, "minimaxh3_triggers", holder, () => heightOf(node));

    buttonRow(node, "triggers_actions", [
        {
            label: ADD_LABEL,
            tooltip: ADD_TOOLTIP,
            onClick: () => addRow(node),
        },
    ]);

    redraw(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.lora_triggers",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) repaintOn(nodeType, build);
    },
});
