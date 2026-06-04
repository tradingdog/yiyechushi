from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
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
COVER_TEMPLATE_FILE = ROOT_DIR / "cover_promtp_cankao.txt"
CANKAO_DIR = ROOT_DIR / "cankao"
HAIBAO_TEMPLATE_FILE = CANKAO_DIR / "haibao.txt"
XIJIETU_TEMPLATE_FILE = CANKAO_DIR / "xijietu.txt"
CAIPU_TEMPLATE_FILE = CANKAO_DIR / "caipu.txt"
FENGMIAN_TEMPLATE_FILE = CANKAO_DIR / "fengmian.txt"
CHARACTER_REFERENCE_FILE = CANKAO_DIR / "juese.png"
OUTPUT_DIR = ROOT_DIR / "output"

DEFAULT_DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-0-mini-260428"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "low"
DEFAULT_IMAGE_COUNT = 1
DEFAULT_COVER_IMAGE_COUNT = 1
DEFAULT_CONTENT_TRACK = "电饭煲一锅出"

_RUNTIME_CONFIG_LOADED = False
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{变量(?:[：:,，][^{}]*)?\}")
CANKAO_PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
MODE2_GROUP_KEYS = ("poster", "detail", "recipe", "cover")
STEP_PREFIX_PATTERN = re.compile(r"^\s*(?:第?\s*\d+\s*[步段]|步骤\s*\d+|step\s*\d+)\s*[:：、.．-]?\s*", re.IGNORECASE)
META_COPY_PATTERN = re.compile(
    r"(图解教程|图解\s*\d+\s*/\s*\d+|步骤与转化页|教程页|转化页|第\s*[一二三123]\s*张|第\s*[一二三123]\s*步)",
    re.IGNORECASE,
)
MEASURE_UNIT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(g|克|kg|千克|ml|毫升|l|升|汤匙|茶匙|勺|大勺|小勺|个|根|片|块|瓣|碗|杯)",
    re.IGNORECASE,
)
TRACK_LITERAL_BAN_LIST = (
    "电饭煲一锅出",
    "家常硬菜",
    "家庭宴客菜",
    "餐饮店招牌菜",
    "下酒夜宵菜",
    "节日年菜",
)


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


def load_cover_template(template_file: Path = COVER_TEMPLATE_FILE) -> str:
    if not template_file.exists():
        raise FileNotFoundError(f"未找到封面模板：{template_file}")
    template = template_file.read_text(encoding="utf-8").strip()
    if not template:
        raise ValueError(f"封面模板为空：{template_file}")
    return template


def load_cankao_group_template(template_file: Path) -> str:
    if not template_file.exists():
        raise FileNotFoundError(f"未找到模式2模板：{template_file}")
    template = template_file.read_text(encoding="utf-8").strip()
    if not template:
        raise ValueError(f"模式2模板为空：{template_file}")
    return template


def collect_cankao_placeholders(template_text: str) -> list[str]:
    placeholders: list[str] = []
    for match in CANKAO_PLACEHOLDER_PATTERN.finditer(template_text):
        placeholders.append(match.group(0))
    return placeholders


def render_cankao_template_by_replacements(template_text: str, replacements: list[str]) -> str:
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

    rendered = CANKAO_PLACEHOLDER_PATTERN.sub(_replace, template_text)
    if replaced_count != len(replacements):
        raise ValueError("变量替换数量超出模板需求。")
    if CANKAO_PLACEHOLDER_PATTERN.search(rendered):
        raise ValueError("模板仍存在未替换的变量占位符。")
    return rendered.strip()


