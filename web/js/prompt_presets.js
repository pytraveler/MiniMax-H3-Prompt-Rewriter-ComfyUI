import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import { buttonRow, installStyle, setWidgetValue, told, widgetNamed } from "./mmx_controls.js";
import { ask, copy, element, frame, openEdit } from "./prompt_library.js";

const NODE = "MiniMaxH3PromptPresets";
const PRESET = "preset";
const ROOT = "/minimax_h3_rewriter/presets";

const PICK_LABEL = "Pick a preset";
const PICK_TOOLTIP =
    "Browse the thousand prompts that come with the pack: filter by shooting style, " +
    "subject or shape, search the words, and click a frame to watch the clip it was " +
    "written for.";

const SAVE_LABEL = "Save to the library";
const SAVE_TOOLTIP =
    "Put a copy of this preset into one of your own prompt sets, where it can be " +
    "renamed, filed under your own groups, edited with the self-check running, and " +
    "handed to any writer from the library window.\n\n" +
    "The copy carries where it came from, so a prompt that travels on from there still " +
    "says whose it was. The bundled preset itself is not touched.";

const CHUNK = 40;
const TYPING = 160;
const PATIENCE = 2500;

const VIEW_H = 132;

const STYLE_ID = "minimax-h3-presets-style";
const STYLE = `
.mmxpre-view { display: flex; gap: 10px; width: 100%; height: 100%;
    font-family: system-ui, sans-serif; overflow: hidden; }
.mmxpre-view img { flex: 0 0 auto; align-self: flex-start; width: 120px;
    height: auto; max-height: 100%; object-fit: contain; border-radius: 4px;
    border: 1px solid var(--border-color, #4e4e4e); background: rgba(0, 0, 0, 0.25); }
.mmxpre-side { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }
.mmxpre-label { flex: 0 0 auto; font-size: 12px; font-weight: 600;
    color: var(--input-text, #ddd); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.mmxpre-body { flex: 1 1 auto; min-height: 0; margin-top: 4px; overflow: auto;
    font-size: 11px; line-height: 1.45; color: var(--descrip-text, #999);
    white-space: pre-wrap; }
.mmxpre-empty { display: flex; align-items: center; justify-content: center;
    width: 100%; height: 100%; font-size: 11px; text-align: center;
    color: var(--descrip-text, #999); font-family: system-ui, sans-serif; }

.mmxlib-back .mmxlib-panel.mmxpre-panel { width: min(1500px, 80vw); }
.mmxlib-back .mmxlib-panel.mmxpre-clip { width: min(900px, 70vw); }

.mmxpre-credit { font-size: 10px; line-height: 1.6; color: var(--descrip-text, #999);
    margin: 6px 0 2px; }
.mmxpre-credit a { color: #7FB2F5; }
.mmxpre-rows { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.mmxpre-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.mmxpre-legend { flex: 0 0 58px; font-size: 10px; color: var(--descrip-text, #999);
    text-transform: uppercase; letter-spacing: 0.04em; }
.mmxpre-chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; cursor: pointer;
    user-select: none; border: 1px solid var(--border-color, #4e4e4e);
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b); }
.mmxpre-chip.mmxpre-on { background: #3B7DD8; border-color: #3B7DD8; color: #fff; }
.mmxpre-chip.mmxpre-clear { border-style: dashed; }
.mmxpre-count { font-size: 11px; color: var(--descrip-text, #999); margin-top: 8px; }

.mmxpre-card { display: flex; gap: 10px; padding: 8px 2px;
    border-bottom: 1px solid var(--border-color, #4e4e4e); }
.mmxpre-thumb { flex: 0 0 96px; width: 96px; height: 96px; border-radius: 4px;
    border: 1px solid var(--border-color, #4e4e4e); background: rgba(0, 0, 0, 0.25);
    object-fit: contain; cursor: zoom-in; display: block; }
.mmxpre-info { flex: 1 1 auto; min-width: 0; }
.mmxpre-name { font-size: 13px; font-weight: 600; }
.mmxpre-meta { font-size: 10px; color: var(--descrip-text, #999); margin-top: 2px; }
.mmxpre-lines { font-size: 11px; color: var(--descrip-text, #999); margin-top: 4px;
    overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3;
    -webkit-box-orient: vertical; }
.mmxpre-acts { display: flex; flex-direction: column; gap: 6px; flex: 0 0 auto; }
.mmxpre-acts button { padding: 4px 12px; font-size: 12px; }
.mmxpre-more { height: 1px; }

.mmxpre-player video { width: 100%; max-height: 62vh; border-radius: 6px;
    background: #000; display: block; }
.mmxpre-where { font-size: 11px; margin-top: 8px; color: var(--descrip-text, #999); }
.mmxpre-where a { color: #7FB2F5; margin-right: 12px; }
`;

