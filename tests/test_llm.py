import pytest

from app.llm import LLMError, call_chat_completion, chat_completions_url, extract_chat_content, normalize_chat_messages
from app.schemas import LLMConfig


def test_chat_completions_url_appends_endpoint():
    assert chat_completions_url("https://api.example.com/v1") == "https://api.example.com/v1/chat/completions"


def test_chat_completions_url_keeps_full_endpoint():
    assert (
        chat_completions_url("https://api.example.com/v1/chat/completions")
        == "https://api.example.com/v1/chat/completions"
    )


def test_extract_chat_content():
    payload = {"choices": [{"message": {"content": "存在"}}]}

    assert extract_chat_content(payload) == "存在"


def test_extract_chat_content_rejects_invalid_payload():
    with pytest.raises(LLMError):
        extract_chat_content({"choices": []})


def test_normalize_chat_messages_wraps_string_prompt():
    assert normalize_chat_messages("hello") == [{"role": "user", "content": "hello"}]


def test_normalize_chat_messages_keeps_message_list_order():
    messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "输入"},
        {"role": "assistant", "content": "回复"},
    ]

    assert normalize_chat_messages(messages) == messages


def test_normalize_chat_messages_rejects_invalid_message():
    with pytest.raises(LLMError, match="content"):
        normalize_chat_messages([{"role": "user"}])


@pytest.mark.asyncio
async def test_call_chat_completion_sends_thinking_when_enabled(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.llm.httpx.AsyncClient", FakeAsyncClient)

    result = await call_chat_completion(
        LLMConfig(
            base_url="https://api.example.com/v1",
            model="model-a",
            thinking=True,
        ),
        "hello",
    )

    assert result == "ok"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert "response_format" not in captured["json"]


@pytest.mark.asyncio
async def test_call_chat_completion_sends_json_only_response_format(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"overall_score":1}'}}]}

    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.llm.httpx.AsyncClient", FakeAsyncClient)

    result = await call_chat_completion(
        LLMConfig(
            base_url="https://api.example.com/v1",
            model="model-a",
            force_json_only=True,
        ),
        "hello",
    )

    assert result == '{"overall_score":1}'
    assert captured["json"]["response_format"] == {"type": "json_object"}
