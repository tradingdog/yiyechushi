from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from openai import OpenAI

TEXT_PROVIDER_DOUBAO = "doubao"
TEXT_PROVIDER_CURSOR = "cursor"
DEFAULT_TEXT_PROVIDER = TEXT_PROVIDER_CURSOR
DEFAULT_CURSOR_TEXT_MODEL = "default"
DEFAULT_DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-0-lite-260428"
CURSOR_API_BASE = "https://api.cursor.com"
CURSOR_MODEL_FALLBACKS = ("gpt-latest", "default")

_DATA_URL_PATTERN = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)


def normalize_text_provider(value: str | None = None) -> str:
    raw = str(value or os.getenv("TEXT_PROVIDER", DEFAULT_TEXT_PROVIDER)).strip().lower()
    if raw in {TEXT_PROVIDER_CURSOR, "auto"}:
        return TEXT_PROVIDER_CURSOR
    if raw in {TEXT_PROVIDER_DOUBAO, "doubao-seed", "seed"}:
        return TEXT_PROVIDER_DOUBAO
    return DEFAULT_TEXT_PROVIDER


def resolve_text_model(provider: str | None = None) -> str:
    resolved = normalize_text_provider(provider)
    if resolved == TEXT_PROVIDER_CURSOR:
        return (
            os.getenv("CURSOR_TEXT_MODEL", DEFAULT_CURSOR_TEXT_MODEL).strip()
            or DEFAULT_CURSOR_TEXT_MODEL
        )
    return (
        os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip()
        or DEFAULT_DOUBAO_TEXT_MODEL
    )


def format_text_provider_label(provider: str | None = None) -> str:
    resolved = normalize_text_provider(provider)
    model = resolve_text_model(resolved)
    if resolved == TEXT_PROVIDER_CURSOR:
        return f"Cursor（{model}）"
    return f"豆包（{model}）"


def format_text_runtime_label(provider: str | None = None) -> str:
    from v2_core import ensure_runtime_config_loaded

    ensure_runtime_config_loaded()
    resolved = normalize_text_provider(provider)
    if resolved == TEXT_PROVIDER_CURSOR:
        key = os.getenv("CURSOR_API_KEY", "").strip()
        key_hint = f"{key[:10]}..." if len(key) > 10 else "(未配置)"
        return f"文本环境：Cursor model={resolve_text_model(resolved)}，key={key_hint}"
    key = os.getenv("DOUBAO_API_KEY", "").strip()
    key_hint = f"{key[:10]}..." if len(key) > 10 else "(未配置)"
    base_url = os.getenv("DOUBAO_BASE_URL", DEFAULT_DOUBAO_BASE_URL).strip() or DEFAULT_DOUBAO_BASE_URL
    return (
        f"文本环境：豆包 base_url={base_url}，model={resolve_text_model(resolved)}，key={key_hint}"
    )


def build_doubao_openai_client() -> OpenAI:
    from v2_core import build_httpx_client, ensure_runtime_config_loaded, parse_bool_env, parse_float_env

    ensure_runtime_config_loaded()
    api_key = os.getenv("DOUBAO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 DOUBAO_API_KEY，请在根目录 .env 中配置。")
    base_url = os.getenv("DOUBAO_BASE_URL", DEFAULT_DOUBAO_BASE_URL).strip() or DEFAULT_DOUBAO_BASE_URL
    timeout = parse_float_env("TEXT_REQUEST_TIMEOUT_SECONDS", 120.0)
    trust_env = parse_bool_env("DOUBAO_HTTP_TRUST_ENV", default=False)
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        http_client=build_httpx_client(timeout_seconds=timeout, trust_env=trust_env),
    )


@dataclass
class _SimpleMessage:
    content: str


@dataclass
class _SimpleChoice:
    message: _SimpleMessage


@dataclass
class _SimpleCompletion:
    choices: list[_SimpleChoice]


