from __future__ import annotations

import base64
import difflib
import json
import random
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
from collections import Counter
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
HUASU_FORBIDDEN_FILE = CANKAO_DIR / "huasu.txt"
CHARACTER_REFERENCE_FILE = CANKAO_DIR / "juese.png"
OUTPUT_DIR = ROOT_DIR / "output"
DISH_POOL_DIR = ROOT_DIR / "dish_pool"
DISH_ARCHIVE_DIR = ROOT_DIR / "dish_archive"
FAVORITES_FILE = ROOT_DIR / "dish_favorites.json"
BUBBLE_COPY_HISTORY_FILE = ROOT_DIR / "bubble_copy_history.txt"
BUBBLE_COPY_FILE_SUFFIX = "_气泡文案.txt"
BUBBLE_COPY_HISTORY_PROMPT_LIMIT = 80
PUBLISH_COPY_HISTORY_FILE = ROOT_DIR / "publish_copy_history.txt"
PUBLISH_COPY_HISTORY_TITLE_PROMPT_LIMIT = 120
PUBLISH_COPY_HISTORY_DESC_PROMPT_LIMIT = 80
PUBLISH_COPY_SIMILARITY_RATIO = 0.78
PUBLISH_COPY_DESC_BODY_SUFFIX = "_图文描述正文.txt"
PUBLISH_COPY_DEFAULT_TEMPERATURE = 0.85
PUBLISH_COPY_CREATIVE_ANGLES: tuple[str, ...] = (
    "打工人下班想解馋、又不想点外卖，需要快手的硬菜",
    "周末家庭聚餐/来客，需要一道能撑场面、拍照也好看的菜",
    "追剧、看球、小酌时需要能边啃边吃的佐酒菜",
    "想给家常炸物换口味、吃腻普通蒜香排骨的人",
    "喜欢咸鲜口、对「虾酱+肉类」组合好奇的南方胃",
    "厨房新手想试新搭配、又怕翻车的谨慎型选手",
    "夏天没胃口、需要咸香开胃、提振食欲的一盘",
    "带娃家庭：大人小孩都能接受的非辣硬菜",
)
DISH_MEAL_TAGS_FILE = ROOT_DIR / "dish_meal_tags.json"
PUBLISH_PLAN_FILE = ROOT_DIR / "publish_plan.json"
MEAL_TAG_LABELS_CN: dict[str, str] = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "late_night": "夜宵",
}
PLAN_SLOT_TO_MEAL_TAG: dict[str, str] = {
    "morning": "breakfast",
    "noon": "lunch",
    "evening": "dinner",
}
_DISH_OCCASION_MARKERS: dict[str, tuple[str, ...]] = {
    "breakfast": (
        "早餐",
        "早茶",
        "brunch",
        "Brunch",
        "流心蛋",
        "蛋墩",
        "水波蛋",
        "太阳蛋",
        "溏心蛋",
        "吐司",
        "贝果",
        "三明治",
        "粥",
        "包子",
        "馒头",
        "豆浆",
        "燕麦",
        "可颂",
        "松饼",
        "华夫",
        "班尼迪克",
    ),
    "lunch": ("便当", "盖饭", "简餐", "工作餐", "午市", "一人食"),
    "dinner": ("家宴", "下饭", "炖", "煲", "砂锅", "宴客", "全家"),
    "late_night": ("夜宵", "烧烤", "烤串", "串串", "小龙虾", "下酒", "小酌", "深夜", "啤酒"),
}
_LATE_NIGHT_COPY_PATTERN = re.compile(
    r"夜宵|佐酒|冰啤|配酒|小酌|下酒|追剧看球|#夜宵|#佐酒"
)
_LATE_NIGHT_CREATIVE_MARKERS: tuple[str, ...] = ("小酌", "佐酒", "追剧", "看球")
_HUASU_DISH_CATEGORY_TAIL = re.compile(
    r"(菜|餐|料|食|小吃|甜品|火锅|烧烤|家常|快手|懒人|减脂|轻食|素食|日料|韩料|泰料|川菜|粤菜|湘菜|鲁菜|苏菜|浙菜|闽菜|徽菜|东北菜|西北菜|云南菜|贵州菜|广西菜|海南菜|新疆菜|西藏菜|内蒙古菜|宁夏菜|青海菜|甘肃菜|陕西菜|山西菜|河北菜|河南菜|山东菜|江苏菜|安徽菜|湖北菜|湖南菜|江西菜|福建菜|台湾菜|香港菜|澳门菜)天花板$"
)
_FORBIDDEN_COPY_REGEX: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"好吃到"), "好吃到…夸张套话"),
    (re.compile(r"香到流"), "香到流口水类套话"),
    (re.compile(r"香到停"), "香到停不下来类套话"),
    (re.compile(r"香到邻"), "香到邻居…类套话"),
    (re.compile(r"香到舔"), "香到舔手指类套话"),
    (re.compile(r"一口(入魂|沦陷)"), "一口入魂/沦陷"),
    (re.compile(r"谁懂啊"), "谁懂啊"),
    (re.compile(r"天花板"), "天花板"),
    (re.compile(r"绝绝子|yyds", re.I), "绝绝子/yyds"),
)
_HUASU_FORBIDDEN_PHRASES_CACHE: tuple[str, ...] | None = None

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


BAT_PROXY_PORT = "17890"


def read_windows_ie_proxy_url() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not int(enabled or 0):
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""

    server_text = str(server or "").strip()
    if not server_text:
        return ""
    if ";" in server_text:
        chosen = ""
        for part in server_text.split(";"):
            item = part.strip()
            if item.lower().startswith("https="):
                chosen = item.split("=", 1)[1].strip()
                break
        if not chosen:
            chosen = server_text.split(";")[0].strip()
        server_text = chosen
    if "://" not in server_text:
        server_text = f"http://{server_text}"
    return server_text


def resolve_env_proxy_url() -> str:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def is_stale_bat_proxy_url(proxy_url: str) -> bool:
    return BAT_PROXY_PORT in str(proxy_url or "")


def clear_stale_bat_proxy_env() -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = os.getenv(name, "").strip()
        if value and is_stale_bat_proxy_url(value):
            os.environ.pop(name, None)


def resolve_effective_http_proxy(*, prefer_system: bool = True) -> str:
    env_proxy = resolve_env_proxy_url()
    system_proxy = read_windows_ie_proxy_url() if prefer_system else ""
    if env_proxy and is_stale_bat_proxy_url(env_proxy):
        clear_stale_bat_proxy_env()
        env_proxy = ""
    if env_proxy:
        return env_proxy
    return system_proxy


