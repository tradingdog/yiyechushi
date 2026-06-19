from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

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
DISH_POOL_DIR = ROOT_DIR / "dish_pool"
DISH_ARCHIVE_DIR = ROOT_DIR / "dish_archive"
FAVORITES_FILE = ROOT_DIR / "dish_favorites.json"
BUBBLE_COPY_HISTORY_FILE = ROOT_DIR / "bubble_copy_history.txt"
BUBBLE_COPY_FILE_SUFFIX = "_气泡文案.txt"
BUBBLE_COPY_HISTORY_PROMPT_LIMIT = 80

DEFAULT_DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_DOUBAO_TEXT_MODEL = "doubao-seed-2-0-pro-260215"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_SILKROAD_BASE_URL = "https://ai.silkroadai.io/v1"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "low"
DEFAULT_IMAGE_COUNT = 1
DEFAULT_COVER_IMAGE_COUNT = 1
DEFAULT_IMAGE_REQUEST_MAX_ATTEMPTS = 3
DEFAULT_CONTENT_TRACK = "电饭煲一锅出"

_RUNTIME_CONFIG_LOADED = False
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{变量(?:[：:,，][^{}]*)?\}")
CANKAO_PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
MODE2_GROUP_KEYS = ("poster", "detail", "recipe", "cover")
AUTO_DISH_CUISINE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("1", "中华料理"),
    ("0", "全部随机"),
    ("2", "新马泰"),
    ("3", "日韩"),
    ("4", "西餐"),
    ("5", "中东北非"),
    ("6", "东欧"),
    ("7", "拉美"),
)
DEFAULT_AUTO_DISH_CUISINE_MODE = "1"
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


ROOT_CONFIG_FILE = ROOT_DIR.parent / "config.env"

WORK_CANCEL_EVENT = threading.Event()


def request_work_cancel() -> None:
    WORK_CANCEL_EVENT.set()


def clear_work_cancel() -> None:
    WORK_CANCEL_EVENT.clear()


def raise_if_work_cancelled(stage_label: str = "") -> None:
    if WORK_CANCEL_EVENT.is_set():
        prefix = f"{stage_label}：" if stage_label else ""
        raise RuntimeError(f"{prefix}用户已请求停止后台任务。")


def sleep_with_cancel(seconds: float, *, stage_label: str = "") -> None:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        raise_if_work_cancelled(stage_label)
        time.sleep(min(0.35, deadline - time.time()))


def is_silkroad_openai_gateway(base_url: str) -> bool:
    return "silkroadai.io" in base_url.strip().lower()


def get_image_api_batch_n(requested: int) -> int:
    """丝路网关单次请求不支持 n>1（会报 Unknown parameter: tools[0].n），改为每次 n=1 由上层循环凑张数。"""
    batch_n = max(1, int(requested or 1))
    from image_gen_profile import (
        IMAGE_PROVIDER_OFFICIAL,
        IMAGE_PROVIDER_SILKROAD,
        normalize_image_provider,
    )

    provider = normalize_image_provider(os.getenv("IMAGE_API_PROVIDER", ""))
    if provider == IMAGE_PROVIDER_SILKROAD:
        return 1
    if provider == IMAGE_PROVIDER_OFFICIAL:
        return min(batch_n, 4)
    base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
    if is_silkroad_openai_gateway(base_url):
        return 1
    return min(batch_n, 4)


def is_tools_batch_n_error(exc: Exception) -> bool:
    message = " ".join(str(error) for error in iter_exception_chain(exc)).lower()
    return "tools[0].n" in message or "unknown parameter: 'tools[0].n'" in message


