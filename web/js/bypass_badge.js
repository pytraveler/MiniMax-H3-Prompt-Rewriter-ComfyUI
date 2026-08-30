import { app } from "../../scripts/app.js";

const WRITERS = [
    "MiniMaxH3PromptRewriter",
    "MiniMaxH3PromptWriter8B",
    "MiniMaxH3PromptWriterOmni",
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3UniversalWriter",
    "MiniMaxH3UniversalRewriter",
];
const CAPTIONERS = ["MiniMaxH3ReferenceCaption", "MiniMaxH3MultiReferenceCaption"];

const TITLE_COLOR = "#5B3A7E";
const BODY_COLOR = "#3A2750";
const OFF_BG = "#353535";
const OFF_FG = "#B0B0B0";

function widgetOf(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function chosenName(node) {
    const raw = widgetOf(node, "library_pick")?.value;
    if (!raw) return "";
    try {
        return JSON.parse(raw)?.name || "";
    } catch (error) {
        return "";
    }
}

const BADGES = [
    {
        widget: "bypass",
        nodes: [...WRITERS, ...CAPTIONERS],
        on: () => "BYPASSED",
        off: "bypass",
        onBg: "#7A3FA0",
        onFg: "#FFFFFF",
        tint: true, always: true,
    },
    {
        widget: "repeat_last",
        nodes: [...WRITERS, ...CAPTIONERS],
        on: (node) => (chosenName(node) ? "LIBRARY" : "REPEAT"),
        off: "repeat_last",
        onBg: "#3B7DD8",
        onFg: "#FFFFFF",
        tint: false, always: true,
    },
];

function inheritedGetter(node, name) {
    for (let proto = Object.getPrototypeOf(node); proto; proto = Object.getPrototypeOf(proto)) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, name);
        if (descriptor) return descriptor.get;
    }
    return undefined;
}

function tint(node, spec) {
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
                if (!widgetOf(this, spec.widget)?.value) return inherited.call(this);
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

function badgeOf(node, spec) {
    const key = `__minimaxH3Badge_${spec.widget}`;
    let badge = node[key];
    if (!badge) badge = node[key] = new window.LGraphBadge({ text: "" });
    return badge;
}

function refresh(node, spec) {
    const badge = badgeOf(node, spec);
    const widget = widgetOf(node, spec.widget);
    const on = !!widget?.value;

    if (!widget || (!on && !node.flags?.collapsed && !spec.always)) {
        badge.text = "";
        badge.onClick = undefined;
        return badge;
    }

    badge.text = on ? spec.on(node) : spec.off;
    badge.fgColor = on ? spec.onFg : OFF_FG;
    badge.bgColor = on ? spec.onBg : OFF_BG;
    badge.onClick = () => toggle(node, widget);
    return badge;
}

function addBadges(nodeType, specs) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        for (const spec of specs) {
            if (spec.tint) tint(this, spec);
            if (window.LGraphBadge && Array.isArray(this.badges)) {
                this.badges.push(() => refresh(this, spec));
            }
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
        const specs = BADGES.filter((spec) => spec.nodes.includes(nodeData.name));
        if (specs.length) addBadges(nodeType, specs);
    },
});
