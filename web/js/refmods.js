import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { refresh } from "./mmx_controls.js";

export const REFMOD_NODE = "MiniMaxH3RewriterRefMod";
export const REFMOD_TYPE = "H3_REWRITER_REFMOD";

const LIST_URL = "/minimax_h3_rewriter/refmods";
const PREVIEW_URL = "/minimax_h3_rewriter/refmods/preview";
const WIDGET = "refmod";
const OUTPUTS = ["visual", "audio"];
const REROUTE_HOPS = 8;
const RETRY_MS = 5000;
const HOOKED = "__minimaxH3RefModHook";

const entries = new Map();
let loading = null;
let loadedAt = 0;


function linkIn(graph, id) {
    const links = graph?.links;
    return links?.get ? links.get(id) : links?.[id];
}

function refreshAll() {
    const seen = new Set();
    for (const graph of [app.graph, app.canvas?.graph]) {
        if (!graph || seen.has(graph)) continue;
        seen.add(graph);
        for (const node of graph.nodes ?? []) refresh(node);
    }
}

function load() {
    if (loading) return loading;
    loading = (async () => {
        try {
            const response = await api.fetchApi(LIST_URL);
            if (response.ok) {
                const answer = await response.json();
                entries.clear();
                for (const item of answer?.items ?? []) entries.set(item.name, item);
            }
        } catch (error) {
            console.debug("[minimax_h3_rewriter] could not read the RefMod list", error);
        }
        loadedAt = Date.now();
        loading = null;
        refreshAll();
    })();
    return loading;
}

function origin(node, link) {
    const graph = node.graph;
    let info = linkIn(graph, link);
    for (let hop = 0; info && hop < REROUTE_HOPS; hop++) {
        const source = graph?.getNodeById?.(info.origin_id);
        if (!source) return null;
        const through = source.type === "Reroute" ? source.inputs?.[0]?.link : null;
        if (through === null || through === undefined) return { source, slot: info.origin_slot };
        info = linkIn(graph, through);
    }
    return null;
}

function watch(widget) {
    if (!widget || widget[HOOKED]) return;
    widget[HOOKED] = true;
    const callback = widget.callback;
    widget.callback = function () {
        const result = callback?.apply(this, arguments);
        refreshAll();
        return result;
    };
}

export function isRefmodLink(node, link) {
    const type = String(linkIn(node.graph, link)?.type || "").toUpperCase();
    return type === REFMOD_TYPE;
}

export function refmodAt(node, link) {
    const found = origin(node, link);
    if (!found || found.source.type !== REFMOD_NODE) return null;
    const widget = found.source.widgets?.find((entry) => entry.name === WIDGET);
    watch(widget);
    const name = String(widget?.value ?? "");
    const channel = OUTPUTS[found.slot] ?? OUTPUTS[0];
    const entry = entries.get(name) ?? null;
    if (!entry && !loading && Date.now() - loadedAt > RETRY_MS) load();
    const kind = channel === "audio" ? "audio" : entry?.visual?.kind ?? null;
    return { source: found.source, name, channel, kind, entry };
}

export function refmodPreview(found) {
    if (!found) return null;
    const pictured = found.channel === "visual" && found.entry?.preview;
    return {
        filename: found.name.slice(found.name.lastIndexOf("/") + 1),
        subfolder: "",
        type: "refmod",
        kind: pictured ? "image" : "refmod",
        start: 0,
        url: pictured ? api.apiURL(`${PREVIEW_URL}?${new URLSearchParams({ name: found.name })}`) : null,
        key: `${found.source.id}:${found.name}:${found.channel}`,
    };
}

app.registerExtension({
    name: "minimax_h3_rewriter.refmods",
    setup() {
        load();
    },
    afterConfigureGraph() {
        load();
    },
});