def resolve_openai_image_model(model: str, base_url: str) -> str:
    normalized = (model or DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL
    gateway = (base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
    if is_silkroad_openai_gateway(gateway) and normalized.startswith("gpt-image-2") and normalized != DEFAULT_IMAGE_MODEL:
        return DEFAULT_IMAGE_MODEL
    return normalized


def sync_v2_openai_image_settings() -> None:
    """V2/config.env 的生图配置优先，并在丝路网关上规范化模型名。"""
    v2_values = parse_env_file(CONFIG_FILE)
    for key in (
        "OPENAI_BASE_URL",
        "OPENAI_IMAGE_MODEL",
        "OPENAI_IMAGE_SIZE",
        "OPENAI_IMAGE_QUALITY",
        "OPENAI_IMAGE_COUNT",
    ):
        if key in v2_values:
            os.environ[key] = v2_values[key]

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL
    model = os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL
    resolved = resolve_openai_image_model(model, base_url)
    if resolved != model:
        print(f"OpenAI 生图模型已适配丝路网关：{model} -> {resolved}")
        os.environ["OPENAI_IMAGE_MODEL"] = resolved


def ensure_runtime_config_loaded() -> None:
    global _RUNTIME_CONFIG_LOADED
    if _RUNTIME_CONFIG_LOADED:
        sync_v2_openai_image_settings()
        return

    merged_values: dict[str, str] = {}
    merged_values.update(parse_env_file(ROOT_CONFIG_FILE))
    merged_values.update(parse_env_file(CONFIG_FILE))
    merged_values.update(parse_env_file(ROOT_DIR.parent / ".env"))

    # 配置文件优先于进程启动前已存在的系统/终端环境变量，避免旧 OPENAI_* 覆盖 .env 与 config.env。
    for key, value in merged_values.items():
        os.environ[key] = value

    sync_v2_openai_image_settings()
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


def is_moderation_blocked_error(exc: Exception) -> bool:
    for error in iter_exception_chain(exc):
        message = str(error).lower()
        if "moderation_blocked" in message or "safety system" in message:
            return True
    return False


def get_image_request_max_attempts() -> int:
    return max(1, parse_int_env("IMAGE_REQUEST_RETRY_COUNT", 3))


def format_image_generation_request_label(
    settings: dict[str, Any],
    *,
    mode: str,
    reference_paths: list[Path] | None = None,
) -> str:
    ref_part = ""
    if reference_paths:
        ref_part = f"，参考图={len(reference_paths)}张（{', '.join(path.name for path in reference_paths)}）"
    return (
        f"mode={mode}，model={settings.get('model')}，size={settings.get('size')}，"
        f"quality={settings.get('quality')}，n={settings.get('image_count')}{ref_part}"
    )


def classify_image_api_error(exc: Exception) -> str:
    if is_timeout_error(exc):
        return "timeout"
    if is_moderation_blocked_error(exc):
        return "moderation_blocked"
    message = " ".join(str(error) for error in iter_exception_chain(exc)).lower()
    if "model_not_found" in message or "no available channel for model" in message:
        return "model_not_found"
    if "billing_hard_limit" in message or "billing limit" in message:
        return "billing_limit"
    if "502" in message or "bad gateway" in message:
        return "http_502"
    if "503" in message or "service unavailable" in message:
        return "http_503"
    if "504" in message or "gateway timeout" in message:
        return "http_504"
    if "429" in message or "rate limit" in message or "unusual activity" in message:
        return "rate_limit"
    if "connection error" in message or "connection reset" in message:
        return "connection"
    for error in iter_exception_chain(exc):
        if isinstance(error, (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError)):
            return "connection"
    if "internal server error" in message or "error code: 500" in message:
        return "http_500"
    return "api_error"


def is_retriable_image_api_error(exc: Exception) -> bool:
    if is_moderation_blocked_error(exc):
        return False
    if is_tools_batch_n_error(exc):
        return True
    error_kind = classify_image_api_error(exc)
    if error_kind in {"billing_limit", "moderation_blocked", "model_not_found", "api_error"}:
        return False
    if error_kind in {"timeout", "http_502", "http_503", "http_504", "http_500", "rate_limit", "connection"}:
        return True
    for error in iter_exception_chain(exc):
        if isinstance(error, (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError, OSError)):
            return True
    return is_timeout_error(exc)


def classify_text_api_error(exc: Exception) -> str:
    if is_timeout_error(exc):
        return "timeout"
    message = " ".join(str(error) for error in iter_exception_chain(exc)).lower()
    if "429" in message or "rate limit" in message or "unusual activity" in message:
        return "rate_limit"
    if "connection error" in message or "connection reset" in message:
        return "connection"
    for error in iter_exception_chain(exc):
        if isinstance(error, (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError)):
            return "connection"
    if "502" in message or "bad gateway" in message:
        return "http_502"
    if "503" in message or "service unavailable" in message:
        return "http_503"
    if "504" in message or "gateway timeout" in message:
        return "http_504"
    if "internal server error" in message or "error code: 500" in message:
        return "http_500"
    return "api_error"


def is_retriable_text_api_error(exc: Exception) -> bool:
    error_kind = classify_text_api_error(exc)
    if error_kind in {"timeout", "http_502", "http_503", "http_504", "http_500", "rate_limit", "connection"}:
        return True
    for error in iter_exception_chain(exc):
        if isinstance(error, (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError, OSError, APITimeoutError)):
            return True
    return is_timeout_error(exc)


def api_retry_delay_seconds(attempt_index: int, *, error_kind: str = "") -> float:
    base = 3.0 if error_kind in {"connection", "rate_limit", "http_500"} else 1.5
    return min(45.0, base * (2 ** max(0, attempt_index - 1)))


def execute_image_generation_with_retries(
    *,
    stage_label: str,
    failure_prefix: str,
    settings: dict[str, Any],
    request_label: str,
    call_api: Any,
) -> list[dict[str, str]]:
    expected_count = max(1, int(settings.get("image_count") or 1))
    max_attempts = get_image_request_max_attempts()
    accumulated: list[dict[str, str]] = []
    print(f"{stage_label}开始请求：{request_label}；目标 n={expected_count}；最多尝试 {max_attempts} 次")

    for attempt in range(1, max_attempts + 1):
        raise_if_work_cancelled(stage_label)
        remaining = expected_count - len(accumulated)
        batch_n = get_image_api_batch_n(remaining)
        print(
            f"{stage_label}第 {attempt}/{max_attempts} 次调用生图接口"
            f"（本轮 n={batch_n}，目标剩余 {remaining}，已累计 {len(accumulated)}/{expected_count}）…"
        )
        try:
            if batch_n < remaining:
                print(f"{stage_label}单次最多 n={batch_n}（目标剩余 {remaining} 张，将分多次请求）…")
            response = call_api(batch_n)
            batch_items = extract_image_items(response)
            if batch_items:
                take_count = min(len(batch_items), remaining)
                accumulated.extend(batch_items[:take_count])
                if len(accumulated) >= expected_count:
                    print(f"{stage_label}成功：累计 {len(accumulated)}/{expected_count} 张。")
                    return accumulated[:expected_count]
                print(
                    f"{stage_label}本轮返回 {len(batch_items)} 张，已累计 {len(accumulated)}/{expected_count}，"
                    f"补生剩余 {expected_count - len(accumulated)} 张…"
                )
                if attempt < max_attempts:
                    continue
                raise RuntimeError(
                    f"{failure_prefix}失败：已达最大尝试次数，仍缺图片（{len(accumulated)}/{expected_count}）。"
                )

            print(
                f"{stage_label}第 {attempt} 次未返回有效图片（本轮 n={remaining}，"
                f"已累计 {len(accumulated)}/{expected_count}）。"
            )
            if attempt < max_attempts:
                print(f"{stage_label}将自动重试（{attempt + 1}/{max_attempts}）…")
                continue
            raise RuntimeError(
                f"{failure_prefix}失败：连续 {max_attempts} 次均未凑够图片（{len(accumulated)}/{expected_count}）。"
            )
        except Exception as exc:
            error_kind = classify_image_api_error(exc)
            print(
                f"{stage_label}第 {attempt} 次失败：error_type={error_kind}，"
                f"已累计 {len(accumulated)}/{expected_count}，详情={exc}"
            )
            if len(accumulated) >= expected_count:
                print(f"{stage_label}成功：累计 {len(accumulated)}/{expected_count} 张。")
                return accumulated[:expected_count]
            if attempt >= max_attempts or not is_retriable_image_api_error(exc):
                if accumulated:
                    raise RuntimeError(
                        f"{failure_prefix}失败：{exc}（已累计 {len(accumulated)}/{expected_count} 张，未凑满）。"
                    ) from exc
                raise RuntimeError(f"{failure_prefix}失败：{exc}") from exc
            delay = api_retry_delay_seconds(attempt, error_kind=error_kind)
            print(
                f"{stage_label}命中可重试错误（{error_kind}），"
                f"{delay:.0f}s 后准备第 {attempt + 1}/{max_attempts} 次…"
            )
            sleep_with_cancel(delay, stage_label=stage_label)

    if accumulated:
        if len(accumulated) >= expected_count:
            return accumulated[:expected_count]
        raise RuntimeError(
            f"{failure_prefix}失败：接口未凑够图片（{len(accumulated)}/{expected_count}）。"
        )
    raise RuntimeError(f"{failure_prefix}失败：接口未返回有效图片数据。")


def soften_detail_image_prompt(prompt_text: str) -> str:
    """弱化易触发 OpenAI 内容审核的面部/进食特写描述，改为手部局部特写。"""
    replacements = [
        (
            "特写人物下半张脸（仅露出嘴巴和下巴，绝对不能出现眼睛和鼻子），人物正张嘴准备进食",
            "特写人物双手持筷或持勺的局部，仅露出白色厨师服袖口和酒红色围裙一角，绝对不出现面部",
        ),
        (
            "人物嘴旁边放置一个黄色填充、黑色粗描边的圆角气泡对话框",
            "画面右上角放置一个黄色填充、黑色粗描边的圆角气泡对话框",
        ),
        ("使用 @juese.png 的人物角色（白色厨师服 + 酒红色围裙）", "使用固定 VI 角色（白色厨师服 + 酒红色围裙）"),
        ("使用 @juese.pgn 的人物角色（白色厨师服 + 酒红色围裙）", "使用固定 VI 角色（白色厨师服 + 酒红色围裙）"),
        ("@juese.png", "固定VI角色"),
        ("@juese.pgn", "固定VI角色"),
    ]
    softened = prompt_text
    for old, new in replacements:
        softened = softened.replace(old, new)
    return softened


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


def resolve_image_api_credentials(provider: str | None = None) -> tuple[str, str, str]:
    from image_gen_profile import IMAGE_PROVIDER_OFFICIAL, IMAGE_PROVIDER_SILKROAD, normalize_image_provider

    ensure_runtime_config_loaded()
    sync_v2_openai_image_settings()
    resolved_provider = normalize_image_provider(
        provider or os.getenv("IMAGE_API_PROVIDER", IMAGE_PROVIDER_OFFICIAL)
    )
    if resolved_provider == IMAGE_PROVIDER_OFFICIAL:
        api_key = os.getenv("OPENAIOFFICIAL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("未找到 OPENAIOFFICIAL_API_KEY，请在根目录 .env 中配置。")
        base_url = DEFAULT_OPENAI_BASE_URL
        model = DEFAULT_IMAGE_MODEL
        return api_key, base_url, model

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY（丝路），请在根目录 .env 或 config.env 中配置。")
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_SILKROAD_BASE_URL
    model = resolve_openai_image_model(
        os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL,
        base_url,
    )
    return api_key, base_url, model


def format_openai_image_runtime_label(provider: str | None = None) -> str:
    from image_gen_profile import format_image_gen_controls_label, image_gen_controls_from_mapping

    ensure_runtime_config_loaded()
    api_key, base_url, model = resolve_image_api_credentials(provider)
    key_hint = f"{api_key[:10]}..." if len(api_key) > 10 else "(未配置)"
    controls = image_gen_controls_from_mapping({})
    return (
        f"OpenAI 生图环境：{format_image_gen_controls_label(controls)}；"
        f"base_url={base_url}，model={model}，key={key_hint}"
    )


def build_openai_image_client(*, provider: str | None = None) -> OpenAI:
    from image_gen_profile import IMAGE_PROVIDER_SILKROAD, normalize_image_provider

    ensure_runtime_config_loaded()
    sync_v2_openai_image_settings()
    resolved_provider = normalize_image_provider(provider or os.getenv("IMAGE_API_PROVIDER", ""))
    api_key, base_url, model = resolve_image_api_credentials(resolved_provider)
    if normalize_image_provider(resolved_provider) == IMAGE_PROVIDER_SILKROAD:
        os.environ["OPENAI_IMAGE_MODEL"] = model
    timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)
    print(format_openai_image_runtime_label(resolved_provider))
    return OpenAI(
        api_key=api_key,
        timeout=timeout,
        base_url=base_url,
        http_client=build_httpx_client(timeout_seconds=timeout),
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


def auto_generate_dish_idea(
    client: OpenAI,
    *,
    session_banned_main_ingredients: list[str] | None = None,
) -> dict[str, str]:
    del client

    # 对齐 V1 自动造菜配置，默认把记忆文件落在 V2 目录下。
    if not os.getenv("AUTO_DISH_MEMORY_FILE", "").strip():
        os.environ["AUTO_DISH_MEMORY_FILE"] = "V2/dish_idea_memory.jsonl"
    if not os.getenv("AUTO_DISH_LIBRARY_FILE", "").strip():
        os.environ["AUTO_DISH_LIBRARY_FILE"] = "chuantongcaipu.txt"
    if not os.getenv("AUTO_DISH_INGREDIENT_LIBRARY_FILE", "").strip():
        os.environ["AUTO_DISH_INGREDIENT_LIBRARY_FILE"] = "V2/cankao/zhushicai.txt"
    if not os.getenv("AUTO_DISH_CUISINE_MODE", "").strip():
        os.environ["AUTO_DISH_CUISINE_MODE"] = DEFAULT_AUTO_DISH_CUISINE_MODE

    v1_client = v1_build_text_client()
    try:
        payload = v1_generate_auto_dish_idea(
            idea_file=IDEA_FILE,
            client=v1_client,
            session_banned_main_ingredients=session_banned_main_ingredients,
        )
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
        "main_ingredient": payload.get("main_ingredient", ""),
        "dish_type": payload.get("dish_type", ""),
        "cut_style": payload.get("cut_style", ""),
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


def find_cankao_placeholder_spans(template_text: str) -> list[tuple[int, int, str]]:
    """按花括号嵌套深度识别顶层变量位，示例文案内可含 {…} 子串。"""
    spans: list[tuple[int, int, str]] = []
    index = 0
    length = len(template_text)
    while index < length:
        if template_text[index] != "{":
            index += 1
            continue
        depth = 0
        start = index
        cursor = index
        while cursor < length:
            char = template_text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    spans.append((start, cursor + 1, template_text[start : cursor + 1]))
                    index = cursor + 1
                    break
            cursor += 1
        else:
            index += 1
    return spans


def collect_cankao_placeholders(template_text: str) -> list[str]:
    return [placeholder for _, _, placeholder in find_cankao_placeholder_spans(template_text)]


def render_cankao_template_by_replacements(template_text: str, replacements: list[str]) -> str:
    spans = find_cankao_placeholder_spans(template_text)
    if len(replacements) != len(spans):
        raise ValueError(
            f"变量替换数量不匹配：需要 {len(spans)} 个，实际 {len(replacements)} 个。"
        )

    parts: list[str] = []
    last_end = 0
    for index, (start, end, _) in enumerate(spans):
        parts.append(template_text[last_end:start])
        value = str(replacements[index]).strip()
        if not value:
            raise ValueError("变量替换值不能为空。")
        parts.append(value)
        last_end = end
    parts.append(template_text[last_end:])
    rendered = "".join(parts)
    if find_cankao_placeholder_spans(rendered):
        raise ValueError("模板仍存在未替换的变量占位符。")
    return rendered.strip()


EATING_ACTION_PLACEHOLDER_MARKERS = (
    "互动内容",
    "夹取本菜",
    "代表性一口",
    "细节特写一",
)

EATING_ACTION_GUIDANCE = (
    "海报图夹起的主菜、细节图人物送嘴的主菜，均须是人类吃这道菜时通常会食用的部分。"
)


def template_needs_eating_action_guidance(placeholders: list[str]) -> bool:
    return any(
        any(marker in placeholder for marker in EATING_ACTION_PLACEHOLDER_MARKERS)
        for placeholder in placeholders
    )


def append_eating_action_guidance(system_prompt: str, placeholders: list[str]) -> str:
    if not template_needs_eating_action_guidance(placeholders):
        return system_prompt
    return f"{system_prompt}\n\n{EATING_ACTION_GUIDANCE}"


def _compress_image_to_byte_limit(
    image_path: Path,
    *,
    max_bytes: int,
    log_prefix: str,
) -> tuple[bytes, str]:
    """仅在超过体积上限时缩放并转 JPEG（保留尽可能大的尺寸）。"""
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"{log_prefix} {image_path.name} 超过 {max_bytes / (1024 * 1024):.0f} MiB 限制，"
            "且未安装 Pillow，无法压缩。"
        ) from exc

    raw = image_path.read_bytes()
    with Image.open(image_path) as image:
        original_size = image.size
        canvas = image.convert("RGB")
        for _ in range(10):
            quality = 92
            for _ in range(8):
                buffer = BytesIO()
                canvas.save(buffer, format="JPEG", quality=quality, optimize=True)
                compressed = buffer.getvalue()
                if len(compressed) <= max_bytes:
                    print(
                        f"{log_prefix}已压缩："
                        f"{image_path.name} {original_size[0]}x{original_size[1]} "
                        f"({len(raw) / (1024 * 1024):.1f} MiB) -> "
                        f"{canvas.size[0]}x{canvas.size[1]} JPEG "
                        f"({len(compressed) / 1024:.0f} KB)"
                    )
                    return compressed, "image/jpeg"
                quality = max(50, quality - 8)
            if canvas.width <= 320 and canvas.height <= 320:
                break
            canvas = canvas.resize(
                (max(1, canvas.width * 3 // 4), max(1, canvas.height * 3 // 4)),
                Image.Resampling.LANCZOS,
            )

    raise RuntimeError(
        f"{log_prefix} {image_path.name} 压缩后仍超过 {max_bytes / (1024 * 1024):.0f} MiB 限制。"
    )


def prepare_image_bytes_for_vision_api(image_path: Path) -> tuple[bytes, str]:
    """多模态 image_url 用图：未超限则原样返回，仅豆包 10 MiB 等体积超限时才压缩。"""
    raw = image_path.read_bytes()
    guessed_mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    max_bytes = parse_int_env("VISION_IMAGE_MAX_BYTES", 9 * 1024 * 1024)
    if len(raw) <= max_bytes:
        return raw, guessed_mime
    return _compress_image_to_byte_limit(
        image_path,
        max_bytes=max_bytes,
        log_prefix="多模态参考图",
    )


def encode_image_as_data_url(image_path: Path) -> str:
    image_bytes, mime = prepare_image_bytes_for_vision_api(image_path)
    encoded = base64.b64encode(image_bytes).decode("ascii")
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

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 5)
    response = None
    last_error: Exception | None = None
    for attempt in range(1, max_retry + 1):
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
            error_kind = classify_text_api_error(exc)
            if attempt >= max_retry or not is_retriable_text_api_error(exc):
                break
            delay = api_retry_delay_seconds(attempt, error_kind=error_kind)
            print(f"豆包模板改写第 {attempt} 次失败（{error_kind}），{delay:.0f}s 后重试…")
            sleep_with_cancel(delay)
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

    system_prompt = append_eating_action_guidance(
        """
你是菜谱视觉策划总监。你的任务是只为模板里的变量位提供替换值。
强制要求：
1) 你不能改写模板任何固定文本，只输出变量替换值。
2) 你必须按“变量位从上到下顺序”给出 replacement 数组。
3) replacement 数组长度必须与变量位数量完全一致。
4) 输出 JSON：{"replacements":["值1","值2",...]}，不要输出其它内容。
5) 每个变量位形如 {标签，示例}，请结合菜名与补充说明生成贴合该菜的替换值，不要照抄示例。
""".strip(),
        placeholders,
    )

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

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 5)
    last_error: Exception | None = None
    validation_feedback = ""
    for attempt in range(1, max_retry + 1):
        attempt_user = user_prompt
        if validation_feedback:
            attempt_user = user_prompt + f"\n\n上次输出不合格：{validation_feedback}\n请修正 replacements。"
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": attempt_user},
                ],
                max_tokens=2200,
                temperature=temperature,
            )
        except Exception as exc:
            last_error = exc
            error_kind = classify_text_api_error(exc)
            if is_retriable_text_api_error(exc) and attempt < max_retry:
                delay = api_retry_delay_seconds(attempt, error_kind=error_kind)
                print(f"豆包模板改写第 {attempt} 次失败（{error_kind}），{delay:.0f}s 后重试…")
                sleep_with_cancel(delay)
                continue
            break

        raw_text = extract_chat_text_output(response).strip()
        if not raw_text:
            validation_feedback = "豆包未返回有效内容。"
            last_error = ValueError(validation_feedback)
            continue
        try:
            payload = extract_json_object_from_text(raw_text)
            replacements_raw = payload.get("replacements")
            if not isinstance(replacements_raw, list):
                raise ValueError("豆包返回 JSON 缺少 replacements 数组。")
            replacements = [str(item).strip() for item in replacements_raw]
            if len(replacements) != len(placeholders):
                raise ValueError(f"变量替换数量不匹配：需要 {len(placeholders)} 个，实际 {len(replacements)} 个。")

            prompt_text = render_cankao_template_by_replacements(
                template_text=template_text, replacements=replacements
            )
            return {"model": model, "prompt": prompt_text}
        except ValueError as exc:
            validation_feedback = str(exc)
            last_error = exc

    raise RuntimeError(f"豆包模板改写失败：{last_error or '未知错误'}")


