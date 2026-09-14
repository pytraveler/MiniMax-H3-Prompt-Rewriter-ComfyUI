import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { refresh } from "./mmx_controls.js";

const EVENT = "minimax_h3_rewriter.previews";
const STATE_URL = "/minimax_h3_rewriter/previews";

const CYCLE_MS = 320;
const REROUTE_HOPS = 8;
const CLIP_STILL_AT = 0.1;
const SVG_NS = "http://www.w3.org/2000/svg";
const HOOKED = "__minimaxH3PreviewHook";

const LISTEN_SECONDS = 6;
const LISTEN_DELAY_MS = 300;
const FADE_SECONDS = 0.25;
const CLOSE_MS = 120;

const LOADERS = {
    LoadImage: { widget: "image", kind: "image" },
    LoadAudio: { widget: "audio", kind: "audio" },
    VHS_LoadAudioUpload: { widget: "audio", kind: "audio", start: "start_time" },
    LoadVideo: { widget: "file", kind: "video" },
    VHS_LoadVideo: { widget: "video", kind: "video" },
    VHS_LoadVideoFFmpeg: { widget: "video", kind: "video" },
};

const received = new Map();


function tailOf(name) {
    const text = String(name || "");
    return text.slice(text.lastIndexOf(".") + 1);
}

function linkIn(graph, id) {
    const links = graph?.links;
    return links?.get ? links.get(id) : links?.[id];
}

function inputLink(node, slot) {
    const input = (node.inputs || []).find((entry) => tailOf(entry.name) === slot);
    const link = input?.link;
    return link === null || link === undefined ? null : link;
}

function sourceNode(node, link) {
    const graph = node.graph;
    let info = linkIn(graph, link);
    for (let hop = 0; info && hop < REROUTE_HOPS; hop++) {
        const source = graph?.getNodeById?.(info.origin_id);
        if (!source) return null;
        const through = source.type === "Reroute" ? source.inputs?.[0]?.link : null;
        if (through === null || through === undefined) return source;
        info = linkIn(graph, through);
    }
    return null;
}

function fileOf(value) {
    const text = String(value || "").trim();
    if (!text) return null;
    const match = /^(.*?)(?:\s*\[(input|output|temp)\])?$/.exec(text);
    const path = match[1];
    const cut = path.lastIndexOf("/");
    return {
        filename: cut >= 0 ? path.slice(cut + 1) : path,
        subfolder: cut >= 0 ? path.slice(0, cut) : "",
        type: match[2] || "input",
    };
}

function refreshAll() {
    const seen = new Set();
    for (const graph of [app.graph, app.canvas?.graph]) {
        if (!graph || seen.has(graph)) continue;
        seen.add(graph);
        for (const node of graph.nodes ?? []) refresh(node);
    }
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

function startOf(source, spec) {
    if (!spec.start) return 0;
    const value = Number(source.widgets?.find((entry) => entry.name === spec.start)?.value);
    return Number.isFinite(value) && value > 0 ? value : 0;
}

function loaderFile(node, slot) {
    const link = inputLink(node, slot);
    if (link === null) return null;
    const source = sourceNode(node, link);
    const spec = source && LOADERS[source.type];
    if (!spec) return null;
    const widget = source.widgets?.find((entry) => entry.name === spec.widget);
    watch(widget);
    const file = fileOf(widget?.value);
    if (!file) return null;
    return {
        ...file,
        kind: spec.kind,
        start: startOf(source, spec),
        url: api.apiURL("/view?" + new URLSearchParams(file)),
        key: `${source.id}:${widget.value}`,
    };
}

function findNode(id) {
    const tail = String(id).split(":").pop();
    for (const graph of [app.canvas?.graph, app.graph]) {
        const node = graph?.getNodeById?.(Number(tail)) ?? graph?.getNodeById?.(tail);
        if (node) return node;
    }
    return null;
}

function recordOf(node) {
    const id = String(node.id);
    if (received.has(id)) return received.get(id);
    for (const [key, slots] of received) {
        if (key.endsWith(`:${id}`)) return slots;
    }
    return null;
}

function remember(id, slots) {
    const node = findNode(id);
    const kept = {};
    for (const [slot, data] of Object.entries(slots || {})) {
        kept[slot] = node
            ? { data, link: inputLink(node, slot), source: loaderFile(node, slot)?.key ?? null }
            : { data };
    }
    received.set(id, kept);
    return node;
}

export function previewFor(node, slot, kind) {
    const file = loaderFile(node, slot);
    const got = recordOf(node)?.[slot];
    let data = null;
    if (got) {
        const sameLink = got.link === undefined || got.link === inputLink(node, slot);
        const sameSource = got.source === undefined || got.source === (file?.key ?? null);
        if (sameLink && sameSource && got.data?.kind === kind) data = got.data;
    }
    return file || data ? { file, data } : null;
}


function icon(kind) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("class", "mmx-chip-icon");
    const shape = (tag, attributes) => {
        const element = document.createElementNS(SVG_NS, tag);
        for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
        svg.appendChild(element);
    };
    if (kind === "audio") {
        shape("path", { d: "M2 6h3l4-3v10l-4-3H2z", fill: "#fff" });
        shape("path", { d: "M11 5a4 4 0 0 1 0 6", fill: "none", stroke: "#fff", "stroke-width": "1.6" });
    } else {
        shape("polygon", { points: "5,3 13,8 5,13", fill: "#fff" });
    }
    return svg;
}

