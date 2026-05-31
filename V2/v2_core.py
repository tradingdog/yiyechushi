from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import APITimeoutError, OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from image_generator import (
    build_text_client as v1_build_text_client,
    generate_auto_dish_idea as v1_generate_auto_dish_idea,
)


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "config.env"
IDEA_FILE = ROOT_DIR / "dish_name.txt"
REFERENCE_FILE = ROOT_DIR / "cankao.txt"
OUTPUT_DIR = ROOT_DIR / "output"

DEFAULT_DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-0-mini-260428"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "low"
DEFAULT_IMAGE_COUNT = 1

_RUNTIME_CONFIG_LOADED = False
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{变量(?:[：:,，][^{}]*)?\}")


def strip_inline_env_comment(raw_value: str) -> str:
    result_chars: list[str] = []
    quote_char = ""
    for char in raw_value:
        if quote_char:
            if char == quote_char:
                quote_char = ""
            result_chars.append(char)
            continue
        if char in {'"', "'"}:
            quote_char = char
            result_chars.append(char)
            continue
        if char == "#":
            break
        result_chars.append(char)
    return "".join(result_chars).strip()


def parse_env_file(env_file: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not env_file.exists():
        return parsed

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_inline_env_comment(value).strip().strip('"').strip("'")
        if key:
            parsed[key] = value
    return parsed


def ensure_runtime_config_loaded() -> None:
    global _RUNTIME_CONFIG_LOADED
    if _RUNTIME_CONFIG_LOADED:
        return

    existing_keys = set(os.environ.keys())
    merged_values: dict[str, str] = {}
    merged_values.update(parse_env_file(CONFIG_FILE))
    merged_values.update(parse_env_file(ROOT_DIR.parent / ".env"))

    for key, value in merged_values.items():
        if key not in existing_keys:
            os.environ[key] = value

    _RUNTIME_CONFIG_LOADED = True


def parse_bool_env(env_name: str, default: bool) -> bool:
    raw_value = os.getenv(env_name, "1" if default else "0").strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{env_name} 只支持 1/0/true/false/on/off。")


def build_httpx_client(timeout_seconds: float) -> httpx.Client:
    # 与 V1 行为保持一致：默认继承系统代理/证书环境。
    trust_env = parse_bool_env("HTTP_TRUST_ENV", default=True)
    return httpx.Client(timeout=timeout_seconds, trust_env=trust_env)


def parse_float_env(env_name: str, default: float) -> float:
    raw_value = os.getenv(env_name, str(default)).strip() or str(default)
    try:
        return float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{env_name} 必须是数字。") from exc


def parse_int_env(env_name: str, default: int) -> int:
    raw_value = os.getenv(env_name, str(default)).strip() or str(default)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{env_name} 必须是整数。") from exc
    if value < 1:
        raise RuntimeError(f"{env_name} 必须大于等于 1。")
    return value


def iter_exception_chain(exc: BaseException) -> list[BaseException]:
    pending: list[BaseException] = [exc]
    chain: list[BaseException] = []
    visited: set[int] = set()

    while pending:
        current = pending.pop(0)
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)
        chain.append(current)

        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)

    return chain


def is_timeout_error(exc: Exception) -> bool:
    for error in iter_exception_chain(exc):
        if isinstance(error, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
            return True

        message = str(error).lower()
        if "timed out" in message or "timeout" in message:
            return True
    return False


def get_text_temperature() -> float:
    # 兼容旧配置 DOUBAO_TEXT_TEMPERATURE，同时支持统一的 MODEL_TEMPERATURE。
    raw_value = os.getenv("MODEL_TEMPERATURE", "").strip()
    if not raw_value:
        raw_value = os.getenv("DOUBAO_TEXT_TEMPERATURE", "0.3").strip() or "0.3"
    try:
        return float(raw_value)
    except ValueError as exc:
        raise RuntimeError("MODEL_TEMPERATURE 必须是数字。") from exc


def parse_image_size(size_text: str) -> str:
    normalized = size_text.strip().lower()
    if re.fullmatch(r"\d+x\d+", normalized) is None:
        raise RuntimeError("OPENAI_IMAGE_SIZE 必须是 宽x高，例如 1024x1536。")
    return normalized


def sanitize_file_name(name: str) -> str:
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return sanitized or "新菜"


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_chat_text_output(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    text_parts: list[str] = []
    for item in content or []:
        text = getattr(item, "text", None) or item.get("text") or ""
        if text:
            text_parts.append(text)
    return "\n".join(text_parts).strip()


def build_doubao_client() -> OpenAI:
    ensure_runtime_config_loaded()
    api_key = os.getenv("DOUBAO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 DOUBAO_API_KEY，请在根目录 .env 中配置。")
    base_url = os.getenv("DOUBAO_BASE_URL", DEFAULT_DOUBAO_BASE_URL).strip() or DEFAULT_DOUBAO_BASE_URL
    timeout = parse_float_env("TEXT_REQUEST_TIMEOUT_SECONDS", 120.0)
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        http_client=build_httpx_client(timeout_seconds=timeout),
    )


def build_openai_image_client() -> OpenAI:
    ensure_runtime_config_loaded()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY，请在根目录 .env 中配置。")
    timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout,
        "base_url": base_url,
    }
    return OpenAI(**client_kwargs)


