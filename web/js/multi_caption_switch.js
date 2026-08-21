import { app } from "../../scripts/app.js";
import { addSlotSwitches, hideWidget } from "./slot_switches.js";


const NODE = "MiniMaxH3MultiReferenceCaption";
const MASK = "enabled_mask";
const PREFIXES = ["subject_", "picture_", "video_", "audio_"];

function maskWidget(node) {
    return node.widgets?.find((w) => w.name === MASK);
}

function readMask(node) {
    try {
        const parsed = JSON.parse(maskWidget(node)?.value ?? "{}");
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
    const widget = maskWidget(node);
    if (widget) widget.value = JSON.stringify(mask);
}

function addSwitches(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        hideWidget(maskWidget(this));
        return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const result = onConfigure?.apply(this, arguments);
        hideWidget(maskWidget(this));
        return result;
    };

    addSlotSwitches(nodeType, { prefixes: PREFIXES, enabled: isEnabled, toggle });
}

app.registerExtension({
    name: "minimax_h3_rewriter.multi_caption_switch",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) addSwitches(nodeType);
    },
});