function pictureElement(src) {
    const image = document.createElement("img");
    image.draggable = false;
    image.decoding = "async";
    image.src = src;
    return image;
}

function clipElement(url) {
    const clip = document.createElement("video");
    clip.draggable = false;
    clip.muted = true;
    clip.defaultMuted = true;
    clip.loop = true;
    clip.playsInline = true;
    clip.preload = "metadata";
    clip.src = `${url}#t=${CLIP_STILL_AT}`;
    return clip;
}

function waveElement(values) {
    const bars = document.createElement("div");
    bars.className = "mmx-wave";
    for (const value of values) {
        const bar = document.createElement("i");
        bar.style.height = `${Math.max(8, Math.round(Number(value) * 100))}%`;
        bars.appendChild(bar);
    }
    return bars;
}

function stem(filename) {
    return String(filename || "").replace(/\.[^.]+$/, "");
}

let listening = null;

function stopListening() {
    listening?.stop();
    listening = null;
}

function listen(holder, url, start, total) {
    stopListening();
    if (!holder.isConnected) return;

    const sound = new Audio();
    sound.preload = "auto";
    sound.volume = 0;
    sound.src = start > 0 ? `${url}#t=${start}` : url;
    const bars = [...holder.querySelectorAll(".mmx-wave i")];
    let line = null;
    if (!bars.length) {
        line = document.createElement("span");
        line.className = "mmx-listen";
        holder.appendChild(line);
    }
    holder.classList.add("mmx-playing");

    const session = { holder, closing: 0, done: false };
    let frame = 0;
    let deadline = 0;
    const finish = () => {
        if (session.done) return;
        session.done = true;
        cancelAnimationFrame(frame);
        clearTimeout(deadline);
        sound.pause();
        sound.removeAttribute("src");
        sound.load();
        holder.classList.remove("mmx-playing");
        for (const bar of bars) bar.classList.remove("mmx-played");
        line?.remove();
    };
    session.stop = () => {
        if (session.done || session.closing) return;
        session.closing = performance.now() + CLOSE_MS;
        setTimeout(finish, CLOSE_MS + 50);
    };
    listening = session;

    const tick = (now) => {
        if (session.done) return;
        if (!holder.isConnected) return finish();
        const heard = Math.max(0, sound.currentTime - start);
        const left = LISTEN_SECONDS - heard;
        let gain = Math.min(1, heard / FADE_SECONDS, left / FADE_SECONDS);
        if (session.closing) gain = Math.min(gain, (session.closing - now) / CLOSE_MS);
        sound.volume = Math.max(0, Math.min(1, gain));
        if (left <= 0 || sound.ended || (session.closing && now >= session.closing)) {
            return finish();
        }
        const whole = total > 0 ? total : sound.duration - start;
        if (Number.isFinite(whole) && whole > 0) {
            if (line) line.style.width = `${Math.min(100, (heard / Math.min(whole, LISTEN_SECONDS)) * 100)}%`;
            const lit = Math.ceil(Math.min(1, heard / whole) * bars.length);
            bars.forEach((bar, index) => bar.classList.toggle("mmx-played", index < lit));
        }
        frame = requestAnimationFrame(tick);
    };

    sound.addEventListener("loadedmetadata", () => {
        if (start > 0 && sound.currentTime < start - 0.05) sound.currentTime = start;
    });
    sound.addEventListener("playing", () => {
        clearTimeout(deadline);
        deadline = setTimeout(finish, (LISTEN_SECONDS + 1) * 1000);
    });
    sound.play()?.catch?.(() => finish());
    frame = requestAnimationFrame(tick);
}

