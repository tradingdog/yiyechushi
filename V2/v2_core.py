from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "config.env"
IDEA_FILE = ROOT_DIR / "dish_name.txt"
REFERENCE_FILE = ROOT_DIR / "cankao.txt"
OUTPUT_DIR = ROOT_DIR / "output"

DEFAULT_DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-0-mini-260428"
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
    trust_env = parse_bool_env("HTTP_TRUST_ENV", default=False)
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
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout,
        "http_client": build_httpx_client(timeout_seconds=timeout),
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    return OpenAI(
        **client_kwargs,
    )


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
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    max_retry = parse_int_env("AUTO_DISH_RETRY_COUNT", 3)

    system_prompt = (
        "你是专业菜品研发主厨。你只输出 JSON，不要输出任何额外解释。"
        '输出格式固定为 {"dish_name":"...","notes":"..."}。'
    )
    user_prompt = """
请自动生成一个适合中国家庭厨房、可真实落地的爆款新菜。
要求：
1) dish_name 是自然口语化菜名，6~12 个中文字符，不要花哨词。
2) notes 是 1 段不超过 120 字的补充说明，包含主口味和关键做法。
3) 不要出现品牌名。
""".strip()

    last_error: Exception | None = None
    for _ in range(max_retry):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=500,
                temperature=temperature,
            )
            raw_text = extract_chat_text_output(response)
            payload = json.loads(raw_text)
            dish_name = str(payload.get("dish_name", "")).strip()
            notes = str(payload.get("notes", "")).strip()
            if not dish_name:
                raise ValueError("dish_name 为空。")
            return {"dish_name": dish_name, "notes": notes}
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"自动造菜失败：{last_error}")


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


def generate_doubao_prompt_by_template(
    client: OpenAI,
    dish_name: str,
    notes: str,
    template_text: str,
) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()

    system_prompt = """
你是菜谱视觉策划总监。你的任务是把用户给的模板改写成可直接喂给 gpt-image-2 的高质量中文生图提示词。
强制要求：
1) 必须基于模板完整改写，不要丢字段。
2) 把模板中出现的 {变量} 都替换成贴合当前菜名的具体内容，不允许保留任何花括号占位符。
3) 输出纯文本提示词，不要 Markdown，不要解释。
4) 必须体现真实家庭餐桌场景、真实拍摄质感、食欲感和可执行步骤。
""".strip()

    user_prompt = f"""
菜名：{dish_name}
补充说明：{notes or "无"}

模板如下：
{template_text}

请输出最终可直接给 gpt-image-2 的完整提示词。
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

    prompt_text = extract_chat_text_output(response).strip()
    if not prompt_text:
        raise ValueError("豆包未返回有效提示词。")
    if TEMPLATE_PLACEHOLDER_PATTERN.search(prompt_text):
        raise ValueError("豆包提示词仍包含未替换占位符。")
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

    rendered = template_text
    for replacement in replacements:
        rendered = TEMPLATE_PLACEHOLDER_PATTERN.sub(replacement, rendered, count=1)
    rendered = TEMPLATE_PLACEHOLDER_PATTERN.sub(dish_name, rendered)
    return rendered


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
    response = None
    last_error: Exception | None = None
    for _ in range(max_retry):
        try:
            response = client.images.generate(
                model=settings["model"],
                prompt=prompt_text,
                size=settings["size"],
                quality=settings["quality"],
                n=settings["image_count"],
            )
            break
        except Exception as exc:
            last_error = exc
    if response is None:
        raise RuntimeError(f"生图失败：{last_error}")

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
