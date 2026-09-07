import { app } from "../../scripts/app.js";
import { ask } from "./prompt_library.js";
import {
    MARGIN,
    buttonRow,
    installBaseStyle,
    installStyle,
    onRefresh,
    replaceWithDom,
    repaintOn,
    told,
    widgetNamed,
} from "./mmx_controls.js";

const NODE = "MiniMaxH3EffectEmbeddings";
const EFFECTS = "effects";

const STATE = "__minimaxH3Effects";
const LABEL = "download the ten effects (10 MB)";
const STYLE_ID = "minimax-h3-effects-style";

const ROW_H = 18;
const HINT_H = 12;
const GAP = 2;

const STYLE = `
.mmx-fx { display: flex; flex-direction: column; gap: ${GAP}px;
    width: 100%; height: 100%; overflow: hidden;
    font-family: system-ui, sans-serif; font-size: 11px; }
.mmx-fx-row { display: flex; align-items: center; gap: 6px;
    height: ${ROW_H}px; padding: 0 4px; border-radius: 3px; cursor: pointer;
    color: var(--input-text, #ddd); }
.mmx-fx-row:hover { background: var(--comfy-input-bg, #2b2b2b); }
.mmx-fx-box { flex: 0 0 12px; height: 12px; border-radius: 3px;
    border: 1px solid var(--border-color, #6a6a6a); background: #2b2b2b;
    display: flex; align-items: center; justify-content: center;
    font-size: 9px; line-height: 1; color: #fff; }
.mmx-fx-on .mmx-fx-box { background: #4a9d5b; border-color: #4a9d5b; }
.mmx-fx-name { flex: 1 1 auto; overflow: hidden; white-space: nowrap;
    text-overflow: ellipsis; }
.mmx-fx-cost { flex: 0 0 auto; color: var(--descrip-text, #999); }
.mmx-fx-gone { flex: 0 0 auto; color: #e0a45a; }
.mmx-fx-hint { flex: 0 0 ${HINT_H}px; font-size: 9px; line-height: ${HINT_H}px;
    color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
`;

let known = null;

function catalogue(again = false) {
    if (again || !known) {
        known = ask("/minimax_h3_rewriter/embeddings").then(({ ok, payload }) =>
            ok && payload?.ok ? payload : null
        );
    }
    return known;
}

function readChosen(node) {
    try {
        const parsed = JSON.parse(widgetNamed(node, EFFECTS)?.value ?? "{}");
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (error) {
        return {};
    }
}

function toggle(node, name) {
    const chosen = readChosen(node);
    if (chosen[name] === true) delete chosen[name];
    else chosen[name] = true;
    const widget = widgetNamed(node, EFFECTS);
    if (widget) widget.value = JSON.stringify(chosen);
    redraw(node);
}

function rowsFor(node) {
    const state = node[STATE];
    if (state?.effects) return state.effects;
    return Object.keys(readChosen(node)).map((name) => ({
        name,
        title: name.replace(/^minimaxh3_/, "").replace(/_/g, " "),
        tokens: 0,
        present: true,
    }));
}

function buildRow(node, effect, chosen) {
    const row = document.createElement("div");
    row.className = "mmx-fx-row" + (chosen[effect.name] === true ? " mmx-fx-on" : "");

    const box = document.createElement("div");
    box.className = "mmx-fx-box";
    box.textContent = chosen[effect.name] === true ? "x" : "";

    const name = document.createElement("span");
    name.className = "mmx-fx-name";
    name.textContent = effect.title;

    const cost = document.createElement("span");
    cost.className = "mmx-fx-cost";
    cost.textContent = effect.tokens ? `${effect.tokens} tok` : "";

    row.appendChild(box);
    row.appendChild(name);
    row.appendChild(cost);

    if (effect.foreign) {
        const mark = document.createElement("span");
        mark.className = "mmx-fx-gone";
        mark.textContent = "not H3";
        row.appendChild(mark);
    } else if (!effect.present) {
        const mark = document.createElement("span");
        mark.className = "mmx-fx-gone";
        mark.textContent = "no file";
        row.appendChild(mark);
    }

    row.title =
        `${effect.token || "embedding:" + effect.name}\n` +
        (effect.tokens ? `${effect.tokens} of the prompt's positions\n` : "") +
        (effect.foreign
            ? "a file of this name is here, but it is not a MiniMax-H3 embedding"
            : effect.present
              ? "on this disk"
              : "not downloaded yet - the button below fetches all ten");

    row.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        toggle(node, effect.name);
    });
    return row;
}