def encode_image_as_data_url(image_path: Path) -> str:
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def move_image_to_publish(source_image_path: str | Path, publish_dir: Path) -> str:
    source = Path(source_image_path)
    publish_dir.mkdir(parents=True, exist_ok=True)
    target = publish_dir / source.name
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    return str(target)


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
5) 关于“米饭”与“互动”的变量必须满足：
   - 菜是菜、饭是饭：米饭必须是单独一碗，不与主菜混炒或混拌成一道。
   - 可在米饭表面点缀少量主菜，表达“夹菜盖饭前”的状态，但主菜主体仍在主盘中。
   - 互动餐具需在米饭前方形成“准备入口”的生活化动作，不要描述成菜饭已经混合。
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

    if len(replacements) >= 4:
        rice_text = replacements[2]
        interaction_text = replacements[3]
        replacements[2] = (
            "要，米饭与主菜必须分离呈现：单独一碗白米饭放在主菜旁，"
            "仅少量主菜自然点缀在饭面，体现准备入口状态，不与整盘主菜混成一道"
        )
        if rice_text:
            replacements[2] = f"{replacements[2]}；{rice_text}"
        replacements[3] = (
            "使用最适合这道菜的餐具放在米饭前方，与主菜形成准备夹取入口的互动，"
            "生活化、像人在开吃前，不要表现成菜饭混合"
        )
        if interaction_text:
            replacements[3] = f"{replacements[3]}；{interaction_text}"

    prompt_text = render_template_by_replacements(template_text=template_text, replacements=replacements)
    return {"model": model, "prompt": prompt_text}


def generate_cankao_prompt_by_template(
    client: OpenAI,
    dish_name: str,
    notes: str,
    template_text: str,
) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    placeholders = collect_cankao_placeholders(template_text)
    if not placeholders:
        return {"model": model, "prompt": template_text.strip()}

    system_prompt = """
你是菜谱视觉策划总监。你的任务是只为模板里的变量位提供替换值。
强制要求：
1) 你不能改写模板任何固定文本，只输出变量替换值。
2) 你必须按“变量位从上到下顺序”给出 replacement 数组。
3) replacement 数组长度必须与变量位数量完全一致。
4) 输出 JSON：{"replacements":["值1","值2",...]}，不要输出其它内容。
5) 每个变量位形如 {标签，示例}，请结合菜名与补充说明生成贴合该菜的替换值，不要照抄示例。
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
                max_tokens=2200,
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

    prompt_text = render_cankao_template_by_replacements(template_text=template_text, replacements=replacements)
    return {"model": model, "prompt": prompt_text}


def generate_cankao_prompt_with_images(
    client: OpenAI,
    dish_name: str,
    notes: str,
    template_text: str,
    image_paths: list[Path],
    *,
    bubble_text: str = "",
    stage_name: str = "模式2多模态模板",
) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    placeholders = collect_cankao_placeholders(template_text)
    if not placeholders:
        return {"model": model, "prompt": template_text.strip()}

    system_prompt = """
你是抖音美食图文视觉策划。请结合参考图片，只为模板变量位提供替换值。
要求：
1) 不得改写模板固定文本，只输出 replacements 数组。
2) replacements 顺序必须与变量位从上到下完全一致。
3) 只输出 JSON：{"replacements":["值1","值2",...]}。
4) 变量位含“海报图”时，用一句话描述参考海报中的菜品视觉，不要写“见上图”。
5) 变量位含“豆包生成的气泡话语”时，必须使用用户提供的已定稿气泡文案。
""".strip()

    placeholder_lines = "\n".join(f"{index + 1}. {placeholder}" for index, placeholder in enumerate(placeholders))
    bubble_block = f"\n已定稿气泡文案：{bubble_text}" if bubble_text.strip() else ""
    user_prompt = f"""
菜名：{dish_name}
补充说明：{notes or "无"}{bubble_block}

模板如下：
{template_text}

变量位清单（按顺序）：
{placeholder_lines}

