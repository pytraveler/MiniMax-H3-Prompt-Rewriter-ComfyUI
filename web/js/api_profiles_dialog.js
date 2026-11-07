import { api } from "../../scripts/api.js";
import { installStyle, setWidgetValue } from "./mmx_controls.js";
import { confirmed, element, frame, mayClose } from "./mmx_window.js";
import { managementRequest } from "./api_profiles_transport.js";

const CHANGED = "minimax-h3-api-profiles-changed";
const clone = (value) => JSON.parse(JSON.stringify(value));
const defaults = () => ({ id: "new-model", capabilities: { modalities: ["text"], formats: ["text"], parameters: ["max_tokens", "temperature"] },
    limits: { output_tokens: 8192, context_tokens: null, request_bytes: 1048576, response_bytes: 8388608, references: 12 } });
let opened;

function style() {
    installStyle("minimax-h3-api-providers-style", `
.mmxlib-panel.mmx-api-providers { width: min(980px, 94vw); height: min(800px, 88vh); }
.mmx-api-providers .mmx-api-workspace { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 16px; flex: 1; min-height: 0; }
.mmx-api-providers .mmx-api-sidebar { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
.mmx-api-providers .mmx-api-provider-list { overflow: auto; display: flex; flex-direction: column; gap: 6px; }
.mmx-api-providers .mmx-api-provider-list button { text-align: left; overflow-wrap: anywhere; }
.mmx-api-providers button { font: inherit; font-size: 12px; cursor: pointer; padding: 6px 10px; border-radius: 6px;
    color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b); border: 1px solid var(--border-color, #4e4e4e); }
.mmx-api-providers button:disabled { opacity: .5; cursor: not-allowed; }
.mmx-api-providers button[aria-pressed=true] { border-color: #3B7DD8; background: rgba(59,125,216,.18); }
.mmx-api-providers .mmx-api-form { overflow: auto; min-height: 0; padding: 0 8px 12px 0; }
.mmx-api-providers .mmx-api-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }
.mmx-api-providers input[type=password], .mmx-api-providers input[type=number], .mmx-api-providers input[type=url] { width: 100%; box-sizing: border-box;
    font: inherit; font-size: 13px; padding: 6px 8px; border-radius: 6px; color: var(--input-text, #ddd);
    background: var(--comfy-input-bg, #2b2b2b); border: 1px solid var(--border-color, #4e4e4e); }
.mmx-api-providers .mmx-api-section { font-size: 13px; margin: 20px 0 6px; padding-top: 12px; border-top: 1px solid var(--border-color, #4e4e4e); }
.mmx-api-providers .mmx-api-note { font-size: 11px; color: var(--descrip-text, #aaa); line-height: 1.5; margin: 6px 0; }
.mmx-api-providers .mmx-api-checks { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; }
.mmx-api-providers .mmx-api-checks label { display: flex; align-items: center; gap: 4px; }
.mmx-api-providers .mmx-api-model-tools { display: flex; gap: 8px; align-items: center; }
.mmx-api-providers .mmx-api-model-tools select { flex: 1; min-width: 0; }
.mmx-api-providers .mmx-api-bottom { flex-wrap: wrap; }
.mmx-api-providers .mmxlib-problem { flex-basis: 100%; white-space: pre-line; }
.mmx-api-providers [hidden] { display: none !important; }
@media(max-width: 650px) { .mmx-api-providers .mmx-api-workspace { grid-template-columns: 140px minmax(0,1fr); gap: 8px; }
    .mmx-api-providers .mmx-api-grid { grid-template-columns: minmax(0,1fr); } }
`);
}

function choices(items, label) {
    const select = element("select");
    select.setAttribute("aria-label", label);
    for (const [value, title] of items) {
        const option = element("option", "", title);
        option.value = value;
        select.appendChild(option);
    }
    return select;
}

function field(parent, title, control, tip = "") {
    const label = element("label", "mmxlib-field", title);
    control.setAttribute("aria-label", title);
    if (tip) control.title = tip;
    label.appendChild(control);
    parent.appendChild(label);
    return control;
}

function textInput(type, value = "") {
    const input = element("input");
    input.type = type;
    input.value = value;
    return input;
}

function button(title, action, css = "") {
    const made = element("button", css, title);
    made.type = "button";
    made.addEventListener("click", action);
    return made;
}

