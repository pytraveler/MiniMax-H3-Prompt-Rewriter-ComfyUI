import { api } from "../../scripts/api.js";
import { installStyle } from "./mmx_controls.js";

const STYLE_ID = "minimax-h3-window-style";
const STYLE = `
.mmxlib-back { position: fixed; inset: 0; z-index: 1300; display: flex;
    align-items: center; justify-content: center; background: rgba(0, 0, 0, 0.55);
    font-family: system-ui, sans-serif; }
.mmxlib-panel { width: min(560px, 92vw); max-height: 88vh; overflow: auto;
    background: var(--comfy-menu-bg, #353535); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #4e4e4e); border-radius: 10px;
    padding: 18px 20px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); }
.mmxlib-panel:focus { outline: none; }
.mmxlib-panel.mmxlib-wide { width: min(760px, 94vw); display: flex;
    flex-direction: column; overflow: hidden; }
.mmxlib-title { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.mmxlib-sub { font-size: 11px; color: var(--descrip-text, #999); margin: 0 0 14px; }
.mmxlib-field { display: block; font-size: 11px; color: var(--descrip-text, #999);
    margin: 12px 0 4px; }
.mmxlib-panel input[type="text"], .mmxlib-panel textarea, .mmxlib-panel select {
    width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
    padding: 6px 8px; border-radius: 6px; color: var(--input-text, #ddd);
    background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-panel textarea { resize: vertical; min-height: 58px; }
.mmxlib-row { display: flex; gap: 8px; justify-content: flex-end;
    align-items: center; margin-top: 18px; }
.mmxlib-row button, .mmxlib-card button { font: inherit; font-size: 13px;
    padding: 6px 16px; border-radius: 6px; cursor: pointer;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b);
    border: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-row button.mmxlib-go, .mmxlib-card button.mmxlib-go {
    background: #3B7DD8; border-color: #3B7DD8; color: #fff; }
.mmxlib-row button.mmxlib-danger { background: #B4433A; border-color: #B4433A; color: #fff; }
.mmxlib-row button[disabled] { opacity: 0.5; cursor: not-allowed; }
.mmxlib-problem { flex: 1 1 auto; font-size: 11px; color: #E08A8A; text-align: left; }
.mmxlib-problem.mmxlib-calm { color: var(--descrip-text, #999); }
.mmxlib-head { display: flex; gap: 8px; align-items: center; }
.mmxlib-head input[type="text"] { flex: 1 1 auto; }
.mmxlib-head select { flex: 0 0 150px; }
.mmxlib-list { flex: 1 1 auto; overflow: auto; margin-top: 12px;
    border-top: 1px solid var(--border-color, #4e4e4e); }
.mmxlib-none { padding: 24px 4px; text-align: center; font-size: 12px;
    color: var(--descrip-text, #999); }
.mmxlib-list .mmxlib-lit { outline: 1px solid #3B7DD8; outline-offset: -1px; }

.mmxlib-panel.mmxlib-asked { width: min(460px, 92vw); }
.mmxlib-asked-text { font-size: 13px; line-height: 1.5; white-space: pre-line;
    color: var(--descrip-text, #bbb); }
`;