let SHELF = null;

function shelf() {
    if (!SHELF) {
        SHELF = ask(ROOT).then(({ ok, payload }) => {
            if (!ok || !Array.isArray(payload?.records)) return null;
            for (const item of payload.records) {
                item.label = nameOf(item, payload);
                item.spoken = (item.langs || []).length > 0;
                item.hay = [
                    item.id,
                    item.label,
                    item.description,
                    item.soundscape,
                    item.music,
                    item.aspect,
                    (item.topics || []).map((key) => payload.topics[key] || key).join(" "),
                    (item.langs || []).join(" "),
                ]
                    .join(" ")
                    .toLowerCase();
            }
            return payload;
        });
    }
    return SHELF;
}

function nameOf(item, data) {
    const look = data.styles?.[item.style] || "";
    const named = `H3 1K #${item.id}`;
    return look ? `${named} - ${look}` : named;
}

function wordsOf(item, data) {
    const fields = data.fields || [];
    const parts = data.parts || [];
    return fields.map((field, at) => `${field}: ${item[parts[at]] || ""}`).join("\n");
}

function watchAt(item, data) {
    const templates = data.video || {};
    const found = {};
    for (const [name, template] of Object.entries(templates)) {
        found[name] = String(template).replace("{id}", item.id);
    }
    return found;
}

function frameUrl(id) {
    return api.apiURL(`${ROOT}/thumb/${encodeURIComponent(id)}`);
}

function sayCredit(holder, data) {
    holder.replaceChildren();
    for (const part of Object.values(data.credit || {})) {
        if (!part.what || !part.who) continue;
        const line = element("div");
        line.append(document.createTextNode(`${part.what}: `));
        if (part.url) {
            const link = element("a", "", part.who);
            link.href = part.url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            line.appendChild(link);
        } else {
            line.append(document.createTextNode(part.who));
        }
        holder.appendChild(line);
    }
    if (data.notice) holder.appendChild(element("div", "", data.notice));
}

function watchClip(item, where) {
    installStyle(STYLE_ID, STYLE);
    const { panel, close } = frame(false);
    panel.classList.add("mmxpre-clip");
    panel.parentElement.style.alignItems = "flex-start";
    panel.style.marginTop = "40px";
    panel.appendChild(element("h3", "mmxlib-title", item.label));

    const holder = element("div", "mmxpre-player");
    const clip = document.createElement("video");
    clip.controls = true;
    clip.autoplay = true;
    clip.loop = true;
    clip.playsInline = true;
    clip.src = where.huggingface || where.mirror || "";
    holder.appendChild(clip);
    panel.appendChild(holder);

    let moved = false;
    function toMirror() {
        if (moved || !where.mirror || clip.src === where.mirror) return;
        moved = true;
        clip.src = where.mirror;
        clip.play?.().catch(() => {});
    }
    const waiting = setTimeout(toMirror, PATIENCE);
    clip.addEventListener("loadeddata", () => clearTimeout(waiting));
    clip.addEventListener("error", toMirror);

    const said = element("div", "mmxpre-where");
    said.append(document.createTextNode("The clip streams from "));
    for (const [name, address] of Object.entries(where)) {
        const link = element("a", "", name === "mirror" ? "hf-mirror.com" : "huggingface.co");
        link.href = address;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.title =
            name === "mirror"
                ? "The copy that answers from mainland China."
                : "The dataset this prompt and its clip come from.";
        said.appendChild(link);
    }
    panel.appendChild(said);

    const row = element("div", "mmxlib-row");
    const shut = element("button", "", "Close");
    shut.addEventListener("click", () => {
        clearTimeout(waiting);
        clip.pause?.();
        close();
    });
    row.appendChild(shut);
    panel.appendChild(row);
}

