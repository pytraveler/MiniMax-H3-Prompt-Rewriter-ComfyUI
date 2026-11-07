import { app } from "../../scripts/app.js";
import { installStyle } from "./mmx_controls.js";

const SELECTOR = ".mmx-api-input-switch";

export function apiInputSwitches(nodes, enabled, toggle) {
    let pending = false;
    const update = () => {
        const graph = app.canvas?.graph ?? app.graph;
        for (const root of document.querySelectorAll(".lg-node[data-node-id]")) {
            const node = graph?.getNodeById?.(root.dataset.nodeId);
            if (!nodes.has(node) || root.hasAttribute("data-collapsed")) continue;
            for (const row of root.querySelectorAll(".lg-slot--input")) {
                const key = row.querySelector("[data-slot-key]")?.dataset.slotKey;
                const match = /-in-(\d+)$/.exec(key || "");
                const input = match && node.inputs?.[Number(match[1])];
                let check = row.querySelector(SELECTOR);
                if (!/^ref_(?:[0-9]|1[01])$/.test(input?.name || "")) {
                    check?.remove();
                    continue;
                }
                if (!check) {
                    check = document.createElement("input");
                    check.type = "checkbox";
                    check.className = SELECTOR.slice(1);
                    for (const type of ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick", "keydown", "keyup"]) {
                        check.addEventListener(type, (event) => event.stopPropagation());
                    }
                    check.addEventListener("change", () => {
                        const { node: current, name } = check.mmxSlot;
                        if (current.graph === (app.canvas?.graph ?? app.graph)) toggle(current, name);
                    });
                    row.appendChild(check);
                }
                check.mmxSlot = { node, name: input.name };
                check.dataset.slot = input.name;
                check.setAttribute("aria-label", `Enable ${input.name}`);
                check.title = `Enable or disable ${input.name}; keep its connection`;
                check.checked = enabled(node, input.name);
                check.classList.toggle("mmx-unconnected", input.link == null);
            }
        }
    };
    const schedule = () => {
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => { pending = false; update(); });
    };
    installStyle("minimax-h3-api-input-switch-style", `
.mmx-api-input-switch { appearance: none; box-sizing: border-box; flex: 0 0 14px;
    width: 14px; height: 14px; margin: 0 0 0 12px; padding: 0; position: relative;
    border: 1px solid #6A6A6A; border-radius: 3px; background: #2B2B2B; cursor: pointer; }
.mmx-api-input-switch:checked { background: #4A9D5B; }
.mmx-api-input-switch:checked::after { content: ""; position: absolute; left: 4px; top: 1px;
    width: 4px; height: 8px; border: solid #fff; border-width: 0 2px 2px 0; transform: rotate(45deg); }
.mmx-api-input-switch.mmx-unconnected { opacity: 0.35; }
.mmx-api-input-switch:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
`);
    const observer = new MutationObserver((records) => {
        if (records.some((record) => record.target.closest?.(".lg-node") ||
            [...record.addedNodes].some((added) => added.matches?.(".lg-node") || added.querySelector?.(".lg-node")))) schedule();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return schedule;
}