def build_httpx_client(
    timeout_seconds: float,
    *,
    trust_env: bool | None = None,
    use_system_proxy: bool = True,
) -> httpx.Client:
    if trust_env is None:
        trust_env = parse_bool_env("HTTP_TRUST_ENV", default=True)
    if not trust_env:
        return httpx.Client(timeout=timeout_seconds, trust_env=False)

    proxy_url = resolve_effective_http_proxy(prefer_system=use_system_proxy)
    if proxy_url:
        if is_stale_bat_proxy_url(proxy_url):
            proxy_url = read_windows_ie_proxy_url()
        if proxy_url:
            return httpx.Client(timeout=timeout_seconds, proxy=proxy_url, trust_env=False)
    return httpx.Client(timeout=timeout_seconds, trust_env=True)


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


def get_publish_copy_temperature() -> float:
    raw_value = os.getenv("PUBLISH_COPY_TEMPERATURE", "").strip()
    if raw_value:
        try:
            return float(raw_value)
        except ValueError as exc:
            raise RuntimeError("PUBLISH_COPY_TEMPERATURE 必须是数字。") from exc
    return PUBLISH_COPY_DEFAULT_TEMPERATURE


def pick_publish_copy_creative_angle(*, avoid_late_night: bool = False) -> str:
    candidates = list(PUBLISH_COPY_CREATIVE_ANGLES)
    if avoid_late_night:
        filtered = [
            angle
            for angle in candidates
            if not any(marker in angle for marker in _LATE_NIGHT_CREATIVE_MARKERS)
        ]
        if filtered:
            candidates = filtered
    return random.choice(candidates)


_COPY_WEEKDAY_LABELS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _resolve_season_label(month: int) -> str:
    if month in {3, 4, 5}:
        return "春季"
    if month in {6, 7, 8}:
        return "初夏/夏季"
    if month in {9, 10, 11}:
        return "秋季"
    return "冬季"


def _normalize_meal_tags(raw_tags: Any) -> list[str]:
    if not isinstance(raw_tags, list):
        return []
    normalized: list[str] = []
    for item in raw_tags:
        tag = str(item or "").strip()
        if tag in MEAL_TAG_LABELS_CN and tag not in normalized:
            normalized.append(tag)
    return normalized