def load_manual_dish_idea(idea_file: Path = IDEA_FILE) -> dict[str, str]:
    if not idea_file.exists():
        raise FileNotFoundError(f"未找到手动菜名文件：{idea_file}")
    lines = [line.strip() for line in idea_file.read_text(encoding="utf-8").splitlines()]
    non_empty_lines = [line for line in lines if line]
    if not non_empty_lines:
        raise ValueError(f"手动菜名文件为空：{idea_file}")

    dish_name = non_empty_lines[0]
    notes = "\n".join(non_empty_lines[1:]).strip()
    return {"dish_name": dish_name, "notes": notes}


def auto_generate_dish_idea(client: OpenAI) -> dict[str, str]:
    del client

    # 对齐 V1 自动造菜配置，默认把记忆文件落在 V2 目录下。
    if not os.getenv("AUTO_DISH_MEMORY_FILE", "").strip():
        os.environ["AUTO_DISH_MEMORY_FILE"] = "V2/dish_idea_memory.jsonl"
    if not os.getenv("AUTO_DISH_LIBRARY_FILE", "").strip():
        os.environ["AUTO_DISH_LIBRARY_FILE"] = "chuantongcaipu.txt"
    if not os.getenv("AUTO_DISH_CUISINE_MODE", "").strip():
        os.environ["AUTO_DISH_CUISINE_MODE"] = "1"

    v1_client = v1_build_text_client()
    try:
        payload = v1_generate_auto_dish_idea(idea_file=IDEA_FILE, client=v1_client)
    finally:
        close_method = getattr(v1_client, "close", None)
        if callable(close_method):
            close_method()

    return {
        "dish_name": payload["dish_idea"],
        "notes": payload.get("notes", ""),
        "region_code": payload.get("region_code", ""),
        "region_label": payload.get("region_label", ""),
        "reference_dish": payload.get("reference_dish", ""),
        "memory_file": payload.get("memory_file", ""),
        "library_file": payload.get("library_file", ""),
        "generation_model": payload.get("generation_model", ""),
    }


def write_dish_idea_file(dish_name: str, notes: str, idea_file: Path = IDEA_FILE) -> None:
    text = dish_name.strip()
    notes = notes.strip()
    if notes:
        text = f"{text}\n{notes}"
    idea_file.write_text(text + "\n", encoding="utf-8")


def load_cankao_template(template_file: Path = REFERENCE_FILE) -> str:
    if not template_file.exists():
        raise FileNotFoundError(f"未找到参考模板：{template_file}")
    template = template_file.read_text(encoding="utf-8").strip()
    if not template:
        raise ValueError(f"参考模板为空：{template_file}")
    return template


def collect_template_placeholders(template_text: str) -> list[str]:
    placeholders: list[str] = []
    for match in TEMPLATE_PLACEHOLDER_PATTERN.finditer(template_text):
        placeholders.append(match.group(0))
    return placeholders


def extract_json_object_from_text(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("模型返回中未找到 JSON 对象。")
    return json.loads(match.group(0))


def render_template_by_replacements(template_text: str, replacements: list[str]) -> str:
    replaced_count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal replaced_count
        if replaced_count >= len(replacements):
            raise ValueError("变量替换数量不足，无法覆盖模板中的所有变量位。")
        value = str(replacements[replaced_count]).strip()
        replaced_count += 1
        if not value:
            raise ValueError("变量替换值不能为空。")
        return value

    rendered = TEMPLATE_PLACEHOLDER_PATTERN.sub(_replace, template_text)
    if replaced_count != len(replacements):
        raise ValueError("变量替换数量超出模板需求。")
    if TEMPLATE_PLACEHOLDER_PATTERN.search(rendered):
        raise ValueError("模板仍存在未替换的变量占位符。")
    return rendered.strip()


def generate_doubao_prompt_by_template(
    client: OpenAI,
    dish_name: str,
    notes: str,
    template_text: str,
) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    placeholders = collect_template_placeholders(template_text)
    if not placeholders:
        return {"model": model, "prompt": template_text.strip()}

    system_prompt = """
你是菜谱视觉策划总监。你的任务是只为模板里的变量位提供替换值。
强制要求：
1) 你不能改写模板任何固定文本，只输出变量替换值。
2) 你必须按“变量位从上到下顺序”给出 replacement 数组。
3) replacement 数组长度必须与变量位数量完全一致。
4) 输出 JSON：{"replacements":["值1","值2",...]}，不要输出其它内容。
""".strip()

    placeholder_lines = "\n".join(f"{index + 1}. {placeholder}" for index, placeholder in enumerate(placeholders))
    user_prompt = f"""
菜名：{dish_name}
补充说明：{notes or "无"}

模板如下：
{template_text}

变量位清单（按顺序）：
{placeholder_lines}

请只返回 replacements JSON。
""".strip()

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 3)
    response = None
    last_error: Exception | None = None
    for _ in range(max_retry):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1800,
                temperature=temperature,
            )
            break
        except Exception as exc:
            last_error = exc
    if response is None:
        raise RuntimeError(f"豆包模板改写失败：{last_error}")

    raw_text = extract_chat_text_output(response).strip()
    if not raw_text:
        raise ValueError("豆包未返回有效内容。")
    payload = extract_json_object_from_text(raw_text)
    replacements_raw = payload.get("replacements")
    if not isinstance(replacements_raw, list):
        raise ValueError("豆包返回 JSON 缺少 replacements 数组。")
    replacements = [str(item).strip() for item in replacements_raw]
    if len(replacements) != len(placeholders):
        raise ValueError(f"变量替换数量不匹配：需要 {len(placeholders)} 个，实际 {len(replacements)} 个。")
    prompt_text = render_template_by_replacements(template_text=template_text, replacements=replacements)
    return {"model": model, "prompt": prompt_text}


