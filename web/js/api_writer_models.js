import { api } from "../../scripts/api.js";
import { graphChanged, installStyle, refresh, setWidgetValue, widgetNamed } from "./mmx_controls.js";
import { openApiProviders } from "./api_profiles_dialog.js";

function fillSelect(select, options, value) {
    const signature = JSON.stringify(options);
    if (select.dataset.items !== signature) {
        select.replaceChildren(...options.map((one) => {
            const option = document.createElement("option");
            option.value = one.id;
            option.textContent = one.label;
            return option;
        }));
        select.dataset.items = signature;
    }
    select.value = value;
}

export function modelControls(node, { chooseProfile = false } = {}) {
    installStyle("minimax-h3-api-models-style", `
.mmx-api-models { display: flex; flex-direction: column; gap: 3px; width: 100%; height: 100%; font: 11px system-ui, sans-serif; }
.mmx-api-model-row { display: flex; gap: 6px; min-height: 25px; }
.mmx-api-model-row select { flex: 1 1 0; min-width: 0; }
.mmx-api-profile { min-height: 25px; }
.mmx-api-models select, .mmx-api-model-row button { color: var(--input-text, #ddd); background: var(--comfy-input-bg, #2b2b2b); border: 1px solid var(--border-color, #555); border-radius: 5px; font: inherit; }
.mmx-api-model-row button { padding: 0 8px; cursor: pointer; }
.mmx-api-model-note { color: var(--descrip-text, #aaa); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`);
    const holder = document.createElement("div");
    holder.className = "mmx-api-models";
    const row = document.createElement("div");
    row.className = "mmx-api-model-row";
    const select = document.createElement("select");
    select.setAttribute("aria-label", "API model");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Refresh models";
    button.title = "Read this connection's /models on the ComfyUI server. No generation, downloads or model-management requests.";
    const manage = document.createElement("button");
    manage.type = "button";
    manage.textContent = "API providers";
    manage.title = "Edit server connections, allowed models, capabilities and encrypted credentials.";
    manage.addEventListener("click", () => openApiProviders(node));
    const note = document.createElement("div");
    note.className = "mmx-api-model-note";
    row.append(select, button, manage);
    holder.append(row, note);
    const profileSelect = chooseProfile ? document.createElement("select") : null;
    if (profileSelect) {
        profileSelect.className = "mmx-api-profile";
        profileSelect.setAttribute("aria-label", "API profile");
        holder.prepend(profileSelect);
    }
    let profileId, listing, problem = "", generation = 0, loading = false;
    let profiles, profilesProblem = "", profileGeneration = 0;
    const currentProfile = () => widgetNamed(node, "profile_id")?.value || "";
    const driven = (name) => node.inputs?.some((one) => one.name === name && one.link != null);

    function draw() {
        if (profileSelect) {
            const id = currentProfile();
            const choices = [{ id: "", label: "Choose API profile" },
                ...(profiles || []).map((one) => ({ id: one.id, label: one.id }))];
            if (id && !choices.some((one) => one.id === id)) choices.push({ id, label: id });
            fillSelect(profileSelect, choices, id);
            profileSelect.disabled = driven("profile_id");
        }
        const selected = widgetNamed(node, "model")?.value || "";
        const wired = driven("profile_id") || driven("model");
        const items = listing?.models || [];
        const options = [{ id: "", label: `From profile${listing?.default_model ? " (" + listing.default_model + ")" : ""}` },
            ...items.map((one) => ({ id: one.id, label: one.id + (one.available === false ? " (unavailable)" : "") }))];
        if (selected && !options.some((one) => one.id === selected)) options.push({ id: selected, label: selected + " (not allowed in this profile)" });
        fillSelect(select, options, selected);
        select.disabled = wired || !profileId;
        button.disabled = loading || wired || !profileId;
        note.textContent = wired ? "Profile/model comes from a wire; checked during execution."
            : loading ? "Reading model list..." : problem || profilesProblem
            || (!profileId && chooseProfile ? "Choose a profile or add one in API providers." : listing?.source === "server"
                ? "Server list, limited to models allowed locally."
                : "Configured models. Refresh to check server availability.");
        note.title = problem || listing?.note || note.textContent;
    }

    async function pull(remote) {
        const id = profileId;
        const ticket = ++generation;
        loading = true;
        problem = "";
        draw();
        try {
            const response = await api.fetchApi(remote ? "/minimax_h3_rewriter/api/models/refresh"
                : "/minimax_h3_rewriter/api/models?profile_id=" + encodeURIComponent(id), remote ? {
                    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: id }),
                } : undefined);
            const result = await response.json();
            if (ticket !== generation || currentProfile() !== id || !node.graph) return;
            if (!response.ok || result.profile_id !== id || !Array.isArray(result.models)) {
                problem = typeof result.error === "string" ? result.error : "Model list unavailable; check the local profile.";
            } else listing = result;
        } catch {
            if (ticket !== generation || currentProfile() !== id || !node.graph) return;
            problem = "Model list unavailable; check the connection and refresh again.";
        } finally {
            if (ticket === generation && currentProfile() === id) {
                loading = false;
                draw();
                refresh(node);
            }
        }
    }

    async function pullProfiles() {
        const ticket = ++profileGeneration;
        profiles ||= [];
        profilesProblem = "";
        try {
            const response = await api.fetchApi("/minimax_h3_rewriter/api/profiles");
            const result = await response.json();
            if (ticket !== profileGeneration || !node.graph) return;
            if (!response.ok || !Array.isArray(result.profiles)) throw new Error("Invalid profile list");
            profiles = result.profiles;
        } catch {
            if (ticket !== profileGeneration || !node.graph) return;
            profilesProblem = "Profile list unavailable. Open API providers to check the configuration.";
        }
        draw();
    }

    profileSelect?.addEventListener("change", () => {
        if (!driven("model")) widgetNamed(node, "model").value = "";
        setWidgetValue(node, "profile_id", profileSelect.value);
    });
    select.addEventListener("change", () => {
        widgetNamed(node, "model").value = select.value;
        refresh(node);
        graphChanged();
    });
    button.addEventListener("click", () => pull(true));
    return { holder,
        update(force = false) {
            if (profileSelect && (!profiles || force) && node.graph) pullProfiles();
            const next = currentProfile();
            if (next !== profileId || force) {
                profileId = next;
                listing = null;
                problem = "";
                ++generation;
                loading = false;
                if (profileId && !driven("profile_id")) pull(false);
            }
            draw();
        },
        selected() {
            const id = widgetNamed(node, "model")?.value || listing?.default_model;
            return listing?.models?.find((one) => one.id === id);
        },
    };
}
