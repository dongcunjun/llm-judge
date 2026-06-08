from __future__ import annotations

from typing import Any

import httpx

from app.schemas import LLMConfig


class LLMError(RuntimeError):
    """Raised when an LLM request fails or returns an unexpected payload."""


def chat_completions_url(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if not clean:
        raise LLMError("LLM base_url 不能为空")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def extract_chat_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("LLM 响应缺少 choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMError("LLM 响应 choices[0] 格式错误")

    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(first.get("text"), str):
        return first["text"]
    raise LLMError("LLM 响应缺少 message.content")


def normalize_chat_messages(prompt_or_messages: str | list[dict[str, Any]]) -> list[dict[str, str]]:
    if isinstance(prompt_or_messages, str):
        return [{"role": "user", "content": prompt_or_messages}]
    if not isinstance(prompt_or_messages, list) or not prompt_or_messages:
        raise LLMError("LLM messages 必须是非空数组")

    messages: list[dict[str, str]] = []
    for index, message in enumerate(prompt_or_messages, start=1):
        if not isinstance(message, dict):
            raise LLMError(f"LLM messages 第 {index} 项必须是对象")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not role.strip():
            raise LLMError(f"LLM messages 第 {index} 项 role 必须是非空字符串")
        if not isinstance(content, str):
            raise LLMError(f"LLM messages 第 {index} 项 content 必须是字符串")
        messages.append({"role": role.strip(), "content": content})
    return messages


async def call_chat_completion(config: LLMConfig, prompt: str | list[dict[str, Any]]) -> str:
    if not config.model.strip():
        raise LLMError("LLM model 不能为空")

    headers = {"Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = f"Bearer {config.api_key.strip()}"

    request_payload: dict[str, Any] = {
        "model": config.model.strip(),
        "messages": normalize_chat_messages(prompt),
    }
    if config.temperature is not None:
        request_payload["temperature"] = config.temperature
    if config.top_k is not None:
        request_payload["top_k"] = config.top_k
    if config.top_p is not None:
        request_payload["top_p"] = config.top_p
    if config.thinking:
        request_payload["thinking"] = {"type": "enabled"}
    if config.force_json_only:
        request_payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=config.timeout) as client:
            response = await client.post(
                chat_completions_url(config.base_url),
                headers=headers,
                json=request_payload,
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise LLMError(f"LLM HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM 请求失败: {exc}") from exc
    except ValueError as exc:
        raise LLMError("LLM 响应不是合法 JSON") from exc

    return extract_chat_content(payload).strip()