function height(node) {
    return rowsFor(node).length * (ROW_H + GAP) + HINT_H + GAP;
}

function redraw(node) {
    const state = node[STATE];
    if (!state) return;

    const chosen = readChosen(node);
    const effects = rowsFor(node);

    state.grid.replaceChildren();
    for (const effect of effects) state.grid.appendChild(buildRow(node, effect, chosen));

    const picked = effects.filter((effect) => chosen[effect.name] === true);
    const cost = picked.reduce((sum, effect) => sum + (effect.tokens || 0), 0);
    const absent = effects.filter((effect) => !effect.present).length;

    state.hint.textContent = !effects.length
        ? "reading the embeddings folder..."
        : absent
          ? `${absent} of ${effects.length} not downloaded yet`
          : picked.length
            ? `${picked.length} selected, ${cost} tokens`
            : "click to add an effect";

    if (state.button) {
        state.button.style.display = absent || !effects.length ? "" : "none";
    }
    node.setDirtyCanvas?.(true, true);
}

async function load(node, again = false) {
    const answer = await catalogue(again);
    const state = node[STATE];
    if (!state) return;
    state.effects = answer?.effects || null;
    state.folder = answer?.dir || "";
    redraw(node);
}

async function download(node, button) {
    button.disabled = true;
    const total = node[STATE]?.effects?.length || 10;
    button.textContent = `downloading... 0 of ${total}`;

    const watching = setInterval(async () => {
        const { ok, payload } = await ask("/minimax_h3_rewriter/embeddings");
        if (!ok || !payload?.ok) return;
        const here = payload.effects.filter((effect) => effect.present).length;
        if (button.disabled) button.textContent = `downloading... ${here} of ${total}`;
    }, 3000);

    let ok = false;
    let payload = {};
    try {
        ({ ok, payload } = await ask("/minimax_h3_rewriter/embeddings/download", {}));
    } finally {
        clearInterval(watching);
        button.disabled = false;
        button.textContent = LABEL;
    }
    if (ok && payload?.ok) {
        known = Promise.resolve(payload);
        await load(node, false);
        told(button, `fetched ${payload.downloaded}`);
    } else {
        told(button, payload?.error ? "failed - see console" : "failed", 4);
        if (payload?.error) console.error("[minimax_h3_rewriter] effects:", payload.error);
    }
}

function build(node) {
    installBaseStyle();
    installStyle(STYLE_ID, STYLE);

    const holder = document.createElement("div");
    holder.className = "mmx-fx";
    const grid = document.createElement("div");
    const hint = document.createElement("div");
    hint.className = "mmx-fx-hint";
    holder.appendChild(grid);
    holder.appendChild(hint);

    node[STATE] = { grid, hint, effects: null, button: null };
    onRefresh(node, () => redraw(node));

    replaceWithDom(node, EFFECTS, "minimaxh3_effects", holder, () => height(node));

    const { buttons } = buttonRow(node, "effects_actions", [
        {
            label: LABEL,
            tooltip:
                "Fetches the ten files into ComfyUI's models/embeddings folder, which is the " +
                "only place 'embedding:' looks. Already-complete files are left alone.",
            onClick: (button) => download(node, button),
        },
    ]);
    node[STATE].button = buttons[0];

    redraw(node);
    load(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.effect_embeddings",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) repaintOn(nodeType, build);
    },
});