请只返回 replacements JSON。
""".strip()

    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for image_path in image_paths:
        if not image_path.exists():
            raise FileNotFoundError(f"参考图不存在：{image_path}")
        user_content.append({"type": "text", "text": f"参考图：{image_path.name}"})
        user_content.append({"type": "image_url", "image_url": {"url": encode_image_as_data_url(image_path)}})

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 3)
    response = None
    last_error: Exception | None = None
    for _ in range(max_retry):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=2600,
                temperature=temperature,
            )
            break
        except Exception as exc:
            last_error = exc
    if response is None:
        raise RuntimeError(f"{stage_name}失败：{last_error}")

    raw_text = extract_chat_text_output(response).strip()
    if not raw_text:
        raise ValueError(f"{stage_name}未返回有效内容。")
    payload = extract_json_object_from_text(raw_text)
    replacements_raw = payload.get("replacements")
    if not isinstance(replacements_raw, list):
        raise ValueError(f"{stage_name}返回 JSON 缺少 replacements 数组。")
    replacements = [str(item).strip() for item in replacements_raw]
    if len(replacements) != len(placeholders):
        raise ValueError(f"{stage_name}变量替换数量不匹配：需要 {len(placeholders)} 个，实际 {len(replacements)} 个。")

    for index, placeholder in enumerate(placeholders):
        if "豆包生成的气泡话语" in placeholder and bubble_text.strip():
            replacements[index] = bubble_text.strip()

    prompt_text = render_cankao_template_by_replacements(template_text=template_text, replacements=replacements)
    return {"model": model, "prompt": prompt_text}


def generate_poster_bubble_copy(client: OpenAI, poster_image_path: Path) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    if not poster_image_path.exists():
        raise FileNotFoundError(f"海报图不存在：{poster_image_path}")

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请根据参考海报中的菜品，用角色第一人称的口吻写一条气泡框内文案，"
                "让抖音用户一看就有食欲！只输出气泡内要说的话，不要解释、不要加引号外的说明。"
            ),
        },
        {"type": "image_url", "image_url": {"url": encode_image_as_data_url(poster_image_path)}},
    ]
    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 3)
    response = None
    last_error: Exception | None = None
    for _ in range(max_retry):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=300,
                temperature=temperature,
            )
            break
        except Exception as exc:
            last_error = exc
    if response is None:
        raise RuntimeError(f"气泡文案生成失败：{last_error}")

    content = extract_chat_text_output(response).strip()
    if not content:
        raise ValueError("豆包未返回有效气泡文案。")
    return {"model": model, "content": content}


def select_douyin_publish_image(
    client: OpenAI,
    image_paths: list[Path],
    *,
    image_kind: str = "图文",
) -> dict[str, Any]:
    if not image_paths:
        raise ValueError(f"没有可筛选的{image_kind}候选图。")
    if len(image_paths) == 1:
        only_path = image_paths[0]
        return {
            "auto_selected": True,
            "winner_index": 1,
            "winner_image_name": only_path.name,
            "winner_reason": f"仅 1 张{image_kind}候选，直接入 publish。",
        }

    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"选出最适合在抖音发布的{image_kind}。"
                "只输出 JSON：{\"winner_index\": 1, \"winner_reason\": \"一句话理由\"}，"
                "winner_index 从 1 开始，对应下面候选顺序。"
            ),
        }
    ]
    for index, image_path in enumerate(image_paths, start=1):
        user_content.append({"type": "text", "text": f"候选{index}：{image_path.name}"})
        user_content.append({"type": "image_url", "image_url": {"url": encode_image_as_data_url(image_path)}})

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=400,
        temperature=0,
    )
    raw_text = extract_chat_text_output(response).strip()
    if not raw_text:
        raise ValueError(f"豆包未返回{image_kind}筛选结果。")
    payload = extract_json_object_from_text(raw_text)
    winner_index = int(payload.get("winner_index", 1))
    if winner_index < 1 or winner_index > len(image_paths):
        winner_index = 1
    winner_path = image_paths[winner_index - 1]
    return {
        "auto_selected": False,
        "winner_index": winner_index,
        "winner_image_name": winner_path.name,
        "winner_reason": str(payload.get("winner_reason", "")).strip() or f"豆包已选出抖音{image_kind}。",
    }


def select_douyin_poster_image(client: OpenAI, image_paths: list[Path]) -> dict[str, Any]:
    return select_douyin_publish_image(client, image_paths, image_kind="图文海报")


def select_and_publish_image_group(
    client: OpenAI,
    *,
    publish_dir: Path,
    candidate_paths: list[str],
    image_kind: str,
    selection_report_name: str,
) -> tuple[str, str, dict[str, Any]]:
    paths = [Path(path) for path in candidate_paths if str(path).strip() and Path(path).exists()]
    if not paths:
        return "", "", {}

    publish_dir.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        selected = move_image_to_publish(str(paths[0]), publish_dir)
        result = {
            "auto_selected": True,
            "winner_index": 1,
            "winner_image_name": paths[0].name,
            "winner_reason": f"仅 1 张{image_kind}，直接入 publish。",
        }
        print(f"{image_kind}数量=1，直接入 publish：{selected}")
        return selected, "direct", result

    try:
        selection_result = select_douyin_publish_image(client, paths, image_kind=image_kind)
        winner_index = int(selection_result.get("winner_index", 1))
        winner_path = paths[max(0, min(winner_index - 1, len(paths) - 1))]
        selected = move_image_to_publish(str(winner_path), publish_dir)
        save_text_output(
            json.dumps(selection_result, ensure_ascii=False, indent=2),
            publish_dir / selection_report_name,
        )
        print(
            f"{image_kind}筛选完成：{selected}，理由：{selection_result.get('winner_reason', '')}"
        )
        return selected, "scored", selection_result
    except Exception as select_exc:
        selected = move_image_to_publish(str(paths[0]), publish_dir)
        fallback_result = {
            "auto_selected": False,
            "winner_index": 1,
            "winner_image_name": paths[0].name,
            "winner_reason": f"筛选失败，回退首图：{select_exc}",
        }
        print(f"{image_kind}筛选失败，回退直通首图：{selected}")
        return selected, "fallback_direct", fallback_result


def get_mode2_group_settings(group: str) -> dict[str, Any]:
    if group not in MODE2_GROUP_KEYS:
        raise ValueError(f"未知模式2分组：{group}")
    ensure_runtime_config_loaded()
    prefix = group.upper()
    quality = (
        os.getenv(f"MODE2_{prefix}_IMAGE_QUALITY", "").strip()
        or os.getenv("OPENAI_IMAGE_QUALITY", DEFAULT_IMAGE_QUALITY).strip()
        or DEFAULT_IMAGE_QUALITY
    )
    count_env = f"MODE2_{prefix}_IMAGE_COUNT"
    default_count = parse_int_env("OPENAI_IMAGE_COUNT", DEFAULT_IMAGE_COUNT)
    raw_count = os.getenv(count_env, "").strip()
    image_count = parse_int_env(count_env, default_count) if raw_count else default_count
    base = get_image_settings()
    return {
        "model": base["model"],
        "size": base["size"],
        "quality": quality,
        "image_count": image_count,
    }


def generate_images_from_references(
    client: OpenAI,
    prompt_text: str,
    reference_paths: list[str | Path],
    settings: dict[str, Any],
) -> list[dict[str, str]]:
    valid_paths = [Path(path) for path in reference_paths if str(path).strip() and Path(path).exists()]
    if not valid_paths:
        return generate_images_by_prompt(client=client, prompt_text=prompt_text, settings=settings)

    max_retry = parse_int_env("IMAGE_REQUEST_RETRY_COUNT", 2)
    request_timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)
    response = None
    opened_files: list[Any] = []
    try:
        for path in valid_paths:
            opened_files.append(open(path, "rb"))
        image_arg: Any = opened_files[0] if len(opened_files) == 1 else opened_files
        for attempt in range(1, max_retry + 1):
            try:
                response = client.images.edit(
                    model=settings["model"],
                    image=image_arg,
                    prompt=prompt_text,
                    size=settings["size"],
                    quality=settings["quality"],
                    n=settings["image_count"],
                    timeout=request_timeout,
                )
                break
            except Exception as exc:
                if attempt >= max_retry or not is_timeout_error(exc):
                    raise RuntimeError(f"参考图生图失败：{exc}") from exc
                print(f"参考图生图超时，正在重试第 {attempt + 1}/{max_retry} 次...")
    finally:
        for file_obj in opened_files:
            file_obj.close()

    if response is None:
        raise RuntimeError("参考图生图失败：接口未返回有效响应。")
    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError("参考图生图失败：接口未返回有效图片数据。")
    return image_items


def render_prompt_fallback(template_text: str, dish_name: str, notes: str) -> str:
    notes_text = notes.strip() or f"{dish_name}，突出家常真实出锅状态。"
    replacements = [
        "暖棕米白家常配色",
        dish_name,
        "要，米饭与主菜分离：单独一碗热米饭在旁，仅少量主菜点缀在饭面，不与主菜混成一道",
        "使用最适合这道菜的餐具放在米饭前方，与主菜形成准备入口的互动，像人在开吃前",
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


def get_cover_image_count() -> int:
    ensure_runtime_config_loaded()
    return parse_int_env("COVER_IMAGE_COUNT", DEFAULT_COVER_IMAGE_COUNT)


def get_content_track() -> str:
    ensure_runtime_config_loaded()
    return os.getenv("CONTENT_TRACK", DEFAULT_CONTENT_TRACK).strip() or DEFAULT_CONTENT_TRACK


def render_cover_prompt_by_template(template_text: str, dish_name: str) -> str:
    placeholders = collect_template_placeholders(template_text)
    if not placeholders:
        return template_text.strip()
    # 封面模板只允许替换菜名变量，其它文字保持原样。
    replacements = [dish_name.strip()] * len(placeholders)
    return render_template_by_replacements(template_text=template_text, replacements=replacements)


def build_three_card_script_fallback(dish_name: str, notes: str, content_track: str) -> dict[str, Any]:
    notes_text = notes.strip()
    return {
        "content_track": content_track,
        "card1_hook": f"不用开火不用炒！{dish_name}",
        "card1_sub": "零失败，拌米饭能吃三碗",
        "card2_title": "食材清单",
        "card2_items": [f"{dish_name} 300g", "豆腐 300g", "食用油 15ml", "盐 1茶匙", "糖 1茶匙", "清水 200ml"],
        "card3_step": "鸡块裹粉下锅炸至金黄，复炸30秒更脆，最后趁热撒椒盐拌匀即可上桌",
        "card3_cta": "收藏起来，下次想吃直接做；关注@阿叶造新菜，开店家用都不赖！",
        "caption": f"{dish_name}，家常快手，适合{content_track}内容方向。",
        "hashtags": ["#电饭煲美食", "#懒人食谱", "#家常菜"],
        "notes_used": notes_text,
    }


def generate_three_card_script(
    *,
    client: OpenAI,
    dish_name: str,
    notes: str,
    content_track: str,
) -> dict[str, Any]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 3)
    try:
        cankao_text = load_cankao_template()
    except Exception:
        cankao_text = ""
    cankao_style_anchor = (
        "写实风格基线：普通家庭餐桌环境、iPhone主摄直拍低饱和、"
        "保留家常人为痕迹（刀工略不齐、摆盘略随意、非商业棚拍）。"
    )
    if cankao_text:
        cankao_style_anchor = (
            f"{cankao_style_anchor} 必须吸收参考规范中的真实感偏好，不做过度工整的广告模板。"
        )

    system_prompt = """
