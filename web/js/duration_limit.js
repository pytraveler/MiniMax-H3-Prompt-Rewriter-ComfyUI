import { app } from "../../scripts/app.js";
import { setWidgetValue, widgetNamed } from "./mmx_controls.js";

export const DURATION_NODES = [
    "MiniMaxH3PromptRewriter",
    "MiniMaxH3PromptWriter8B",
    "MiniMaxH3PromptWriterOmni",
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3GuidePrompt",
    "MiniMaxH3PromptCheck",
    "MiniMaxH3UniversalWriter",
    "MiniMaxH3UniversalRewriter",
];

const WIDGET = "duration";
const PROPERTY = "max_duration";
const PROPERTY_DEFAULT = 30;

const SPEC = "__minimaxH3DurationSpec";
const FALLBACK = { start: 10, floor: 0.1, ceiling: 600 };

function specOf(node) {
    return node?.[SPEC] || FALLBACK;
}

function tenths(value) {
    return Math.round(Number(value) * 10) / 10;
}

function seconds(value) {
    return `${tenths(value)} s`;
}

function ceilingOf(node) {
    const spec = specOf(node);
    const asked = tenths(node.properties?.[PROPERTY]);
    if (!isFinite(asked) || asked <= spec.floor) return PROPERTY_DEFAULT;
    return Math.min(asked, spec.ceiling);
}

export function applyDurationCeiling(node) {
    const widget = widgetNamed(node, WIDGET);
    if (!widget?.options) return;

    const ceiling = ceilingOf(node);
    widget.options.max = ceiling;
    if (Number(widget.value) > ceiling) widget.value = ceiling;
}

function askFor(title, message, value) {
    const dialog = app.extensionManager?.dialog;
    if (dialog?.prompt) return dialog.prompt({ title, message, defaultValue: value });
    return Promise.resolve(window.prompt(message, value));
}

async function askForCeiling(node) {
    const spec = specOf(node);
    const typed = await askFor(
        "duration",
        `The longest duration this node should offer, ${seconds(spec.floor)} to ` +
            `${seconds(spec.ceiling)}. It is kept to a tenth of a second and remembered ` +
            "with the workflow; the server takes the whole range whatever this says.",
        String(ceilingOf(node))
    );
    if (typed === null || typed === undefined) return;

    const asked = Number(String(typed).trim().replace(",", "."));
    if (!isFinite(asked)) return;

    const ceiling = Math.min(Math.max(tenths(asked), spec.floor), spec.ceiling);
    if (node.setProperty) node.setProperty(PROPERTY, ceiling);
    else if (node.properties) node.properties[PROPERTY] = ceiling;

    applyDurationCeiling(node);
    node.setDirtyCanvas?.(true, true);
}

function menuFor(node) {
    const spec = specOf(node);
    return [
        {
            content: `Default value (${seconds(spec.start)})`,
            callback: () => {
                setWidgetValue(node, WIDGET, spec.start);
                node.setDirtyCanvas?.(true, true);
            },
        },
        {
            content: `Longest offered (now ${seconds(ceilingOf(node))})...`,
            callback: () => {
                void askForCeiling(node);
            },
        },
    ];
}

function pointerOver(node, canvas, widget) {
    try {
        const [x, y] = canvas?.graph_mouse || [];
        return node.getWidgetOnPos?.(x, y, true) === widget;
    } catch (error) {
        return false;
    }
}

export function addDurationLimit(nodeType, nodeData) {
    const declared =
        nodeData?.input?.required?.[WIDGET] || nodeData?.input?.optional?.[WIDGET];
    const options = (Array.isArray(declared) ? declared[1] : null) || {};
    nodeType.prototype[SPEC] = {
        start: Number(options.default ?? FALLBACK.start),
        floor: Number(options.min ?? FALLBACK.floor),
        ceiling: Number(options.max ?? FALLBACK.ceiling),
    };

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        if (this.properties?.[PROPERTY] === undefined) {
            this.addProperty?.(PROPERTY, PROPERTY_DEFAULT, "number");
        }
        applyDurationCeiling(this);
        return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const result = onConfigure?.apply(this, arguments);
        applyDurationCeiling(this);
        return result;
    };

    const onPropertyChanged = nodeType.prototype.onPropertyChanged;
    nodeType.prototype.onPropertyChanged = function (name) {
        const result = onPropertyChanged?.apply(this, arguments);
        if (name === PROPERTY) {
            applyDurationCeiling(this);
            this.setDirtyCanvas?.(true, true);
        }
        return result;
    };

    const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
        const result = getExtraMenuOptions?.apply(this, arguments);
        const widget = widgetNamed(this, WIDGET);
        if (widget && Array.isArray(options)) {
            const entry = {
                content: widget.label || WIDGET,
                has_submenu: true,
                submenu: { title: WIDGET, options: menuFor(this) },
            };
            if (pointerOver(this, canvas, widget)) options.unshift(entry);
            else options.push(entry);
        }
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.duration_limit",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (DURATION_NODES.includes(nodeData.name)) addDurationLimit(nodeType, nodeData);
    },
});
