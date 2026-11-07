"""T2VA behavior at the API, parser, node and administrator-tool boundaries."""

import asyncio
import gzip
import importlib
import json
import pathlib
import sys
import time
import traceback
import types
from dataclasses import replace

import pytest

from test_api_writer import SECRET, client, completion, fake_server, http_client, job, local_profile, profiles

config = importlib.import_module("minimax_h3_rewriter.api_config")
text = importlib.import_module("minimax_h3_rewriter.api_text")
writer = importlib.import_module("minimax_h3_rewriter.api_writer")
secrets = importlib.import_module("minimax_h3_rewriter.api_secrets")
guide_prompt = importlib.import_module("minimax_h3_rewriter.guide_prompt")

SECTIONS = {"integrated_multimodal_description": "A quiet cinematic scene. [Shot 1] A cat sits on a windowsill.",
            "overall_soundscape": "A soft wind rustles the curtains.", "non_diegetic_music": "N/A"}
ANSWER = "\n\n".join(f"{name}: {value}" for name, value in SECTIONS.items())


def result(value):
    return client.ApiResult("local", "test", value, "separate reasoning", client.TokenUsage(10, 20, 30), 0.1, ())


class ScriptedClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def complete(self, request, **_kwargs):
        self.calls.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return result(answer)

    async def complete_async(self, request, **kwargs):
        return self.complete(request, **kwargs)


def run_node(*args, **kwargs):
    return asyncio.run(writer.MiniMaxH3ApiUniversalWriter().execute(*args, **kwargs))


def test_text_path_preserves_raw_answer_and_checks_h3_sections():
    answer = "\n" + ANSWER + "\n"
    connection = ScriptedClient(answer)
    written = text.write(job(), connection)
    assert written.reading.text == answer and written.reading.sections == SECTIONS
    assert not written.reading.issues and len(connection.calls) == 1
    report = json.loads(written.report)
    assert report["requests"] == report["completed_requests"] == 1
    assert report["usage"]["total_tokens"] == 30 and report["validation"] == "h3"
    assert "separate reasoning" not in written.report + repr(written)


@pytest.mark.parametrize("format", ["json_object", "json_schema"])
def test_json_sections_compile_into_regular_h3_text(format):
    request = job(response_format=format)
    reading = text.read_answer(request, json.dumps(SECTIONS))
    assert reading.text == ANSWER and reading.sections == SECTIONS and not reading.issues


@pytest.mark.parametrize("invalid", [
    "{}", "[]", "null", '{"integrated_multimodal_description":"one","integrated_multimodal_description":"two"}',
    json.dumps({**SECTIONS, "extra": "unwanted"}), json.dumps({**SECTIONS, "overall_soundscape": 42}),
    json.dumps({**SECTIONS, "non_diegetic_music": " "}), "```json\n" + json.dumps(SECTIONS) + "\n```",
    '{"overall_soundscape":NaN}', "<html>bad</html>",
], ids=["missing", "list", "null", "duplicate", "extra", "number", "empty", "fence", "nan", "html"])
def test_invalid_json_is_rejected_without_echo_or_repair(invalid):
    connection = ScriptedClient(invalid)
    with pytest.raises(text.AnswerError) as error:
        text.write(job(response_format="json_object"), connection, repair_once=True)
    assert str(error.value) == "The JSON answer must contain exactly the requested nonempty string fields."
    assert len(connection.calls) == 1 and error.value.__context__ is None


def test_plain_unlabelled_answer_is_returned_with_advisory_findings():
    reading = text.read_answer(job(), "A cat sits on the sill.")
    assert reading.text == "A cat sits on the sill."
    assert {one.code for one in reading.issues} >= {"fields", "shots"}


def test_custom_system_and_validation_keep_arbitrary_final_text_as_data():
    malicious = "<script>alert('test')</script>\n__import__('os').system('never-run')"
    request = job(validation="custom", system_prompt="  Write my custom format, exactly. \n")
    connection = ScriptedClient(malicious)
    written = text.write(request, connection, repair_once=True)
    assert written.reading.text == malicious and not any(written.reading.sections.values())
    assert not written.reading.issues and len(connection.calls) == 1
    assert "Custom validation" in written.report


