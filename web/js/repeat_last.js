import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const WIDGET = "repeat_last";
const EVENT = "minimax_h3_rewriter.memory";
const STATE_URL = "/minimax_h3_rewriter/memory";

const state = new Map();

function widgetOf(node) {
    return node?.widgets?.find((entry) => entry.name === WIDGET);
}

function describe(record) {
    if (!record?.stored) return "";
    const head = record.repeated
        ? `Just repeated the answer kept at ${record.clock}`
        : `Kept at ${record.clock}`;
    const changed = record.changed ? ", and the settings have changed since" : "";
    const more = record.chars > (record.preview?.length ?? 0) ? " ..." : "";
    return `${head}${changed} - ${record.chars} characters:\n\n${record.preview}${more}`;
}

function baseTooltip(node, name) {
    const data = node.constructor?.nodeData;
    if (!data) return "";
    const listed = Array.isArray(data.inputs)
        ? data.inputs.find((entry) => entry?.name === name)
        : data.inputs?.[name];
    if (listed?.tooltip) return listed.tooltip;
    const spec = data.input?.optional?.[name] ?? data.input?.required?.[name];
    return (Array.isArray(spec) ? spec[1]?.tooltip : undefined) ?? "";
}

function apply(node) {
    const widget = widgetOf(node);
    if (!widget) return;
    if (!widget.mmxBaseTooltip) widget.mmxBaseTooltip = widget.tooltip || baseTooltip(node, WIDGET);

    const base = widget.mmxBaseTooltip;
    const note = describe(state.get(String(node.id)));
    if (!note) widget.tooltip = base;
    else widget.tooltip = base ? `${base}\n\n--\n${note}` : note;
}

export function recordFor(nodeId) {
    return state.get(String(nodeId));
}

function applyAll() {
    for (const node of app.graph?.nodes ?? []) apply(node);
}

async function pull() {
    try {
        const response = await api.fetchApi(STATE_URL);
        if (!response.ok) return;
        const records = await response.json();
        state.clear();
        for (const [id, record] of Object.entries(records ?? {})) state.set(String(id), record);
        applyAll();
    } catch (error) {
        console.debug("[minimax_h3_rewriter] could not read the prompt memory", error);
    }
}

app.registerExtension({
    name: "minimax_h3_rewriter.repeat_last",
    setup() {
        api.addEventListener(EVENT, (event) => {
            const detail = event.detail ?? {};
            const id = String(detail.node ?? "");
            if (!id) return;
            if (detail.stored) state.set(id, detail);
            else state.delete(id);
            apply(app.graph?.getNodeById?.(Number(id)) ?? app.graph?.getNodeById?.(id));
        });
        pull();
    },
    afterConfigureGraph() {
        pull();
    },
});
