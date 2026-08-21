import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const MODEL_LIST_NODES = [
    "MiniMaxH3PromptRewriter",
    "MiniMaxH3PromptWriter8B",
    "MiniMaxH3GuidedWriter",
    "MiniMaxH3GuidedWriterRef",
    "MiniMaxH3ReferenceCaption",
    "MiniMaxH3MultiReferenceCaption",
    "MiniMaxH3UniversalWriter",
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
        "Opens models.json in the ComfyUI user directory. 'models' feeds the LoRA " +
        "rewriter, 'writers' the guided writers, 'captioners' the reference caption " +
        "node. Refresh the browser to see your edits in the dropdown.",
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

function addButton(nodeType, action) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const result = onNodeCreated?.apply(this, arguments);
        const widget = this.addWidget("button", action.label, null, () => open(action));
        widget.serialize = false;
        widget.tooltip = action.tooltip;
        return result;
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.model_list",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (MODEL_LIST_NODES.includes(nodeData.name)) addButton(nodeType, MODEL_LIST);
        if (GUIDE_NODES.includes(nodeData.name)) addButton(nodeType, GUIDE_FOLDER);
    },
});
