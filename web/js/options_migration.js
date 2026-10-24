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

// 0.31.0 removed trust_remote_code, which sat between llama_backend and
// prompt_file. A workflow could switch it on, and with it on the pack ran
// the Python a model ships with. Every layout saved from 0.17.2 to 0.30.0
// holds that boolean one slot further on than before 0.17.2, merge_lora
// having been inserted ahead of it - the slot prompt_file, a combo, takes
// now. A boolean there is the fingerprint, and the value is dropped.
//
// This one is fixed before the values are dealt out, not after them as the
// merge_lora one is. Dealt out first, a workflow from 0.18.0, which ends at
// prompt_file, would leave its last value in self_check, and by then nothing
// remembers self_check's default to put back. The list above keeps the name:
// it describes the layout of its time, and a widget it no longer finds is
// skipped.
//
// A recent frontend also saves every widget as an input slot, and configure
// restores the saved slots as they are, so the old one would stay on the
// node as a bare socket. It is taken off with removeInput once the node is
// configured: that is the call which moves the links of the slots after it.
const TRUST_SLOT = PRE_MERGE_LORA_ORDER.indexOf("trust_remote_code") + 1;

function withoutTrustRemoteCode(info) {
    const values = info?.widgets_values;
    if (!Array.isArray(values) || typeof values[TRUST_SLOT] !== "boolean") return info;

    const kept = values.slice();
    kept.splice(TRUST_SLOT, 1);
    console.log(
        "[minimax_h3_rewriter] Options node saved before 0.31.0: dropped trust_remote_code's value"
    );
    return { ...info, widgets_values: kept };
}

function dropTrustRemoteCodeInput(node) {
    const index = node.inputs?.findIndex((input) => input.name === "trust_remote_code") ?? -1;
    if (index >= 0) node.removeInput(index);
}

app.registerExtension({
    name: "minimax_h3_rewriter.options_migration",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "MiniMaxH3RewriterOptions") return;

        const configure = nodeType.prototype.configure;
        nodeType.prototype.configure = function (info, ...rest) {
            const result = configure.call(this, withoutTrustRemoteCode(info), ...rest);
            dropTrustRemoteCodeInput(this);
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const result = onConfigure?.apply(this, arguments);
            migrate(this, info);
            return result;
        };
    },
});
