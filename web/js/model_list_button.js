import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { buttonRow } from "./mmx_controls.js";

const MODEL_LIST_NODES = [
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
const GUIDE_NODES = [
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3GuidePrompt",
    "MiniMaxH3UniversalWriter",
];

const MODEL_LIST = {
    url: "/minimax_h3_rewriter/open_model_list",
    label: "Open model list",
    what: "the model list",
    fallback: "ComfyUI/user/minimax_h3_rewriter/models.json",
    tooltip:
        "Opens models.json in the ComfyUI user directory. 'models', 'models_8b' and " +
        "'models_omni' feed the three LoRA rewriters, 'writers' the guided writers, " +
        "'captioners' the caption nodes. Refresh the browser to see your edits in " +
        "the dropdown.",
};

const GUIDE_FOLDER = {
    url: "/minimax_h3_rewriter/open_guide_folder",
    label: "Open guide folder",
    what: "the guide folder",
    fallback: "ComfyUI/user/minimax_h3_rewriter/guides",
    tooltip:
        "Opens the folder holding MiniMax's prompt-writing guides, fetched on first " +
        "use. Editing one changes the system prompt; trimming it is the cheapest way " +
        "to fit a small model's context.",
};

async function open(action) {
    let response;
    try {
        response = await api.fetchApi(action.url, { method: "POST" });
    } catch (error) {
        alert(`Could not reach the ComfyUI server: ${error}`);
        return;
    }

    let payload = {};
    try {
        payload = await response.json();
    } catch (error) {
        payload = {};
    }

    if (!response.ok || !payload.ok) {
        alert(
            `Could not open ${action.what}.\n\n` +
                (payload.error || `HTTP ${response.status}`) +
                "\n\nOpen it by hand instead:\n" +
                (payload.path || action.fallback)
        );
        return;
    }

    console.log(`[MiniMax-H3 Prompt Rewriter] opened ${payload.path}`);
}

function addButtons(nodeType, actions) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        buttonRow(
            this,
            "mmx_open",
            actions.map((action) => ({
                label: action.label,
                tooltip: action.tooltip,
                onClick: () => open(action),
            }))
        );
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.model_list",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        const actions = [];
        if (MODEL_LIST_NODES.includes(nodeData.name)) actions.push(MODEL_LIST);
        if (GUIDE_NODES.includes(nodeData.name)) actions.push(GUIDE_FOLDER);
        if (actions.length) addButtons(nodeType, actions);
    },
});