function fill(holder, items, make) {
    let at = 0;
    let watcher = null;
    const sentinel = element("div", "mmxpre-more");

    function more() {
        const upto = Math.min(at + CHUNK, items.length);
        for (; at < upto; at += 1) holder.insertBefore(make(items[at]), sentinel);
        if (at >= items.length) watcher?.disconnect();
    }

    holder.appendChild(sentinel);
    more();
    watcher = new IntersectionObserver(
        (entries) => {
            if (entries.some((entry) => entry.isIntersecting)) more();
        },
        { root: holder, rootMargin: "600px" }
    );
    watcher.observe(sentinel);
    return () => watcher?.disconnect();
}

function chipRow(holder, legend, options, chosen, redraw) {
    const row = element("div", "mmxpre-row");
    row.appendChild(element("div", "mmxpre-legend", legend));
    for (const option of options) {
        const chip = element(
            "div",
            "mmxpre-chip" + (chosen.has(option.key) ? " mmxpre-on" : ""),
            option.label
        );
        chip.addEventListener("click", () => {
            if (chosen.has(option.key)) chosen.delete(option.key);
            else chosen.add(option.key);
            chip.classList.toggle("mmxpre-on");
            redraw();
        });
        row.appendChild(chip);
    }
    holder.appendChild(row);
}