class _ChatCompletions:
    def __init__(self, owner: "TextCompletionClient") -> None:
        self._owner = owner

    def create(
        self,
        *,
        model: str | None = None,
        messages: list[Mapping[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        **kwargs: Any,
    ) -> _SimpleCompletion:
        del kwargs
        resolved_model = str(model or self._owner.model).strip() or self._owner.model
        if self._owner.provider == TEXT_PROVIDER_CURSOR:
            text = self._owner._cursor_complete(
                messages or [],
                model=resolved_model,
                temperature=temperature,
            )
            return _SimpleCompletion(choices=[_SimpleChoice(message=_SimpleMessage(content=text))])
        response = self._owner._doubao_client.chat.completions.create(
            model=resolved_model,
            messages=list(messages or []),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = ""
        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            content = str(getattr(message, "content", "") or "").strip()
        return _SimpleCompletion(choices=[_SimpleChoice(message=_SimpleMessage(content=content))])


class _Chat:
    def __init__(self, owner: "TextCompletionClient") -> None:
        self.completions = _ChatCompletions(owner)


class TextCompletionClient:
    """统一文本调用入口，兼容现有 `client.chat.completions.create(...)` 写法。"""

    def __init__(self, provider: str | None = None) -> None:
        from v2_core import ensure_runtime_config_loaded

        ensure_runtime_config_loaded()
        self.provider = normalize_text_provider(provider)
        self.model = resolve_text_model(self.provider)
        self.chat = _Chat(self)
        self._doubao_client: OpenAI | None = None
        if self.provider == TEXT_PROVIDER_DOUBAO:
            self._doubao_client = build_doubao_openai_client()

    def close(self) -> None:
        if self._doubao_client is not None:
            close_fn = getattr(self._doubao_client, "close", None)
            if callable(close_fn):
                close_fn()
            self._doubao_client = None

    def _cursor_api_key(self) -> str:
        key = os.getenv("CURSOR_API_KEY", "").strip()
        if not key:
            raise RuntimeError("未找到 CURSOR_API_KEY，请在根目录 .env 中配置。")
        return key

    def _resolve_cursor_model_id(self, client: httpx.Client, key: str, alias: str) -> str:
        response = client.get(f"{CURSOR_API_BASE}/v1/models", auth=(key, ""))
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("items", []):
            model_id = str(item.get("id", "")).strip()
            aliases = [str(x).strip() for x in item.get("aliases", [])]
            if alias == model_id or alias in aliases:
                return model_id
        return alias

    def _messages_to_cursor_prompt(
        self,
        messages: list[Mapping[str, Any]],
    ) -> tuple[str, list[dict[str, str]]]:
        parts: list[str] = []
        images: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role", "user")).strip() or "user"
            content = message.get("content", "")
            if isinstance(content, str):
                if content.strip():
                    parts.append(f"[{role}]\n{content.strip()}")
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type", "")).strip()
                if block_type == "text":
                    text = str(block.get("text", "")).strip()
                    if text:
                        parts.append(text)
                elif block_type == "image_url":
                    url = str((block.get("image_url") or {}).get("url", "")).strip()
                    parsed = _DATA_URL_PATTERN.match(url)
                    if not parsed:
                        continue
                    mime_type, encoded = parsed.group(1), parsed.group(2)
                    images.append({"mimeType": mime_type, "data": encoded})
                    if len(images) >= 5:
                        break
        prompt_text = "\n\n".join(part for part in parts if part).strip()
        if not prompt_text:
            raise ValueError("Cursor 文本请求缺少 prompt 内容。")
        return prompt_text, images

    def _cursor_complete(
        self,
        messages: list[Mapping[str, Any]],
        *,
        model: str,
        temperature: float,
    ) -> str:
        del temperature
        from v2_core import parse_float_env

        key = self._cursor_api_key()
        prompt_text, images = self._messages_to_cursor_prompt(messages)
        timeout = parse_float_env("CURSOR_TEXT_TIMEOUT_SECONDS", 600.0)
        poll_seconds = parse_float_env("CURSOR_TEXT_POLL_SECONDS", 3.0)
        max_polls = max(1, int(timeout / max(poll_seconds, 0.5)))
        model_candidates = [model, *CURSOR_MODEL_FALLBACKS]
        seen_models: set[str] = set()
        last_error = ""

        with httpx.Client(timeout=timeout) as client:
            for candidate in model_candidates:
                candidate = candidate.strip()
                if not candidate or candidate in seen_models:
                    continue
                seen_models.add(candidate)
                model_id = self._resolve_cursor_model_id(client, key, candidate)
                body: dict[str, Any] = {
                    "prompt": {"text": prompt_text},
                    "model": {"id": model_id},
                    "name": "v2-text-generation",
                }
                if images:
                    body["prompt"]["images"] = images
                try:
                    response = client.post(f"{CURSOR_API_BASE}/v1/agents", auth=(key, ""), json=body)
                    if response.status_code not in (200, 201):
                        last_error = response.text[:500]
                        if response.status_code == 400 and "invalid_model" in response.text:
                            continue
                        raise RuntimeError(
                            f"Cursor 创建 Agent 失败 {response.status_code}: {response.text[:500]}"
                        )
                    data = response.json()
                    agent_id = data["agent"]["id"]
                    run_id = data["run"]["id"]
                    for _ in range(max_polls):
                        run_response = client.get(
                            f"{CURSOR_API_BASE}/v1/agents/{agent_id}/runs/{run_id}",
                            auth=(key, ""),
                        )
                        run_response.raise_for_status()
                        run = run_response.json()
                        status = str(run.get("status", ""))
                        if status == "FINISHED":
                            result = str(run.get("result", "")).strip()
                            if result:
                                return result
                            raise RuntimeError("Cursor 返回空结果。")
                        if status in {"ERROR", "CANCELLED", "EXPIRED"}:
                            raise RuntimeError(f"Cursor 运行失败：status={status}")
                        time.sleep(poll_seconds)
                    raise TimeoutError(f"Cursor 等待结果超时：agent={agent_id} run={run_id}")
                except Exception as exc:
                    last_error = str(exc)
                    if "invalid_model" in last_error or "not supported in your region" in last_error:
                        continue
                    raise
        raise RuntimeError(f"Cursor 文本生成失败：{last_error or '未知错误'}")


def build_text_client(provider: str | None = None) -> TextCompletionClient:
    return TextCompletionClient(provider)
