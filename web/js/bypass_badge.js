import { app } from "../../scripts/app.js";


const BYPASS_NODES = [
    "MiniMaxH3PromptRewriter",
    "MiniMaxH3PromptWriter8B",
    "MiniMaxH3PromptWriterOmni",
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3ReferenceCaption",
    "MiniMaxH3MultiReferenceCaption",
    "MiniMaxH3UniversalWriter",
    "MiniMaxH3UniversalRewriter",
];

const WIDGET = "bypass";
const BADGE = "__minimaxH3BypassBadge";

const TITLE_COLOR = "#5B3A7E";
const BODY_COLOR = "#3A2750";
const ON_BG = "#7A3FA0";
const ON_FG = "#FFFFFF";
const OFF_BG = "#353535";
const OFF_FG = "#B0B0B0";

function widgetOf(node) {
    return node.widgets?.find((w) => w.name === WIDGET);
}

function isBypassed(node) {
    return !!widgetOf(node)?.value;
}

function inheritedGetter(node, name) {
    for (let proto = Object.getPrototypeOf(node); proto; proto = Object.getPrototypeOf(proto)) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, name);
        if (descriptor) return descriptor.get;
    }
    return undefined;
}

function tint(node) {
    const swaps = [
        ["renderingColor", "color", TITLE_COLOR],
        ["renderingBgColor", "bgcolor", BODY_COLOR],
    ];
    for (const [name, field, colour] of swaps) {
        const inherited = inheritedGetter(node, name);
        if (!inherited) continue;
        Object.defineProperty(node, name, {
            configurable: true,
            get() {
                if (!isBypassed(this)) return inherited.call(this);
                const before = this[field];
                this[field] = colour;
                try {
                    return inherited.call(this);
                } finally {
                    this[field] = before;
                }
            },
        });
    }
}

function clearStoredTint(node) {
    if (node.color === TITLE_COLOR && node.bgcolor === BODY_COLOR) {
        node.color = undefined;
        node.bgcolor = undefined;
    }
}

function toggle(node, widget) {
    if (app.canvas?.low_quality) return;
    widget.value = !widget.value;
    widget.callback?.(widget.value, app.canvas, node);
    node.setDirtyCanvas(true, true);
}

function badgeOf(node) {
    let badge = node[BADGE];
    if (!badge) badge = node[BADGE] = new window.LGraphBadge({ text: "" });
    return badge;
}

function refresh(node) {
    const badge = badgeOf(node);
    const widget = widgetOf(node);
    const on = !!widget?.value;

    if (!widget || (!on && !node.flags?.collapsed)) {
        badge.text = "";
        badge.onClick = undefined;
        return badge;
    }

    badge.text = on ? "BYPASSED" : "bypass";
    badge.fgColor = on ? ON_FG : OFF_FG;
    badge.bgColor = on ? ON_BG : OFF_BG;
    badge.onClick = () => toggle(node, widget);
    return badge;
}

function addBadge(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        tint(this);
        if (window.LGraphBadge && Array.isArray(this.badges)) {
            this.badges.push(() => refresh(this));
        }
        return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const result = onConfigure?.apply(this, arguments);
        clearStoredTint(this);
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.bypass_badge",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (BYPASS_NODES.includes(nodeData.name)) addBadge(nodeType);
    },
});