def load_meal_tags_for_output_dir(output_dir: Path) -> list[str]:
    if not DISH_MEAL_TAGS_FILE.exists():
        return []
    try:
        payload = json.loads(DISH_MEAL_TAGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return []
    path_key = str(output_dir.resolve())
    for key, value in paths.items():
        if str(key).strip() == path_key:
            return _normalize_meal_tags(value)
    return []


def find_publish_plan_slot_for_path(output_dir: Path) -> str | None:
    if not PUBLISH_PLAN_FILE.exists():
        return None
    try:
        payload = json.loads(PUBLISH_PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    slots = payload.get("slots")
    if not isinstance(slots, dict):
        return None
    path_key = str(output_dir.resolve())
    for slot_map in slots.values():
        if not isinstance(slot_map, dict):
            continue
        for slot_key in PLAN_SLOT_TO_MEAL_TAG:
            assigned = str(slot_map.get(slot_key, "")).strip()
            if assigned and assigned == path_key:
                return slot_key
    return None


def infer_dish_natural_meal_tags(dish_name: str, notes: str = "") -> list[str]:
    text = f"{dish_name}{notes}"
    scores: Counter[str] = Counter()
    for tag, markers in _DISH_OCCASION_MARKERS.items():
        for marker in markers:
            if marker in text:
                scores[tag] += 1
    if not scores:
        return []
    max_score = max(scores.values())
    return [tag for tag, score in scores.items() if score == max_score]


def build_dish_copy_meal_context(
    dish_payload: dict[str, str],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    notes = str(dish_payload.get("notes", "")).strip()
    meal_tags: list[str] = []
    plan_slot: str | None = None
    if output_dir is not None:
        meal_tags = load_meal_tags_for_output_dir(output_dir)
        plan_slot = find_publish_plan_slot_for_path(output_dir)
    inferred = infer_dish_natural_meal_tags(dish_name, notes)

    primary_tags: list[str] = []
    for tag in meal_tags:
        if tag not in primary_tags:
            primary_tags.append(tag)
    if plan_slot:
        plan_tag = PLAN_SLOT_TO_MEAL_TAG[plan_slot]
        if plan_tag not in primary_tags:
            primary_tags.insert(0, plan_tag)
    if not primary_tags and inferred:
        primary_tags = list(inferred)

    primary_labels = [MEAL_TAG_LABELS_CN[tag] for tag in primary_tags if tag in MEAL_TAG_LABELS_CN]
    avoid_late_night = (
        "breakfast" in primary_tags
        or plan_slot == "morning"
        or ("breakfast" in inferred and "late_night" not in primary_tags and "late_night" not in inferred)
    )
    return {
        "meal_tags": meal_tags,
        "plan_slot": plan_slot,
        "inferred_tags": inferred,
        "primary_tags": primary_tags,
        "primary_labels_cn": primary_labels,
        "avoid_late_night": avoid_late_night,
    }


def build_copy_dish_scene_context_block(
    dish_payload: dict[str, str],
    *,
    output_dir: Path | None = None,
) -> str:
    now = datetime.now()
    month = now.month
    weekday = _COPY_WEEKDAY_LABELS[now.weekday()]
    season = _resolve_season_label(month)
    meal_context = build_dish_copy_meal_context(dish_payload, output_dir=output_dir)
    hints: list[str] = []
    if month in {6, 7, 8}:
        hints.extend(
            [
                "天热没胃口时需要开胃但不厚重的菜",
                "夏令时节更想有咀嚼感、咸香不腻的一盘",
            ]
        )
    if month in {11, 12, 1, 2}:
        hints.extend(["御寒暖胃", "砂锅炖菜", "年节家宴"])
    if now.weekday() >= 5:
        hints.append("周末居家加餐、来客小聚")
    else:
        hints.append("工作日也想快速解馋、替代外卖")
    hint_text = "；".join(dict.fromkeys(hints))

    occasion_lines: list[str] = []
    if meal_context["primary_labels_cn"]:
        occasion_lines.append(
            f"- 本菜最适餐次（优先依据，勿被程序运行时刻带偏）：{'、'.join(meal_context['primary_labels_cn'])}"
        )
    elif meal_context["inferred_tags"]:
        inferred_labels = [
            MEAL_TAG_LABELS_CN[tag]
            for tag in meal_context["inferred_tags"]
            if tag in MEAL_TAG_LABELS_CN
        ]
        occasion_lines.append(
            f"- 从菜名/做法推断的适餐次：{'、'.join(inferred_labels)}（须与海报视觉一致）"
        )
    else:
        occasion_lines.append(
            "- 请根据菜名、做法描述与海报视觉，自行判断最自然的餐次与食用场景"
        )
    if meal_context["meal_tags"]:
        occasion_lines.append(f"- 面板餐次标签：{', '.join(meal_context['meal_tags'])}")
    if meal_context["plan_slot"]:
        slot_label = {"morning": "发布计划·早", "noon": "发布计划·中", "evening": "发布计划·晚"}.get(
            meal_context["plan_slot"], meal_context["plan_slot"]
        )
        occasion_lines.append(f"- {slot_label}（排期餐次须优先遵守）")

    return (
        "菜品食用场景（以菜为本，勿本末倒置）：\n"
        + "\n".join(occasion_lines)
        + "\n"
        f"- 时节参考（仅作轻量联想，不得盖过菜品本身）：{season}，{now.strftime('%Y年%m月%d日')} {weekday}\n"
        f"- 可联想的生活场景（须与本菜餐次匹配）：{hint_text}\n"
        "- 禁止：因文案在凌晨/夜间生成，就把早餐类、蛋类等菜写成夜宵、佐酒、配冰啤；"
        "只有菜品本身适合夜宵/小酌时才可写此类场景。\n"
        "- timely_hook 须回答：结合本菜特色与上述适餐次，用户为什么在【对应场景】更想吃这道菜；"
        "勿编造不存在的具体热搜名、新闻或榜单。"
    )


def build_food_desire_framework_block() -> str:
    return (
        "人性食欲切入点（food_desire_angle 选 1–2 个最贴合本菜的，并在标题/描述里落地）：\n"
        "- 即时解馋：嘴馋、饿急、想马上满足\n"
        "- 好奇尝鲜：新搭配、反差做法、没试过\n"
        "- 治愈抚慰：累了一天要热乎、浓香、软糯\n"
        "- 社交分享：适合聚会、待客、能拍照发圈\n"
        "- 怀旧家常：烟火气、下饭踏实、像家里做的\n"
        "- 清爽平衡：怕腻、天热、想开胃不腻口\n"
        "- 小酌佐餐：能边啃边聊、配酒不抢味\n"
        "- 家宴带娃：安全不踩雷、大人小孩都能吃"
    )


def build_copy_three_pillars_block() -> str:
    return (
        "文案三要素（缺一不可，先想清楚再写标题/描述）：\n"
        "1) 菜品食用场景：结合 timely_hook，把「与本菜匹配的餐次/生活场景」和本菜连起来（以菜为本，勿按程序运行时刻硬套夜宵/佐酒）；\n"
        "2) 菜品特色 + 受众：结合 audience_analysis，写清谁会吃、在什么场景点开、本菜独特卖点；\n"
        "3) 人性食欲：结合 food_desire_angle，触达用户想吃的底层冲动，再用具体感官细节落地。"
    )


def collect_dish_specific_markers(dish_payload: dict[str, str]) -> list[str]:
    markers: list[str] = []
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    if len(dish_name) >= 2:
        markers.append(dish_name)
    notes = str(dish_payload.get("notes", "")).strip()
    for token in re.findall(r"[\u4e00-\u9fff]{2,}", f"{dish_name}{notes}"):
        if token not in markers:
            markers.append(token)
    return markers[:8]


def load_huasu_forbidden_phrases() -> tuple[str, ...]:
    global _HUASU_FORBIDDEN_PHRASES_CACHE
    if _HUASU_FORBIDDEN_PHRASES_CACHE is not None:
        return _HUASU_FORBIDDEN_PHRASES_CACHE
    if not HUASU_FORBIDDEN_FILE.exists():
        _HUASU_FORBIDDEN_PHRASES_CACHE = ()
        return _HUASU_FORBIDDEN_PHRASES_CACHE
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_line in HUASU_FORBIDDEN_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("##"):
            continue
        if "不重样" in line:
            continue
        if len(line) > 24:
            continue
        if _HUASU_DISH_CATEGORY_TAIL.search(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(line)
    _HUASU_FORBIDDEN_PHRASES_CACHE = tuple(ordered)
    return _HUASU_FORBIDDEN_PHRASES_CACHE


def find_forbidden_copy_phrase_hit(text: str) -> str | None:
    body = str(text or "").strip()
    if not body:
        return None
    for phrase in load_huasu_forbidden_phrases():
        if phrase in body:
            return phrase
    for pattern, label in _FORBIDDEN_COPY_REGEX:
        if pattern.search(body):
            return label
    return None


def reject_forbidden_copy_phrase(text: str, *, label: str) -> str | None:
    hit = find_forbidden_copy_phrase_hit(text)
    if hit:
        return f"{label}含禁用套话「{hit}」，请改为本道菜的具体感官、场景或动作描写。"
    return None


def build_huasu_forbidden_prompt_block(*, max_items: int = 48) -> str:
    phrases = load_huasu_forbidden_phrases()
    if not phrases:
        return ""
    shown = phrases[:max_items]
    scope = f"（共 {len(phrases)} 条，以下列 {len(shown)} 条）" if len(phrases) > len(shown) else f"（共 {len(phrases)} 条）"
    lines = "\n".join(f"- {phrase}" for phrase in shown)
    return (
        f"\n禁用套话清单{scope}：下列及同类变体一律不得出现；"
        f"请写本道菜的视觉、香气、口感、温度、吃法反差等具体感受，禁止万能美食营销话术：\n"
        f"{lines}\n"
        f"另禁：好吃到…、香到…、一口入魂、天花板、绝绝子、yyds 等同类表达。\n"
    )


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
    # 豆包为国内 API，默认不走系统 HTTP 代理，避免本地代理未启动时 Connection refused。
    trust_env = parse_bool_env("DOUBAO_HTTP_TRUST_ENV", default=False)
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        http_client=build_httpx_client(timeout_seconds=timeout, trust_env=trust_env),
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
    max_long_edge: int | None = None,
) -> tuple[bytes, str]:
    """缩放（可选）并转 JPEG，压至体积上限以内。"""
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
        if max_long_edge and max(canvas.size) > max_long_edge:
            scale = max_long_edge / max(canvas.size)
            canvas = canvas.resize(
                (max(1, int(canvas.width * scale)), max(1, int(canvas.height * scale))),
                Image.Resampling.LANCZOS,
            )
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


def resolve_vision_image_upload_limits(image_count: int) -> tuple[int, int]:
    """返回 (单图最大字节, 长边上限)。"""
    single_max = parse_int_env("VISION_IMAGE_MAX_BYTES", 9 * 1024 * 1024)
    total_budget = parse_int_env("VISION_MULTI_IMAGE_TOTAL_MAX_BYTES", 6 * 1024 * 1024)
    count = max(1, image_count)
    per_image = min(single_max, max(512 * 1024, total_budget // count))
    if count >= 3:
        max_long_edge = parse_int_env("VISION_MULTI_IMAGE_MAX_LONG_EDGE", 1280)
    elif count >= 2:
        max_long_edge = parse_int_env("VISION_DUAL_IMAGE_MAX_LONG_EDGE", 1536)
    else:
        max_long_edge = parse_int_env("VISION_IMAGE_MAX_LONG_EDGE", 2048)
    return per_image, max_long_edge


def prepare_image_bytes_for_vision_api(
    image_path: Path,
    *,
    max_bytes: int | None = None,
    max_long_edge: int | None = None,
    log_prefix: str = "多模态参考图",
) -> tuple[bytes, str]:
    """多模态 image_url 用图：按体积与长边上限归一化后再上传豆包。"""
    per_image_max, default_long_edge = resolve_vision_image_upload_limits(1)
    max_bytes = max_bytes or per_image_max
    max_long_edge = max_long_edge or default_long_edge
    raw = image_path.read_bytes()
    guessed_mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    needs_resize = False
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            needs_resize = max(image.size) > max_long_edge
    except Exception:
        needs_resize = False
    if len(raw) <= max_bytes and not needs_resize and guessed_mime in {"image/jpeg", "image/jpg"}:
        return raw, "image/jpeg"
    return _compress_image_to_byte_limit(
        image_path,
        max_bytes=max_bytes,
        log_prefix=log_prefix,
        max_long_edge=max_long_edge,
    )


def encode_image_as_data_url(
    image_path: Path,
    *,
    max_bytes: int | None = None,
    max_long_edge: int | None = None,
    log_prefix: str = "多模态参考图",
) -> str:
    image_bytes, mime = prepare_image_bytes_for_vision_api(
        image_path,
        max_bytes=max_bytes,
        max_long_edge=max_long_edge,
        log_prefix=log_prefix,
    )
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


POSTER_INGREDIENT_REFERENCE_HINT = "以上传的参考图为菜品的主食材"
MAX_POSTER_INGREDIENT_REFERENCE_COUNT = 3


def inject_poster_ingredient_ref_hint(placeholders: list[str], replacements: list[str]) -> None:
    prefix = f"{POSTER_INGREDIENT_REFERENCE_HINT}，"
    for index, placeholder in enumerate(placeholders):
        if "菜品核心视觉" not in placeholder:
            continue
        value = str(replacements[index]).strip()
        if not value or value.startswith(POSTER_INGREDIENT_REFERENCE_HINT):
            break
        replacements[index] = prefix + value
        break


def render_haibao_prompt_fallback(template_text: str, dish_name: str, notes: str) -> str:
    placeholders = collect_cankao_placeholders(template_text)
    if not placeholders:
        prompt = template_text.strip()
        if POSTER_INGREDIENT_REFERENCE_HINT not in prompt:
            return f"{POSTER_INGREDIENT_REFERENCE_HINT}。\n{prompt}"
        return prompt

    notes_text = notes.strip() or f"{dish_name}，突出家常真实出锅状态。"
    visual_text = f"{POSTER_INGREDIENT_REFERENCE_HINT}，{notes_text[:240]}"
    replacements: list[str] = []
    for placeholder in placeholders:
        if "菜名" in placeholder and "核心" not in placeholder:
            replacements.append(dish_name)
        elif "菜品核心视觉" in placeholder:
            replacements.append(visual_text)
        elif "菜品做法" in placeholder:
            replacements.append(notes_text)
        elif "互动工具" in placeholder:
            replacements.append("筷子")
        elif "互动内容" in placeholder:
            replacements.append(f"夹起一块带红汤汁的{dish_name}主料，酱汁欲滴")
        elif "主食" in placeholder or "画面搭配" in placeholder:
            replacements.append(
                "本道为红汤锅类主菜，不配米饭；深色砂锅或家用锅盛满红亮汤汁，"
                "鳝段与鱼片错落分布，表面浮着辣椒与花椒，锅气腾腾"
            )
        else:
            replacements.append(notes_text)
    return render_cankao_template_by_replacements(template_text=template_text, replacements=replacements)


def generate_haibao_prompt_by_template(
    client: OpenAI,
    dish_name: str,
    notes: str,
    template_text: str,
    *,
    ingredient_reference_paths: list[Path] | None = None,
) -> dict[str, str]:
    paths = [Path(item).resolve() for item in (ingredient_reference_paths or []) if Path(item).is_file()]
    paths = paths[:MAX_POSTER_INGREDIENT_REFERENCE_COUNT]
    if paths:
        try:
            return generate_haibao_prompt_with_ingredient_refs(
                client=client,
                dish_name=dish_name,
                notes=notes,
                template_text=template_text,
                image_paths=paths,
            )
        except RuntimeError as exc:
            if classify_text_api_error(exc) != "connection" and "Connection error" not in str(exc):
                raise
            print(
                "海报主食材多模态改写连接失败，降级为本地模板填充"
                "（参考图仍用于 gpt-image-2 生图）。"
            )
            return {
                "model": "local-fallback",
                "prompt": render_haibao_prompt_fallback(template_text, dish_name, notes),
            }
    return generate_cankao_prompt_by_template(
        client=client,
        dish_name=dish_name,
        notes=notes,
        template_text=template_text,
    )


def _build_haibao_ingredient_ref_user_content(
    *,
    user_prompt: str,
    image_paths: list[Path],
    max_bytes: int,
    max_long_edge: int,
) -> list[dict[str, Any]]:
    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for index, image_path in enumerate(image_paths, start=1):
        if not image_path.exists():
            raise FileNotFoundError(f"海报参考图不存在：{image_path}")
        user_content.append(
            {
                "type": "text",
                "text": f"主食材参考图 {index}（后续 gpt-image-2 生图将使用此图还原食材外观）",
            }
        )
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": encode_image_as_data_url(
                        image_path,
                        max_bytes=max_bytes,
                        max_long_edge=max_long_edge,
                        log_prefix="海报主食材参考图",
                    )
                },
            }
        )
    return user_content


def generate_haibao_prompt_with_ingredient_refs(
    client: OpenAI,
    dish_name: str,
    notes: str,
    template_text: str,
    image_paths: list[Path],
) -> dict[str, str]:
    model = os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip() or DEFAULT_DOUBAO_TEXT_MODEL
    temperature = get_text_temperature()
    placeholders = collect_cankao_placeholders(template_text)
    if not placeholders:
        return {"model": model, "prompt": template_text.strip()}

    system_prompt = append_eating_action_guidance(
        """
你是菜谱视觉策划总监。用户已上传主食材参考图，请结合参考图只为模板变量位提供替换值。
要求：
1) 不得改写模板固定文本，只输出 replacements 数组。
2) replacements 顺序必须与变量位从上到下完全一致。
3) 只输出 JSON：{"replacements":["值1","值2",...]}。
4) 变量位「菜品核心视觉」须依据参考图中的真实食材形态、颜色与质感撰写，不要写「见上图」。
5) 每个变量位形如 {标签，示例}，必须结合菜名、补充说明与参考图生成，严禁照抄模板示例中的其它菜名或食材。
6) 所有 replacements 必须描述同一道菜，不得混入模板示例菜。
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

    per_image_max, max_long_edge = resolve_vision_image_upload_limits(len(image_paths))
    print(
        f"海报主食材参考图多模态上传：{len(image_paths)} 张，"
        f"单图≤{per_image_max // 1024}KB，长边≤{max_long_edge}px"
    )
    compression_profiles: list[tuple[str, int, int]] = [
        ("标准", per_image_max, max_long_edge),
        (
            "降级",
            max(384 * 1024, per_image_max // 2),
            min(max_long_edge, parse_int_env("VISION_MULTI_IMAGE_FALLBACK_LONG_EDGE", 960)),
        ),
    ]

    max_retry = parse_int_env("TEXT_REQUEST_RETRY_COUNT", 5)
    last_error: Exception | None = None
    validation_feedback = ""
    saw_connection_error = False
    for profile_label, profile_bytes, profile_long_edge in compression_profiles:
        user_content = _build_haibao_ingredient_ref_user_content(
            user_prompt=user_prompt,
            image_paths=image_paths,
            max_bytes=profile_bytes,
            max_long_edge=profile_long_edge,
        )
        if profile_label == "降级":
            print(
                f"海报主食材参考图多模态请求改用{profile_label}压缩："
                f"单图≤{profile_bytes // 1024}KB，长边≤{profile_long_edge}px"
            )
        for attempt in range(1, max_retry + 1):
            attempt_content = list(user_content)
            if validation_feedback:
                attempt_content[0] = {
                    "type": "text",
                    "text": user_prompt + f"\n\n上次输出不合格：{validation_feedback}\n请严格按参考图重写 replacements。",
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
                if error_kind == "connection":
                    saw_connection_error = True
                if is_retriable_text_api_error(exc) and attempt < max_retry:
                    delay = api_retry_delay_seconds(attempt, error_kind=error_kind)
                    print(f"海报主食材参考图模板改写第 {attempt} 次失败（{error_kind}），{delay:.0f}s 后重试…")
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

                inject_poster_ingredient_ref_hint(placeholders, replacements)
                prompt_text = render_cankao_template_by_replacements(
                    template_text=template_text,
                    replacements=replacements,
                )
                return {"model": model, "prompt": prompt_text}
            except ValueError as exc:
                validation_feedback = str(exc)
                last_error = exc
                continue
        if saw_connection_error and profile_label == "标准":
            continue
        if not saw_connection_error:
            break

    raise RuntimeError(f"海报主食材参考图模板改写失败：{last_error or '未知错误'}")


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

    per_image_max, max_long_edge = resolve_vision_image_upload_limits(len(image_paths))
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
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": encode_image_as_data_url(
                        image_path,
                        max_bytes=per_image_max,
                        max_long_edge=max_long_edge,
                        log_prefix=f"{stage_name}参考图",
                    )
                },
            }
        )

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
    forbidden_error = reject_forbidden_copy_phrase(normalized, label="气泡文案")
    if forbidden_error:
        raise ValueError(forbidden_error)
    return normalized


def build_bubble_copy_prompt(
    *,
    dish_name: str,
    notes: str = "",
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
    scene_block = build_copy_dish_scene_context_block(
        {"dish_name": dish_name.strip(), "notes": notes.strip()},
    )
    return (
        f"{dish_line}上图是这道菜的海报，另有阿叶厨师角色参考。"
        f"写他在品尝这道菜时，气泡里最能勾起食欲的一句话——他是说话的人，不是菜。"
        f"须同时扣住：①与本菜匹配的餐次/生活场景 ②本菜特色 ③人的食欲冲动（解馋/好奇/治愈等），用具体感官瞬间表达。"
        f"禁止「绝了」「太香了」等空话；禁止因凌晨生成就把早餐类菜写成夜宵佐酒。"
        f"{BUBBLE_COPY_MIN_CHARS}–{BUBBLE_COPY_MAX_CHARS} 个汉字，只输出这一句。"
        f"\n{scene_block}\n"
        f"{build_huasu_forbidden_prompt_block(max_items=20)}"
        f"{history_block}{feedback_block}"
    )


def generate_poster_bubble_copy(
    client: OpenAI,
    poster_image_path: Path,
    *,
    dish_name: str = "",
    notes: str = "",
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
                    notes=notes,
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
        f"先完成三要素分析，再写抖音标题、描述和正好 5 个话题（不超过 5 个）。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}"
        f"标题要有网感、像真人发图文；描述从本菜感官 + 受众场景 + 食欲冲动写起，别写成菜谱步骤。"
    ),
    "xiaohongshu": (
        f"先完成三要素分析，再写小红书标题、描述和 10 个话题。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}"
        f"标题像真实分享笔记；描述写清「我为什么在这个时节想吃它」。"
    ),
    "weixin_mp": (
        f"先完成三要素分析，再写公众号标题、描述和正好 10 个话题（不超过 10 个）。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}"
        f"标题可读、有吸引力；描述稳重但不空，突出本菜特色与食用场景。"
    ),
    "weixin_channels": (
        f"先完成三要素分析，再写视频号标题、描述和正好 30 个话题（不超过 30 个）。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}"
        f"标题适合信息流；描述短平快，抓住时节 + 食欲点。"
    ),
    "kuaishou": (
        f"先完成三要素分析，再写快手标题、描述和 4 个话题。"
        f"{_TITLE_LIMIT_HINT}{_TITLE_CLICHE_FORBIDDEN_HINT}"
        f"标题接地气；描述像跟朋友唠这道菜为什么此刻值得做。"
    ),
}

PUBLISH_COPY_TITLE_SUFFIXES: tuple[str, ...] = (
    "_图文标题.txt",
    "_抖音标题.txt",
    "_小红书标题.txt",
    "_微信公众号标题.txt",
    "_微信视频号标题.txt",
    "_快手标题.txt",
)


def publish_copy_desc_platform_suffixes() -> tuple[str, ...]:
    return tuple(f"_{label}图文描述.txt" for _key, label, _count in V2_PUBLISH_PLATFORM_SPECS)


def normalize_publish_copy_compare_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。！？!?.、；;：:\"\"''「」『』（）()【】\\[\\]…—-]", "", text)
    return text.strip()


def iter_publish_copy_source_files() -> list[Path]:
    files: list[Path] = []
    for root in (DISH_POOL_DIR, DISH_ARCHIVE_DIR, OUTPUT_DIR):
        if not root.exists():
            continue
        for path in root.rglob("*.txt"):
            if not path.is_file():
                continue
            name = path.name
            if any(name.endswith(suffix) for suffix in PUBLISH_COPY_TITLE_SUFFIXES):
                files.append(path)
                continue
            if name.endswith(PUBLISH_COPY_DESC_BODY_SUFFIX) or any(
                name.endswith(suffix) for suffix in publish_copy_desc_platform_suffixes()
            ):
                files.append(path)
    return sorted(files, key=lambda item: item.stat().st_mtime)


def read_publish_copy_description_from_file(path: Path) -> str:
    content = path.read_text(encoding="utf-8").strip()
    if path.name.endswith(PUBLISH_COPY_DESC_BODY_SUFFIX):
        return content
    return content.splitlines()[0].strip() if content else ""


def collect_publish_copy_exclude_from_output_dir(output_dir: Path) -> tuple[set[str], set[str]]:
    exclude_titles: set[str] = set()
    exclude_descs: set[str] = set()
    if not output_dir.exists():
        return exclude_titles, exclude_descs
    for path in output_dir.glob("*.txt"):
        name = path.name
        try:
            if any(name.endswith(suffix) for suffix in PUBLISH_COPY_TITLE_SUFFIXES):
                normalized = normalize_publish_copy_compare_text(path.read_text(encoding="utf-8"))
                if normalized:
                    exclude_titles.add(normalized)
            elif name.endswith(PUBLISH_COPY_DESC_BODY_SUFFIX) or any(
                name.endswith(suffix) for suffix in publish_copy_desc_platform_suffixes()
            ):
                normalized = normalize_publish_copy_compare_text(read_publish_copy_description_from_file(path))
                if normalized:
                    exclude_descs.add(normalized)
        except OSError:
            continue
    return exclude_titles, exclude_descs


def sync_publish_copy_history_file(
    *,
    exclude_titles: set[str] | None = None,
    exclude_descs: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """扫描全库平台标题/描述正文（不含话题），去重后写入 publish_copy_history.txt。"""
    exclude_titles = exclude_titles or set()
    exclude_descs = exclude_descs or set()
    ordered_titles: list[str] = []
    ordered_descs: list[str] = []
    seen_titles: set[str] = set()
    seen_descs: set[str] = set()
    title_source_count = 0
    desc_source_count = 0

    def add_title(raw_text: str) -> None:
        normalized = normalize_publish_copy_compare_text(raw_text)
        if not normalized or normalized in exclude_titles or normalized in seen_titles:
            return
        seen_titles.add(normalized)
        ordered_titles.append(raw_text.strip())

    def add_desc(raw_text: str) -> None:
        normalized = normalize_publish_copy_compare_text(raw_text)
        if not normalized or normalized in exclude_descs or normalized in seen_descs:
            return
        seen_descs.add(normalized)
        ordered_descs.append(raw_text.strip())

    for path in iter_publish_copy_source_files():
        try:
            name = path.name
            if any(name.endswith(suffix) for suffix in PUBLISH_COPY_TITLE_SUFFIXES):
                content = path.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                title_source_count += 1
                add_title(content)
                continue
            content = read_publish_copy_description_from_file(path)
            if not content:
                continue
            desc_source_count += 1
            add_desc(content)
        except OSError:
            continue

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_lines = [
        "# 历史平台文案标题与描述正文（自动汇总；话题不在此列）",
        f"# 标题来源文件数：{title_source_count}，去重条目：{len(ordered_titles)}",
        f"# 描述来源文件数：{desc_source_count}，去重条目：{len(ordered_descs)}",
        f"# 更新：{timestamp}",
        "",
        "## titles",
    ]
    body_lines = header_lines + [f"- {text}" for text in ordered_titles]
    body_lines.extend(["", "## descriptions"])
    body_lines.extend(f"- {text}" for text in ordered_descs)
    PUBLISH_COPY_HISTORY_FILE.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    print(
        f"平台文案历史已同步：标题 {len(ordered_titles)} 条、描述 {len(ordered_descs)} 条 -> "
        f"{PUBLISH_COPY_HISTORY_FILE}"
    )
    return ordered_titles, ordered_descs


def is_publish_copy_text_similar(candidate: str, history_text: str) -> bool:
    left = normalize_publish_copy_compare_text(candidate)
    right = normalize_publish_copy_compare_text(history_text)
    if not left or not right:
        return False
    if left == right:
        return True
    min_len = min(len(left), len(right))
    if min_len >= 8 and (left in right or right in left):
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= PUBLISH_COPY_SIMILARITY_RATIO


def find_publish_copy_history_conflict(
    text: str,
    history_texts: list[str],
    *,
    label: str,
) -> str | None:
    body = str(text or "").strip()
    if not body:
        return None
    for history_text in history_texts:
        if is_publish_copy_text_similar(body, history_text):
            return f"{label}与历史过于相似，请重写：{body}"
    return None


def validate_v2_publish_copy_not_in_history(
    platform_payload: dict[str, dict[str, Any]],
    history_titles: list[str],
    history_descriptions: list[str],
) -> None:
    for platform_key, platform_label, _count in V2_PUBLISH_PLATFORM_SPECS:
        block = platform_payload[platform_key]
        title_error = find_publish_copy_history_conflict(
            str(block.get("title", "")),
            history_titles,
            label=f"{platform_label}标题",
        )
        if title_error:
            raise ValueError(title_error)
        desc_error = find_publish_copy_history_conflict(
            str(block.get("description", "")),
            history_descriptions,
            label=f"{platform_label}描述",
        )
        if desc_error:
            raise ValueError(desc_error)


def build_publish_copy_history_prompt_block(
    history_titles: list[str],
    history_descriptions: list[str],
) -> str:
    blocks: list[str] = []
    if history_titles:
        total = len(history_titles)
        shown = history_titles[-PUBLISH_COPY_HISTORY_TITLE_PROMPT_LIMIT:]
        scope = f"共 {total} 条" if total <= len(shown) else f"共 {total} 条，以下列最近 {len(shown)} 条"
        lines = "\n".join(f"- {text}" for text in shown)
        blocks.append(
            f"历史标题（{scope}，均已使用过；禁止重复、禁止只改几个字或同义改写，禁止套用相同句式骨架）：\n{lines}"
        )
    if history_descriptions:
        total = len(history_descriptions)
        shown = history_descriptions[-PUBLISH_COPY_HISTORY_DESC_PROMPT_LIMIT:]
        scope = f"共 {total} 条" if total <= len(shown) else f"共 {total} 条，以下列最近 {len(shown)} 条"
        lines = "\n".join(f"- {text}" for text in shown)
        blocks.append(
            f"历史描述正文（{scope}，均已使用过；禁止重复、禁止只换菜名或同义改写；话题标签不在此列）：\n{lines}"
        )
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n\n"


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


def build_v2_publish_multimodal_prompt(
    dish_payload: dict[str, str],
    *,
    history_titles: list[str] | None = None,
    history_descriptions: list[str] | None = None,
    creative_angle: str = "",
    output_dir: Path | None = None,
) -> str:
    platform_lines = "\n".join(
        f"- {label}：{V2_PUBLISH_PLATFORM_TASKS[key]}" for key, label, _count in V2_PUBLISH_PLATFORM_SPECS
    )
    history_block = build_publish_copy_history_prompt_block(
        history_titles or [],
        history_descriptions or [],
    )
    meal_context = build_dish_copy_meal_context(dish_payload, output_dir=output_dir)
    angle_line = creative_angle.strip() or pick_publish_copy_creative_angle(
        avoid_late_night=bool(meal_context["avoid_late_night"]),
    )
    forbidden_block = build_huasu_forbidden_prompt_block(max_items=56)
    pillars_block = build_copy_three_pillars_block()
    scene_block = build_copy_dish_scene_context_block(dish_payload, output_dir=output_dir)
    desire_block = build_food_desire_framework_block()
    return f"""你是多平台美食图文运营。请结合附件里的「入选海报图」和下方菜品信息，为这道【全新菜品】写各平台发布文案。

{pillars_block}

写作流程（必须按顺序思考，不要跳步）：
1) 阅读「菜品食用场景」，写出 timely_hook：与本菜餐次/特色匹配的生活场景，让用户更想吃这道菜（勿按程序运行时刻硬套夜宵/佐酒）。
2) 阅读菜品信息与海报，写出 audience_analysis：谁会吃、在什么场景点开、本菜独特卖点是什么。
3) 从「人性食欲切入点」中选最贴合的，写出 food_desire_angle：触达哪种底层食欲冲动。
4) 本次创意辅助角度（标题与描述须与之协调，但不能替代三要素；若与本菜餐次冲突则忽略该角度）：{angle_line}
5) 基于以上三点，用口语/网感写各平台标题与描述：多写色泽、香气、声响、质感、余味、搭配反差；禁止万能菜谱腔。

