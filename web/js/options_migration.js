import { app } from "../../scripts/app.js";

// ComfyUI restores a saved node's widgets_values by position, so 0.17.2
// inserting merge_lora between use_lora and auto_download broke every
// Options node saved before it: auto_download's boolean landed in
// merge_lora's combo, gpu_layers got n_ctx's 8192, n_ctx read "auto" as
// NaN, llama_backend got trust_remote_code's false - and validation
// refused the whole graph (issue #11).
//
// A boolean sitting in merge_lora's slot is the fingerprint of that old
// layout: merge_lora is a combo and never saves one. On seeing it, the
// saved array is dealt back out along the order the widgets had when the
// workflow was written. Widgets past the end of a short array - a workflow
// older still - keep their defaults, which is what positional matching
// would have done had nothing been inserted.
const PRE_MERGE_LORA_ORDER = [
    "max_new_tokens",
    "temperature",
    "top_p",
    "top_k",
    "repetition_penalty",
    "attn_implementation",
    "adapter",
    "use_lora",
    "auto_download",
    "gpu_layers",
    "n_ctx",
    "gguf_runtime",
    "device",
    "llama_backend",
    "trust_remote_code",
];

const MERGE_SLOT = PRE_MERGE_LORA_ORDER.indexOf("auto_download");

function byName(node, name) {
    return node.widgets?.find((widget) => widget.name === name);
}

function migrate(node, info) {
    const values = info?.widgets_values ?? node.widgets_values;
    if (!Array.isArray(values)) return;
    if (typeof values[MERGE_SLOT] !== "boolean") return;

    for (let i = 0; i < PRE_MERGE_LORA_ORDER.length && i < values.length; i++) {
        const widget = byName(node, PRE_MERGE_LORA_ORDER[i]);
        if (widget) widget.value = values[i];
    }
    const merge = byName(node, "merge_lora");
    if (merge) merge.value = "auto";

    node.setDirtyCanvas?.(true, true);
    console.log(
        "[minimax_h3_rewriter] Options node saved before 0.17.2: realigned its widget values"
    );
}

app.registerExtension({
    name: "minimax_h3_rewriter.options_migration",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "MiniMaxH3RewriterOptions") return;

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const result = onConfigure?.apply(this, arguments);
            migrate(this, info);
            return result;
        };
    },
});
