import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { installStyle, refresh, setWidgetValue, widgetNamed } from "./mmx_controls.js";
import { frame } from "./mmx_window.js";

const URL = "/minimax_h3_rewriter/ph3/frames";
const FIELD = "frame_selection";
const records = new Map();
const PAGE = 12;
const STYLE = `
.mmxlib-panel.mmxlib-wide.mmx-ph3-picker { width: min(740px, 92vw); padding: 16px;
    box-sizing: border-box; font: 13px system-ui; }
.mmx-ph3-picker h3 { flex: 0 0 auto; margin: 0 0 8px; }
.mmx-ph3-frame-content { flex: 1 1 auto; min-height: 0; overflow: auto;
    display: flex; flex-direction: column; }
.mmx-ph3-frame-content > * { flex-shrink: 0; }
.mmx-ph3-picker button, .mmx-ph3-picker input { font: inherit; color: inherit;
    border: 1px solid #666; border-radius: 4px; background: var(--comfy-input-bg, #222); }
.mmx-ph3-picker button { padding: 5px 9px; cursor: pointer; }
.mmx-ph3-picker button:disabled { opacity: .4; cursor: default; }
.mmx-ph3-picker button:focus-visible { outline: 2px solid #75adff; }
.mmx-ph3-frame-view { flex: 1 0 min(30vh, 280px); width: 100%; min-height: 0;
    height: min(30vh, 280px); object-fit: contain; background: #181818; }
.mmx-ph3-frame-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.mmx-ph3-frame-toolbar input[type=range] { flex: 1 1 150px; min-width: 80px; }
.mmx-ph3-frame-toolbar input[type=number] { width: 78px; padding: 4px; }
.mmx-ph3-filmstrip { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 5px; }
.mmx-ph3-filmstrip label { min-width: 0; border: 1px solid #555; border-radius: 5px; overflow: hidden; }
.mmx-ph3-filmstrip label.mmx-selected { border-color: #64a6ff; background: #264367; }
.mmx-ph3-filmstrip label.mmx-current { outline: 2px solid #ddd; outline-offset: -3px; }
.mmx-ph3-filmstrip button { display: block; width: 100%; padding: 0; border: 0; }
.mmx-ph3-filmstrip img { display: block; width: 100%; height: 55px; object-fit: contain; background: #181818; }
.mmx-ph3-filmstrip span { display: flex; align-items: center; justify-content: center; gap: 5px; padding: 3px; }
.mmx-ph3-picked { display: flex; gap: 5px; flex-wrap: wrap; min-height: 57px; }
.mmx-ph3-picked button { width: 72px; padding: 2px; }
.mmx-ph3-picked img { display: block; width: 100%; height: 40px; object-fit: contain; }
.mmx-ph3-frame-note { min-height: 18px; margin: 6px 0; color: var(--descrip-text, #bbb); }
.mmx-ph3-frame-error { color: #ffb4a9; }
.mmx-ph3-frame-footer { flex: 0 0 auto; display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.mmx-ph3-frame-apply { background: #2968b9 !important; color: #fff !important; }
@media (max-width: 550px) { .mmx-ph3-filmstrip { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
`;

