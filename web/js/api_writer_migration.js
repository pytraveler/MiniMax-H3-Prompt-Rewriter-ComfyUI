const ORDER = "minimax_h3_api_widget_order";
const LEGACY = [
    "profile_id", "task", "prompt", "resolution", "duration", "max_tokens",
    "system_prompt", "validation", "response_format", "temperature", "seed",
    "control_after_generate", "timeout_seconds", "repair_once", "auto_download_guide",
    "reference_layout", "reference_instructions", "image_max_side", "repeat_last",
    "library_pick", "media_mode", "reference_descriptions", "video_max_frames",
    "video_sample_seconds", "video_max_seconds", "batch_video_fps", "audio_max_seconds",
    "audio_sample_rate", "api_recipient",
];

export function moveApiControls(node) {
    const first = ["reference_layout", "task", "resolution", "duration", "profile_id", "model", "prompt", "system_prompt"];
    const widgets = node.widgets || [];
    const front = first.map((name) => widgets.find((widget) => widget.name === name)).filter(Boolean);
    node.widgets.splice(0, widgets.length, ...front, ...widgets.filter((widget) => !front.includes(widget)));
}

export function addApiWidgetMigration(nodeType) {
    const configure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info, ...rest) {
        if (!Array.isArray(info?.widgets_values)) return configure.call(this, info, ...rest);
        const marked = info.properties?.[ORDER];
        const order = Array.isArray(marked) && marked.every((name) => typeof name === "string") ? marked : LEGACY;
        const saved = new Map(order.slice(0, info.widgets_values.length).map((name, index) => [name, info.widgets_values[index]]));
        if (info.widgets_values_named && typeof info.widgets_values_named === "object") {
            for (const [name, value] of Object.entries(info.widgets_values_named)) saved.set(name, value);
        }
        const values = (this.widgets || []).map((widget) => saved.has(widget.name) ? saved.get(widget.name) : widget.value);
        const named = Object.fromEntries((this.widgets || []).map((widget, index) => [widget.name, values[index]]));
        const properties = { ...info.properties };
        const duration = saved.get("duration");
        if (properties.max_duration === undefined && Number.isFinite(duration) && duration > 30) {
            properties.max_duration = Math.min(600, Math.ceil(duration * 10) / 10);
        }
        return configure.call(this, { ...info, properties, widgets_values: values, widgets_values_named: named }, ...rest);
    };

    const serialize = nodeType.prototype.onSerialize;
    nodeType.prototype.onSerialize = function (info) {
        const result = serialize?.apply(this, arguments);
        info.properties = { ...info.properties, [ORDER]: (this.widgets || []).map((widget) => widget.name) };
        return result;
    };
}
