import { app } from "../../scripts/app.js";
import { addSlotSwitches, hideWidget } from "./slot_switches.js";
import {
    installBaseStyle,
    onRefresh,
    renderRatios,
    renderSegments,
    replaceWithDom,
    repaintOn,
    setWidgetValue,
    showWidget,
    widgetNamed,
    widgetValues,
} from "./mmx_controls.js";


const NODE = "MiniMaxH3UniversalRewriter";

const LORA = "lora";
const TASK = "task";
const RESOLUTION = "resolution";
const SWITCHES = "frame_switches";

const LORA_27B = "27B LoRA";
const LORA_8B = "8B LoRA";
const LORA_OMNI = "Omni LoRA";

const TEXT_TASK = "T2VA";

const PER_TAB = {
    [LORA_27B]: ["model_27b", "quantization_27b"],
    [LORA_8B]: ["model_8b", "quantization_8b"],
    [LORA_OMNI]: ["model_omni", "quantization_omni"],
};

const TAB_SUB = {
    [LORA_27B]: "text only",
    [LORA_8B]: "sees frames",
    [LORA_OMNI]: "sees, hears",
};

const TAB_TITLE = {
    [LORA_27B]:
        "Qwen3.6-27B, one task. Reference frames reach it only as words, so the two IMAGE " +
        "inputs are not read on this tab.",
    [LORA_8B]:
        "Qwen3-VL-8B, four tasks. It looks at the frames you connect and writes the alignment " +
        "line from what it sees.",
    [LORA_OMNI]:
        "Qwen2.5-Omni-7B, the same four tasks here. It looks at the frames too, and it is the " +
        "one that hears -- but sound, clips and the six-field Ref2AV task are on the Prompt " +
        "Rewriter Omni node, which has the strip they need.",
};

const REF_TASK = "Ref2VA";

const FRAME_FOR_TASK = {
    T2VA: [],
    I2VA: ["first_frame"],
    FL2VA: ["first_frame", "last_frame"],
    L2VA: ["last_frame"],
};

const REFERENCE_SLOTS = ["first_frame", "last_frame", "reference_video", "reference_audio"];

const HEARD_SLOTS = ["reference_video", "reference_audio"];

const PREFIXES = ["first_", "last_", "reference_"];

const TABS_H = 34;
const TASKS_H = 26;
const RATIOS_H = 38;

const STATE = "__minimaxH3UniversalRewriter";


function readSwitches(node) {
    try {
        const parsed = JSON.parse(widgetNamed(node, SWITCHES)?.value ?? "{}");
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
        return {};
    }
}

function isSlotEnabled(node, name) {
    const map = readSwitches(node);
    return map[name] === undefined ? true : !!map[name];
}

function toggleSlot(node, name) {
    const map = readSwitches(node);
    map[name] = !isSlotEnabled(node, name);
    for (const key of Object.keys(map)) {
        if (map[key] !== false) delete map[key];
    }
    const widget = widgetNamed(node, SWITCHES);
    if (widget) widget.value = JSON.stringify(map);
    redraw(node);
}

function frameReady(node, name) {
    if (!isSlotEnabled(node, name)) return false;
    const input = (node.inputs || []).find((slot) => {
        const label = String(slot.name || "");
        return label.slice(label.lastIndexOf(".") + 1) === name;
    });
    return input?.link !== null && input?.link !== undefined;
}

function currentTab(node) {
    const chosen = widgetNamed(node, LORA)?.value;
    return chosen === LORA_8B || chosen === LORA_OMNI ? chosen : LORA_27B;
}


function renderTabs(node) {
    const holder = node[STATE]?.tabs;
    if (!holder) return;
    const chosen = currentTab(node);

    renderSegments(
        holder,
        widgetValues(node, LORA).map((name) => ({
            label: name,
            sub: TAB_SUB[name],
            on: name === chosen,
            title: TAB_TITLE[name] || name,
            pick: () => setWidgetValue(node, LORA, name),
        })),
        "mmx-tabs"
    );
}

function whyShut(node, name, tab) {
    if (tab === LORA_27B) {
        return name === TEXT_TASK
            ? ""
            : `${name} needs the frames, which only the 8B and Omni LoRAs read. Switch tabs.`;
    }
    if (name === REF_TASK) {
        if (tab !== LORA_OMNI) {
            return (
                "Ref2VA is the full-reference task, and only the Omni LoRA was trained on it. " +
                "Switch to the Omni tab."
            );
        }
        return REFERENCE_SLOTS.some((slot) => frameReady(node, slot))
            ? ""
            : "Ref2VA is written from at least one reference, and nothing is connected. Plug " +
              "in a frame, a clip or a sound and switch its row on.";
    }
    const missing = (FRAME_FOR_TASK[name] || []).filter((slot) => !frameReady(node, slot));
    if (missing.length) {
        return (
            `${name} is written from ${missing.join(" and ")}, which is not connected or is ` +
            "switched off on its row."
        );
    }
    const heard = HEARD_SLOTS.filter((slot) => frameReady(node, slot));
    if (heard.length && tab === LORA_OMNI) {
        return (
            `${name} is written from pictures alone, and ${heard.join(" and ")} ` +
            `${heard.length > 1 ? "are" : "is"} connected. Switch those rows off, or pick ` +
            "Ref2VA."
        );
    }
    return "";
}

function renderTasks(node) {
    const holder = node[STATE]?.tasks;
    if (!holder) return;

    const tab = currentTab(node);
    const text = tab === LORA_27B;
    const chosen = text ? TEXT_TASK : widgetNamed(node, TASK)?.value;

    renderSegments(
        holder,
        widgetValues(node, TASK).map((name) => {
            const why = whyShut(node, name, tab);
            let title = why || name;
            if (text && name === TEXT_TASK) {
                title =
                    "The 27B LoRA writes T2VA and nothing else. Switch to the 8B or Omni tab " +
                    "to choose a task -- the one you had there is still set.";
            }
            return {
                label: name,
                on: name === chosen,
                shut: !!why,
                title,
                pick: text ? null : () => setWidgetValue(node, TASK, name),
            };
        })
    );
}

function applyTab(node) {
    const chosen = currentTab(node);
    for (const [tab, names] of Object.entries(PER_TAB)) {
        for (const name of names) showWidget(node, name, tab === chosen);
    }
}


function redraw(node) {
    if (!node[STATE]) return;
    hideWidget(widgetNamed(node, SWITCHES));
    applyTab(node);
    renderTabs(node);
    renderTasks(node);
    renderRatios(node, node[STATE].ratios, RESOLUTION);
    node.setDirtyCanvas?.(true, true);
}

function build(node) {
    installBaseStyle();

    const tabs = document.createElement("div");
    const tasks = document.createElement("div");
    const ratios = document.createElement("div");
    ratios.className = "mmx-ratios";

    node[STATE] = { tabs, tasks, ratios };
    onRefresh(node, () => redraw(node));

    replaceWithDom(node, LORA, "minimaxh3_lora", tabs, () => TABS_H);
    replaceWithDom(node, TASK, "minimaxh3_task", tasks, () => TASKS_H);
    replaceWithDom(node, RESOLUTION, "minimaxh3_ratio", ratios, () => RATIOS_H);

    redraw(node);
}

function addControls(nodeType) {
    repaintOn(nodeType, build);

    addSlotSwitches(nodeType, {
        prefixes: PREFIXES,
        enabled: isSlotEnabled,
        toggle: toggleSlot,
    });
}

app.registerExtension({
    name: "minimax_h3_rewriter.universal_rewriter_widgets",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE) addControls(nodeType);
    },
});
