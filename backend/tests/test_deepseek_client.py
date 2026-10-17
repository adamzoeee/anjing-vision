import io
import json
import traceback
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest
from pydantic import ValidationError

from app.config import Settings
from assistant.deepseek_client import (
    DeepSeekInvalidResponse,
    DeepSeekUnavailable,
    request_candidates,
)


KEY = "synthetic-test-credential"


def completion(content='{"proposals": [{"title": "Move desk", "intent": "Clear passage", "edits": []}]}', finish_reason="stop"):
    return json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}]}).encode()


def respond(monkeypatch, body):
    monkeypatch.setattr("assistant.deepseek_client.urlopen", lambda *args, **kwargs: io.BytesIO(body))


def test_requests_json_candidates_with_credentials_only_in_header(monkeypatch):
    captured = {}

    def transport(request, timeout):
        captured.update(request=request, timeout=timeout)
        return io.BytesIO(completion())

    monkeypatch.setattr("assistant.deepseek_client.urlopen", transport)
    result = request_candidates({"room": {"width_m": 4}}, None, api_key=KEY)
    assert result == [{"title": "Move desk", "intent": "Clear passage", "edits": []}]
    request = captured["request"]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert KEY not in request.data.decode()
    assert payload["model"] == "deepseek-flash"
    assert payload["response_format"] == {"type": "json_object"}
    assert 0 < payload["max_tokens"] <= 4096
    assert json.loads(payload["messages"][-1]["content"]) == {"context": {"room": {"width_m": 4}}, "feedback": []}
    assert captured["timeout"] == 20


def test_disables_thinking_for_bounded_json_candidate_request(monkeypatch):
    def transport(request, timeout):
        payload = json.loads(request.data)
        assert payload["thinking"] == {"type": "disabled"}
        return io.BytesIO(completion())

    monkeypatch.setattr("assistant.deepseek_client.urlopen", transport)
    request_candidates({}, None, api_key=KEY)


def test_forwards_room_context_and_allowed_schema_without_adding_data(monkeypatch):
    context = {
        "goal": "Clear the entrance",
        "objects": [{"id": "desk_01", "center": [1.0, 2.0, 0.4]}],
        "allowed_edits": {"move": ["target", "dx", "dy"]},
        "risk_codes": ["passage_narrow"],
    }

    def transport(request, timeout):
        payload = json.loads(request.data)
        assert json.loads(payload["messages"][-1]["content"]) == {
            "context": {
                "goal": "Clear the entrance",
                "objects": [{"id": "desk_01", "center": [1.0, 2.0, 0.4]}],
                "allowed_edits": {"move": ["target", "dx", "dy"]},
                "risk_codes": ["passage_narrow"],
            },
            "feedback": [],
        }
        return io.BytesIO(completion())

    monkeypatch.setattr("assistant.deepseek_client.urlopen", transport)
    request_candidates(context, None, api_key=KEY)
    assert context["objects"] == [{"id": "desk_01", "center": [1.0, 2.0, 0.4]}]


def test_prompt_directs_exact_distinct_verified_starter_selection(monkeypatch):
    context = {"verified_starters": [
        {"edits": [{"op": "move", "target": "desk_01", "dx": 0, "dy": -0.6}], "risk_codes": ["access"]},
        {"edits": [{"op": "resize_door", "width_m": 1.0}], "risk_codes": ["door"]},
    ]}

    def transport(request, timeout):
        payload = json.loads(request.data)
        system = payload["messages"][0]["content"].lower()
        assert "verified_starters" in system
        assert "exactly" in system
        assert "distinct" in system
        assert "do not combine" in system
        assert json.loads(payload["messages"][1]["content"])["context"] == context
        return io.BytesIO(completion())

    monkeypatch.setattr("assistant.deepseek_client.urlopen", transport)
    request_candidates(context, None, api_key=KEY)


def test_passes_revision_feedback_and_connection_overrides(monkeypatch):
    def transport(request, timeout):
        payload = json.loads(request.data)
        assert payload["model"] == "custom-model"
        assert payload["max_tokens"] == 1024
        assert request.full_url == "https://example.test/completions"
        assert timeout == 3
        assert json.loads(payload["messages"][-1]["content"])["feedback"] == [{"reason": "overlap"}]
        return io.BytesIO(completion('{"proposals": []}'))

    monkeypatch.setattr("assistant.deepseek_client.urlopen", transport)
    assert request_candidates({}, [{"reason": "overlap"}], api_key=KEY, model="custom-model", endpoint="https://example.test/completions", timeout_s=3, max_tokens=1024) == []