def render_prompt_fallback(template_text: str, dish_name: str, notes: str) -> str:
    notes_text = notes.strip() or f"{dish_name}，突出家常真实出锅状态。"
    replacements = [
        "暖棕米白家常配色",
        dish_name,
        "要配上一碗热米饭，主菜覆盖部分米饭提升食欲",
        "使用最适合这道菜的餐具与主菜互动，动作自然像正在吃",
        "鲜香微辣酱香",
        notes_text,
        "整张图像必须真实手机实拍风，禁止品牌名与logo",
    ]

    placeholders = collect_template_placeholders(template_text)
    if not placeholders:
        return template_text.strip()

    fill_values = replacements[:]
    if len(fill_values) < len(placeholders):
        fill_values.extend([dish_name] * (len(placeholders) - len(fill_values)))
    return render_template_by_replacements(template_text=template_text, replacements=fill_values[: len(placeholders)])


def get_image_settings() -> dict[str, Any]:
    ensure_runtime_config_loaded()
    return {
        "model": os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL,
        "size": parse_image_size(os.getenv("OPENAI_IMAGE_SIZE", DEFAULT_IMAGE_SIZE).strip() or DEFAULT_IMAGE_SIZE),
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", DEFAULT_IMAGE_QUALITY).strip() or DEFAULT_IMAGE_QUALITY,
        "image_count": parse_int_env("OPENAI_IMAGE_COUNT", DEFAULT_IMAGE_COUNT),
    }


def build_run_output_dir(timestamp: str, dish_name: str) -> Path:
    run_dir = OUTPUT_DIR / f"{timestamp}_{sanitize_file_name(dish_name)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_text_output(content: str, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content.strip() + "\n", encoding="utf-8")


def extract_image_items(response: Any) -> list[dict[str, str]]:
    payload = response.model_dump() if hasattr(response, "model_dump") else {"data": getattr(response, "data", [])}
    image_items: list[dict[str, str]] = []
    for item in payload.get("data", []):
        if isinstance(item, dict):
            image_base64 = item.get("b64_json") or item.get("result") or ""
            revised_prompt = item.get("revised_prompt") or ""
        else:
            image_base64 = getattr(item, "b64_json", None) or getattr(item, "result", None) or ""
            revised_prompt = getattr(item, "revised_prompt", None) or ""
        if image_base64:
            image_items.append({"image_base64": image_base64, "revised_prompt": revised_prompt})
    return image_items


def generate_images_by_prompt(client: OpenAI, prompt_text: str, settings: dict[str, Any]) -> list[dict[str, str]]:
    max_retry = parse_int_env("IMAGE_REQUEST_RETRY_COUNT", 2)
    request_timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)
    response = None
    for attempt in range(1, max_retry + 1):
        try:
            response = client.images.generate(
                model=settings["model"],
                prompt=prompt_text,
                size=settings["size"],
                quality=settings["quality"],
                n=settings["image_count"],
                timeout=request_timeout,
            )
            break
        except Exception as exc:
            if attempt >= max_retry or not is_timeout_error(exc):
                raise RuntimeError(f"生图失败：{exc}") from exc
            print(f"生图请求超时，正在重试第 {attempt + 1}/{max_retry} 次...")
    if response is None:
        raise RuntimeError("生图失败：接口未返回有效响应。")

    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("图片接口未返回有效图片数据。")
    return image_items


def save_generated_images(
    image_items: list[dict[str, str]],
    output_dir: Path,
    timestamp: str,
    dish_name: str,
) -> list[str]:
    safe_name = sanitize_file_name(dish_name)
    saved_files: list[str] = []
    for index, item in enumerate(image_items, start=1):
        image_file = output_dir / f"{timestamp}_{safe_name}_{index:02d}.png"
        image_file.write_bytes(base64.b64decode(item["image_base64"]))
        saved_files.append(str(image_file))
        revised_prompt = item["revised_prompt"].strip()
        if revised_prompt:
            revised_file = output_dir / f"{timestamp}_{safe_name}_{index:02d}_revised_prompt.txt"
            save_text_output(revised_prompt, revised_file)
    return saved_files
