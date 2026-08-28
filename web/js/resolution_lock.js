import { app } from "../../scripts/app.js";
import { ASPECT_INPUT, RESOLUTION_WIDGET, drivenByWire, refresh } from "./mmx_controls.js";

const NODES = [
    "MiniMaxH3PromptRewriter",
    "MiniMaxH3PromptWriter8B",
    "MiniMaxH3PromptWriterOmni",
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3GuidePrompt",
    "MiniMaxH3UniversalWriter",
    "MiniMaxH3UniversalRewriter",
];

function apply(node) {
    const widget = node.widgets?.find((entry) => entry.name === RESOLUTION_WIDGET);
    if (!widget) return;

    const driven = drivenByWire(node);
    widget.disabled = driven;
    widget.options = widget.options || {};
    widget.options.disabled = driven;

    refresh(node);
    node.setDirtyCanvas?.(true, true);
}

function wireLeft(node) {
    const widget = node.widgets?.find((entry) => entry.name === ASPECT_INPUT);
    if (!widget || widget.value === "") return;
    widget.value = "";
    widget.callback?.("", app.canvas, node);
}

const OUTPUT_SLOT = 2;

function follow(nodeType) {
    for (const hook of ["onNodeCreated", "onConfigure"]) {
        const original = nodeType.prototype[hook];
        nodeType.prototype[hook] = function () {
            const result = original?.apply(this, arguments);
            apply(this);
            return result;
        };
    }

    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function (type, index, connected, link, slot) {
        const result = onConnectionsChange?.apply(this, arguments);
        const name = slot?.name ?? (type === OUTPUT_SLOT ? undefined : this.inputs?.[index]?.name);
        if (!connected && type !== OUTPUT_SLOT && name === ASPECT_INPUT) wireLeft(this);
        apply(this);
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.resolution_lock",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (NODES.includes(nodeData.name)) follow(nodeType);
    },
});