你是短视频美食图文编导。请为同一道菜输出“固定3张图”脚本：
1) 图1：成品 + 反常识钩子大字（吸引滑动）
2) 图2：纯食材清单（两列可读）
3) 图3：一句话步骤 + 收藏引导

要求：
- 必须适配任意菜名，不能写死具体菜。
- 语言口语化、简短、强行动导向。
- 三张图必须是同一套视觉体系：同一菜品、同一色温与配色倾向、同一字体风格、同一版式骨架。
- card3_step 必须是具体可执行做法，包含关键动作/火候或时间，不能空泛。
- card3_step 禁止出现“步骤1/步骤2/步骤3”“Step 1/2/3”这类编号前缀。
- card3_cta 必须包含“收藏”语义。
- card3_cta 末尾必须追加固定文案：“关注@阿叶造新菜，开店家用都不赖！”
- 全局视觉语气必须更“人做饭”而非“AI模板”：允许自然不完美，禁止机械化设计感。
- card2_items 每一项都必须包含明确计量单位（如 g/ml/汤匙/茶匙/个），禁止只写“适量/少许”。
- 内容赛道仅用于风格控制，严禁把赛道名或赛道标签词写进画面文案。
- 仅输出 JSON，不要解释。
""".strip()

    user_prompt = f"""
