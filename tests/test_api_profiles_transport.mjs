import assert from "node:assert/strict";
import test from "node:test";
import { managementRequest, ManagementRequestError } from "../web/js/api_profiles_transport.js";

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

test("one validated stage, with the supplied abort signal and no retries", async () => {
    let calls = 0;
    const result = await managementRequest("stage", { session: "dummy" }, { token: "dummy-admin", fetchApi: async (url, options) => {
        calls++;
        assert.equal(url, "/minimax_h3_rewriter/api/manage/stage");
        assert.equal(options.method, "POST");
        assert.equal(options.redirect, "error");
        assert.equal(options.headers["X-MiniMax-H3-Admin"], "dummy-admin");
        assert.equal(options.body, '{"session":"dummy"}');
        assert.ok(options.signal instanceof AbortSignal);
        return json({ challenge: "one-use" });
    } });
    assert.equal(result.challenge, "one-use");
    assert.equal(calls, 1);
});

test("connection-test result does not require a save or mark an unknown outcome", async () => {
    const data = { ok: true, model: "chosen", model_available: false, models: [], elapsed_seconds: .1, credential: "sent" };
    let calls = 0;
    const result = await managementRequest("test", { challenge: "one-use" }, { fetchApi: async (url) => {
        calls++;
        assert.equal(url, "/minimax_h3_rewriter/api/manage/test");
        return json(data);
    } });
    assert.deepEqual(result, data);
    assert.equal(calls, 1);
    await assert.rejects(managementRequest("test", {}, { fetchApi: () => new Promise(() => {}), timeoutMs: 25 }),
        (error) => !error.uncertain && /testing the connection/.test(error.message) && /No save request was sent/.test(error.message));
});

test("failed connection tests retain safe HTTP details and permit another test", async () => {
    await assert.rejects(managementRequest("test", {}, { fetchApi: async () => json({ error: "The API returned an unsuccessful HTTP status.", upstream_status: 404 }, 503) }),
        (error) => !error.uncertain && /HTTP 404/.test(error.message));
    for (const data of [{ ok: true }, { ok: true, model: "x", model_available: true, models: [], elapsed_seconds: -1, credential: "none" }]) {
        await assert.rejects(managementRequest("test", {}, { fetchApi: async () => json(data) }),
            (error) => !error.uncertain && /Unexpected editor response/.test(error.message));
    }
});

test("a wrapper ignoring AbortSignal still cannot hold the window indefinitely", async () => {
    let calls = 0, signal;
    await assert.rejects(managementRequest("stage", {}, { timeoutMs: 25, fetchApi: (_url, options) => {
        calls++;
        signal = options.signal;
        return new Promise(() => {});
    } }), (error) => error instanceof ManagementRequestError && !error.uncertain && /validating provider/.test(error.message));
    assert.ok(signal.aborted);
    assert.equal(calls, 1);
});

test("deadline also covers a response body that never finishes", async () => {
    const response = { ok: true, status: 200, headers: new Headers({ "Content-Type": "application/json" }), json: () => new Promise(() => {}) };
    await assert.rejects(managementRequest("commit", {}, { timeoutMs: 25, fetchApi: async () => response }),
        (error) => error.uncertain && /save result is unknown/.test(error.message));
});

test("a late commit response cannot turn a timeout into success or send another request", async () => {
    let finish, calls = 0;
    const pending = managementRequest("commit", {}, { timeoutMs: 25, fetchApi: () => {
        calls++;
        return new Promise((resolve) => { finish = resolve; });
    } });
    await assert.rejects(pending, (error) => error.uncertain);
    finish(json({ ok: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(calls, 1);
});

test("HTTP status survives a proxy's non-JSON rejection without exposing its body", async () => {
    await assert.rejects(managementRequest("stage", {}, { fetchApi: async () => new Response("private-dummy-response", { status: 403 }) }),
        (error) => error.status === 403 && !error.uncertain && /HTTP 403/.test(error.message) && !error.message.includes("private-dummy"));
});

test("malformed successful commit response is an unknown outcome", async () => {
    await assert.rejects(managementRequest("commit", {}, { fetchApi: async () => new Response("{", { headers: { "Content-Type": "application/json" } }) }),
        (error) => error.uncertain && /JSON response/.test(error.message));
});

test("network failure and invalid success contract also preserve commit uncertainty", async () => {
    for (const fetchApi of [async () => { throw new Error("private-transport-text"); }, async () => json({})]) {
        await assert.rejects(managementRequest("commit", {}, { fetchApi }),
            (error) => error.uncertain && !error.message.includes("private-transport-text"));
    }
});

test("ordinary validation/authentication errors retain safe server status", async () => {
    for (const status of [400, 401, 409]) {
        await assert.rejects(managementRequest("stage", {}, { fetchApi: async () => json({ error: "Safe validation error" }, status) }),
            (error) => error.status === status && error.message === "Safe validation error" && !error.uncertain);
    }
});

test("a server failure after commit requires checking the saved state", async () => {
    await assert.rejects(managementRequest("commit", {}, { fetchApi: async () => json({ error: "Saving failed." }, 503) }),
        (error) => error.status === 503 && error.uncertain && /Reopen API providers/.test(error.message));
});

test("invalid adapter responses cannot make a failed commit safe to repeat", async () => {
    for (const response of [undefined, {}, { ok: false, status: 0, headers: new Headers(), json: async () => ({}) }]) {
        await assert.rejects(managementRequest("commit", {}, { fetchApi: async () => response }), (error) => error.uncertain);
    }
    await assert.rejects(managementRequest("commit", {}, { fetchApi: async () => json({ ok: true }) }), (error) => error.uncertain);
});

test("valid editor snapshots and close acknowledgements are accepted", async () => {
    for (const [action, data] of [["open", { session: "dummy", profiles: [], revision: "dummy" }],
        ["commit", { ok: true, profiles: [], revision: "dummy" }], ["close", { ok: true }]]) {
        assert.deepEqual(await managementRequest(action, {}, { fetchApi: async () => json(data) }), data);
    }
});