function base64(bytes) {
    let text = "";
    for (let offset = 0; offset < bytes.length; offset += 8192) text += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    return btoa(text);
}

async function encryptKey(challenge, input, { clear = true } = {}) {
    if (!window.isSecureContext || !crypto.subtle) throw new Error("Web Crypto is unavailable. Open ComfyUI through localhost or HTTPS.");
    const label = new TextEncoder().encode("minimax-h3-api-key:v1:" + challenge.challenge);
    const der = Uint8Array.from(atob(challenge.public_key), (char) => char.charCodeAt(0));
    const rsa = await crypto.subtle.importKey("spki", der, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]);
    const aes = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt"]);
    const raw = new Uint8Array(await crypto.subtle.exportKey("raw", aes));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const message = new TextEncoder().encode(input.value);
    if (clear) input.value = "";
    try {
        const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: label, tagLength: 128 }, aes, message);
        const wrapped = await crypto.subtle.encrypt({ name: "RSA-OAEP", label }, rsa, raw);
        return { wrapped_key: base64(new Uint8Array(wrapped)), iv: base64(iv), ciphertext: base64(new Uint8Array(ciphertext)) };
    } finally {
        message.fill(0);
        raw.fill(0);
    }
}

export function openApiProviders(node) {
    if (opened) {
        opened.panel.focus();
        return;
    }
    style();
    let state, draft, original, selected = -1, dirty = false, busy = false, closed = false, keyInput, adminToken = "", connecting = false, needsReload = false;
    let readModel = () => {}, collect = () => {}, readKeyAction = () => "keep";
    const dialog = frame(true, { size: "api-providers", sticky: true, onClose() {
        closed = true;
        if (keyInput) keyInput.value = "";
        if (state?.session) post("close", { session: state.session }).catch(() => {});
        for (const input of form.querySelectorAll('input[type="password"]')) input.value = "";
        adminToken = "";
        state = null;
        draft = null;
        opened = null;
    } });
    opened = dialog;
    const { panel } = dialog;
    panel.classList.add("mmx-api-providers");
    panel.setAttribute("aria-label", "API providers");
    panel.append(element("h3", "mmxlib-title", "API providers"), element("div", "mmxlib-sub",
        "Connections and model capabilities are saved on the ComfyUI server. API keys stay outside workflows."));
    const workspace = element("div", "mmx-api-workspace");
    const sidebar = element("div", "mmx-api-sidebar");
    const template = choices([["custom", "Custom connection"]], "New provider template");
    const listing = element("div", "mmx-api-provider-list");
    sidebar.append(template, button("Add provider", () => addProvider()), listing);
    const form = element("form", "mmx-api-form");
    form.autocomplete = "off";
    form.addEventListener("submit", (event) => { event.preventDefault(); save(); });
    const changed = (event) => { if (!draft || event.target.dataset.browse) return; dirty = true; enable(); };
    form.addEventListener("input", changed);
    form.addEventListener("change", changed);
    workspace.append(sidebar, form);
    const actions = element("div", "mmxlib-row mmx-api-bottom");
    const note = element("div", "mmxlib-problem mmxlib-calm", "Loading local configuration...");
    note.setAttribute("role", "status");
    const remove = button("Delete provider", () => deleteProvider(), "mmxlib-danger");
    const testButton = button("Test connection", () => testConnection());
    const use = button("Use in this node", () => useProvider());
    const cancel = button("Close", () => tryClose());
    const saveButton = button("Save provider", () => save(), "mmxlib-go");
    actions.append(note, testButton, remove, use, cancel, saveButton);
    panel.append(workspace, actions);
    dialog.handle.onEscape = tryClose;

    function say(message, failed = false) {
        if (closed) return;
        note.textContent = message;
        note.classList.toggle("mmxlib-calm", !failed);
    }

    async function post(action, body, access = adminToken) {
        return managementRequest(action, body, { fetchApi: api.fetchApi.bind(api), token: access });
    }

    function enable() {
        for (const control of sidebar.querySelectorAll("button, select")) control.disabled = busy || needsReload || !state;
        remove.disabled = busy || needsReload || !original;
        saveButton.disabled = busy || needsReload || !draft;
        testButton.disabled = busy || needsReload || !draft;
        use.disabled = busy || needsReload || !original || dirty || !node?.graph;
        cancel.disabled = busy;
        const fields = form.querySelector("fieldset");
        if (fields) fields.disabled = busy || needsReload;
    }

    async function tryClose() {
        if (!busy && (needsReload || await mayClose(dirty))) dialog.close();
    }

    function providers() {
        listing.replaceChildren(...(state?.profiles || []).map((profile) => {
            const pick = button(profile.name + "\n" + profile.id, async () => {
                if (!busy && await mayClose(dirty)) edit(profile);
            });
            pick.setAttribute("aria-pressed", String(profile.id === original?.id));
            return pick;
        }));
        enable();
    }

    function updateModelChoices(select) {
        select.replaceChildren(...draft.models.map((model, index) => {
            const option = element("option", "", model.id + (draft.defaultIndex === index ? " (default)" : ""));
            option.value = String(index);
            return option;
        }));
        select.value = String(selected);
    }

    function edit(profile, isNew = false, preferredModel) {
        if (keyInput) keyInput.value = "";
        original = isNew ? null : clone(profile);
        draft = { ...clone(profile), models: [{ id: profile.model, capabilities: clone(profile.capabilities), limits: clone(profile.limits) }, ...clone(profile.models || [])], defaultIndex: 0 };
        const nodeProfile = node?.widgets?.find((one) => one.name === "profile_id")?.value;
        const nodeModel = nodeProfile === profile.id ? node?.widgets?.find((one) => one.name === "model")?.value : "";
        const wanted = preferredModel ?? (isNew ? "" : nodeModel);
        selected = Math.max(0, draft.models.findIndex((one) => one.id === wanted));
        dirty = isNew;
        form.replaceChildren();
        const fields = element("fieldset");
        fields.style.cssText = "border:0;padding:0;margin:0;min-width:0";
        form.appendChild(fields);
        const main = element("div", "mmx-api-grid");
        const id = field(main, "Profile ID", textInput("text", profile.id), "Stable workflow ID. Existing IDs cannot be renamed.");
        id.required = true;
        id.maxLength = 80;
        id.pattern = "[A-Za-z0-9][A-Za-z0-9_.\\-]{0,79}";
        id.readOnly = !isNew;
        const name = field(main, "Display name", textInput("text", profile.name));
        name.required = true;
        name.maxLength = 200;
        fields.appendChild(main);
        const url = field(fields, "Base URL", textInput("url", profile.base_url), "Chat Completions base URL, including /v1 if required. No API keys, query parameters or credentials in the address.");
        url.required = true;
        const connection = element("div", "mmx-api-grid");
        const network = field(connection, "Network", choices([["loopback", "Local loopback"], ["lan", "Private LAN (explicit IP)"], ["cloud", "Public HTTPS"]], "Network"));
        network.value = profile.network;
        const protocol = field(connection, "API protocol", choices([["chat_completions", "OpenAI-compatible Chat Completions"]], "API protocol"));
        protocol.value = profile.protocol;
        fields.appendChild(connection);
        const streamLabel = element("label", "mmxlib-field");
        const stream = textInput("checkbox");
        stream.checked = profile.stream !== false;
        streamLabel.append(stream, document.createTextNode(" Stream response (recommended for cancellation)"));
        fields.appendChild(streamLabel);
        fields.appendChild(element("div", "mmx-api-note", "Test connection checks the current form through GET /models without saving or generating text. Model capabilities and generation parameters are not verified."));
        fields.appendChild(element("h4", "mmx-api-section", "Credential"));
        const source = field(fields, "Credential source", choices([["none", "No key"], ["vault", "Encrypted server storage"], ["environment", "Server environment variable"]], "Credential source"));
        source.value = profile.secret.kind;
        const environment = field(fields, "Environment variable name", textInput("text", profile.secret.kind === "environment" ? profile.secret.name : ""));
        const keyAction = field(fields, "Saved key", choices([["keep", "Keep existing key / configure later"], ["replace", "Enter or replace key"],
            ...(original?.secret.kind === "vault" ? [["delete", "Delete saved key"]] : [])], "Saved key"));
        keyInput = field(fields, "New API key", textInput("password"));
        keyInput.autocomplete = "new-password";
        keyInput.maxLength = 65536;
        keyInput.spellcheck = false;
        keyInput.setAttribute("data-lpignore", "true");
        const credentialNote = element("div", "mmx-api-note", state.key_storage === "windows_dpapi"
            ? "A new key is encrypted in the browser for a one-use server challenge, then stored with Windows DPAPI. Saved keys are never returned."
            : state.key_storage === "server_key_aesgcm"
                ? "A new key is encrypted in the browser for a one-use server challenge, then stored with AES-GCM using the server's vault key. Saved keys are never returned."
                : "For encrypted storage, configure MINIMAX_H3_VAULT_KEY on the server (base64 of 32 random bytes). Explicit environment credentials work without it.");
        fields.appendChild(credentialNote);
        function credentialVisibility() {
            environment.parentElement.hidden = source.value !== "environment";
            keyAction.parentElement.hidden = source.value !== "vault";
            keyInput.parentElement.hidden = source.value !== "vault" || keyAction.value !== "replace";
            if (keyInput.parentElement.hidden) keyInput.value = "";
            for (const option of keyAction.options) if (option.value === "replace") option.disabled = state.key_storage === "environment_only" || !window.isSecureContext || !crypto.subtle;
        }
        source.addEventListener("change", credentialVisibility);
        keyAction.addEventListener("change", credentialVisibility);
        credentialVisibility();

        fields.appendChild(element("h4", "mmx-api-section", "Allowed models"));
        fields.appendChild(element("div", "mmx-api-note", "Declare this model's actual server support. Video references use sampled images and need image support; the video flag records a declaration. These settings do not add server capabilities."));
        const modelTools = element("div", "mmx-api-model-tools");
        const modelSelect = choices([], "Edit model");
        modelSelect.dataset.browse = "true";
        const modelBox = element("div");
        const add = button("Add model", () => {
            readModel();
            if (draft.models.length >= 64) return say("At most 64 models per profile.", true);
            const model = defaults();
            for (let index = 1; draft.models.some((one) => one.id === model.id); index++) model.id = "new-model-" + index;
            draft.models.push(model);
            selected = draft.models.length - 1;
            dirty = true;
            renderModel();
        });
        const drop = button("Remove model", () => {
            readModel();
            if (draft.models.length <= 1) return;
            draft.models.splice(selected, 1);
            if (draft.defaultIndex === selected) draft.defaultIndex = 0;
            else if (draft.defaultIndex > selected) draft.defaultIndex--;
            selected = Math.min(selected, draft.models.length - 1);
            dirty = true;
            renderModel();
        });
        modelTools.append(modelSelect, add, drop);
        fields.append(modelTools, modelBox);
        modelSelect.addEventListener("change", () => { readModel(); selected = Number(modelSelect.value); renderModel(); });

        function renderModel() {
            updateModelChoices(modelSelect);
            drop.disabled = draft.models.length <= 1;
            modelBox.replaceChildren();
            const model = draft.models[selected];
            const modelId = field(modelBox, "Model ID", textInput("text", model.id));
            modelId.required = true;
            modelId.maxLength = 256;
            const defaultLabel = element("label", "mmxlib-field");
            const isDefault = textInput("checkbox");
            isDefault.checked = draft.defaultIndex === selected;
            isDefault.setAttribute("aria-label", "Default model");
            isDefault.addEventListener("change", () => { readModel(); draft.defaultIndex = selected; isDefault.checked = true; updateModelChoices(modelSelect); });
            defaultLabel.append(isDefault, document.createTextNode(" Default model for this profile"));
            modelBox.appendChild(defaultLabel);
            const groups = {};
            for (const [group, title, names] of [["modalities", "Input modalities", ["text", "image", "audio", "video"]],
                ["formats", "Output formats", ["text", "json_object", "json_schema"]], ["parameters", "Supported parameters", ["max_tokens", "temperature", "seed"]]]) {
                modelBox.appendChild(element("div", "mmxlib-field", title));
                const row = element("div", "mmx-api-checks");
                groups[group] = names.map((value, index) => {
                    const label = element("label");
                    const check = textInput("checkbox");
                    check.checked = model.capabilities[group].includes(value);
                    check.disabled = index === 0;
                    check.setAttribute("aria-label", group + ": " + value);
                    label.append(check, document.createTextNode(value));
                    row.appendChild(label);
                    return [value, check];
                });
                modelBox.appendChild(row);
            }
            const limits = element("div", "mmx-api-grid");
            const numbers = {};
            for (const [key, title] of [["output_tokens", "Output token limit"], ["context_tokens", "Context tokens (optional)"],
                ["request_bytes", "Request byte limit"], ["response_bytes", "Response byte limit"], ["references", "Reference limit"]]) {
                const input = field(limits, title, textInput("number", model.limits[key] ?? ""));
                input.min = "1";
                input.max = "2147483647";
                input.step = "1";
                input.required = key !== "context_tokens";
                numbers[key] = input;
            }
            modelBox.appendChild(limits);
            readModel = () => {
                model.id = modelId.value;
                for (const [group, boxes] of Object.entries(groups)) model.capabilities[group] = boxes.filter(([, check]) => check.checked).map(([value]) => value);
                for (const [key, input] of Object.entries(numbers)) model.limits[key] = input.value === "" && key === "context_tokens" ? null : Number(input.value);
            };
        }
        renderModel();
        readKeyAction = () => original?.secret.kind === "vault" && source.value !== "vault" ? "delete" : source.value === "vault" ? keyAction.value : "keep";
        collect = () => {
            readModel();
            const model = draft.models[draft.defaultIndex];
            return { id: id.value, name: name.value, base_url: url.value, network: network.value, protocol: protocol.value, stream: stream.checked,
                secret: { kind: source.value, name: source.value === "environment" ? environment.value : source.value === "vault" && original?.secret.kind === "vault" ? original.secret.name : "" },
                model: model.id, capabilities: clone(model.capabilities), limits: clone(model.limits),
                models: draft.models.filter((_, index) => index !== draft.defaultIndex).map(clone) };
        };
        say(isNew ? "New provider. Save it before using it in a workflow." : "Saved keys are not loaded into this window. Leave the key operation unchanged to keep one.");
        providers();
    }

    async function addProvider() {
        if (!state || busy || !await mayClose(dirty)) return;
        const picked = state.templates.find((one) => one.id === template.value);
        const first = defaults();
        const profile = picked ? clone(picked) : { id: "new-provider", name: "New provider", base_url: "http://127.0.0.1:8080/v1", network: "loopback",
            protocol: "chat_completions", stream: true, secret: { kind: "none", name: "" }, model: first.id, capabilities: first.capabilities, limits: first.limits };
        profile.secret.name = "";
        for (let index = 1; state.profiles.some((one) => one.id === profile.id); index++) profile.id = (picked?.id || "new-provider") + "-" + index;
        edit(profile, true);
    }

    async function commit(operation, profile, keyAction) {
        const selectedModel = operation === "save" ? draft?.models[selected]?.id : undefined;
        busy = true;
        enable();
        say("Validating provider settings...");
        try {
            const staged = await post("stage", { session: state.session, revision: state.revision, operation, original_id: original?.id || "",
                ...(profile ? { profile } : {}), key_action: keyAction });
            if (keyAction === "replace") say("Encrypting the new API key...");
            const envelope = keyAction === "replace" ? await encryptKey(staged, keyInput) : null;
            say("Saving provider settings...");
            const result = await post("commit", { session: state.session, challenge: staged.challenge, ...(envelope ? { envelope } : {}) });
            if (closed) return;
            state = { ...state, ...result };
            dirty = false;
            const next = state.profiles.find((one) => one.id === profile?.id) || state.profiles[0];
            if (next) edit(next, false, next.id === profile?.id ? selectedModel : undefined);
            else { draft = null; original = null; form.replaceChildren(); providers(); }
            window.dispatchEvent(new CustomEvent(CHANGED));
            say(operation === "delete" ? "Provider deleted." : "Provider saved. No request was sent to a model.");
        } catch (error) {
            needsReload = Boolean(error.uncertain);
            say(error.message || "Saving failed. Reopen the editor to check the current configuration.", true);
        } finally {
            if (keyInput) keyInput.value = "";
            busy = false;
            if (!closed) enable();
        }
    }

    async function save() {
        if (busy || needsReload || !draft || !form.reportValidity()) return;
        const action = readKeyAction();
        if (action === "replace" && !/^[\x21-\x7e]{1,65536}$/.test(keyInput.value)) return say("Enter a nonempty API key without spaces or control characters.", true);
        if (action === "delete" && !await confirmed("The saved key for this profile will be deleted from the server.", { title: "Delete saved API key?", ok: "Save and delete key", danger: true })) return;
        await commit("save", collect(), action);
    }

    async function testConnection() {
        if (busy || needsReload || !draft || !form.reportValidity()) return;
        const action = readKeyAction();
        if (action === "replace" && !/^[\x21-\x7e]{1,65536}$/.test(keyInput.value)) return say("Enter a nonempty API key without spaces or control characters.", true);
        const profile = collect();
        const model = draft.models[selected].id;
        busy = true;
        enable();
        say("Validating connection settings...");
        try {
            const staged = await post("stage", { session: state.session, revision: state.revision, operation: "test",
                original_id: original?.id || "", profile, key_action: action });
            if (action === "replace") say("Encrypting the test key...");
            const envelope = action === "replace" ? await encryptKey(staged, keyInput, { clear: false }) : null;
            say("Testing connection through GET /models...");
            const result = await post("test", { session: state.session, challenge: staged.challenge, model, ...(envelope ? { envelope } : {}) });
            const detail = result.model_available ? `Selected model ${result.model} is listed.` : `Selected model ${result.model} was not returned by /models. Check its ID and server configuration.`;
            say(`Connected in ${result.elapsed_seconds.toFixed(3)} s. ${detail}\n`
                + (result.credential === "sent" ? "Credentials were sent; a public /models endpoint cannot prove that a key is valid.\n" : "Anonymous access was tested.\n")
                + "No generation was requested. Settings and keys were not saved.", !result.model_available);
        } catch (error) {
            say(error.message || "Connection test failed. No settings or keys were saved.", true);
        } finally {
            busy = false;
            if (!closed) enable();
        }
    }

    async function deleteProvider() {
        if (busy || needsReload || !original || !await confirmed("Delete this provider and its saved API key? Workflows keep its ID and will report it as unavailable.",
            { title: "Delete API provider?", ok: "Delete provider", danger: true })) return;
        await commit("delete", null, original.secret.kind === "vault" ? "delete" : "keep");
    }

    function useProvider() {
        if (busy || needsReload || dirty || !original || !node?.graph) return;
        if (node.inputs?.some((one) => ["profile_id", "model"].includes(one.name) && one.link != null)) return say("Profile or model comes from a wire. Change it at the source.", true);
        const model = draft?.models[selected]?.id;
        const allowed = [original.model, ...(original.models || []).map((one) => one.id)];
        if (!allowed.includes(model)) return say("Save this model before using it in the node.", true);
        setWidgetValue(node, "profile_id", original.id);
        setWidgetValue(node, "model", model);
        dialog.close();
    }

    enable();
    function authenticate() {
        form.replaceChildren();
        const token = field(form, "Administrator token", textInput("password"));
        token.autocomplete = "off";
        token.maxLength = 512;
        token.setAttribute("data-lpignore", "true");
        form.append(element("div", "mmx-api-note", "Use the token configured by your server administrator. It stays in this window's memory until you close it."));
        const login = button("Sign in", async () => {
            if (connecting) return;
            adminToken = token.value;
            token.value = "";
            token.disabled = true;
            login.disabled = true;
            await connect();
            if (token.isConnected) { token.disabled = false; login.disabled = false; }
        });
        token.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); login.click(); } });
        form.appendChild(login);
        token.focus();
    }

    async function connect() {
        if (connecting) return;
        connecting = true;
        const access = adminToken;
        try {
            const result = await post("open", {}, access);
            if (closed) { post("close", { session: result.session }, access).catch(() => {}); return; }
            state = result;
            for (const item of state.templates) {
                const option = element("option", "", item.name);
                option.value = item.id;
                template.appendChild(option);
            }
            const profile = state.profiles.find((one) => one.id === node?.widgets?.find((one) => one.name === "profile_id")?.value) || state.profiles[0];
            if (profile) edit(profile);
            else { say("Add your first provider. Its API key will stay on the server."); providers(); }
        } catch (error) {
            if (closed) return;
            if (error.status === 401) { adminToken = ""; authenticate(); }
            say(error.message, true);
        } finally {
            connecting = false;
        }
    }
    connect();
    panel.focus();
}
