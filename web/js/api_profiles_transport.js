const ROOT = "/minimax_h3_rewriter/api/manage/";

export class ManagementRequestError extends Error {
    constructor(message, action, status = 0, uncertain = false) {
        super(message);
        this.name = "ManagementRequestError";
        this.action = action;
        this.status = status;
        this.uncertain = uncertain;
    }
}

export async function managementRequest(action, body, { fetchApi, token = "", timeoutMs = 20000 }) {
    if (!["open", "stage", "commit", "test", "close"].includes(action) || typeof fetchApi !== "function"
        || !Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError("Invalid editor request.");
    const labels = { open: "opening the editor", stage: "validating provider settings", commit: "saving provider settings", test: "testing the connection", close: "closing the editor" };
    const unknown = " The save result is unknown. Reopen API providers to check the saved settings before trying again.";
    const controller = new AbortController();
    let timer, expired = false;
    const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
            expired = true;
            controller.abort();
            reject(new ManagementRequestError(`Timed out while ${labels[action]}.` + (action === "commit" ? unknown : " No save request was sent."), action, 0, action === "commit"));
        }, timeoutMs);
    });
    const operation = async () => {
        let response;
        try {
            response = await fetchApi(ROOT + action, { method: "POST", headers: { "Content-Type": "application/json", ...(token ? { "X-MiniMax-H3-Admin": token } : {}) },
                body: JSON.stringify(body), cache: "no-store", redirect: "error", signal: controller.signal, timeoutMs });
        } catch {
            throw new ManagementRequestError(`Connection failed while ${labels[action]}.` + (action === "commit" ? unknown : " Check the ComfyUI connection; no save request was sent."), action, 0, action === "commit");
        }
        if (!response || !Number.isInteger(response.status) || typeof response.ok !== "boolean"
            || typeof response.headers?.get !== "function" || typeof response.json !== "function") {
            throw new ManagementRequestError(`Invalid transport response while ${labels[action]}.` + (action === "commit" ? unknown : ""), action, 0, action === "commit");
        }
        const uncertain = action === "commit" && (response.ok || response.status === 0 || response.status >= 500);
        if (!response.headers.get("Content-Type")?.toLowerCase().includes("application/json")) {
            throw new ManagementRequestError(`ComfyUI returned HTTP ${response.status} without a JSON response while ${labels[action]}.` + (uncertain ? unknown : ""), action, response.status, uncertain);
        }
        let payload;
        try {
            payload = await response.json();
        } catch {
            throw new ManagementRequestError(`The JSON response was incomplete or invalid while ${labels[action]} (HTTP ${response.status}).` + (uncertain ? unknown : ""), action, response.status, uncertain);
        }
        if (!response.ok) {
            let message = typeof payload?.error === "string" ? payload.error : `The editor request failed (HTTP ${response.status}).`;
            if (action === "test" && Number.isInteger(payload?.upstream_status) && !message.includes(String(payload.upstream_status))) message += ` HTTP ${payload.upstream_status}.`;
            throw new ManagementRequestError(message + (uncertain ? unknown : ""), action, response.status, uncertain);
        }
        const valid = payload && !Array.isArray(payload) && typeof payload === "object"
            && (action === "open" ? typeof payload.session === "string" && Array.isArray(payload.profiles) && typeof payload.revision === "string"
                : action === "stage" ? typeof payload.challenge === "string"
                    : action === "commit" ? payload.ok === true && Array.isArray(payload.profiles) && typeof payload.revision === "string"
                        : action === "test" ? payload.ok === true && typeof payload.model === "string" && typeof payload.model_available === "boolean"
                            && Array.isArray(payload.models) && payload.models.every((one) => typeof one === "string")
                            && Number.isFinite(payload.elapsed_seconds) && payload.elapsed_seconds >= 0 && ["sent", "none"].includes(payload.credential)
                        : payload.ok === true);
        if (!valid) throw new ManagementRequestError(`Unexpected editor response while ${labels[action]} (HTTP ${response.status}).` + (uncertain ? unknown : ""), action, response.status, uncertain);
        return payload;
    };
    try {
        return await Promise.race([operation(), deadline]);
    } catch (error) {
        if (expired) throw new ManagementRequestError(`Timed out while ${labels[action]}.` + (action === "commit" ? unknown : " No save request was sent."), action, 0, action === "commit");
        throw error;
    } finally {
        clearTimeout(timer);
    }
}