def test_repair_is_off_by_default_and_limited_to_one_better_answer():
    broken = ANSWER.replace("[Shot 1]", "[Shot 3]")
    first = ScriptedClient(broken)
    assert text.write(job(), first).reading.text == broken and len(first.calls) == 1
    enabled = ScriptedClient(broken, ANSWER)
    written = text.write(job(), enabled, repair_once=True)
    assert written.reading.text == ANSWER and len(enabled.calls) == 2
    assert enabled.calls[1].prompt.startswith(enabled.calls[0].prompt)
    assert enabled.calls[1].system_prompt == enabled.calls[0].system_prompt
    report = json.loads(written.report)
    assert report["requests"] == report["completed_requests"] == 2
    assert report["usage"]["total_tokens"] == 60


def test_repair_keeps_first_on_tie_and_skips_hopeless_answers():
    broken = ANSWER.replace("[Shot 1]", "[Shot 3]")
    connection = ScriptedClient(broken, broken)
    written = text.write(job(), connection, repair_once=True)
    assert written.reading.text == broken and "no improvement" in written.report
    hopeless = ScriptedClient("Unstructured output.")
    assert "skipped" in text.write(job(), hopeless, repair_once=True).report and len(hopeless.calls) == 1


def test_failed_repair_is_counted_without_retrying_or_inventing_usage():
    broken = ANSWER.replace("[Shot 1]", "[Shot 3]")
    connection = ScriptedClient(broken, client.ApiError("timeout"))
    written = text.write(job(), connection, repair_once=True)
    report = json.loads(written.report)
    assert written.reading.text == broken and len(connection.calls) == 2
    assert report["requests"] == 2 and report["completed_requests"] == 1
    assert report["usage"]["total_tokens"] is None and "timeout" in written.report


def test_cancelled_repair_does_not_hide_comfy_interrupt():
    broken = ANSWER.replace("[Shot 1]", "[Shot 3]")
    with pytest.raises(client.ApiError) as error:
        text.write(job(), ScriptedClient(broken, client.ApiError("cancelled")), repair_once=True)
    assert error.value.code == "cancelled"


def test_timeout_or_refusal_on_first_request_never_buys_repair():
    connection = ScriptedClient(client.ApiError("timeout"))
    with pytest.raises(client.ApiError):
        text.write(job(), connection, repair_once=True)
    assert len(connection.calls) == 1


def test_native_body_deadline_cannot_be_extended_by_dripping_bytes():
    with fake_server() as api:
        api.chunk_size, api.chunk_delay = 1, 0.01
        start = time.monotonic()
        with pytest.raises(client.ApiError) as error:
            http_client(local_profile(api), timeout=(1, 1), deadline=0.15).complete(job())
        assert error.value.code == "timeout" and time.monotonic() - start < 0.9
        assert len(api.calls) == 1


def test_cancellation_interrupts_wait_for_response_headers_promptly():
    with fake_server() as api:
        api.delay = 1.5
        start = time.monotonic()
        with pytest.raises(client.ApiError) as error:
            http_client(local_profile(api), timeout=(2, 2), deadline=3).complete(job(), cancelled=lambda: bool(api.calls))
        assert error.value.code == "cancelled" and time.monotonic() - start < 0.9
        assert len(api.calls) == 1


def test_cancellation_interrupts_response_body_read_promptly():
    with fake_server() as api:
        api.chunk_size, api.chunk_delay = 1, 0.02
        start = time.monotonic()
        with pytest.raises(client.ApiError) as error:
            http_client(local_profile(api), timeout=(2, 2)).complete(job(), cancelled=lambda: time.monotonic() - start > 0.15)
        assert error.value.code == "cancelled" and time.monotonic() - start < 0.9


def test_decompressed_response_size_is_bounded():
    with fake_server() as api:
        api.body = gzip.compress(json.dumps(completion("x" * 20000)).encode())
        api.headers["Content-Encoding"] = "gzip"
        profile = local_profile(api, limits=profiles.ProfileLimits(response_bytes=1024))
        with pytest.raises(client.ApiError) as error:
            http_client(profile).complete(job())
        assert error.value.code == "response_limit"