const SIZED_STYLE_ID = "minimax-h3-sized-style";
const SIZED_STYLE = `
.mmxlib-back .mmxlib-panel.mmxlib-sized { position: relative; box-sizing: border-box;
    max-width: calc(100vw - 16px); }
.mmxlib-back .mmxlib-panel.mmxlib-sized.mmxlib-held { max-height: calc(100vh - 16px); }
.mmxlib-back .mmxlib-panel.mmxlib-sized.mmxlib-max { width: calc(100vw - 16px) !important;
    height: calc(100vh - 16px) !important; }
.mmxlib-panel.mmxlib-sized > h3:first-of-type { padding-right: 34px; }
.mmxlib-grip { position: absolute; z-index: 5; }
.mmxlib-grip-n, .mmxlib-grip-s { left: 14px; right: 14px; height: 7px; cursor: ns-resize; }
.mmxlib-grip-e, .mmxlib-grip-w { top: 14px; bottom: 14px; width: 7px; cursor: ew-resize; }
.mmxlib-grip-n { top: 0; }
.mmxlib-grip-s { bottom: 0; }
.mmxlib-grip-e { right: 0; }
.mmxlib-grip-w { left: 0; }
.mmxlib-grip-ne, .mmxlib-grip-nw, .mmxlib-grip-se, .mmxlib-grip-sw { width: 14px; height: 14px; }
.mmxlib-grip-ne { top: 0; right: 0; cursor: nesw-resize; }
.mmxlib-grip-sw { bottom: 0; left: 0; cursor: nesw-resize; }
.mmxlib-grip-nw { top: 0; left: 0; cursor: nwse-resize; }
.mmxlib-grip-se { bottom: 0; right: 0; cursor: nwse-resize; }
.mmxlib-panel.mmxlib-max > .mmxlib-grip { display: none; }
.mmxlib-panel.mmxlib-sized > button.mmxlib-fill { position: absolute; top: 12px; right: 12px;
    z-index: 6; width: 28px; height: 24px; padding: 0; display: flex; align-items: center;
    justify-content: center; border-radius: 6px; cursor: pointer;
    color: var(--descrip-text, #aaa); background: transparent; border: 1px solid transparent; }
.mmxlib-panel.mmxlib-sized > button.mmxlib-fill:hover { color: var(--input-text, #ddd);
    background: var(--comfy-input-bg, #2b2b2b); border-color: var(--border-color, #4e4e4e); }
.mmxlib-fill svg { width: 13px; height: 13px; display: block; }
`;

const GRIPS = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
const LEAST_W = 420;
const LEAST_H = 260;
const EDGE = 16;
const FILL_ICON =
    '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6">' +
    '<rect x="1.5" y="1.5" width="11" height="11" rx="1.5"/></svg>';
const BACK_ICON =
    '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5">' +
    '<rect x="1.5" y="4.5" width="8" height="8" rx="1.2"/>' +
    '<path d="M4.5 4.5V2.7c0-.7.5-1.2 1.2-1.2h5.6c.7 0 1.2.5 1.2 1.2v5.6c0 .7-.5 1.2-1.2 ' +
    '1.2H9.5"/></svg>';

export async function ask(url, body, { signal } = {}) {
    let response;
    try {
        response = await api.fetchApi(url, {
            method: body ? "POST" : "GET",
            headers: body ? { "Content-Type": "application/json" } : undefined,
            body: body ? JSON.stringify(body) : undefined,
            signal,
        });
    } catch (error) {
        return {
            ok: false,
            status: 0,
            payload: {
                ok: false,
                error: `The ComfyUI server could not be reached: ${error?.message || error}`,
            },
        };
    }
    let payload = {};
    try {
        payload = await response.json();
    } catch (error) {
        payload = {};
    }
    return { ok: response.ok, status: response.status, payload };
}

export function element(tag, className, text) {
    const made = document.createElement(tag);
    if (className) made.className = className;
    if (text !== undefined) made.textContent = text;
    return made;
}

export async function copy(text, button) {
    const before = button.textContent;
    try {
        await navigator.clipboard.writeText(text);
    } catch (error) {
        const box = document.createElement("textarea");
        box.value = text;
        box.style.position = "fixed";
        box.style.opacity = "0";
        document.body.appendChild(box);
        box.select();
        try {
            document.execCommand("copy");
        } catch (failed) {
            button.textContent = "Cannot copy";
            setTimeout(() => (button.textContent = before), 1600);
            box.remove();
            return;
        }
        box.remove();
    }
    button.textContent = "Copied";
    setTimeout(() => (button.textContent = before), 1600);
}

function sizeKey(kind) {
    return "minimax_h3_rewriter.window." + kind;
}

function rememberedSize(kind) {
    try {
        const kept = JSON.parse(window.localStorage.getItem(sizeKey(kind)) || "null");
        if (kept && typeof kept === "object") return kept;
    } catch (error) {
        void error;
    }
    return {};
}

function rememberSize(kind, size) {
    try {
        window.localStorage.setItem(sizeKey(kind), JSON.stringify(size));
    } catch (error) {
        void error;
    }
}

function within(value, least, most) {
    return Math.max(least, Math.min(most, value));
}