function values(node) {
    try {
        const parsed = JSON.parse(widgetNamed(node, FIELD)?.value || "{}");
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch { return {}; }
}

function origin(node, slot) {
    const input = node.inputs?.find((one) => one.name.split(".").pop() === slot);
    let link = node.graph?.links?.get?.(input?.link) ?? node.graph?.links?.[input?.link];
    for (let hop = 0; link && hop < 8; hop++) {
        const source = node.graph?.getNodeById?.(link.origin_id);
        if (source?.type !== "Reroute") return [String(link.origin_id), link.origin_slot];
        const id = source.inputs?.[0]?.link;
        link = node.graph?.links?.get?.(id) ?? node.graph?.links?.[id];
    }
    return null;
}

export function frameInfo(node, slot) {
    const record = records.get(String(node.id)) ?? [...records.values()].find((one) => one.node.endsWith(`:${node.id}`));
    const info = record?.slots?.[slot];
    if (!info) return null;
    const source = origin(node, slot);
    if (info.link && (!source || source[0] !== String(info.link[0]) || source[1] !== info.link[1])) return null;
    return { ...info, node: record.node };
}

function automatic(node, info) {
    if (describing(node)) return [0];
    const count = info.count;
    if (info.kind === "image" && count <= 8) return Array.from({ length: count }, (_, index) => index);
    const limit = Math.min(count, Math.max(1, Number(widgetNamed(node, info.kind === "refmod" ? "refmod_images" : "video_images")?.value) || 3));
    return Array.from({ length: limit }, (_, index) => {
        const position = limit === 1 ? 0 : index * (count - 1) / (limit - 1);
        const low = Math.floor(position);
        return position - low === 0.5 ? low + low % 2 : Math.round(position);
    });
}

export function selectedCount(node, slot) {
    if (describing(node)) return 1;
    const saved = values(node)[slot];
    if (Array.isArray(saved)) return new Set(saved).size;
    const info = frameInfo(node, slot);
    return info ? automatic(node, info).length : null;
}

function describing(node) {
    return widgetNamed(node, "task")?.value === "Text2Image"
        && !!widgetNamed(node, "use_ref_description")?.value;
}

export function firstSelectedFrame(node, slot) {
    const saved = values(node)[slot];
    if (!Array.isArray(saved)) return 0;
    if (!saved.length || saved.some((index) => !Number.isInteger(index) || index < 0)) return null;
    return saved.reduce((first, index) => Math.min(first, index), Infinity);
}

function remember(node, slots) {
    records.set(String(node), { node: String(node), slots });
    for (const graph of new Set([app.graph, app.canvas?.graph])) {
        for (const item of graph?.nodes ?? []) if (String(node).split(":").pop() === String(item.id)) refresh(item);
    }
}

async function get(query) {
    const response = await api.fetchApi(URL + "?" + new URLSearchParams(query));
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not read these frames.");
    return result;
}

export async function openFramePicker(node, entry) {
    installStyle("minimax-h3-ph3-frames", STYLE);
    let sequence = 0;
    let timer;
    const { panel, close } = frame(true, {
        size: "ph3_frames",
        onClose: () => { clearTimeout(timer); sequence++; },
    });
    panel.classList.add("mmx-ph3-picker");
    panel.setAttribute("aria-label", `${entry.name}: reference frames`);
    const title = document.createElement("h3");
    title.textContent = `${entry.name}: reference frames`;
    const content = document.createElement("div");
    content.className = "mmx-ph3-frame-content";
    const note = document.createElement("p");
    note.className = "mmx-ph3-frame-note";
    note.textContent = "Loading frames from the last run...";
    const footer = document.createElement("div");
    footer.className = "mmx-ph3-frame-footer";
    function button(text, action, parent = footer) {
        const element = document.createElement("button");
        element.type = "button";
        element.textContent = text;
        element.addEventListener("click", action);
        parent.appendChild(element);
        return element;
    }
    button("Cancel", close);
    content.appendChild(note);
    panel.append(title, content, footer);
    panel.focus({ preventScroll: true });
    let info;
    try {
        const owner = frameInfo(node, entry.name)?.node ?? String(node.id);
        const result = await get({ node: owner });
        remember(owner, result.slots);
        info = frameInfo(node, entry.name);
        if (!info) throw new Error("Run this input branch once with bypass to inspect its frames. After changing the source, run it again to refresh the preview.");
    } catch (error) { note.textContent = error.message; return; }
    if (!panel.isConnected) return;
    const stored = values(node)[entry.name];
    const single = describing(node);
    let auto = !Array.isArray(stored);
    const first = firstSelectedFrame(node, entry.name);
    let chosen = new Set(auto ? automatic(node, info) : single ? first === null ? [] : [first] : stored);
    let off;
    try { off = JSON.parse(widgetNamed(node, "reference_layout")?.value || "{}").off; } catch { off = []; }
    const disabled = new Set(Array.isArray(off) ? off : []);
    const others = single ? 0 : (node.inputs ?? []).filter((input) => input.link !== null && input.link !== undefined)
        .map((input) => input.name.split(".").pop())
        .filter((name) => name.startsWith("ref_") && name !== entry.name && !disabled.has(name))
        .reduce((count, name) => count + (selectedCount(node, name) ?? 0), 0);
    const budget = single ? 1 : Math.max(0, 8 - others);
    let current = [...chosen].find((index) => Number.isInteger(index) && index >= 0 && index < info.count) ?? 0;
    let overview = true;
    const images = new Map();
    const view = document.createElement("img");
    view.className = "mmx-ph3-frame-view";
    view.alt = "Current reference frame";
    const toolbar = document.createElement("div");
    toolbar.className = "mmx-ph3-frame-toolbar";
    const slider = document.createElement("input");
    slider.type = "range"; slider.min = "1"; slider.max = String(info.count); slider.step = "1";
    slider.setAttribute("aria-label", "Frame position");
    const number = document.createElement("input");
    number.type = "number"; number.min = "1"; number.max = String(info.count); number.step = "1";
    number.setAttribute("aria-label", "Frame number");
    const position = document.createElement("span");
    button("<", () => focus(current - 1), toolbar).setAttribute("aria-label", "Previous frame");
    toolbar.append(slider, number, position);
    button(">", () => focus(current + 1), toolbar).setAttribute("aria-label", "Next frame");
    const toggle = button("Add frame", () => select(current), toolbar);
    const browse = document.createElement("div");
    browse.className = "mmx-ph3-frame-toolbar";
    button("Overview", () => { overview = true; render(); }, browse);
    button("Nearby frames", () => { overview = false; render(); }, browse);
    button("< 12", () => { overview = false; focus(current - PAGE); }, browse);
    button("12 >", () => { overview = false; focus(current + PAGE); }, browse);
    const filmstrip = document.createElement("div");
    filmstrip.className = "mmx-ph3-filmstrip";
    const pickedLabel = document.createElement("p");
    const picked = document.createElement("div");
    picked.className = "mmx-ph3-picked";
    const help = document.createElement("p");
    help.className = "mmx-ph3-frame-note";
    help.textContent = single
        ? "Choose one frame for the text description. Only the first active reference is read; the image generator receives text only. Numbers are 1-based in the received batch."
        : "Click a thumbnail to inspect; tick its box to select. Numbers are 1-based in the received batch. Frames pass on in chronological order. Eight pictures total across all references.";
    content.insertBefore(view, note);
    content.insertBefore(toolbar, note);
    content.insertBefore(browse, note);
    content.insertBefore(filmstrip, note);
    content.insertBefore(pickedLabel, note);
    content.insertBefore(picked, note);
    content.insertBefore(help, note);
    button("Automatic", () => { auto = true; chosen = new Set(automatic(node, info)); render(); });
    const apply = button("Apply frames", () => {
        const map = values(node);
        if (auto) delete map[entry.name];
        else map[entry.name] = [...chosen].sort((a, b) => a - b);
        setWidgetValue(node, FIELD, JSON.stringify(map));
        close();
    });
    apply.className = "mmx-ph3-frame-apply";
    function valid() {
        return chosen.size > 0 && chosen.size <= budget && [...chosen].every((index) => Number.isInteger(index) && index >= 0 && index < info.count);
    }
    function select(index) {
        if (chosen.has(index)) chosen.delete(index);
        else if (single) { chosen.clear(); chosen.add(index); }
        else if (chosen.size < budget) chosen.add(index);
        auto = false;
        render();
    }
    function focus(index, delayed = false) {
        current = Math.max(0, Math.min(info.count - 1, Math.round(index)));
        if (!Number.isFinite(current)) current = 0;
        slider.value = number.value = String(current + 1);
        clearTimeout(timer);
        if (delayed) timer = setTimeout(render, 100);
        else render();
    }
    slider.addEventListener("input", () => focus(Number(slider.value) - 1, true));
    number.addEventListener("change", () => focus(Number(number.value) - 1));
    function thumbnail(index, parent) {
        const element = document.createElement("img");
        element.alt = `Frame ${index + 1}`;
        if (images.has(index)) element.src = images.get(index).thumb;
        parent.appendChild(element);
        return element;
    }
    async function render() {
        if (!panel.isConnected) return;
        const revision = ++sequence;
        slider.value = number.value = String(current + 1);
        position.textContent = `/ ${info.count}`;
        toggle.textContent = chosen.has(current) ? "Remove frame" : "Add frame";
        toggle.disabled = !single && !chosen.has(current) && chosen.size >= budget;
        apply.disabled = !valid();
        pickedLabel.textContent = `${auto ? "Automatic" : "Selected"}: ${[...chosen].sort((a, b) => a - b).map((index) => index + 1).join(", ") || "none"}`;
        note.textContent = valid() ? single
            ? "This frame supplies the text description when its reference is first active. Run again after changing the source."
            : `Preview of the last executed input. ${others} picture(s) in other references. Run again after changing the source.`
            : budget === 0 ? "The other references already use eight pictures. Cancel and reduce or disable one of them."
                : `Select 1 to ${budget} valid frames here (${others} in other references), or choose Automatic to reset an old selection.`;
        note.classList.toggle("mmx-ph3-frame-error", !valid());
        const start = Math.floor(current / PAGE) * PAGE;
        const samples = Math.min(PAGE, info.count);
        const indices = overview
            ? [...new Set(Array.from({ length: samples }, (_, index) => Math.round(index * (info.count - 1) / Math.max(1, samples - 1))))]
            : Array.from({ length: Math.min(PAGE, info.count - start) }, (_, index) => start + index);
        const needed = [...new Set([current, ...indices, ...chosen])].filter((index) => index >= 0 && index < info.count && !images.has(index));
        try {
            if (needed.length) {
                const response = await get({ node: info.node, slot: entry.name, token: info.token, indices: needed.join(",") });
                for (const frame of response.frames) images.set(frame.index, frame);
            }
            if (revision !== sequence || !panel.isConnected) return;
            view.src = images.get(current).thumb;
            const time = images.get(current).time;
            position.textContent = `/ ${info.count}${time !== null ? ` (${time.toFixed(3)} s)` : ""}`;
            filmstrip.replaceChildren();
            for (const index of indices) {
                const label = document.createElement("label");
                label.className = (chosen.has(index) ? "mmx-selected " : "") + (current === index ? "mmx-current" : "");
                const inspect = button("", () => focus(index), label);
                inspect.setAttribute("aria-label", `Inspect frame ${index + 1}`);
                thumbnail(index, inspect);
                const caption = document.createElement("span");
                const check = document.createElement("input");
                check.type = "checkbox"; check.checked = chosen.has(index);
                check.disabled = !single && !check.checked && chosen.size >= budget;
                check.setAttribute("aria-label", `Select frame ${index + 1}`);
                check.addEventListener("change", () => select(index));
                caption.append(check, document.createTextNode(String(index + 1)));
                label.appendChild(caption);
                filmstrip.appendChild(label);
            }
            picked.replaceChildren();
            for (const index of [...chosen].sort((a, b) => a - b)) {
                if (index < 0 || index >= info.count) continue;
                const item = button("", () => focus(index), picked);
                item.setAttribute("aria-label", `Inspect selected frame ${index + 1}`);
                thumbnail(index, item);
                item.appendChild(document.createTextNode(String(index + 1)));
            }
        } catch (error) {
            if (revision !== sequence || !panel.isConnected) return;
            note.textContent = error.message;
            note.classList.add("mmx-ph3-frame-error");
            apply.disabled = true;
        }
    }
    render();
}

api.addEventListener("minimax_h3_rewriter.ph3_frames", (event) => {
    remember(event.detail.node, event.detail.slots || {});
});
