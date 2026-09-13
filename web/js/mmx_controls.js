import { app } from "../../scripts/app.js";

export const MARGIN = 6;

export function boxed(content) {
    return content + 2 * MARGIN;
}

const REFRESH = "__minimaxH3Refresh";

export function onRefresh(node, callback) {
    node[REFRESH] = callback;
}

export function refresh(node) {
    node[REFRESH]?.();
}

const RATIO_BOX_W = 34;
const RATIO_BOX_H = 22;
const RATIO_BOX_MIN = 4;
const RATIO_GAP = 4;
const RATIO_ITEM_W = RATIO_BOX_W + 2;
const RATIO_ITEM_MAX = 72;
const RATIO_ROW_H = 38;

const BASE_STYLE_ID = "minimax-h3-controls-style";

const TICK_SVG =
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'" +
    "%3E%3Cpolyline points='3.4,7.3 6,10.1 10.9,3.9' fill='none' stroke='%23fff'" +
    " stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E";

const BASE_STYLE = `
.mmx-note { font-size: 11px; color: var(--descrip-text, #999);
    font-family: system-ui, sans-serif; }

.mmx-tick { flex: 0 0 14px; width: 14px; height: 14px; box-sizing: border-box;
    border-radius: 3px; cursor: pointer; background: #2B2B2B;
    border: 1px solid #6A6A6A; }
.mmx-tick.mmx-on { background-color: #4A9D5B; border-color: #4A9D5B;
    background-image: url("${TICK_SVG}"); background-repeat: no-repeat;
    background-position: center; background-size: 14px 14px; }

.mmx-buttons { display: flex; width: 100%; height: 100%; gap: 6px;
    font-family: system-ui, sans-serif; }
.mmx-buttons button { flex: 1 1 0; min-width: 0; font: inherit; font-size: 12px;
    padding: 0 8px; border-radius: 6px; cursor: pointer;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmx-buttons button:hover { background: var(--comfy-menu-bg, #353535); }
.mmx-buttons button:active { background: #3B7DD8; border-color: #3B7DD8; color: #fff; }

.mmx-seg-row { display: flex; width: 100%; height: 100%; overflow: hidden;
    border: 1px solid var(--border-color, #4e4e4e); border-radius: 6px;
    font-family: system-ui, sans-serif; }
.mmx-seg { flex: 1 1 0; min-width: 0; display: flex; align-items: center;
    justify-content: center; font-size: 10px; letter-spacing: 0.02em;
    cursor: pointer; user-select: none; touch-action: none; text-align: center;
    color: var(--input-text, #ddd); background: var(--comfy-menu-bg, #353535);
    border-left: 1px solid var(--border-color, #4e4e4e); }
.mmx-seg:first-child { border-left: 0; }
.mmx-seg.mmx-on { background: #3B7DD8; color: #fff; font-weight: 600; }
.mmx-seg.mmx-shut { opacity: 0.4; cursor: not-allowed; }
.mmx-seg.mmx-on.mmx-shut { background: #8A3B3B; opacity: 1; }

.mmx-seg-row.mmx-tabs { border: 0; border-radius: 0; gap: 3px;
    border-bottom: 2px solid #3B7DD8; align-items: stretch; }
.mmx-tabs .mmx-seg { flex-direction: column; justify-content: center; gap: 1px;
    border-left: 0; border-radius: 6px 6px 0 0; font-size: 12px;
    background: var(--comfy-input-bg, #2b2b2b); }
.mmx-tabs .mmx-seg.mmx-on { background: #3B7DD8; }
.mmx-seg-sub { font-size: 9px; line-height: 10px; font-weight: 400;
    opacity: 0.75; }

.mmx-ratios { display: flex; flex-wrap: wrap; align-content: center;
    justify-content: center; gap: ${RATIO_GAP}px; width: 100%; height: 100%;
    overflow: hidden; font-family: system-ui, sans-serif; }
.mmx-ratio { flex: 1 1 0; min-width: ${RATIO_ITEM_W}px; max-width: ${RATIO_ITEM_MAX}px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 3px; cursor: pointer;
    user-select: none; touch-action: none; border-radius: 5px;
    border: 1px solid transparent; }
.mmx-ratio.mmx-on { border-color: #3B7DD8; background: rgba(59, 125, 216, 0.16); }
.mmx-ratios.mmx-driven { opacity: 0.4; }
.mmx-ratios.mmx-driven .mmx-ratio { cursor: not-allowed; }
.mmx-box { flex: 0 0 auto; box-sizing: border-box;
    border: 1.5px solid var(--descrip-text, #999); border-radius: 2px; }
.mmx-ratio.mmx-on .mmx-box { border-color: #7FB2F5;
    background: rgba(127, 178, 245, 0.28); }
.mmx-ratio-label { flex: 0 0 auto; font-size: 9px; line-height: 11px;
    color: var(--descrip-text, #999); }
.mmx-ratio.mmx-on .mmx-ratio-label { color: var(--input-text, #ddd); }
`;

export function installStyle(id, css) {
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.textContent = css;
    document.head.appendChild(style);
}

export function installBaseStyle() {
    installStyle(BASE_STYLE_ID, BASE_STYLE);
}