function sizable(panel, kind) {
    installStyle(SIZED_STYLE_ID, SIZED_STYLE);
    panel.classList.add("mmxlib-sized");
    const kept = rememberedSize(kind);
    if (kept.w > 0) panel.style.width = Math.round(kept.w) + "px";
    if (kept.h > 0) panel.style.height = Math.round(kept.h) + "px";

    const fill = element("button", "mmxlib-fill");
    fill.type = "button";
    const hold = () =>
        panel.classList.toggle(
            "mmxlib-held",
            Boolean(panel.style.height) || panel.classList.contains("mmxlib-max")
        );
    const show = (filled) => {
        panel.classList.toggle("mmxlib-max", filled);
        hold();
        fill.innerHTML = filled ? BACK_ICON : FILL_ICON;
        fill.title = filled ? "Back to the window's own size" : "Fill the browser tab";
        fill.setAttribute("aria-label", fill.title);
    };
    const save = () =>
        rememberSize(kind, {
            w: parseFloat(panel.style.width) || 0,
            h: parseFloat(panel.style.height) || 0,
            max: panel.classList.contains("mmxlib-max"),
        });
    show(Boolean(kept.max));
    fill.addEventListener("click", (event) => {
        event.stopPropagation();
        show(!panel.classList.contains("mmxlib-max"));
        save();
    });
    fill.addEventListener("mousedown", (event) => event.preventDefault());

    for (const side of GRIPS) {
        const grip = element("div", "mmxlib-grip mmxlib-grip-" + side);
        grip.addEventListener("pointerdown", (event) => drag(event, grip, side));
        panel.appendChild(grip);
    }
    panel.appendChild(fill);

    function overflowing() {
        return panel.scrollHeight - panel.clientHeight > 0;
    }

    function fit() {
        const over = panel.scrollHeight - panel.clientHeight;
        if (over <= 0) return;
        panel.style.height =
            Math.round(
                Math.min(panel.getBoundingClientRect().height + over, window.innerHeight - EDGE)
            ) + "px";
    }

    function drag(event, grip, side) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const box = panel.getBoundingClientRect();
        const x0 = event.clientX;
        const y0 = event.clientY;
        // The panel stays centred, so an edge moves by half of what the width changes.
        const across = side.includes("e") ? 2 : side.includes("w") ? -2 : 0;
        const down = side.includes("s") ? 2 : side.includes("n") ? -2 : 0;
        try {
            grip.setPointerCapture(event.pointerId);
        } catch (error) {
            void error;
        }
        const move = (moved) => {
            const was = {
                width: panel.style.width,
                height: panel.style.height,
                over: overflowing(),
            };
            if (across) {
                panel.style.width =
                    Math.round(
                        within(
                            box.width + across * (moved.clientX - x0),
                            LEAST_W,
                            window.innerWidth - EDGE
                        )
                    ) + "px";
            }
            if (down) {
                panel.style.height =
                    Math.round(
                        within(
                            box.height + down * (moved.clientY - y0),
                            LEAST_H,
                            window.innerHeight - EDGE
                        )
                    ) + "px";
            }
            hold();
            fit();
            if (!was.over && overflowing()) {
                panel.style.width = was.width;
                panel.style.height = was.height;
            }
            hold();
        };
        const end = () => {
            grip.removeEventListener("pointermove", move);
            grip.removeEventListener("pointerup", end);
            grip.removeEventListener("pointercancel", end);
            grip.removeEventListener("lostpointercapture", end);
            save();
        };
        grip.addEventListener("pointermove", move);
        grip.addEventListener("pointerup", end);
        grip.addEventListener("pointercancel", end);
        grip.addEventListener("lostpointercapture", end);
    }
}

const OPEN = [];

export function onTop(handle) {
    return OPEN[OPEN.length - 1] === handle;
}

export function frame(wide, { onClose, sticky, size } = {}) {
    installStyle(STYLE_ID, STYLE);
    const back = element("div", "mmxlib-back");
    const panel = element("div", "mmxlib-panel" + (wide ? " mmxlib-wide" : ""));
    panel.tabIndex = -1;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    if (size) sizable(panel, size);
    back.appendChild(panel);
    const before = document.activeElement;
    const me = { close };
    OPEN.push(me);

    function close() {
        const at = OPEN.indexOf(me);
        if (at < 0) return;
        OPEN.splice(at, 1);
        document.removeEventListener("keydown", onKey, true);
        back.remove();
        if (before?.isConnected && before.closest?.(".mmxlib-panel")) {
            before.focus({ preventScroll: true });
        }
        onClose?.();
    }

    function onKey(event) {
        if (event.key !== "Escape" || OPEN[OPEN.length - 1] !== me) return;
        event.stopPropagation();
        if (me.onEscape) me.onEscape();
        else close();
    }

    panel.addEventListener("keydown", (event) => event.stopPropagation());
    document.addEventListener("keydown", onKey, true);
    back.addEventListener("pointerdown", (event) => {
        if (event.target === back && !sticky) close();
    });
    document.body.appendChild(back);
    return { back, panel, close, handle: me };
}

