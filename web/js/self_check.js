import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EVENT = "minimax_h3_rewriter.notices";
const SHOWN = 4;
const KIND_LABEL = { check: "Self-check", refusal: "Stopped" };

const LIFE_MIN = 10000;
const LIFE_MAX = 30000;
const LIFE_PER_CHAR = 45;
const STYLE_ID = "minimax-h3-self-check-style";

function installStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = [
        ".p-toast-detail { white-space: pre-line; }",
        ".mmx-selfcheck-toast .p-toast-detail {",
        "  text-indent: -1.1em; padding-left: 1.1em; line-height: 1.45;",
        "  text-indent: -1.1em each-line;",
        "}",
    ].join("\n");
    document.head.appendChild(style);
}

export function lifeFor(detail) {
    return Math.min(LIFE_MAX, Math.max(LIFE_MIN, (detail || "").length * LIFE_PER_CHAR));
}

export function toast(severity, summary, detail, styleClass) {
    app.extensionManager?.toast?.add({
        severity,
        summary,
        detail,
        life: lifeFor(detail),
        styleClass,
    });
}

function nodeTitle(id) {
    const node =
        app.graph?.getNodeById?.(id) ?? app.graph?.getNodeById?.(Number(id));
    return node?.title || node?.type || `node ${id}`;
}

app.registerExtension({
    name: "minimax_h3_rewriter.self_check",
    setup() {
        installStyle();
        api.addEventListener(EVENT, ({ detail }) => {
            const issues = detail?.issues || [];
            if (!issues.length) return;
            const lines = issues
                .slice(0, SHOWN)
                .map((issue) => (issue.level === "warn" ? "! " : "- ") + issue.message);
            if (issues.length > SHOWN) {
                lines.push(
                    `...and ${issues.length - SHOWN} more -- the full list is under ` +
                    "the node and in the console"
                );
            }
            const worst = issues.some((issue) => issue.level === "warn");
            const label = KIND_LABEL[detail.kind] || "Heads-up";
            console.log(
                `[MiniMax-H3 Prompt Rewriter] ${label.toLowerCase()} on ` +
                `${nodeTitle(detail.node)}:\n` +
                issues.map((issue) => issue.message).join("\n")
            );
            toast(
                worst ? "warn" : "info",
                `${label}: ${nodeTitle(detail.node)}`,
                lines.join("\n\n"),
                "mmx-selfcheck-toast"
            );
        });
    },
});