function openPicker(node) {
    installStyle(STYLE_ID, STYLE);
    const { panel, close } = frame(true);
    panel.classList.add("mmxpre-panel");
    panel.appendChild(element("h3", "mmxlib-title", "Bundled prompts"));
    panel.appendChild(
        element(
            "p",
            "mmxlib-sub",
            "A thousand finished MiniMax-H3 prompts. Click a frame to watch the clip it " +
                "was written for; Pick puts the prompt on this node."
        )
    );

    const credit = element("div", "mmxpre-credit");
    panel.appendChild(credit);

    const head = element("div", "mmxlib-head");
    const search = document.createElement("input");
    search.type = "text";
    search.placeholder = "Search the prompts, the sound, the tags";
    head.appendChild(search);
    panel.appendChild(head);

    const rows = element("div", "mmxpre-rows");
    panel.appendChild(rows);
    const count = element("div", "mmxpre-count");
    panel.appendChild(count);

    const list = element("div", "mmxlib-list");
    panel.appendChild(list);

    const row = element("div", "mmxlib-row");
    const shut = element("button", "", "Close");
    shut.addEventListener("click", close);
    row.append(element("div", "mmxlib-problem"), shut);
    panel.appendChild(row);

    const wanted = {
        style: new Set(),
        topic: new Set(),
        aspect: new Set(),
        shots: new Set(),
        spoken: new Set(),
    };
    let data = null;
    let stop = null;
    let typing = null;
    let clearing = null;

    function narrowed() {
        if (clearing) {
            clearing.style.display = Object.values(wanted).some((set) => set.size)
                ? ""
                : "none";
        }
        draw();
    }

    function keeps(item) {
        if (wanted.style.size && !wanted.style.has(item.style)) return false;
        if (wanted.topic.size && !(item.topics || []).some((key) => wanted.topic.has(key))) {
            return false;
        }
        if (wanted.aspect.size && !wanted.aspect.has(item.aspect)) return false;
        if (wanted.shots.size && !wanted.shots.has(String(item.shots))) return false;
        if (wanted.spoken.size && !wanted.spoken.has(item.spoken ? "yes" : "no")) return false;
        const typed = search.value.trim().toLowerCase();
        return !typed || item.hay.includes(typed);
    }

    function make(item) {
        const holder = element("div", "mmxpre-card");

        const picture = document.createElement("img");
        picture.className = "mmxpre-thumb";
        picture.loading = "lazy";
        picture.decoding = "async";
        picture.src = frameUrl(item.id);
        picture.alt = "";
        picture.title = "Watch the clip this prompt was written for";
        picture.addEventListener("click", () => watchClip(item, watchAt(item, data)));
        holder.appendChild(picture);

        const info = element("div", "mmxpre-info");
        info.appendChild(element("div", "mmxpre-name", item.label));
        const said = [
            `${item.w}x${item.h}`,
            `${item.shots} shot${item.shots === 1 ? "" : "s"}`,
            (item.langs || []).join(", ") || "no dialogue",
            (item.topics || []).map((key) => data.topics[key] || key).join(", "),
        ].filter(Boolean);
        info.appendChild(element("div", "mmxpre-meta", said.join("  ·  ")));
        info.appendChild(element("div", "mmxpre-lines", item.description));
        holder.appendChild(info);

        const acts = element("div", "mmxpre-acts");
        const take = element("button", "", "Pick");
        take.addEventListener("click", () => {
            setWidgetValue(node, PRESET, item.id);
            show(node, { id: item.id, label: item.label, text: wordsOf(item, data) });
            close();
        });
        const hand = element("button", "", "Copy");
        hand.addEventListener("click", () => copy(wordsOf(item, data), hand));
        acts.append(take, hand);
        holder.appendChild(acts);
        return holder;
    }

    function draw() {
        stop?.();
        list.replaceChildren();
        list.scrollTop = 0;
        const shown = data.records.filter(keeps);
        count.textContent = `${shown.length} of ${data.records.length}`;
        if (!shown.length) {
            list.appendChild(element("div", "mmxlib-none", "Nothing here matches."));
            stop = null;
            return;
        }
        stop = fill(list, shown, make);
    }

    function facets() {
        rows.replaceChildren();
        const looks = Object.entries(data.styles || {}).map(([key, label]) => ({ key, label }));
        const subjects = Object.entries(data.topics || {}).map(([key, label]) => ({
            key,
            label,
        }));
        chipRow(rows, "Style", looks, wanted.style, narrowed);
        chipRow(rows, "Subject", subjects, wanted.topic, narrowed);
        chipRow(
            rows,
            "Shape",
            [
                { key: "landscape", label: "Landscape" },
                { key: "portrait", label: "Portrait" },
                { key: "square", label: "Square" },
            ],
            wanted.aspect,
            narrowed
        );
        const counted = [...new Set(data.records.map((item) => String(item.shots)))].sort();
        chipRow(
            rows,
            "Shots",
            counted.map((key) => ({ key, label: key === "1" ? "1 shot" : `${key} shots` })),
            wanted.shots,
            narrowed
        );
        chipRow(
            rows,
            "Speech",
            [
                { key: "yes", label: "Dialogue" },
                { key: "no", label: "No dialogue" },
            ],
            wanted.spoken,
            narrowed
        );

        clearing = element("div", "mmxpre-row");
        clearing.style.display = "none";
        clearing.appendChild(element("div", "mmxpre-legend", ""));
        const chip = element("div", "mmxpre-chip mmxpre-clear", "Clear the tags");
        chip.addEventListener("click", () => {
            for (const set of Object.values(wanted)) set.clear();
            for (const lit of rows.querySelectorAll(".mmxpre-on")) {
                lit.classList.remove("mmxpre-on");
            }
            narrowed();
        });
        clearing.appendChild(chip);
        rows.appendChild(clearing);
    }

    search.addEventListener("input", () => {
        clearTimeout(typing);
        typing = setTimeout(draw, TYPING);
    });

    list.appendChild(element("div", "mmxlib-none", "Reading the collection..."));
    shelf().then((found) => {
        data = found;
        if (!data) {
            list.replaceChildren(
                element(
                    "div",
                    "mmxlib-none",
                    "The bundled prompts are not installed: this copy of the pack has no " +
                        "'presets' folder."
                )
            );
            return;
        }
        sayCredit(credit, data);
        facets();
        draw();
        search.focus();
    });
}

async function fileField() {
    const holder = element("div");
    holder.appendChild(element("label", "mmxlib-field", "Save in"));
    const chooser = document.createElement("select");
    holder.appendChild(chooser);
    const { payload } = await ask("/minimax_h3_rewriter/library/files");
    for (const name of payload.files || ["global"]) {
        chooser.appendChild(new Option(name, name));
    }
    return { holder, chooser };
}