@pytest.mark.parametrize("body", [
    b"", b"{", completion(""), completion("{"), completion(f"not json {KEY}"),
    completion(finish_reason="length"), completion(finish_reason="content_filter"),
    completion('{"proposals": {}}'), completion('{"proposals": [1]}'),
    completion('[]'), completion('{}'), b'{}', b'{"choices": []}',
    b'{"choices": [null]}', b'\xff',
])
def test_rejects_incomplete_or_malformed_responses_without_credentials(monkeypatch, body):
    respond(monkeypatch, body)
    with pytest.raises(DeepSeekInvalidResponse) as caught:
        request_candidates({}, None, api_key=KEY)
    assert KEY not in "".join(traceback.format_exception(caught.value))


def test_bounds_response_read(monkeypatch):
    class LargeResponse(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024 + 1
            return b" " * size

    monkeypatch.setattr("assistant.deepseek_client.urlopen", lambda *a, **k: LargeResponse())
    with pytest.raises(DeepSeekInvalidResponse):
        request_candidates({}, None, api_key=KEY)


def test_interrupted_response_read_is_controlled_and_hides_partial_content(monkeypatch):
    class InterruptedResponse(io.BytesIO):
        def read(self, size=-1):
            raise IncompleteRead(KEY.encode(), 100)

    monkeypatch.setattr("assistant.deepseek_client.urlopen", lambda *a, **k: InterruptedResponse())
    with pytest.raises(DeepSeekUnavailable) as caught:
        request_candidates({}, None, api_key=KEY)
    assert KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("failure", [
    HTTPError("https://example.test", 401, KEY, {}, io.BytesIO(KEY.encode())),
    HTTPError("https://example.test", 429, KEY, {}, io.BytesIO(KEY.encode())),
    TimeoutError(KEY), URLError(KEY), OSError(KEY),
])
def test_transport_failures_are_controlled_and_hide_credentials(monkeypatch, failure):
    def transport(*args, **kwargs):
        raise failure

    monkeypatch.setattr("assistant.deepseek_client.urlopen", transport)
    with pytest.raises(DeepSeekUnavailable) as caught:
        request_candidates({}, None, api_key=KEY)
    assert KEY not in "".join(traceback.format_exception(caught.value))


def test_settings_load_deepseek_environment_without_exposing_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setenv("DEEPSEEK_MODEL", "custom-model")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_S", "7.5")
    monkeypatch.setenv("DEEPSEEK_ENDPOINT", "https://example.test/v1/chat/completions")
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "1024")
    settings = Settings(_env_file=None)
    assert settings.deepseek_api_key == KEY
    assert settings.deepseek_model == "custom-model"
    assert settings.deepseek_timeout_s == 7.5
    assert settings.deepseek_endpoint == "https://example.test/v1/chat/completions"
    assert settings.deepseek_max_tokens == 1024
    assert KEY not in repr(settings)
    assert "deepseek_api_key" not in settings.model_dump()


def test_settings_allow_unconfigured_client_and_reject_invalid_timeout(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_TIMEOUT_S", raising=False)
    monkeypatch.delenv("DEEPSEEK_ENDPOINT", raising=False)
    monkeypatch.delenv("DEEPSEEK_MAX_TOKENS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.deepseek_api_key is None
    assert settings.deepseek_model == "deepseek-flash"
    assert settings.deepseek_timeout_s == 20
    assert settings.deepseek_endpoint == "https://api.deepseek.com/chat/completions"
    assert settings.deepseek_max_tokens == 2048
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_timeout_s=0)


@pytest.mark.parametrize("endpoint", [
    "http://example.test/chat", "https://user:secret@example.test/chat",
    "https://example.test/chat?key=secret", "https://example.test/chat#fragment",
    "https://example.test:99999/chat", "https:///chat", "",
])
def test_settings_reject_insecure_or_malformed_deepseek_endpoint(endpoint):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_endpoint=endpoint)


@pytest.mark.parametrize("max_tokens", [0, 127, 4097, 1.5])
def test_settings_reject_deepseek_token_cap_outside_bounded_integer_range(max_tokens):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_max_tokens=max_tokens)