function listenOnHover(chip, holder, url, start, total) {
    let timer = null;
    const cancel = () => {
        clearTimeout(timer);
        timer = null;
        if (listening?.holder === holder) stopListening();
    };
    chip.addEventListener("pointerenter", () => {
        clearTimeout(timer);
        timer = setTimeout(() => listen(holder, url, start, total), LISTEN_DELAY_MS);
    });
    chip.addEventListener("pointerleave", cancel);
    chip.addEventListener("pointerdown", cancel);
}

function drawSound(holder, chip, file, data) {
    holder.classList.add("mmx-has");
    holder.appendChild(icon("audio"));
    if (file) {
        const name = document.createElement("span");
        name.className = "mmx-chip-name";
        name.textContent = stem(file.filename);
        holder.appendChild(name);
    }
    if (data?.peaks?.length) holder.appendChild(waveElement(data.peaks));
    const url = file?.url || data?.listen;
    if (url) listenOnHover(chip, holder, url, file ? file.start : 0, data?.seconds || 0);
    return [file?.filename, data?.seconds && `${data.seconds} s`].filter(Boolean).join(", ");
}

function cycleOnHover(chip, image, frames) {
    const still = image.src;
    let timer = null;
    let at = 0;
    const stop = () => {
        clearInterval(timer);
        timer = null;
        at = 0;
        image.src = still;
    };
    chip.addEventListener("pointerenter", () => {
        clearInterval(timer);
        timer = setInterval(() => {
            if (!image.isConnected) {
                clearInterval(timer);
                return;
            }
            at = (at + 1) % frames.length;
            image.src = frames[at];
        }, CYCLE_MS);
    });
    chip.addEventListener("pointerleave", stop);
}

function playOnHover(chip, clip) {
    chip.addEventListener("pointerenter", () => {
        clip.play?.()?.catch?.(() => {});
    });
    chip.addEventListener("pointerleave", () => {
        clip.pause?.();
        try {
            clip.currentTime = CLIP_STILL_AT;
        } catch (error) {
            // not loaded yet
        }
    });
}

function drawPicture(holder, chip, kind, file, data) {
    let element = null;
    if (file?.kind === "video") element = clipElement(file.url);
    else if (file?.kind === "image") element = pictureElement(file.url);
    else if (data?.thumb) element = pictureElement(data.thumb);
    if (!element) return "";

    holder.classList.add("mmx-has");
    holder.appendChild(element);
    const isClip = element.tagName === "VIDEO";
    if (kind === "video" || isClip) holder.appendChild(icon("video"));

    const frames = Array.isArray(data?.frames) && data.frames.length > 1 ? data.frames : null;
    if (isClip) playOnHover(chip, element);
    else if (frames) cycleOnHover(chip, element, frames);

    return [
        file?.filename,
        data?.width && `${data.width}x${data.height}`,
        data?.count > 1 && `${data.count} frames`,
        data?.seconds && `${data.seconds} s`,
    ]
        .filter(Boolean)
        .join(", ");
}

export function drawPreview(holder, chip, kind, preview) {
    if (!preview) return "";
    const { file, data } = preview;
    return kind === "audio"
        ? drawSound(holder, chip, file, data)
        : drawPicture(holder, chip, kind, file, data);
}


async function pull() {
    try {
        const response = await api.fetchApi(STATE_URL);
        if (!response.ok) return;
        const records = await response.json();
        for (const [id, slots] of Object.entries(records ?? {})) remember(String(id), slots);
        refreshAll();
    } catch (error) {
        console.debug("[minimax_h3_rewriter] could not read the reference previews", error);
    }
}

app.registerExtension({
    name: "minimax_h3_rewriter.reference_previews",
    setup() {
        api.addEventListener(EVENT, (event) => {
            const detail = event.detail ?? {};
            const id = String(detail.node ?? "");
            if (!id) return;
            const node = remember(id, detail.slots);
            if (node) refresh(node);
        });
        pull();
    },
    afterConfigureGraph() {
        pull();
    },
});