async function keepPreset(node, button) {
    const id = String(widgetNamed(node, PRESET)?.value || "").trim();
    if (!id) {
        told(button, "Pick a preset first");
        return;
    }
    const { ok, payload } = await ask(`${ROOT}/record/${encodeURIComponent(id)}`);
    if (!ok || !payload?.preset) {
        told(button, "No such preset");
        return;
    }
    const held = payload.preset;
    const where = await fileField();

    const saved = await openEdit({
        title: "Save a bundled prompt",
        note:
            "A copy in your own set: rename it, file it, edit it. The bundled preset " +
            "stays as it is, and this node goes on handing on the original.",
        naming: true,
        saveLabel: "Save the prompt",
        extra: where.holder,
        name: held.label,
        description: held.credit,
        groups: held.groups,
        text: held.text,
        references: [],
        provenance: `Bundled preset ${held.id}, written for a ${held.seconds}s clip at ${held.w}x${held.h}.`,
        async offerGroups() {
            const { payload: known } = await ask(
                `/minimax_h3_rewriter/library?file=${encodeURIComponent(where.chooser.value)}`
            );
            return known.groups || [];
        },
        async check(value) {
            const { payload: found } = await ask(`${ROOT}/check`, {
                preset: held.id,
                text: value,
            });
            return found.issues || [];
        },
        async save(edited) {
            const answer = await ask("/minimax_h3_rewriter/library/save_preset", {
                file: where.chooser.value,
                preset: held.id,
                name: edited.name,
                description: edited.description,
                groups: edited.groups,
                text: edited.text,
            });
            if (!answer.ok) {
                return { error: answer.payload?.error || "the prompt could not be saved" };
            }
            return { value: answer.payload.file };
        },
    });
    if (saved) told(button, `Saved in ${saved}`);
}

function show(node, held) {
    const view = node.mmxPresetView;
    if (!view) return;
    if (!held) {
        view.replaceChildren(
            element(
                "div",
                "mmxpre-empty",
                "No preset chosen yet. Press 'Pick a preset' to browse the thousand that " +
                    "come with the pack."
            )
        );
        return;
    }
    const picture = document.createElement("img");
    picture.loading = "lazy";
    picture.decoding = "async";
    picture.src = frameUrl(held.id);
    picture.alt = "";
    const side = element("div", "mmxpre-side");
    side.appendChild(element("div", "mmxpre-label", held.label));
    side.appendChild(element("div", "mmxpre-body", held.text));
    view.replaceChildren(picture, side);
}

async function restore(node) {
    const id = String(widgetNamed(node, PRESET)?.value || "").trim();
    if (!id) {
        show(node, null);
        return;
    }
    const { ok, payload } = await ask(`${ROOT}/record/${encodeURIComponent(id)}`);
    if (!ok || !payload?.preset) {
        show(node, {
            id,
            label: `#${id} is not in this collection`,
            text:
                "Either the number was edited by hand, or this copy of the pack was " +
                "installed without the bundled prompts.",
        });
        return;
    }
    show(node, payload.preset);
}

function build(node) {
    installStyle(STYLE_ID, STYLE);

    buttonRow(node, "mmx_preset_pick", [
        {
            label: PICK_LABEL,
            tooltip: PICK_TOOLTIP,
            onClick: () => openPicker(node),
        },
        {
            label: SAVE_LABEL,
            tooltip: SAVE_TOOLTIP,
            onClick: (button) => keepPreset(node, button),
        },
    ]);

    const view = element("div", "mmxpre-view");
    const widget = node.addDOMWidget(PRESET + "_view", "minimaxh3_preset_view", view, {
        hideOnZoom: false,
        margin: 6,
        hideInPanel: true,
        getValue: () => undefined,
        setValue: () => {},
        getMinHeight: () => VIEW_H,
    });
    widget.serialize = false;
    widget.serializeValue = () => undefined;
    node.mmxPresetView = view;

    const chooser = widgetNamed(node, PRESET);
    if (chooser) {
        const before = chooser.callback;
        chooser.callback = function () {
            const result = before?.apply(this, arguments);
            restore(node);
            return result;
        };
    }
    restore(node);
}

app.registerExtension({
    name: "minimax_h3_rewriter.prompt_presets",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            build(this);
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            restore(this);
            return result;
        };
    },
});