function asked(text, title, onClose) {
    const made = frame(false, { onClose });
    made.panel.classList.add("mmxlib-asked");
    made.panel.appendChild(element("h3", "mmxlib-title", title));
    made.panel.appendChild(element("div", "mmxlib-asked-text", String(text)));
    const row = element("div", "mmxlib-row");
    made.panel.appendChild(row);
    return { close: made.close, row };
}

export function confirmed(text, { title = "", ok = "OK", cancel = "Cancel", danger = false } = {}) {
    return new Promise((resolve) => {
        let answer = false;
        const { close, row } = asked(text, title, () => resolve(answer));
        const no = element("button", "", cancel);
        const yes = element("button", danger ? "mmxlib-danger" : "mmxlib-go", ok);
        no.addEventListener("click", close);
        yes.addEventListener("click", () => {
            answer = true;
            close();
        });
        row.append(no, yes);
        (danger ? no : yes).focus();
    });
}

export function mayClose(changed) {
    if (!changed) return Promise.resolve(true);
    return confirmed(
        "What was typed here has not been saved. Closing the window throws it away.",
        {
            title: "Close without saving?",
            ok: "Close and lose it",
            cancel: "Keep editing",
            danger: true,
        }
    );
}

export function warned(text, { title = "That did not work", ok = "OK" } = {}) {
    return new Promise((resolve) => {
        const { close, row } = asked(text, title, resolve);
        const yes = element("button", "mmxlib-go", ok);
        yes.addEventListener("click", close);
        row.appendChild(yes);
        yes.focus();
    });
}

export function footNote(idle = () => "") {
    const holder = element("div", "mmxlib-problem mmxlib-calm");
    let message = "";
    let bad = false;
    let standing = "";

    function paint() {
        const text = message || standing;
        holder.textContent = text || idle();
        holder.classList.toggle("mmxlib-calm", !text || (Boolean(message) && !bad));
    }

    return {
        holder,
        paint,
        say(text, failed = false) {
            message = text || "";
            bad = failed;
            paint();
        },
        stand(text) {
            standing = text || "";
            paint();
        },
    };
}

const TAKES_ITS_OWN_KEYS = "select, textarea, input, button, a";

export function walkable(panel, list, { search, row: selector, take, more }) {
    let key = null;
    const rows = () => [...list.querySelectorAll(selector)];

    function light(row, block = "nearest") {
        for (const one of list.querySelectorAll(".mmxlib-lit")) one.classList.remove("mmxlib-lit");
        key = row ? row.dataset.key : null;
        if (!row) return;
        row.classList.add("mmxlib-lit");
        if (block) row.scrollIntoView({ block });
    }

    function lit() {
        return key === null ? null : rows().find((row) => row.dataset.key === key) || null;
    }

    function step(by) {
        let all = rows();
        let at = all.indexOf(lit());
        if (at + by >= all.length && more) {
            more();
            all = rows();
        }
        if (!all.length) return;
        light(all[at < 0 ? 0 : within(at + by, 0, all.length - 1)]);
    }

    panel.addEventListener("keydown", (event) => {
        if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
        const own = event.target !== search && event.target.closest?.(TAKES_ITS_OWN_KEYS);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            if (own && !own.matches("button, a")) return;
            event.preventDefault();
            step(event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Enter" && !own) {
            const row = lit();
            if (!row) return;
            event.preventDefault();
            take(row);
        }
    });
    list.addEventListener("click", (event) => {
        const row = event.target.closest?.(selector);
        if (row) light(row, null);
    });
    list.addEventListener("dblclick", (event) => {
        const row = event.target.closest?.(selector);
        if (!row || event.target.closest(TAKES_ITS_OWN_KEYS + ", img, video")) return;
        window.getSelection?.()?.removeAllRanges();
        take(row);
    });

    return { light, lit, rows };
}