@pytest.mark.parametrize("where", ["content", "reasoning_content"])
def test_successful_response_cannot_export_an_echoed_api_key(where, caplog):
    with fake_server() as api:
        payload = {where: SECRET}
        api.body = completion(**payload) if where != "content" else completion(SECRET)
        profile = local_profile(api, secret=profiles.SecretRef("vault", "dummy"))
        with pytest.raises(client.ApiError) as error:
            http_client(profile, authorize=lambda _profile: SECRET).complete(job(profile))
        assert error.value.code == "credential_echo"
        assert SECRET not in caplog.text + "".join(traceback.format_exception(error.value))
        assert error.value.__context__ is None


def test_async_host_uses_complete_async_without_leaking_tasks():
    async def run(api):
        connection = http_client(local_profile(api))
        with pytest.raises(client.ContractError, match="Await complete_async"):
            connection.complete(job())
        answer = await connection.complete_async(job())
        assert answer.text
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}

    with fake_server() as api:
        asyncio.run(run(api))


@pytest.mark.parametrize("where", ["proxy", "post"])
def test_malformed_cloud_proxy_errors_are_sanitized_without_network(monkeypatch, where):
    def fail(*_args, **_kwargs):
        raise ValueError(SECRET)

    if where == "proxy":
        monkeypatch.setattr(client.requests.utils, "get_environ_proxies", fail)
    else:
        monkeypatch.setattr(client.requests.utils, "get_environ_proxies", lambda _url: {"https": "invalid-" + SECRET})
        monkeypatch.setattr(client.aiohttp.ClientSession, "post", fail)
    profile = profiles.example_profiles().profiles[1]
    with pytest.raises(client.ApiError) as error:
        http_client(profile, authorize=lambda _profile: SECRET).complete(job(profile))
    assert error.value.code == "connection" and error.value.__context__ is None
    assert SECRET not in "".join(traceback.format_exception(error.value))


@pytest.fixture
def node_environment(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace(get_user_directory=lambda: str(tmp_path)))
    monkeypatch.setattr(writer, "_cancelled", lambda: False)
    messages = []

    class Progress:
        def __init__(self, *_args):
            pass

        def text(self, value, **_kwargs):
            messages.append(value)

        def finish(self, value):
            messages.append(value)

    monkeypatch.setattr(writer, "NodeProgress", Progress)
    return tmp_path, messages


def test_node_executes_custom_system_through_real_http_without_guide_or_key_export(node_environment, monkeypatch, caplog):
    directory, messages = node_environment
    monkeypatch.setattr(writer.guides, "text", lambda *_args: pytest.fail("A custom system read/downloaded a guide"))
    with fake_server() as api:
        profile = local_profile(api, secret=profiles.SecretRef("environment", "MINIMAX_NODE_TEST_KEY"))
        monkeypatch.setenv("MINIMAX_NODE_TEST_KEY", SECRET)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion(ANSWER, reasoning_content="reasoning apart from final text")
        schema = writer.MiniMaxH3ApiUniversalWriter.INPUT_TYPES()
        assert schema["required"]["profile_id"][0] == [profile.id]
        assert not {"api_key", "base_url", "headers", "credential", "secret"} & (schema["required"].keys() | schema["optional"].keys())
        stamp = writer.MiniMaxH3ApiUniversalWriter.IS_CHANGED()
        raw_prompt, system = " \n A cat in a window. \n ", "  This is the entire system message. \n"
        outputs = run_node(profile.id, "T2VA", raw_prompt, "16:9", 5, 512,
                                                              system_prompt=system)
        assert outputs[0] == ANSWER and len(outputs) == len(writer.MiniMaxH3ApiUniversalWriter.RETURN_TYPES)
        assert outputs[-4] == "reasoning apart from final text" and not outputs[-3]
        assert SECRET not in repr(outputs) + caplog.text + "".join(messages)
        assert api.calls[0]["body"]["messages"][0]["content"] == system
        assert api.calls[0]["body"]["messages"][1]["content"].endswith("original_prompt:\n" + raw_prompt)
        assert "seed" not in api.calls[0]["body"] and "temperature" not in api.calls[0]["body"]
        config.save_profiles(profiles.ProfileSet((replace(profile, model="new-model"),)), directory)
        assert writer.MiniMaxH3ApiUniversalWriter.IS_CHANGED() != stamp