def validate_detail_prompt_dish_consistency(dish_name: str, prompt_text: str) -> None:
    """拒绝细节图 prompt 仍残留其它菜的模板示例用语。"""
    stale_markers = ("仔姜爆猪颈肉", "猪颈肉", "仔姜爆", "金黄脆壳的猪")
    dish_name = dish_name.strip()
    if not dish_name:
        return
    for marker in stale_markers:
        if marker not in prompt_text:
            continue
        if marker in dish_name or dish_name in prompt_text:
            continue
        raise ValueError(
            f"细节图 prompt 仍含模板示例「{marker}」，与当前菜「{dish_name}」不符，请严格依据参考海报重写。"
        )
    core_name = dish_name.replace(" ", "")
    if core_name and core_name not in prompt_text.replace(" ", ""):
        dish_keyword = core_name[: min(2, len(core_name))]
        if dish_keyword and dish_keyword not in prompt_text:
            raise ValueError(
                f"细节图 prompt 未体现当前菜「{dish_name}」，请严格依据参考海报中的同一道菜重写。"
            )


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

    multi_ingredient_hint = ""
    if "细节" in stage_name:
        multi_ingredient_hint = infer_detail_multi_ingredient_guidance(dish_name)

    system_prompt = append_eating_action_guidance(
        """
你是抖音美食图文视觉策划。请结合参考图片，只为模板变量位提供替换值。
要求：
1) 不得改写模板固定文本，只输出 replacements 数组。
2) replacements 顺序必须与变量位从上到下完全一致。
3) 只输出 JSON：{"replacements":["值1","值2",...]}。
4) 变量位含“海报图”时，用一句话描述参考海报中的菜品视觉，不要写“见上图”。
5) 变量位含“豆包生成的气泡话语”时，必须使用用户提供的已定稿气泡文案。
6) 每个变量位形如 {标签，示例}，必须结合菜名、补充说明与「参考海报图」生成，严禁照抄模板示例中的其它菜名或食材。
7) 所有 replacements 必须描述参考海报中的同一道菜，不得混入模板示例菜。
8) 变量位含“细节特写”时，若菜名含多种主食材，三宫格须分别展示不同主食材，不得三格只拍一种。
""".strip(),
        placeholders,
    )
    if multi_ingredient_hint:
        system_prompt = f"{system_prompt}\n\n{multi_ingredient_hint}"

    placeholder_lines = "\n".join(f"{index + 1}. {placeholder}" for index, placeholder in enumerate(placeholders))
    bubble_block = f"\n气泡台词（原样填入）：{bubble_text}" if bubble_text.strip() else ""
    multi_block = f"\n\n{multi_ingredient_hint}" if multi_ingredient_hint else ""
    user_prompt = f"""
菜名：{dish_name}
补充说明：{notes or "无"}{bubble_block}{multi_block}

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
        is_character_ref = "juese" in image_path.name.lower()
        if is_character_ref:
            label = "角色参考图（仅 VI 人物造型；菜品外观必须以参考海报图为准）"
        else:
            label = "参考海报图（细节图文案必须与本图菜品完全一致）"
        user_content.append({"type": "text", "text": label})
        user_content.append({"type": "image_url", "image_url": {"url": encode_image_as_data_url(image_path)}})

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 5)
    last_error: Exception | None = None
    validation_feedback = ""
    for attempt in range(1, max_retry + 1):
        attempt_content = list(user_content)
        if validation_feedback:
            attempt_content[0] = {
                "type": "text",
                "text": user_prompt + f"\n\n上次输出不合格：{validation_feedback}\n请严格按参考海报图重写 replacements。",
            }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": attempt_content},
                ],
                max_tokens=2600,
                temperature=temperature,
            )
        except Exception as exc:
            last_error = exc
            error_kind = classify_text_api_error(exc)
            if is_retriable_text_api_error(exc) and attempt < max_retry:
                delay = api_retry_delay_seconds(attempt, error_kind=error_kind)
                print(f"{stage_name}第 {attempt} 次失败（{error_kind}），{delay:.0f}s 后重试…")
                sleep_with_cancel(delay)
                continue
            break

        raw_text = extract_chat_text_output(response).strip()
        if not raw_text:
            validation_feedback = f"{stage_name}未返回有效内容。"
            last_error = ValueError(validation_feedback)
            continue
        try:
            payload = extract_json_object_from_text(raw_text)
            replacements_raw = payload.get("replacements")
            if not isinstance(replacements_raw, list):
                raise ValueError(f"{stage_name}返回 JSON 缺少 replacements 数组。")
            replacements = [str(item).strip() for item in replacements_raw]
            if len(replacements) != len(placeholders):
                raise ValueError(
                    f"{stage_name}变量替换数量不匹配：需要 {len(placeholders)} 个，实际 {len(replacements)} 个。"
                )

            for index, placeholder in enumerate(placeholders):
                if "豆包生成的气泡话语" in placeholder and bubble_text.strip():
                    replacements[index] = bubble_text.strip()

            prompt_text = render_cankao_template_by_replacements(template_text=template_text, replacements=replacements)
            validate_detail_prompt_dish_consistency(dish_name, prompt_text)
            return {"model": model, "prompt": prompt_text}
        except ValueError as exc:
            validation_feedback = str(exc)
            last_error = exc

    raise RuntimeError(f"{stage_name}失败：{last_error or '未知错误'}")


BUBBLE_COPY_MIN_CHARS = 5
BUBBLE_COPY_MAX_CHARS = 12


def iter_bubble_copy_source_files() -> list[Path]:
    files: list[Path] = []
    for root in (DISH_POOL_DIR, DISH_ARCHIVE_DIR, OUTPUT_DIR):
        if not root.exists():
            continue
        for path in root.rglob(f"*{BUBBLE_COPY_FILE_SUFFIX}"):
            if path.is_file():
                files.append(path)
    return sorted(files, key=lambda path: path.stat().st_mtime)


def sync_bubble_copy_history_file(*, exclude_normalized: set[str] | None = None) -> list[str]:
    """扫描菜品池/归档/output 下所有气泡文案 txt，去重后写入持久化历史文件。"""
    exclude_normalized = exclude_normalized or set()
    ordered: list[str] = []
    seen_norm: set[str] = set()
    source_count = 0

    def add_text(raw_text: str) -> None:
        normalized = normalize_bubble_copy_text(raw_text)
        if not normalized or normalized in exclude_normalized or normalized in seen_norm:
            return
        seen_norm.add(normalized)
        ordered.append(normalized)

    for path in iter_bubble_copy_source_files():
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content:
            continue
        source_count += 1
        add_text(content)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_lines = [
        "# 历史气泡文案（自动汇总，细节图生成前刷新）",
        f"# 来源文件数：{source_count}",
        f"# 去重条目数：{len(ordered)}",
        f"# 更新：{timestamp}",
        "",
    ]
    BUBBLE_COPY_HISTORY_FILE.write_text(
        "\n".join(header_lines + ordered) + ("\n" if ordered else ""),
        encoding="utf-8",
    )
    print(
        f"气泡文案历史已同步：扫描 {source_count} 个源文件，"
        f"去重后 {len(ordered)} 条 -> {BUBBLE_COPY_HISTORY_FILE}"
    )
    return ordered


def is_bubble_copy_in_history(text: str, history_texts: list[str]) -> bool:
    normalized = normalize_bubble_copy_text(text)
    if not normalized:
        return False
    history_norm = {normalize_bubble_copy_text(item) for item in history_texts if item.strip()}
    return normalized in history_norm


def normalize_bubble_copy_text(raw_text: str) -> str:
    text = raw_text.strip()
    text = re.sub(r"^[「『\"'“”]+", "", text)
    text = re.sub(r"[」』\"'“”]+$", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip("，。！？!?.、；;：:")


def count_bubble_copy_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def validate_bubble_copy_text(text: str) -> str:
    normalized = normalize_bubble_copy_text(text)
    if not normalized:
        raise ValueError("气泡文案为空。")
    char_count = count_bubble_copy_chars(normalized)
    if char_count < BUBBLE_COPY_MIN_CHARS or char_count > BUBBLE_COPY_MAX_CHARS:
        raise ValueError(
            f"气泡文案须 {BUBBLE_COPY_MIN_CHARS}–{BUBBLE_COPY_MAX_CHARS} 个汉字，当前 {char_count} 个。"
        )
    return normalized


def build_bubble_copy_prompt(
    *,
    dish_name: str,
    retry_feedback: str = "",
    history_texts: list[str] | None = None,
) -> str:
    dish_line = f"菜名：{dish_name.strip()}。" if dish_name.strip() else ""
    feedback_block = f"（{retry_feedback}）" if retry_feedback.strip() else ""
    history_block = ""
    if history_texts:
        total = len(history_texts)
        shown = history_texts[-BUBBLE_COPY_HISTORY_PROMPT_LIMIT:]
        if total > len(shown):
            scope_note = f"（共 {total} 条，以下列最近 {len(shown)} 条）"
        else:
            scope_note = f"（共 {total} 条）"
        lines = "\n".join(f"- {text}" for text in shown)
        history_block = (
            f"\n\n以下历史气泡文案均已使用过{scope_note}，"
            f"禁止重复、仅改几个字或同义改写，必须写出全新句式：\n{lines}"
        )
    return (
        f"{dish_line}上图是这道菜的海报，另有阿叶厨师角色参考。"
        f"写他在品尝这道菜时，气泡里最能勾起食欲的一句话——他是说话的人，不是菜。"
        f"{BUBBLE_COPY_MIN_CHARS}–{BUBBLE_COPY_MAX_CHARS} 个汉字，只输出这一句。"
        f"{history_block}{feedback_block}"
    )


def generate_poster_bubble_copy(
    client: OpenAI,
    poster_image_path: Path,
    *,
    dish_name: str = "",
    current_bubble_file: Path | str | None = None,
) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    if not poster_image_path.exists():
        raise FileNotFoundError(f"海报图不存在：{poster_image_path}")

    exclude_normalized: set[str] = set()
    if current_bubble_file:
        bubble_path = Path(current_bubble_file)
        if bubble_path.exists():
            try:
                existing = normalize_bubble_copy_text(bubble_path.read_text(encoding="utf-8"))
                if existing:
                    exclude_normalized.add(existing)
            except OSError:
                pass

    history_texts = sync_bubble_copy_history_file(exclude_normalized=exclude_normalized)

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 3)
    last_error = ""
    last_model = model
    for attempt in range(1, max_retry + 1):
        retry_feedback = last_error if attempt > 1 else ""
        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": build_bubble_copy_prompt(
                    dish_name=dish_name,
                    retry_feedback=retry_feedback,
                    history_texts=history_texts,
                ),
            },
            {"type": "image_url", "image_url": {"url": encode_image_as_data_url(poster_image_path)}},
        ]
        if CHARACTER_REFERENCE_FILE.exists():
            user_content.append(
                {"type": "image_url", "image_url": {"url": encode_image_as_data_url(CHARACTER_REFERENCE_FILE)}}
            )
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=80,
                temperature=temperature,
            )
            last_model = model
        except Exception as exc:
            last_error = str(exc)
            error_kind = classify_text_api_error(exc)
            if is_retriable_text_api_error(exc) and attempt < max_retry:
                delay = api_retry_delay_seconds(attempt, error_kind=error_kind)
                print(f"气泡文案第 {attempt} 次失败（{error_kind}），{delay:.0f}s 后重试…")
                sleep_with_cancel(delay)
            continue

        raw_content = extract_chat_text_output(response).strip()
        if not raw_content:
            last_error = "豆包未返回有效气泡文案。"
            continue
        try:
            content = validate_bubble_copy_text(raw_content)
            if is_bubble_copy_in_history(content, history_texts):
                last_error = f"与历史气泡文案重复：{content}"
                continue
            return {"model": last_model, "content": content}
        except ValueError as exc:
            last_error = str(exc)

    raise RuntimeError(f"气泡文案生成失败：{last_error or '多次输出字数不合规。'}")


class AllCandidatesDefectiveError(RuntimeError):
    """所有候选图均被质量审核判定为明显缺陷。"""

    def __init__(self, message: str, *, rejected: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.rejected = list(rejected or [])


DETAIL_MAIN_INGREDIENT_TOKENS: tuple[str, ...] = (
    "鳝段",
    "鳝鱼",
    "黑鱼片",
    "鱼片",
    "嫩鱼",
    "鱼块",
    "鱼头",
    "鱼尾",
    "牛肉",
    "羊肉",
    "猪肉",
    "鸡肉",
    "鸡腿",
    "鸭",
    "虾",
    "蟹",
    "排骨",
    "肥肠",
    "肚丝",
    "豆干",
    "豆腐",
)


def infer_detail_multi_ingredient_guidance(dish_name: str) -> str:
    """菜名含多种主食材时，要求细节图三宫格分别展示。"""
    name = dish_name.replace(" ", "").strip()
    if not name:
        return ""
    found: list[str] = []
    for token in DETAIL_MAIN_INGREDIENT_TOKENS:
        if token not in name:
            continue
        if token == "鱼" and any(part in name for part in ("鱼片", "嫩鱼", "鳝段", "鳝鱼", "鱼头", "鱼尾", "鱼块")):
            continue
        if any(token != existing and token in existing for existing in found):
            continue
        if token not in found:
            found.append(token)
    if len(found) < 2:
        return ""
    joined = "、".join(found)
    return (
        f"菜名「{dish_name}」含多种主食材（{joined}）。"
        "下半部分三宫格须分别展示不同主食材：至少两格各用一种主食材特写，"
        "禁止三格全部只拍同一种主料；须与参考海报中的食材一致。"
    )


def filter_defective_publish_candidates(
    client: OpenAI,
    image_paths: list[Path],
    *,
    image_kind: str,
) -> tuple[list[Path], list[dict[str, Any]]]:
    """豆包视觉审核：剔除悬空手/筷、肢体断开等一眼可辨的错误图。"""
    if not image_paths:
        return [], []

    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"你是图文质量审核员。请逐一检查下面 {len(image_paths)} 张{image_kind}候选图，"
                "判断是否存在**一眼可辨的明显生成错误**，例如："
                "手/筷子/勺子/食材悬空无依托、肢体或餐具断开漂浮、"
                "多指/畸形手、文字乱码、容器与食材比例严重失真。"
                "轻微风格化、景深模糊不算缺陷。"
                "只输出 JSON："
                '{"reviews":[{"index":1,"acceptable":true,"reason":""},...]}，'
                "index 从 1 开始；acceptable=false 时 reason 必填（一句话）。"
            ),
        }
    ]
    for index, image_path in enumerate(image_paths, start=1):
        user_content.append({"type": "text", "text": f"候选{index}：{image_path.name}"})
        user_content.append({"type": "image_url", "image_url": {"url": encode_image_as_data_url(image_path)}})

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=800,
        temperature=0,
    )
    raw_text = extract_chat_text_output(response).strip()
    if not raw_text:
        return list(image_paths), []

    try:
        payload = extract_json_object_from_text(raw_text)
    except Exception as exc:
        print(f"{image_kind}缺陷审核 JSON 解析失败，保留全部候选：{exc}")
        return list(image_paths), []

    reviews_raw = payload.get("reviews")
    if not isinstance(reviews_raw, list):
        return list(image_paths), []

    review_by_index: dict[int, dict[str, Any]] = {}
    for item in reviews_raw:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(image_paths):
            review_by_index[index] = item

    acceptable: list[Path] = []
    rejected: list[dict[str, Any]] = []
    for index, image_path in enumerate(image_paths, start=1):
        review = review_by_index.get(index, {})
        acceptable_flag = bool(review.get("acceptable", True))
        reason = str(review.get("reason", "")).strip()
        if acceptable_flag:
            acceptable.append(image_path)
        else:
            rejected.append(
                {
                    "index": index,
                    "acceptable": False,
                    "reason": reason or "明显生成缺陷",
                    "image_name": image_path.name,
                }
            )
            print(f"剔除{image_kind}候选{index}（{image_path.name}）：{reason or '明显生成缺陷'}")
    return acceptable, rejected


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
                "优先选择无悬空手/筷、无漂浮餐具、无肢体畸形、构图完整的图片。"
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
    skip_defect_filter: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    paths = [Path(path) for path in candidate_paths if str(path).strip() and Path(path).exists()]
    if not paths:
        return "", "", {}

    publish_dir.mkdir(parents=True, exist_ok=True)
    pool = paths
    rejected: list[dict[str, Any]] = []
    if not skip_defect_filter:
        try:
            pool, rejected = filter_defective_publish_candidates(client, pool, image_kind=image_kind)
        except Exception as exc:
            print(f"{image_kind}缺陷审核失败，继续原候选：{exc}")
            pool = paths

    if not pool:
        raise AllCandidatesDefectiveError(
            f"{image_kind}候选均含明显生成缺陷，已全部剔除（共 {len(rejected)} 张）。",
            rejected=rejected,
        )

    if len(pool) == 1:
        selected = move_image_to_publish(str(pool[0]), publish_dir)
        result = {
            "auto_selected": True,
            "winner_index": 1,
            "winner_image_name": pool[0].name,
            "winner_reason": f"仅 1 张合格{image_kind}，直接入 publish。",
            "rejected_candidates": rejected,
        }
        if rejected:
            save_text_output(
                json.dumps({"rejected_candidates": rejected}, ensure_ascii=False, indent=2),
                publish_dir / selection_report_name.replace(".json", "_剔除.json"),
            )
        print(f"{image_kind}数量=1，直接入 publish：{selected}")
        return selected, "direct", result

    try:
        selection_result = select_douyin_publish_image(client, pool, image_kind=image_kind)
        if rejected:
            selection_result["rejected_candidates"] = rejected
        winner_index = int(selection_result.get("winner_index", 1))
        winner_path = pool[max(0, min(winner_index - 1, len(pool) - 1))]
        selected = move_image_to_publish(str(winner_path), publish_dir)
        save_text_output(
            json.dumps(selection_result, ensure_ascii=False, indent=2),
            publish_dir / selection_report_name,
        )
        if rejected:
            save_text_output(
                json.dumps({"rejected_candidates": rejected}, ensure_ascii=False, indent=2),
                publish_dir / selection_report_name.replace(".json", "_剔除.json"),
            )
        print(
            f"{image_kind}筛选完成：{selected}，理由：{selection_result.get('winner_reason', '')}"
        )
        return selected, "scored", selection_result
    except Exception as select_exc:
        selected = move_image_to_publish(str(pool[0]), publish_dir)
        fallback_result = {
            "auto_selected": False,
            "winner_index": 1,
            "winner_image_name": pool[0].name,
            "winner_reason": f"筛选失败，回退首图：{select_exc}",
            "rejected_candidates": rejected,
        }
        print(f"{image_kind}筛选失败，回退直通首图：{selected}")
        return selected, "fallback_direct", fallback_result


def snapshot_mode2_image_settings() -> dict[str, dict[str, Any]]:
    return {group: get_mode2_group_settings(group) for group in MODE2_GROUP_KEYS}


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
        "provider": base["provider"],
        "aspect_ratio": base["aspect_ratio"],
        "resolution_tier": base["resolution_tier"],
        "template_file": base["template_file"],
        "model": base["model"],
        "size": base["size"],
        "quality": quality,
        "image_count": image_count,
    }


def augment_prompt_with_reference_labels(prompt_text: str, reference_paths: list[Path]) -> str:
    if len(reference_paths) <= 1:
        return prompt_text
    labels: list[str] = []
    for index, path in enumerate(reference_paths, start=1):
        if "juese" in path.name.lower():
            labels.append(
                f"第{index}张参考图为固定VI男性厨师角色造型表（{path.name}），"
                "人物脸型、短发、白色厨师服与酒红色围裙必须与此图完全一致，严禁替换为其他人物。"
            )
        else:
            labels.append(
                f"第{index}张参考图为海报原图（{path.name}），"
                "菜品外观、餐具与餐桌环境必须与此图完全一致。"
            )
    return f"{prompt_text.rstrip()}\n\n【参考图说明】{' '.join(labels)}"


def prepare_reference_image_for_edit_api(image_path: Path) -> tuple[str, bytes, str]:
    """images.edit 参考图默认原样上传（官方 multipart 单图上限 50 MiB）。"""
    raw = image_path.read_bytes()
    guessed_mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    max_bytes = parse_int_env("EDIT_REF_IMAGE_MAX_BYTES", 50 * 1024 * 1024)
    if len(raw) <= max_bytes:
        return image_path.name, raw, guessed_mime
    compressed, mime = _compress_image_to_byte_limit(
        image_path,
        max_bytes=max_bytes,
        log_prefix="参考图",
    )
    stem = image_path.stem or "ref"
    return f"{stem}.jpg", compressed, mime


def open_reference_image_uploads(reference_paths: list[Path]) -> tuple[list[Any], list[Any]]:
    uploads: list[Any] = []
    handles: list[Any] = []
    max_bytes = parse_int_env("EDIT_REF_IMAGE_MAX_BYTES", 50 * 1024 * 1024)
    for path in reference_paths:
        if path.stat().st_size <= max_bytes:
            handle = open(path, "rb")
            handles.append(handle)
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            uploads.append((path.name, handle, mime_type))
            continue
        filename, data, mime_type = prepare_reference_image_for_edit_api(path)
        print(
            f"参考图超限已压缩：{path.name} ({path.stat().st_size / (1024 * 1024):.1f} MiB) -> "
            f"{filename} ({len(data) / 1024:.0f} KB)"
        )
        buffer = BytesIO(data)
        handles.append(buffer)
        uploads.append((filename, buffer, mime_type))
    return uploads, handles


def generate_images_from_references(
    client: OpenAI,
    prompt_text: str,
    reference_paths: list[str | Path],
    settings: dict[str, Any],
) -> list[dict[str, str]]:
    valid_paths = [Path(path) for path in reference_paths if str(path).strip() and Path(path).exists()]
    if valid_paths:
        print(
            "参考图生图输入："
            + "，".join(f"{path.name}({path.resolve()})" for path in valid_paths)
            + f"；quality={settings.get('quality')}，n={settings.get('image_count')}"
        )
    if not valid_paths:
        return generate_images_by_prompt(client=client, prompt_text=prompt_text, settings=settings)

    request_timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)
    request_prompt = augment_prompt_with_reference_labels(prompt_text, valid_paths)
    model_name = str(settings.get("model", "")).strip().lower()

    def call_edit_api(batch_n: int) -> Any:
        file_handles: list[Any] = []
        try:
            uploads, file_handles = open_reference_image_uploads(valid_paths)
            image_arg: Any = uploads[0] if len(uploads) == 1 else uploads
            edit_kwargs: dict[str, Any] = {
                "model": settings["model"],
                "image": image_arg,
                "prompt": request_prompt,
                "size": settings["size"],
                "quality": settings["quality"],
                "n": batch_n,
                "timeout": request_timeout,
            }
            if "gpt-image-2" not in model_name:
                edit_kwargs["input_fidelity"] = "high"
            return client.images.edit(**edit_kwargs)
        finally:
            for file_obj in file_handles:
                file_obj.close()

    request_label = format_image_generation_request_label(
        settings,
        mode="images.edit",
        reference_paths=valid_paths,
    )
    return execute_image_generation_with_retries(
        stage_label="参考图生图",
        failure_prefix="参考图生图",
        settings=settings,
        request_label=request_label,
        call_api=call_edit_api,
    )


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
    from image_gen_profile import image_gen_controls_from_mapping

    ensure_runtime_config_loaded()
    sync_v2_openai_image_settings()
    controls = image_gen_controls_from_mapping({})
    _, _, model = resolve_image_api_credentials(controls["provider"])
    return {
        "provider": controls["provider"],
        "aspect_ratio": controls["aspect_ratio"],
        "resolution_tier": controls["resolution_tier"],
        "template_file": controls["template_file"],
        "model": model,
        "size": parse_image_size(controls["size"]),
        "quality": controls["quality"],
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


def normalize_dish_name_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "").strip())


def collect_dish_pool_dirs() -> list[Path]:
    dirs: list[Path] = []
    if OUTPUT_DIR.exists():
        dirs.extend(path for path in OUTPUT_DIR.iterdir() if path.is_dir())
    if DISH_POOL_DIR.exists():
        for batch_dir in DISH_POOL_DIR.iterdir():
            if not batch_dir.is_dir() or not batch_dir.name.endswith("_batch"):
                continue
            dirs.extend(path for path in batch_dir.iterdir() if path.is_dir())
    return dirs


def resolve_dish_name_for_folder(folder: Path) -> str:
    try:
        payload = load_dish_idea_record_from_dir(folder)
        name = str(payload.get("dish_name", "")).strip()
        if name:
            return name
    except Exception:
        pass
    meta_file = folder / "_run_meta.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            name = str(meta.get("dish_name", "")).strip()
            if name:
                return name
        except Exception:
            pass
    return infer_dish_name_from_folder(folder.name)


def _remove_dish_favorite_entry(folder: Path) -> None:
    if not FAVORITES_FILE.exists():
        return
    try:
        payload = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return
    key = str(folder.resolve())
    if key not in paths:
        return
    paths.pop(key, None)
    FAVORITES_FILE.write_text(
        json.dumps({"paths": paths}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_archive_destination(folder: Path) -> Path:
    folder = folder.resolve()
    dish_pool_root = DISH_POOL_DIR.resolve()
    output_root = OUTPUT_DIR.resolve()
    if dish_pool_root in folder.parents:
        relative = folder.relative_to(dish_pool_root)
        return DISH_ARCHIVE_DIR / "dish_pool" / relative
    if output_root in folder.parents:
        relative = folder.relative_to(output_root)
        return DISH_ARCHIVE_DIR / "output" / relative
    raise ValueError(f"只能归档 output 或 dish_pool 下的菜品目录：{folder}")


def archive_dish_folder(folder: Path) -> Path:
    folder = folder.resolve()
    if folder in {OUTPUT_DIR.resolve(), DISH_POOL_DIR.resolve()}:
        raise ValueError("不能移出根目录。")
    destination = _build_archive_destination(folder)
    if destination.exists():
        destination = destination.parent / f"{destination.name}_{get_timestamp()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(destination))
    _remove_dish_favorite_entry(folder)
    return destination


def find_duplicate_dish_folders(dish_name: str, *, keep_dir: Path) -> list[Path]:
    key = normalize_dish_name_key(dish_name)
    if not key:
        return []
    keep_resolved = keep_dir.resolve()
    duplicates: list[Path] = []
    for folder in collect_dish_pool_dirs():
        resolved = folder.resolve()
        if resolved == keep_resolved:
            continue
        if normalize_dish_name_key(resolve_dish_name_for_folder(folder)) != key:
            continue
        duplicates.append(folder)
    return duplicates


def dedupe_archive_duplicate_dish_folders(dish_name: str, *, keep_dir: str | Path) -> list[str]:
    """同名菜品只保留 keep_dir，其余目录移入 dish_archive。"""
    keep_path = Path(keep_dir).resolve()
    archived_paths: list[str] = []
    for folder in find_duplicate_dish_folders(dish_name, keep_dir=keep_path):
        try:
            destination = archive_dish_folder(folder)
            archived_paths.append(str(destination))
            print(f"菜品去重：已移出旧目录 {folder.name} -> {destination}")
        except Exception as exc:
            print(f"菜品去重：移出失败 {folder}：{exc}")
    return archived_paths


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


def load_dish_idea_record_from_dir(output_dir: Path) -> dict[str, str]:
    """从已有输出目录读取造菜信息 txt，供「指定造菜」复用同一文件夹。"""
    if not output_dir.exists() or not output_dir.is_dir():
        raise FileNotFoundError(f"输出目录不存在：{output_dir}")
    record_files = sorted(output_dir.glob("*_造菜信息.txt"))
    if not record_files:
        raise FileNotFoundError(f"目录内未找到造菜信息文件：{output_dir}")
    record_file = record_files[0]
    parsed: dict[str, str] = {}
    for raw_line in record_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or "：" not in line:
            continue
        key, _, value = line.partition("：")
        parsed[key.strip()] = value.strip()
    dish_name = parsed.get("菜名", "").strip() or infer_dish_name_from_folder(output_dir.name)
    notes = parsed.get("菜名描述", "").strip()
    if notes == "无":
        notes = ""
    return {
        "dish_name": dish_name,
        "notes": notes,
        "region_label": parsed.get("参考菜系", "").strip(),
        "reference_dish": parsed.get("参考菜品", "").strip(),
        "record_file": str(record_file),
    }


def infer_dish_name_from_folder(folder_name: str) -> str:
    import re

    matched = re.match(r"^\d+_(.+)$", folder_name)
    if matched:
        return matched.group(1).strip()
    parts = folder_name.split("_", 2)
    if len(parts) >= 3 and parts[2].strip():
        return parts[2].strip()
    if len(parts) >= 2 and parts[-1].strip():
        return parts[-1].strip()
    return folder_name.strip()


PUBLISH_TITLE_MAX_UNITS = 40
XIAOHONGSHU_TITLE_MAX_UNITS = PUBLISH_TITLE_MAX_UNITS


def count_publish_title_units(text: str) -> int:
    """各平台标题计字：汉字/表情等非 ASCII 计 2，英文/数字等 ASCII 计 1，上限 40（约 20 个汉字）。"""
    units = 0
    for char in text:
        units += 2 if ord(char) > 127 else 1
    return units


def count_xiaohongshu_title_units(text: str) -> int:
    return count_publish_title_units(text)


def truncate_publish_title(text: str, max_units: int = PUBLISH_TITLE_MAX_UNITS) -> str:
    units = 0
    parts: list[str] = []
    for char in text:
        char_units = 2 if ord(char) > 127 else 1
        if units + char_units > max_units:
            break
        units += char_units
        parts.append(char)
    return "".join(parts).strip()


def normalize_publish_title(title: str, *, platform_key: str = "") -> str:
    text = str(title or "").strip()
    if not text:
        label = platform_key or "平台"
        raise ValueError(f"{label} 标题为空。")
    units = count_publish_title_units(text)
    if units <= PUBLISH_TITLE_MAX_UNITS:
        return text
    truncated = truncate_publish_title(text, PUBLISH_TITLE_MAX_UNITS)
    if not truncated:
        raise ValueError(
            f"{platform_key or '平台'} 标题超过 20 个汉字（40 字符，表情也算）上限，"
            f"当前约 {units} 字符且无法截断：{text}"
        )
    print(
        f"警告：{platform_key or '平台'} 标题超过 40 字符（含表情），"
        f"已由 {units} 自动截断为 {count_publish_title_units(truncated)}：{truncated}"
    )
    return truncated


def normalize_xiaohongshu_title(title: str) -> str:
    return normalize_publish_title(title, platform_key="xiaohongshu")


V2_PUBLISH_PLATFORM_SPECS: tuple[tuple[str, str, int], ...] = (
    ("douyin", "抖音", 5),
    ("xiaohongshu", "小红书", 10),
    ("weixin_mp", "微信公众号", 10),
    ("weixin_channels", "微信视频号", 30),
    ("kuaishou", "快手", 4),
)

_TITLE_LIMIT_HINT = (
    "标题必须不超过 20 个汉字（40 字符上限；汉字/表情/符号等非 ASCII 计 2、英文/数字计 1，表情也算字符）。"
)

_TITLE_CLICHE_FORBIDDEN_HINT = (
    "标题禁止「几碗饭/连吃几碗/连炫几碗」类套话（如「三碗饭」「连炫三碗」「连吃三碗」「能干两碗」等）；"
    "勿用数量+碗/碗饭作钩子，换写口感、做法亮点、场景或反差，各平台标题句式要有变化。"
)

_FORBIDDEN_TITLE_CLICHE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"连[吃炫馋扒撬]"), "连吃/连炫几碗类套话"),
    (re.compile(r"[吃炫干馋][一二三四五六七八九十百千万两\d]+碗"), "吃/炫N碗类套话"),
    (re.compile(r"[一二三四五六七八九十百千万两\d]+碗饭"), "N碗饭类套话"),
)


def reject_cliche_bowl_title(title: str) -> str | None:
    text = str(title or "").strip()
    if not text:
        return None
    for pattern, label in _FORBIDDEN_TITLE_CLICHE_PATTERNS:
        if pattern.search(text):
            return (
                f"标题含禁用{label}（如「三碗饭」「连炫三碗」「连吃三碗」），"
                f"请改写：{text}"
            )
    return None

V2_PUBLISH_PLATFORM_TASKS: dict[str, str] = {
    "douyin": (
        f"为这个新菜写抖音的标题、描述和正好 5 个话题（不超过 5 个）。{_TITLE_LIMIT_HINT}"
        f"{_TITLE_CLICHE_FORBIDDEN_HINT}要有钩子，符合抖音爆款思路。"
    ),
    "xiaohongshu": (
        f"为这个新菜写小红书的标题、描述和 10 个话题。{_TITLE_LIMIT_HINT}"
        f"{_TITLE_CLICHE_FORBIDDEN_HINT}要有钩子，符合小红书图文用户的爆款思路。"
    ),
    "weixin_mp": (
        f"为这个新菜写微信公众号的标题、描述和正好 10 个话题（不超过 10 个）。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}要有钩子，符合公众号图文用户的阅读习惯。"
    ),
    "weixin_channels": (
        f"为这个新菜写微信视频号的标题、描述和正好 30 个话题（不超过 30 个）。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}要有钩子，符合视频号图文传播特点。"
    ),
    "kuaishou": (
        f"为这个新菜写快手的标题、描述和 4 个话题。{_TITLE_LIMIT_HINT}"
        f"{_TITLE_CLICHE_FORBIDDEN_HINT}要有钩子，符合快手爆款思路。"
    ),
}


def build_v2_publish_context_text(dish_payload: dict[str, str]) -> str:
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    notes = str(dish_payload.get("notes", "")).strip()
    region_label = str(dish_payload.get("region_label", "")).strip()
    reference_dish = str(dish_payload.get("reference_dish", "")).strip()
    lines = [f"菜名：{dish_name}"]
    if notes:
        lines.append(f"做法与菜品描述：{notes}")
    if region_label:
        lines.append(f"参考菜系：{region_label}")
    if reference_dish:
        lines.append(f"参考传统菜：{reference_dish}")
    return "\n".join(lines)


def build_v2_publish_multimodal_prompt(dish_payload: dict[str, str]) -> str:
    platform_lines = "\n".join(
        f"- {label}：{V2_PUBLISH_PLATFORM_TASKS[key]}" for key, label, _count in V2_PUBLISH_PLATFORM_SPECS
    )
    return f"""你是多平台美食图文运营。请结合附件里的「入选海报图」和下方菜品信息，为同一道菜写各平台发布文案。