{scene_block}

{desire_block}

{build_v2_publish_context_text(dish_payload)}

{history_block}{forbidden_block}各平台写作要求：
{platform_lines}

通用要求：
1) 标题、描述须能看出来源三要素（菜品场景 + 本菜受众/特色 + 食欲冲动），不要只堆形容词。
2) 各平台标题禁止「几碗饭/连吃几碗/连炫几碗」类句式；五个平台标题不得都用同一开头。
3) 描述 2–4 句：从本菜感官细节切入，可自然提菜名，不要写成步骤清单；五个平台描述不要整段同义复述。
4) topics 数组每项以 # 开头，不要菜品全名话题，不要 #阿叶造新菜（话题可复用通用标签，不受历史标题/描述限制）；餐次话题须与本菜匹配（早餐菜勿写 #夜宵美食 #佐酒菜）。
5) 各平台 title 均必须不超过 20 个汉字（40 字符；汉字/表情/符号等非 ASCII 计 2、英文/数字计 1，表情也算字符），超出会被截断或判不合格。
6) topics 数组长度必须与各平台要求完全一致：抖音 5、小红书 10、微信公众号 10、微信视频号 30、快手 4；不能合并公众号与视频号。
7) 新写的标题、描述不得与上方历史列表重复或高度相似（仅改菜名、同义换词也算不合格）。
8) 只输出 JSON，不要 Markdown，不要解释。必须先写 timely_hook、audience_analysis、food_desire_angle，再写各平台字段。格式如下：
{{
  "timely_hook": "结合本菜适餐次与生活场景，说明为何在该场景更想吃这道菜",
  "audience_analysis": "本菜具体受众、食用场景、核心点击动机与菜品特色",
  "food_desire_angle": "触达的人性食欲（解馋/好奇/治愈/社交等）及如何写进文案",
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


def audience_analysis_mentions_dish(analysis: str, dish_payload: dict[str, str]) -> bool:
    dish_name = str(dish_payload.get("dish_name", "")).strip()
    notes = str(dish_payload.get("notes", "")).strip()
    if dish_name and dish_name in analysis:
        return True
    if len(dish_name) >= 2:
        for size in (3, 2):
            for index in range(len(dish_name) - size + 1):
                if dish_name[index : index + size] in analysis:
                    return True
    for keyword in re.findall(r"[\u4e00-\u9fff]{2,}", f"{dish_name}{notes}"):
        if keyword in analysis:
            return True
    return False


def parse_v2_publish_audience_analysis(raw_payload: dict[str, Any], dish_payload: dict[str, str]) -> str:
    analysis = str(raw_payload.get("audience_analysis", "")).strip()
    if len(analysis) < 20:
        raise ValueError("audience_analysis 过短或缺失，须说明受众、场景、菜品特色与点击动机。")
    generic_markers = ("所有人群", "人人都爱", "任何人")
    if any(marker in analysis for marker in generic_markers):
        raise ValueError(f"audience_analysis 过于笼统，请写具体人群与场景：{analysis}")
    if not audience_analysis_mentions_dish(analysis, dish_payload):
        markers = collect_dish_specific_markers(dish_payload)
        raise ValueError(
            f"audience_analysis 须提到本菜名或核心食材（如 {'/'.join(markers[:3])}）：{analysis}"
        )
    return analysis


def parse_v2_publish_timely_hook(raw_payload: dict[str, Any]) -> str:
    timely_hook = str(raw_payload.get("timely_hook", "")).strip()
    if len(timely_hook) < 12:
        raise ValueError("timely_hook 过短或缺失，须结合本菜适餐次与生活场景说明为何想吃这道菜。")
    if re.search(r"(今日热搜|刚刚刷屏|热搜第一|爆款话题)", timely_hook):
        raise ValueError(f"timely_hook 禁止编造具体热搜或新闻：{timely_hook}")
    return timely_hook


def parse_v2_publish_food_desire_angle(raw_payload: dict[str, Any]) -> str:
    desire = str(raw_payload.get("food_desire_angle", "")).strip()
    if len(desire) < 12:
        raise ValueError("food_desire_angle 过短或缺失，须说明触达哪种人性食欲冲动。")
    return desire


def parse_v2_publish_strategy_fields(
    raw_payload: dict[str, Any],
    dish_payload: dict[str, str],
) -> dict[str, str]:
    return {
        "timely_hook": parse_v2_publish_timely_hook(raw_payload),
        "audience_analysis": parse_v2_publish_audience_analysis(raw_payload, dish_payload),
        "food_desire_angle": parse_v2_publish_food_desire_angle(raw_payload),
    }


def validate_v2_publish_meal_occasion_consistency(
    *,
    raw_payload: dict[str, Any],
    platform_payload: dict[str, dict[str, Any]],
    dish_payload: dict[str, str],
    output_dir: Path | None = None,
) -> None:
    meal_context = build_dish_copy_meal_context(dish_payload, output_dir=output_dir)
    if not meal_context["avoid_late_night"]:
        return

    texts: list[str] = [str(raw_payload.get("timely_hook", ""))]
    for block in platform_payload.values():
        texts.append(str(block.get("title", "")))
        texts.append(str(block.get("description", "")))
        topics = block.get("topics")
        if isinstance(topics, list):
            texts.extend(str(topic) for topic in topics)

    combined = "\n".join(texts)
    if _LATE_NIGHT_COPY_PATTERN.search(combined):
        labels = "、".join(meal_context["primary_labels_cn"]) or "早餐/早市"
        raise ValueError(
            f"本菜适餐次为{labels}，文案却出现夜宵/佐酒/冰啤等表述，请按菜品本身重写。"
        )


def validate_v2_publish_forbidden_phrases(platform_payload: dict[str, dict[str, Any]]) -> None:
    for platform_key, platform_label, _count in V2_PUBLISH_PLATFORM_SPECS:
        block = platform_payload[platform_key]
        for field, field_label in (("title", "标题"), ("description", "描述")):
            error = reject_forbidden_copy_phrase(
                str(block.get(field, "")),
                label=f"{platform_label}{field_label}",
            )
            if error:
                raise ValueError(error)


def validate_v2_publish_copy_platform_diversity(
    platform_payload: dict[str, dict[str, Any]],
    *,
    dish_name: str,
) -> None:
    titles = [str(block.get("title", "")).strip() for block in platform_payload.values()]
    descriptions = [str(block.get("description", "")).strip() for block in platform_payload.values()]

    openings = [title[:4] for title in titles if len(title) >= 4]
    if openings:
        most_common_count = max(Counter(openings).values())
        if most_common_count >= 3:
            raise ValueError("五个平台标题开头过于雷同，请换不同钩子结构。")

    if dish_name:
        dish_prefix_count = sum(1 for title in titles if title.startswith(dish_name))
        if dish_prefix_count >= 4:
            raise ValueError("各平台标题不要都用菜名开头，至少两个平台换钩子结构。")

    desc_pairs = 0
    for index in range(len(descriptions)):
        for other in range(index + 1, len(descriptions)):
            if is_publish_copy_text_similar(descriptions[index], descriptions[other]):
                desc_pairs += 1
    if desc_pairs >= 4:
        raise ValueError("五个平台描述正文过于相似，请按各平台受众分别重写。")


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
    temperature = get_publish_copy_temperature()
    meal_context = build_dish_copy_meal_context(dish_payload, output_dir=output_dir)
    creative_angle = pick_publish_copy_creative_angle(
        avoid_late_night=bool(meal_context["avoid_late_night"]),
    )
    exclude_titles, exclude_descs = collect_publish_copy_exclude_from_output_dir(output_dir)
    history_titles, history_descriptions = sync_publish_copy_history_file(
        exclude_titles=exclude_titles,
        exclude_descs=exclude_descs,
    )
    prompt_text = build_v2_publish_multimodal_prompt(
        dish_payload,
        history_titles=history_titles,
        history_descriptions=history_descriptions,
        creative_angle=creative_angle,
        output_dir=output_dir,
    )
    prompt_file = output_dir / f"{dish_name}_平台文案生成prompt.txt"
    save_text_output(prompt_text, prompt_file)
    print(
        f"平台文案提示词已保存：{prompt_file}（temperature={temperature}，创意角度={creative_angle}）"
    )

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
            strategy = parse_v2_publish_strategy_fields(payload, dish_payload)
            platform_payload = parse_v2_publish_platform_payload(payload)
            validate_v2_publish_forbidden_phrases(platform_payload)
            validate_v2_publish_meal_occasion_consistency(
                raw_payload=payload,
                platform_payload=platform_payload,
                dish_payload=dish_payload,
                output_dir=output_dir,
            )
            validate_v2_publish_copy_platform_diversity(
                platform_payload,
                dish_name=str(dish_payload.get("dish_name", "")).strip(),
            )
            validate_v2_publish_copy_not_in_history(
                platform_payload,
                history_titles,
                history_descriptions,
            )
            saved = save_v2_publish_copy_files(
                output_dir=output_dir,
                timestamp=timestamp,
                dish_name=dish_name,
                platform_payload=platform_payload,
            )
            saved["model"] = model
            saved["prompt_file"] = str(prompt_file)
            saved.update(strategy)
            print(f"平台文案场景切入：{strategy['timely_hook']}")
            print(f"平台文案受众分析：{strategy['audience_analysis']}")
            print(f"平台文案食欲角度：{strategy['food_desire_angle']}")
            print(f"图文标题已保存：{saved['title_file']}")
            print(f"图文描述正文已保存：{saved['description_body_file']}")
            for platform_key, platform_label, _count in V2_PUBLISH_PLATFORM_SPECS:
                print(f"{platform_label}话题已保存：{saved['platform_topic_files'][platform_key]}")
                print(f"{platform_label}图文描述已保存：{saved['platform_description_files'][platform_key]}")
            return saved
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            creative_angle = pick_publish_copy_creative_angle(
                avoid_late_night=bool(meal_context["avoid_late_night"]),
            )
            prompt_text = build_v2_publish_multimodal_prompt(
                dish_payload,
                history_titles=history_titles,
                history_descriptions=history_descriptions,
                creative_angle=creative_angle,
                output_dir=output_dir,
            )
            save_text_output(prompt_text, prompt_file)
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