def test_node_uses_standard_guide_and_its_output_contract(node_environment, monkeypatch):
    directory, _messages = node_environment
    calls = []

    def guide(kind, auto_download, progress):
        calls.append((kind, auto_download))
        return "A dummy writing guide for the HTTP test."

    monkeypatch.setattr(writer.guides, "text", guide)
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion(json.dumps(SECTIONS))
        outputs = run_node(profile.id, "T2VA", "cat", "16:9", 5, 512,
                                                              response_format="json_schema", auto_download_guide=False)
        assert outputs[0] == ANSWER and calls == [("base", False)]
        sent = api.calls[0]["body"]
        assert guide_prompt.GUIDE_OPEN in sent["messages"][0]["content"]
        assert sent["response_format"]["json_schema"]["schema"]["required"] == list(SECTIONS)


def test_missing_or_broken_configuration_does_not_hide_the_node(node_environment):
    directory, _messages = node_environment
    assert writer.MiniMaxH3ApiUniversalWriter.INPUT_TYPES()["required"]["profile_id"][0] == ["configure-a-profile"]
    with pytest.raises(client.ContractError, match="Unknown API profile"):
        run_node("configure-a-profile", "T2VA", "cat", "16:9", 5, 512)
    root = config.user_root(directory)
    root.mkdir()
    (root / config.PROFILES).write_bytes(b"broken json")
    assert writer.MiniMaxH3ApiUniversalWriter.INPUT_TYPES()["required"]["profile_id"][0] == ["configure-a-profile"]


def test_node_rejects_unknown_modes_before_profile_or_guide_access(node_environment):
    with pytest.raises(client.ContractError, match="Unknown H3 task"):
        run_node("x", "unknown", "cat", "16:9", 5, 512)


def test_invalid_job_is_rejected_before_guide_or_credential_access(node_environment, monkeypatch):
    directory, _messages = node_environment
    config.save_profiles(profiles.example_profiles(), directory)
    monkeypatch.setattr(writer.guides, "text", lambda *_args: pytest.fail("Invalid job downloaded a guide"))
    with pytest.raises(client.ContractError, match="output token limit"):
        run_node("deepseek", "T2VA", "cat", "16:9", 5, 100000)


def test_administrator_tool_init_import_status_and_hidden_key_entry(node_environment, monkeypatch, capsys):
    directory, _messages = node_environment
    location = pathlib.Path(__file__).resolve().parent.parent / "tools" / "api_profiles.py"
    spec = importlib.util.spec_from_file_location("minimax_api_admin_test", location)
    admin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admin)
    prefix = ["--user-directory", str(directory)]
    assert admin.main(prefix + ["init"]) == 0
    original = (config.user_root(directory) / config.PROFILES).read_bytes()
    assert admin.main(prefix + ["init"]) == 1
    assert (config.user_root(directory) / config.PROFILES).read_bytes() == original
    assert admin.main(prefix + ["list"]) == 0
    assert SECRET not in capsys.readouterr().out
    monkeypatch.setattr(admin.sys.stdin, "isatty", lambda: False)
    assert admin.main(prefix + ["key-set", "deepseek"]) == 1
    assert not (config.user_root(directory) / config.SECRETS).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Real DPAPI is Windows only")
def test_shipped_cli_sets_and_deletes_dummy_key_without_echo(node_environment, monkeypatch, capsys):
    directory, _messages = node_environment
    admin = importlib.import_module("minimax_h3_rewriter.api_admin")
    prefix = ["--user-directory", str(directory)]
    assert admin.main(prefix + ["init"]) == 0
    monkeypatch.setattr(admin.sys.stdin, "isatty", lambda: True)
    entered = iter((SECRET, SECRET))
    monkeypatch.setattr(admin.getpass, "getpass", lambda _prompt: next(entered))
    assert admin.main(prefix + ["key-set", "deepseek"]) == 0
    profile = config.load_profiles(directory).resolve("deepseek")
    assert secrets.DpapiStore(directory).get(profile) == SECRET
    assert admin.main(prefix + ["list"]) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert admin.main(prefix + ["key-delete", "deepseek"]) == 0
    with pytest.raises(secrets.SecretError, match="no saved API key"):
        secrets.DpapiStore(directory).get(profile)