菜名：{dish_name}
补充说明：{notes or "无"}
内容赛道：{content_track}
写实参考：{cankao_style_anchor}

请输出 JSON，字段固定：
{{
  "content_track": "...",
  "card1_hook": "...",
  "card1_sub": "...",
  "card2_title": "...",
  "card2_items": ["...","..."],
  "card3_step": "...",
  "card3_cta": "...",
  "caption": "...",
  "hashtags": ["#...","#...","#..."]
}}
""".strip()

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
                max_tokens=1600,
                temperature=temperature,
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    if response is None:
        raise RuntimeError(f"三图脚本生成失败：{last_error}")

    raw_text = extract_chat_text_output(response).strip()
    if not raw_text:
        raise RuntimeError("三图脚本生成失败：模型未返回内容。")
    payload = extract_json_object_from_text(raw_text)

    card2_items_raw = payload.get("card2_items", [])
    if not isinstance(card2_items_raw, list):
        card2_items_raw = []
    card2_items = [str(item).strip() for item in card2_items_raw if str(item).strip()]

    def has_measure_unit(item_text: str) -> bool:
        return bool(MEASURE_UNIT_PATTERN.search(item_text))

    def normalize_item_with_unit(item_text: str) -> str:
        cleaned = item_text.strip()
        cleaned = re.sub(r"(适量|少许|若干)\s*$", "", cleaned).strip(" ，。;；")
        if not cleaned:
            return "食材 50g"
        if has_measure_unit(cleaned):
            return cleaned
        liquid_keywords = ("油", "生抽", "老抽", "料酒", "醋", "水", "高汤", "牛奶", "酱")
        powder_keywords = ("盐", "糖", "胡椒", "淀粉", "鸡精", "味精", "孜然", "辣椒粉")
        garnish_keywords = ("葱", "姜", "蒜", "香菜", "辣椒", "芝麻")
        if any(keyword in cleaned for keyword in liquid_keywords):
            return f"{cleaned} 15ml"
        if any(keyword in cleaned for keyword in powder_keywords):
            return f"{cleaned} 1茶匙"
        if any(keyword in cleaned for keyword in garnish_keywords):
            return f"{cleaned} 10g"
        if "豆腐" in cleaned:
            return f"{cleaned} 300g"
        if dish_name and (dish_name in cleaned or cleaned in dish_name):
            return f"{cleaned} 300g"
        return f"{cleaned} 50g"

    card2_items = [normalize_item_with_unit(item) for item in card2_items]
    if not card2_items:
        card2_items = [f"{dish_name} 300g", "豆腐 300g", "食用油 15ml", "盐 1茶匙", "糖 1茶匙", "清水 200ml"]

    hashtags_raw = payload.get("hashtags", [])
    if not isinstance(hashtags_raw, list):
        hashtags_raw = []
    hashtags = [str(item).strip() for item in hashtags_raw if str(item).strip()]
    if not hashtags:
        hashtags = ["#电饭煲美食", "#懒人食谱", "#家常菜"]

    card3_step = str(payload.get("card3_step", "")).strip()
    card3_step = STEP_PREFIX_PATTERN.sub("", card3_step).strip(" ，。；;")
    if not card3_step:
        card3_step = "食材处理好后大火快炒上色，转中火焖3分钟，收汁后立刻出锅"

    card3_cta = str(payload.get("card3_cta", "")).strip() or "收藏起来，下次想吃直接做"
    if "收藏" not in card3_cta:
        card3_cta = f"{card3_cta}，收藏起来下次做"
    fixed_ad = "关注@阿叶造新菜，开店家用都不赖！"
    if fixed_ad not in card3_cta:
        card3_cta = f"{card3_cta}；{fixed_ad}"

    def clean_meta_copy(raw_text: str) -> str:
        cleaned = META_COPY_PATTERN.sub("", raw_text).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"^[·•\-—\s]+", "", cleaned)
        cleaned = re.sub(r"[·•\-—\s]+$", "", cleaned)
        return cleaned

    def strip_track_literals(raw_text: str) -> str:
        text = raw_text
        for track_literal in TRACK_LITERAL_BAN_LIST:
            if track_literal:
                text = text.replace(track_literal, " ")
        if content_track:
            text = text.replace(content_track, " ")
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    card1_hook = strip_track_literals(clean_meta_copy(str(payload.get("card1_hook", "")).strip())) or f"不用开火不用炒！{dish_name}"
    card1_sub = strip_track_literals(clean_meta_copy(str(payload.get("card1_sub", "")).strip())) or "鲜嫩入味，零失败好做"
    card2_title = strip_track_literals(clean_meta_copy(str(payload.get("card2_title", "")).strip())) or "食材清单"
    card3_step = strip_track_literals(card3_step)
    card3_cta = strip_track_literals(clean_meta_copy(card3_cta))

    return {
        "content_track": str(payload.get("content_track", "")).strip() or content_track,
        "card1_hook": card1_hook,
        "card1_sub": card1_sub,
        "card2_title": card2_title,
        "card2_items": card2_items[:10],
        "card3_step": card3_step,
        "card3_cta": card3_cta,
        "caption": str(payload.get("caption", "")).strip() or f"{dish_name}，家常快手，零失败。",
        "hashtags": hashtags[:5],
        "notes_used": notes.strip(),
    }


def build_run_output_dir(timestamp: str, dish_name: str) -> Path:
    run_dir = OUTPUT_DIR / f"{timestamp}_{sanitize_file_name(dish_name)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_text_output(content: str, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content.strip() + "\n", encoding="utf-8")


def build_dish_idea_record_text(dish_payload: dict[str, str]) -> str:
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    notes = str(dish_payload.get("notes", "")).strip()
    region_label = str(dish_payload.get("region_label", "")).strip()
    reference_dish = str(dish_payload.get("reference_dish", "")).strip()
    return (
        f"菜名：{dish_name}\n"
        f"菜名描述：{notes or '无'}\n"
        f"参考菜系：{region_label or '未记录'}\n"
        f"参考菜品：{reference_dish or '未记录'}\n"
    )


def save_dish_idea_record_file(output_dir: Path, dish_payload: dict[str, str]) -> str:
    dish_name = str(dish_payload.get("dish_name", "")).strip() or "新菜"
    output_file = output_dir / f"{dish_name}_造菜信息.txt"
    save_text_output(build_dish_idea_record_text(dish_payload), output_file)
    return str(output_file)


def build_v2_publish_source_text(dish_name: str, notes: str) -> str:
    lines = [f"菜名：{dish_name.strip()}"]
    notes_text = notes.strip()
    if notes_text:
        lines.append(f"菜名描述：{notes_text}")
    return "\n".join(lines)


def generate_v2_publish_copy_assets(
    client: OpenAI,
    *,
    dish_name: str,
    notes: str,
    timestamp: str,
    output_dir: Path,
    topic_reference_text: str = "",
) -> dict[str, Any]:
    from image_generator import generate_publish_copy_assets

    source_text = build_v2_publish_source_text(dish_name=dish_name, notes=notes)
    return generate_publish_copy_assets(
        client=client,
        dish_name=dish_name,
        source_text=source_text,
        timestamp=timestamp,
        notes=notes,
        topic_reference_text=topic_reference_text or source_text,
        output_name=dish_name,
        source_label="V2菜名与描述",
        output_dir=output_dir,
    )


def persist_v2_common_text_assets(
    *,
    client: OpenAI,
    output_dir: Path,
    timestamp: str,
    dish_payload: dict[str, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    notes = str(dish_payload.get("notes", "")).strip()

    dish_record_file = save_dish_idea_record_file(output_dir, dish_payload)
    result["dish_idea_record_file"] = dish_record_file
    print(f"造菜信息已保存：{dish_record_file}")

    try:
        publish_copy = generate_v2_publish_copy_assets(
            client=client,
            dish_name=dish_name,
            notes=notes,
            timestamp=timestamp,
            output_dir=output_dir,
            topic_reference_text=build_dish_idea_record_text(dish_payload),
        )
        result["publish_title_file"] = publish_copy.get("title_file", "")
        result["publish_description_file"] = publish_copy.get("description_file", "")
        result["publish_description_body_file"] = publish_copy.get("description_body_file", "")
        result["publish_platform_topic_files"] = publish_copy.get("platform_topic_files", {})
        result["publish_platform_description_files"] = publish_copy.get("platform_description_files", {})
        result["publish_copy_error"] = ""
    except Exception as exc:
        result["publish_copy_error"] = str(exc)
        error_file = output_dir / f"{dish_name}_平台文案生成失败原因.txt"
        save_text_output(
            "平台发布文案（标题/话题/图文描述）生成失败。\n"
            f"失败原因：{exc}\n"
            "建议：检查豆包文本接口与网络后重试。",
            error_file,
        )
        print(f"平台文案生成失败，已写入说明：{error_file}")
    return result


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
    *,
    name_suffix: str = "",
) -> list[str]:
    safe_name = sanitize_file_name(dish_name)
    suffix_part = f"_{sanitize_file_name(name_suffix)}" if name_suffix.strip() else ""
    saved_files: list[str] = []
    for index, item in enumerate(image_items, start=1):
        image_file = output_dir / f"{timestamp}_{safe_name}{suffix_part}_{index:02d}.png"
        image_file.write_bytes(base64.b64decode(item["image_base64"]))
        saved_files.append(str(image_file))
        revised_prompt = item["revised_prompt"].strip()
        if revised_prompt:
            revised_file = output_dir / f"{timestamp}_{safe_name}{suffix_part}_{index:02d}_revised_prompt.txt"
            save_text_output(revised_prompt, revised_file)
    return saved_files