{build_v2_publish_context_text(dish_payload)}

各平台写作要求：
{platform_lines}

通用要求：
1) 标题、描述都要口语化、有食欲、有画面感，禁止套话（如“先收藏”“原创融合”“想吃时照着做”）。
2) 各平台标题禁止「几碗饭/连吃几碗/连炫几碗」类句式（如「三碗饭」「连炫三碗」「连吃三碗」）；勿用数量+碗作钩子，句式要有变化。
3) 描述 2–4 句，写口感、场景、做法亮点，可自然提菜名，但不要写成说明书。
4) topics 数组每项以 # 开头，不要菜品全名话题，不要 #阿叶造新菜。
5) 各平台 title 均必须不超过 20 个汉字（40 字符；汉字/表情/符号等非 ASCII 计 2、英文/数字计 1，表情也算字符），超出会被截断或判不合格。
6) topics 数组长度必须与各平台要求完全一致：抖音 5、小红书 10、微信公众号 10、微信视频号 30、快手 4；不能合并公众号与视频号。
7) 只输出 JSON，不要 Markdown，不要解释。格式如下：
{{
  "douyin": {{"title": "...", "description": "...", "topics": ["#...", "..."]}},
  "xiaohongshu": {{"title": "...", "description": "...", "topics": ["#...", "..."]}},
  "weixin_mp": {{"title": "...", "description": "...", "topics": ["#...", "..."]}},
  "weixin_channels": {{"title": "...", "description": "...", "topics": ["#...", "..."]}},
  "kuaishou": {{"title": "...", "description": "...", "topics": ["#...", "..."]}}
}}"""


def normalize_v2_publish_topics(raw_topics: Any, expected_count: int) -> list[str]:
    from image_generator import format_topic_tag

    if not isinstance(raw_topics, list):
        raw_topics = re.findall(r"#[^\s#]+", str(raw_topics))
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_topics:
        tag = format_topic_tag(str(item).strip())
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= expected_count:
            break
    if len(tags) != expected_count:
        raise ValueError(f"话题数量必须为 {expected_count} 个，实际 {len(tags)} 个。")
    return tags


def parse_v2_publish_platform_payload(raw_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for platform_key, _label, topic_count in V2_PUBLISH_PLATFORM_SPECS:
        block = raw_payload.get(platform_key)
        if not isinstance(block, dict):
            raise ValueError(f"缺少平台字段：{platform_key}")
        title = normalize_publish_title(str(block.get("title", "")).strip(), platform_key=platform_key)
        cliche_error = reject_cliche_bowl_title(title)
        if cliche_error:
            raise ValueError(f"{platform_key} {cliche_error}")
        description = str(block.get("description", "")).strip()
        topics = normalize_v2_publish_topics(block.get("topics"), topic_count)
        if not title:
            raise ValueError(f"{platform_key} 标题为空。")
        if not description:
            raise ValueError(f"{platform_key} 描述为空。")
        normalized[platform_key] = {
            "title": title,
            "description": description,
            "topics": topics,
        }
    return normalized


def save_v2_publish_copy_files(
    *,
    output_dir: Path,
    timestamp: str,
    dish_name: str,
    platform_payload: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from image_generator import save_text_output as ig_save_text_output

    safe_name = sanitize_file_name(dish_name)
    douyin = platform_payload["douyin"]
    title_file = ig_save_text_output(
        content=douyin["title"],
        output_dir=output_dir,
        timestamp=timestamp,
        base_name=safe_name,
        suffix="_图文标题",
    )
    description_body_file = ig_save_text_output(
        content=douyin["description"],
        output_dir=output_dir,
        timestamp=timestamp,
        base_name=safe_name,
        suffix="_图文描述正文",
    )
    platform_topic_files: dict[str, str] = {}
    platform_description_files: dict[str, str] = {}
    for platform_key, platform_label, _count in V2_PUBLISH_PLATFORM_SPECS:
        block = platform_payload[platform_key]
        topic_line = " ".join(block["topics"])
        platform_topic_files[platform_key] = ig_save_text_output(
            content=topic_line,
            output_dir=output_dir,
            timestamp=timestamp,
            base_name=safe_name,
            suffix=f"_{platform_label}话题",
        )
        platform_description_files[platform_key] = ig_save_text_output(
            content=f"{block['description']}\n{topic_line}".strip(),
            output_dir=output_dir,
            timestamp=timestamp,
            base_name=safe_name,
            suffix=f"_{platform_label}图文描述",
        )
        ig_save_text_output(
            content=block["title"],
            output_dir=output_dir,
            timestamp=timestamp,
            base_name=safe_name,
            suffix=f"_{platform_label}标题",
        )
    return {
        "title": douyin["title"],
        "description": f"{douyin['description']}\n{' '.join(douyin['topics'])}".strip(),
        "description_body": douyin["description"],
        "title_file": title_file,
        "description_file": platform_description_files["douyin"],
        "description_body_file": description_body_file,
        "platform_topic_files": platform_topic_files,
        "platform_description_files": platform_description_files,
        "platform_payload": platform_payload,
    }


def generate_v2_publish_copy_assets(
    client: OpenAI,
    *,
    dish_name: str,
    notes: str,
    timestamp: str,
    output_dir: Path,
    dish_payload: dict[str, str],
    poster_image_path: Path,
) -> dict[str, Any]:
    if not poster_image_path.exists():
        raise FileNotFoundError(f"参考海报图不存在：{poster_image_path}")

    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    prompt_text = build_v2_publish_multimodal_prompt(dish_payload)
    prompt_file = output_dir / f"{dish_name}_平台文案生成prompt.txt"
    save_text_output(prompt_text, prompt_file)
    print(f"平台文案提示词已保存：{prompt_file}")

    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": prompt_text},
        {"type": "text", "text": "参考海报图（已入选 publish 的成品图）："},
        {"type": "image_url", "image_url": {"url": encode_image_as_data_url(poster_image_path)}},
    ]

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 3)
    last_error = ""
    for attempt in range(1, max_retry + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=4096,
                temperature=temperature,
            )
            raw_text = extract_chat_text_output(response).strip()
            if not raw_text:
                raise ValueError("豆包未返回平台文案。")
            payload = extract_json_object_from_text(raw_text)
            platform_payload = parse_v2_publish_platform_payload(payload)
            saved = save_v2_publish_copy_files(
                output_dir=output_dir,
                timestamp=timestamp,
                dish_name=dish_name,
                platform_payload=platform_payload,
            )
            saved["model"] = model
            saved["prompt_file"] = str(prompt_file)
            print(f"图文标题已保存：{saved['title_file']}")
            print(f"图文描述正文已保存：{saved['description_body_file']}")
            for platform_key, platform_label, _count in V2_PUBLISH_PLATFORM_SPECS:
                print(f"{platform_label}话题已保存：{saved['platform_topic_files'][platform_key]}")
                print(f"{platform_label}图文描述已保存：{saved['platform_description_files'][platform_key]}")
            return saved
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            user_content[0] = {
                "type": "text",
                "text": prompt_text + f"\n\n上次输出不合格：{last_error}\n请严格按 JSON 格式重写。",
            }
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(f"平台文案生成失败：{last_error or '未知错误'}")


def persist_v2_dish_record(output_dir: Path, dish_payload: dict[str, str]) -> str:
    dish_record_file = save_dish_idea_record_file(output_dir, dish_payload)
    print(f"造菜信息已保存：{dish_record_file}")
    return dish_record_file


def persist_v2_publish_copy_assets(
    *,
    client: OpenAI,
    output_dir: Path,
    timestamp: str,
    dish_payload: dict[str, str],
    poster_image_path: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {"publish_copy_error": ""}
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    notes = str(dish_payload.get("notes", "")).strip()
    poster_path = Path(poster_image_path)
    if not poster_path.exists():
        result["publish_copy_error"] = f"参考图不存在：{poster_path}"
        return result

    try:
        publish_copy = generate_v2_publish_copy_assets(
            client=client,
            dish_name=dish_name,
            notes=notes,
            timestamp=timestamp,
            output_dir=output_dir,
            dish_payload=dish_payload,
            poster_image_path=poster_path,
        )
        result.update(
            {
                "publish_title_file": publish_copy.get("title_file", ""),
                "publish_description_file": publish_copy.get("description_file", ""),
                "publish_description_body_file": publish_copy.get("description_body_file", ""),
                "publish_platform_topic_files": publish_copy.get("platform_topic_files", {}),
                "publish_platform_description_files": publish_copy.get("platform_description_files", {}),
                "publish_copy_prompt_file": publish_copy.get("prompt_file", ""),
            }
        )
    except Exception as exc:
        result["publish_copy_error"] = str(exc)
        error_file = output_dir / f"{dish_name}_平台文案生成失败原因.txt"
        save_text_output(
            "平台发布文案（标题/话题/图文描述）生成失败。\n"
            f"失败原因：{exc}\n"
            "建议：检查豆包文本接口、参考海报图与网络后重试。",
            error_file,
        )
        print(f"平台文案生成失败，已写入说明：{error_file}")
    return result


def persist_v2_common_text_assets(
    *,
    client: OpenAI,
    output_dir: Path,
    timestamp: str,
    dish_payload: dict[str, str],
    poster_image_path: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["dish_idea_record_file"] = persist_v2_dish_record(output_dir, dish_payload)
    if poster_image_path.strip():
        publish_result = persist_v2_publish_copy_assets(
            client=client,
            output_dir=output_dir,
            timestamp=timestamp,
            dish_payload=dish_payload,
            poster_image_path=poster_image_path,
        )
        result.update(publish_result)
    else:
        result["publish_copy_error"] = ""
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
    request_timeout = parse_float_env("OPENAI_IMAGE_REQUEST_TIMEOUT_SECONDS", 900.0)

    def call_generate_api(batch_n: int) -> Any:
        return client.images.generate(
            model=settings["model"],
            prompt=prompt_text,
            size=settings["size"],
            quality=settings["quality"],
            n=batch_n,
            timeout=request_timeout,
        )

    request_label = format_image_generation_request_label(settings, mode="images.generate")
    return execute_image_generation_with_retries(
        stage_label="文生图",
        failure_prefix="生图",
        settings=settings,
        request_label=request_label,
        call_api=call_generate_api,
    )


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