export function tickBox(on) {
    installBaseStyle();
    const box = document.createElement("div");
    box.className = "mmx-tick" + (on ? " mmx-on" : "");
    return box;
}

export const BUTTON_H = 24;
const BUTTON_MARGIN = 4;

export function buttonRow(node, name, buttons) {
    installBaseStyle();

    const row = document.createElement("div");
    row.className = "mmx-buttons";
    const made = buttons.map((spec) => {
        const button = document.createElement("button");
        button.textContent = spec.label;
        if (spec.tooltip) button.title = spec.tooltip;
        button.addEventListener("click", () => spec.onClick(button));
        row.appendChild(button);
        return button;
    });

    const widget = node.addDOMWidget(name, "minimaxh3_buttons", row, {
        hideOnZoom: false,
        margin: BUTTON_MARGIN,
        hideInPanel: true,
        getValue: () => undefined,
        setValue: () => {},
        getMinHeight: () => BUTTON_H + 2 * BUTTON_MARGIN,
        getMaxHeight: () => BUTTON_H + 2 * BUTTON_MARGIN,
    });
    widget.serialize = false;
    widget.serializeValue = () => undefined;
    return { widget, buttons: made };
}

export function told(button, text, seconds = 2.5) {
    if (!button) return;
    const before = button.textContent;
    button.textContent = text;
    setTimeout(() => {
        button.textContent = before;
    }, seconds * 1000);
}

export function widgetNamed(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

export function widgetValues(node, name) {
    return widgetNamed(node, name)?.options?.values ?? [];
}

export function setWidgetValue(node, name, value) {
    const widget = widgetNamed(node, name);
    if (!widget || widget.value === value) return;
    widget.value = value;
    widget.callback?.(value, app.canvas, node);
    refresh(node);
}

export function showWidget(node, name, shown) {
    const widget = widgetNamed(node, name);
    if (!widget) return;
    widget.hidden = !shown;
    widget.options = widget.options || {};
    widget.options.hidden = !shown;
    if (shown) delete widget.computeSize;
    else widget.computeSize = () => [0, -4];
}

export function replaceWithDom(node, name, type, element, height) {
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

export function renderSegments(holder, items, extraClass = "") {
    holder.className = `mmx-seg-row${extraClass ? " " + extraClass : ""}`;
    holder.replaceChildren();
    for (const item of items) {
        const button = document.createElement("div");
        button.className =
            "mmx-seg" + (item.on ? " mmx-on" : "") + (item.shut ? " mmx-shut" : "");
        button.title = item.title || item.label;

        const label = document.createElement("span");
        label.textContent = item.label;
        button.appendChild(label);

        if (item.sub) {
            const sub = document.createElement("span");
            sub.className = "mmx-seg-sub";
            sub.textContent = item.sub;
            button.appendChild(sub);
        }

        if (!item.shut && item.pick) {
            button.addEventListener("pointerdown", (event) => {
                event.preventDefault();
                event.stopPropagation();
                item.pick();
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
    box.style.width = `${Math.max(RATIO_BOX_MIN, Math.round(width * scale))}px`;
    box.style.height = `${Math.max(RATIO_BOX_MIN, Math.round(height * scale))}px`;
    return box;
}

export function ratiosHeight(node, name) {
    const count = widgetValues(node, name).length || 1;
    const usable = Math.max(node.size?.[0] ?? 300, 120) - 2 * MARGIN - RATIO_GAP;
    const perRow = Math.max(1, Math.floor((usable + RATIO_GAP) / (RATIO_ITEM_W + RATIO_GAP)));
    const rows = Math.max(1, Math.ceil(count / perRow));
    return rows * RATIO_ROW_H + (rows - 1) * RATIO_GAP;
}

export const ASPECT_INPUT = "aspect_ratio";
export const RESOLUTION_WIDGET = "resolution";

export function drivenByWire(node) {
    const input = node.inputs?.find((slot) => slot.name === ASPECT_INPUT);
    return !!input && input.link !== null && input.link !== undefined;
}

const DRIVEN_TITLE =
    "aspect_ratio is connected and decides the ratio, so this is not consulted. " +
    "Unplug it to choose here again.";

export function renderRatios(node, holder, name) {
    if (!holder) return;
    const chosen = widgetNamed(node, name)?.value;
    const driven = drivenByWire(node);
    holder.classList.toggle("mmx-driven", driven);

    holder.replaceChildren();
    for (const ratio of widgetValues(node, name)) {
        const item = document.createElement("div");
        item.className = "mmx-ratio" + (ratio === chosen && !driven ? " mmx-on" : "");
        item.title = driven ? DRIVEN_TITLE : `Compose for ${ratio}`;
        item.appendChild(ratioBox(ratio));

        const label = document.createElement("span");
        label.className = "mmx-ratio-label";
        label.textContent = ratio;
        item.appendChild(label);

        item.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (drivenByWire(node)) return;
            setWidgetValue(node, name, ratio);
        });
        holder.appendChild(item);
    }
}

export function repaintOn(nodeType, build) {
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

    const onResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function () {
        const result = onResize?.apply(this, arguments);
        this.setDirtyCanvas?.(true, true);
        return result;
    };
}
