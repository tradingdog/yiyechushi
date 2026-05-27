from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Sequence, cast

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)


from image_generator import (  # noqa: E402
    DEFAULT_DOUBAO_BASE_URL,
    DEFAULT_DOUBAO_TEXT_MODEL,
    DEFAULT_REQUEST_RETRY_COUNT,
    build_image_client,
    ensure_runtime_config_loaded,
    extract_image_items,
    get_cover_image_settings,
    get_image_settings,
    get_image_request_timeout_seconds,
    get_tujie_image_settings,
    get_text_request_timeout_seconds,
    is_timeout_error,
)


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DEFAULT_PUBLISH_DIR_NAME = "publish"
DEFAULT_REPORT_FILE_NAME = "publish_selection_report.json"
DEFAULT_SUMMARY_FILE_NAME = "publish_selection_report.txt"
DEFAULT_REGEN_WORK_DIR_NAME = "_publish_regen_cache"
DEFAULT_TITLE_RETRY_LIMIT = 3
TITLE_CENTERING_REQUIRED_PAGE_TYPES = {"page01", "guide_page"}
TITLE_CENTERING_HARD_ISSUE_PATTERN = re.compile(
    r"标题.*(?:不居中|偏左|偏右|偏移|没有居中)|(?:不居中|偏左|偏右|偏移).*(?:标题|主标题|黄条|标题条)|(?:标题|主标题|黄条|标题条).*(?:不居中|偏左|偏右|偏移)"
)


@dataclass(frozen=True)
class ReviewSettings:
    input_dir: Path
    publish_dir: Path
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    title_retry_limit: int
    dry_run: bool
    copy_mode: bool


@dataclass(frozen=True)
class ImageGroup:
    group_key: str
    page_type: str
    display_name: str
    image_paths: tuple[Path, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用豆包为某个 output 子目录里的多版本图片打分，并把每页最佳图移动到 publish 文件夹。",
    )
    parser.add_argument("input_dir", help="要筛选的 output 子目录。")
    parser.add_argument(
        "--model",
        help="豆包多模态评审模型；不传时优先读取 DOUBAO_REVIEW_MODEL，再回退到 DOUBAO_TEXT_MODEL。",
    )
    parser.add_argument(
        "--publish-dir-name",
        default=DEFAULT_PUBLISH_DIR_NAME,
        help=f"发布目录名，默认 {DEFAULT_PUBLISH_DIR_NAME}。",
    )
    parser.add_argument(
        "--title-retry-limit",
        type=int,
        help=f"标题居中未通过时，最多按原 prompt 补生几轮；默认读取 PUBLISH_TITLE_CENTER_RETRY_LIMIT，未配置时为 {DEFAULT_TITLE_RETRY_LIMIT}。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只生成评分结果，不移动文件。")
    parser.add_argument("--copy", action="store_true", help="复制最佳图到 publish，而不是移动。")
    return parser.parse_args()


def resolve_path(path_text: str | Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (ROOT_DIR / candidate).resolve()


def parse_non_negative_int(value: Any, *, default: int, field_name: str) -> int:
    raw_value = str(value or "").strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{field_name} 必须是大于等于 0 的整数。") from exc

    if parsed < 0:
        raise RuntimeError(f"{field_name} 必须是大于等于 0 的整数。")
    return parsed


def resolve_review_settings(args: argparse.Namespace) -> ReviewSettings:
    ensure_runtime_config_loaded()

    input_dir = resolve_path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise RuntimeError(f"目标目录不存在：{input_dir}")

    api_key = os.getenv("DOUBAO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 DOUBAO_API_KEY，请先在 .env 或外部环境变量中配置。")

    base_url = os.getenv("DOUBAO_BASE_URL", DEFAULT_DOUBAO_BASE_URL).strip() or DEFAULT_DOUBAO_BASE_URL
    review_model = (
        str(args.model or "").strip()
        or os.getenv("DOUBAO_REVIEW_MODEL", "").strip()
        or os.getenv("DOUBAO_TEXT_MODEL", DEFAULT_DOUBAO_TEXT_MODEL).strip()
        or DEFAULT_DOUBAO_TEXT_MODEL
    )
    title_retry_limit = parse_non_negative_int(
        value=args.title_retry_limit if args.title_retry_limit is not None else os.getenv("PUBLISH_TITLE_CENTER_RETRY_LIMIT"),
        default=DEFAULT_TITLE_RETRY_LIMIT,
        field_name="PUBLISH_TITLE_CENTER_RETRY_LIMIT",
    )
    publish_dir_name = str(args.publish_dir_name or DEFAULT_PUBLISH_DIR_NAME).strip() or DEFAULT_PUBLISH_DIR_NAME
    publish_dir = input_dir / publish_dir_name

    return ReviewSettings(
        input_dir=input_dir,
        publish_dir=publish_dir,
        api_key=api_key,
        base_url=base_url,
        model=review_model,
        timeout_seconds=get_text_request_timeout_seconds(),
        title_retry_limit=title_retry_limit,
        dry_run=bool(args.dry_run),
        copy_mode=bool(args.copy),
    )


def build_review_client(settings: ReviewSettings) -> OpenAI:
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout_seconds)


def requires_title_centering(group: ImageGroup) -> bool:
    return group.page_type in TITLE_CENTERING_REQUIRED_PAGE_TYPES


def normalize_group_key(image_path: Path) -> str:
    match = re.match(r"^(.*)_\d+$", image_path.stem)
    if match:
        return match.group(1)
    return image_path.stem


def infer_page_type(group_key: str) -> str:
    if "封面" in group_key:
        return "cover"
    if "图解01_一页菜谱" in group_key:
        return "page01"
    if re.search(r"图解0[2-6]_", group_key):
        return "guide_page"
    return "other"


def build_display_name(group_key: str) -> str:
    if "图解01_一页菜谱" in group_key:
        return "图解01_一页菜谱"

    guide_match = re.search(r"(图解0[2-6]_[^_]+)$", group_key)
    if guide_match:
        return guide_match.group(1)

    cover_match = re.search(r"([^\\/]+封面)$", group_key)
    if cover_match:
        return cover_match.group(1)

    return group_key


def collect_image_groups(input_dir: Path) -> list[ImageGroup]:
    grouped_paths: dict[str, list[Path]] = {}

    for path in sorted(input_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            continue
        group_key = normalize_group_key(path)
        grouped_paths.setdefault(group_key, []).append(path)

    groups: list[ImageGroup] = []
    for group_key, image_paths in grouped_paths.items():
        sorted_paths = tuple(sorted(image_paths, key=lambda item: item.name.lower()))
        groups.append(
            ImageGroup(
                group_key=group_key,
                page_type=infer_page_type(group_key),
                display_name=build_display_name(group_key),
                image_paths=sorted_paths,
            )
        )

    return sorted(groups, key=lambda item: item.display_name)


def filter_image_groups_by_page_types(groups: list[ImageGroup], include_page_types: set[str] | None = None) -> list[ImageGroup]:
    if not include_page_types:
        return groups
    return [group for group in groups if group.page_type in include_page_types]


def read_text_file_if_exists(file_path: Path, max_chars: int = 2400) -> str:
    if not file_path.exists() or not file_path.is_file():
        return ""

    content = file_path.read_text(encoding="utf-8").strip()
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n...(已截断)"
    return f"[{file_path.name}]\n{content}"


def build_reference_text(group: ImageGroup, input_dir: Path) -> str:
    if group.page_type == "page01":
        return read_text_file_if_exists(input_dir / f"{group.group_key}.txt")

    if group.page_type == "guide_page":
        return read_text_file_if_exists(input_dir / f"{group.group_key}_图解文案.txt")

    if group.page_type == "cover":
        return read_text_file_if_exists(input_dir / f"{group.group_key}_文生图prompt.txt", max_chars=1600)

    text_parts: list[str] = []
    for text_path in sorted(input_dir.glob(f"{group.group_key}*.txt"), key=lambda item: item.name.lower()):
        part = read_text_file_if_exists(text_path, max_chars=1200)
        if part:
            text_parts.append(part)
    return "\n\n".join(text_parts)


def encode_image_as_data_url(image_path: Path) -> str:
    mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{image_b64}"


def title_center_offset_threshold(group: ImageGroup) -> int:
    return 1 if group.page_type == "page01" else 2


def encode_image_with_center_guide_data_url(image_path: Path) -> str:
    try:
        from PIL import Image, ImageDraw
    except ModuleNotFoundError as exc:
        raise RuntimeError("未安装 Pillow，无法生成标题居中辅助图；请先安装 requirements.txt。") from exc

    with Image.open(image_path) as image:
        canvas = image.convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        center_x = canvas.width // 2
        guide_bottom = max(80, int(canvas.height * 0.42))
        stripe_half_width = max(2, canvas.width // 256)
        horizontal_line_width = max(2, canvas.width // 320)

        draw.rectangle(
            [center_x - stripe_half_width, 0, center_x + stripe_half_width, guide_bottom],
            fill=(0, 255, 255, 108),
        )
        draw.line(
            [(0, guide_bottom), (canvas.width, guide_bottom)],
            fill=(0, 255, 255, 132),
            width=horizontal_line_width,
        )

        merged = Image.alpha_composite(canvas, overlay).convert("RGB")
        buffer = BytesIO()
        merged.save(buffer, format="PNG")

    image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{image_b64}"


def estimate_local_title_offset_percent(image_path: Path, group: ImageGroup) -> int | None:
    if not requires_title_centering(group):
        return None

    try:
        from PIL import Image
    except ModuleNotFoundError:
        return None

    crop_ratio = 0.24 if group.page_type == "page01" else 0.18
    min_width_ratio = 0.35 if group.page_type == "page01" else 0.16
    min_pixels = 400 if group.page_type == "page01" else 180

    with Image.open(image_path) as image:
        canvas = image.convert("RGB")
        width, height = canvas.size
        crop = canvas.crop((0, 0, width, int(height * crop_ratio)))

        xs: list[int] = []
        for y in range(crop.height):
            for x in range(crop.width):
                pixel = crop.getpixel((x, y))
                if not isinstance(pixel, tuple) or len(pixel) < 3:
                    continue
                red, green, blue = pixel[:3]
                if red >= 210 and 40 <= green <= 150 and blue <= 90 and (red - green) >= 70:
                    xs.append(x)

    if len(xs) < min_pixels:
        return None

    left = min(xs)
    right = max(xs)
    if (right - left) < int(width * min_width_ratio):
        return None

    center_x = (left + right) / 2
    return int(round((center_x - width / 2) / width * 100))


def build_group_rubric(group: ImageGroup) -> tuple[str, tuple[str, ...]]:
    if group.page_type == "page01":
        return (
            "页面类型：首图。标题区是否沿画面正中竖轴堆叠，是这一页最重要的硬门槛；主标题、上方引导句、黄条卖点和收藏提示条可以允许很轻微视觉偏移，但只要人眼一看觉得整体偏左或偏右，就算不通过。除此之外，再看主菜照片是否更像真实手机拍摄、哪张更有食欲、哪张整体排版更顺眼，以及文案和食材卡是否存在明显逻辑错误、错字或离谱内容。\n"
            "权重建议：标题居中与版心 40，主菜真实感 20，食欲 15，整体排版与信息层级 15，文案逻辑与正确性 10。\n"
            "硬性扣分：标题明显偏左偏右、标题区整体未沿中轴、食材卡和菜本身逻辑冲突、明显乱码、品牌包装或可读商品标签、明显假光和塑料食物感。",
            ("标题居中与版心", "主菜真实感", "食欲", "整体排版与信息层级", "文案逻辑与正确性"),
        )

    if group.page_type == "guide_page":
        return (
            "页面类型：图解02到图解06。标题是否水平居中是这一页的硬门槛；允许非常轻微的视觉偏差，但只要人眼明显觉得标题组整体偏左或偏右，就算不通过。通过这道门槛后，再看有没有品牌文字或可读标签的酱料瓶/锅具/食材包装、哪张更符合人类阅读顺序和卡片层级，并严查那种出现刻度图、温度条、进度环或量表却没有具体指标的假信息图。\n"
            "权重建议：标题居中 35，无品牌与无可读标签 20，阅读顺序与版式清晰度 20，文字正确性与可读性 15，信息图有效性 10。\n"
            "硬性扣分：标题不居中、标题组明显偏左偏右、品牌名、包装标签、明显假商标、乱码、出现没有具体数字或指标的刻度图/量表/仪表盘。",
            ("标题居中", "无品牌与无可读标签", "阅读顺序与版式清晰度", "文字正确性与可读性", "信息图有效性"),
        )

    if group.page_type == "cover":
        return (
            "页面类型：封面。重点看菜品主体是否更接近画面正中、哪张更有食欲、哪张更干净利落、主体更聚焦。\n"
            "权重建议：菜品居中程度 35，食欲 35，主体聚焦与构图稳定 20，画面干净度 10。\n"
            "硬性扣分：主菜明显跑偏、主体太小、杂乱抢戏、明显假光或食物塑料感。",
            ("菜品居中程度", "食欲", "主体聚焦与构图稳定", "画面干净度"),
        )

    return (
        "页面类型：其它。重点看构图、居中、真实感、可读性和品牌污染。",
        ("构图", "真实感", "可读性", "品牌污染控制"),
    )


def build_review_system_prompt() -> str:
    return (
        "你是阿叶造新菜账号的选图审稿助手。你的任务是比较同一页的多个候选图片，"
        "按给定维度分别打分，再选出最适合进入 publish 的那一张。"
        "必须先看硬性问题，再看审美和阅读体验。"
        "你必须只输出 JSON 对象，不要输出 Markdown，不要输出解释，不要输出额外前后缀。"
    )


def build_review_user_content(group: ImageGroup, reference_text: str) -> list[dict[str, Any]]:
    rubric_text, dimension_names = build_group_rubric(group)
    dimension_list = "、".join(dimension_names)
    reference_block = reference_text or "无额外参考文字。只根据图片本身判断。"
    title_gate_text = (
        "标题居中硬门槛：你必须额外判断每张图的标题是否通过视觉居中。通过标准是：主标题与相关标题条整体仍沿版心中轴，人眼看起来是居中的；若整体偏左、偏右、左右留白明显失衡、标题组重心明显漂移，就算不通过。每个候选后面都会附一张叠加中心参考线的辅助图，其中青色竖线就是整张画面的正中轴，青色横线以上是主要标题判断区。请结合原图和辅助图，给出标题中心相对中轴的估算偏移百分比，左偏为负，右偏为正。"
        if requires_title_centering(group)
        else "该页不启用标题居中硬门槛；title_centering_pass 固定返回 true，并在 note 里写“该页不适用”。"
    )

    prompt_text = f"""
当前页面：{group.display_name}
候选图片数量：{len(group.image_paths)}

{rubric_text}
{title_gate_text}

请把每张候选图都按这些维度单独打分：{dimension_list}。
分数范围统一用 0 到 10 的整数；再给出一个 0 到 100 的 total_score。

参考文字如下，仅用于判断图片中的文字逻辑、标题和信息结构是否明显跑偏，不要求逐字 OCR：
{reference_block}

判图原则：
1. 先排硬伤，再看美观；有硬伤的图即使漂亮也要重扣。
2. 如果多个候选接近，优先选更像真实手机拍摄、文字更顺眼、更适合人类一眼阅读的那张。
3. 不要平均分，必须拉开差距，明确给出 winner_index。
4. 如果某张图出现明显乱码、明显不居中、逻辑错文案、假量表无具体指标，这些都要写进 hard_issues。
5. “品牌污染”只指实体物体上的品牌、logo、商标、商品包装、瓶身、锅具印字、食材标签等；页面本来就允许出现的菜名、标题、页尾关注文案、账号名“阿叶造新菜”不算这类硬伤，不能因此扣分。
6. 对于首图和图解页，title_centering_pass 是硬门槛判断，不要因为别的维度更好就把明显不居中的图判为通过。
7. 对于首图和图解页，title_center_offset_percent 必须是整数，表示标题组中心相对画面正中轴的估算偏移百分比；左偏写负数，右偏写正数，完全居中写 0。

只输出下面这种 JSON 结构，不要多也不要少：
{{
  "group_label": "{group.display_name}",
  "page_type": "{group.page_type}",
  "winner_index": 1,
  "winner_image_name": "示例.jpg",
  "winner_reason": "一句话说明为什么赢",
  "candidates": [
    {{
      "index": 1,
      "image_name": "示例.jpg",
      "dimension_scores": {{
        "{dimension_names[0]}": 0
      }},
            "title_centering_pass": true,
            "title_center_offset_percent": 0,
            "title_centering_note": "一句话说明标题是否视觉居中；若该页不适用就写该页不适用",
      "total_score": 0,
      "hard_issues": ["如果没有硬伤就返回空数组"],
      "summary": "一句话总结优缺点"
    }}
  ]
}}
""".strip()

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    for index, image_path in enumerate(group.image_paths, start=1):
        content.append({"type": "text", "text": f"候选{index} 原图，文件名：{image_path.name}"})
        content.append({"type": "image_url", "image_url": {"url": encode_image_as_data_url(image_path)}})
        if requires_title_centering(group):
            content.append(
                {
                    "type": "text",
                    "text": f"候选{index} 标题居中辅助图：青色竖线就是画面正中轴，青色横线以上是主要标题判断区，请据此判断 title_center_offset_percent 与 title_centering_pass。",
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": encode_image_with_center_guide_data_url(image_path)},
                }
            )
    return content


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
        if isinstance(item, dict):
            text = item.get("text") or ""
        else:
            text = getattr(item, "text", None) or ""
        if text:
            text_parts.append(text)

    return "\n".join(text_parts).strip()


def strip_json_code_fence(text: str) -> str:
    raw_text = text.strip()
    if not raw_text.startswith("```"):
        return raw_text

    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    raw_text = re.sub(r"\s*```$", "", raw_text)
    return raw_text.strip()


def extract_json_object_text(text: str) -> str:
    raw_text = strip_json_code_fence(text)
    start_index = raw_text.find("{")
    end_index = raw_text.rfind("}")
    if start_index < 0 or end_index < 0 or end_index <= start_index:
        return raw_text
    return raw_text[start_index : end_index + 1].strip()


def remove_trailing_commas(text: str) -> str:
    result: list[str] = []
    in_string = False
    string_quote = ""
    is_escaped = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if in_string:
            result.append(char)
            if is_escaped:
                is_escaped = False
            elif char == "\\":
                is_escaped = True
            elif char == string_quote:
                in_string = False
                string_quote = ""
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = True
            string_quote = char
            result.append(char)
            index += 1
            continue

        if char == ",":
            look_ahead = index + 1
            while look_ahead < length and text[look_ahead].isspace():
                look_ahead += 1
            if look_ahead < length and text[look_ahead] in {"]", "}"}:
                index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def replace_identifiers_outside_strings(text: str, replacements: dict[str, str]) -> str:
    result: list[str] = []
    token: list[str] = []
    in_string = False
    string_quote = ""
    is_escaped = False

    def flush_token() -> None:
        if not token:
            return
        token_text = "".join(token)
        result.append(replacements.get(token_text, token_text))
        token.clear()

    for char in text:
        if in_string:
            flush_token()
            result.append(char)
            if is_escaped:
                is_escaped = False
            elif char == "\\":
                is_escaped = True
            elif char == string_quote:
                in_string = False
                string_quote = ""
            continue

        if char in {'"', "'"}:
            flush_token()
            in_string = True
            string_quote = char
            result.append(char)
            continue

        if char.isalpha() or char == "_":
            token.append(char)
            continue

        flush_token()
        result.append(char)

    flush_token()
    return "".join(result)


def parse_json_object(text: str) -> dict[str, Any]:
    raw_text = extract_json_object_text(text)
    if not raw_text:
        raise RuntimeError("豆包返回的评分结果不是有效 JSON。")

    json_candidates: list[str] = [raw_text]
    no_trailing_comma_text = remove_trailing_commas(raw_text)
    if no_trailing_comma_text != raw_text:
        json_candidates.append(no_trailing_comma_text)

    payload: Any = None
    last_error: Exception | None = None
    for candidate_text in json_candidates:
        try:
            payload = json.loads(candidate_text)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    else:
        python_like_text = replace_identifiers_outside_strings(
            no_trailing_comma_text,
            {"true": "True", "false": "False", "null": "None"},
        )
        try:
            payload = ast.literal_eval(python_like_text)
        except (SyntaxError, ValueError) as exc:
            last_error = exc
            snippet = raw_text[:400].replace("\n", "\\n")
            raise RuntimeError(f"豆包返回的评分结果不是有效 JSON：{exc}；原始片段：{snippet}") from exc

    if not isinstance(payload, dict):
        detail = f"：{last_error}" if last_error else "。"
        raise RuntimeError(f"豆包返回的评分结果不是 JSON 对象{detail}")
    return payload


def coerce_score(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def parse_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "y", "pass", "passed", "centered", "acceptable"}:
        return True
    if normalized in {"0", "false", "no", "n", "fail", "failed", "not_centered", "offcenter", "off-center"}:
        return False
    return None


def parse_optional_int(value: Any) -> int | None:
    normalized = str(value or "").strip().replace("%", "")
    if not normalized:
        return None
    try:
        return int(round(float(normalized)))
    except ValueError:
        return None


def normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def normalize_dimension_scores(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): coerce_score(score) for key, score in value.items()}


def get_title_dimension_name(group: ImageGroup) -> str | None:
    if group.page_type == "page01":
        return "标题居中与版心"
    if group.page_type == "guide_page":
        return "标题居中"
    return None


def candidate_title_centering_pass(candidate: dict[str, Any], group: ImageGroup) -> bool:
    if not requires_title_centering(group):
        return True

    local_offset_percent = parse_optional_int(candidate.get("local_title_center_offset_percent"))
    if local_offset_percent is not None:
        return abs(local_offset_percent) <= title_center_offset_threshold(group)

    offset_percent = parse_optional_int(candidate.get("title_center_offset_percent"))
    if offset_percent is not None:
        return abs(offset_percent) <= title_center_offset_threshold(group)

    explicit = parse_optional_bool(candidate.get("title_centering_pass"))
    if explicit is not None:
        return explicit

    hard_issues = normalize_text_list(candidate.get("hard_issues"))
    if any(TITLE_CENTERING_HARD_ISSUE_PATTERN.search(issue) for issue in hard_issues):
        return False

    dimension_name = get_title_dimension_name(group)
    if not dimension_name:
        return True

    dimension_scores = candidate.get("dimension_scores")
    if not isinstance(dimension_scores, dict):
        return False
    return coerce_score(dimension_scores.get(dimension_name)) >= 8


def build_title_centering_note(raw_candidate: dict[str, Any], *, group: ImageGroup, passed: bool) -> str:
    note = str(raw_candidate.get("title_centering_note", "")).strip()
    if note:
        return note
    if not requires_title_centering(group):
        return "该页不适用标题居中硬门槛。"
    if passed:
        return "标题组整体仍沿版心中轴，视觉上可接受。"
    return "标题组整体未沿版心中轴，视觉上不通过。"


def normalize_review_payload(payload: dict[str, Any], group: ImageGroup) -> dict[str, Any]:
    raw_candidates_value = payload.get("candidates")
    raw_candidates = [item for item in raw_candidates_value if isinstance(item, dict)] if isinstance(raw_candidates_value, list) else []

    normalized_candidates: list[dict[str, Any]] = []
    for index, image_path in enumerate(group.image_paths, start=1):
        raw_candidate = next((item for item in raw_candidates if coerce_score(item.get("index")) == index), {})
        if not isinstance(raw_candidate, dict):
            raw_candidate = {}

        candidate = {
            "index": index,
            "image_name": image_path.name,
            "dimension_scores": normalize_dimension_scores(raw_candidate.get("dimension_scores")),
            "total_score": coerce_score(raw_candidate.get("total_score")),
            "hard_issues": normalize_text_list(raw_candidate.get("hard_issues")),
            "summary": str(raw_candidate.get("summary", "")).strip(),
            "title_centering_pass": raw_candidate.get("title_centering_pass"),
            "title_center_offset_percent": parse_optional_int(raw_candidate.get("title_center_offset_percent")),
            "local_title_center_offset_percent": estimate_local_title_offset_percent(image_path, group),
            "title_centering_note": "",
        }
        candidate["title_centering_pass"] = candidate_title_centering_pass(candidate, group)
        candidate["title_centering_note"] = build_title_centering_note(
            raw_candidate,
            group=group,
            passed=bool(candidate["title_centering_pass"]),
        )
        normalized_candidates.append(candidate)

    title_required = requires_title_centering(group)
    passing_candidates = [candidate for candidate in normalized_candidates if bool(candidate.get("title_centering_pass"))]
    all_failed = title_required and not passing_candidates
    candidate_pool = passing_candidates or normalized_candidates

    raw_winner_index = coerce_score(payload.get("winner_index"))
    winner_candidate = next((candidate for candidate in candidate_pool if candidate["index"] == raw_winner_index), None)
    if winner_candidate is None:
        winner_candidate = max(candidate_pool, key=lambda candidate: (candidate["total_score"], -candidate["index"]))

    winner_reason = str(payload.get("winner_reason", "")).strip()
    raw_winner_candidate = next((candidate for candidate in normalized_candidates if candidate["index"] == raw_winner_index), None)
    raw_winner_passed = bool(raw_winner_candidate and raw_winner_candidate.get("title_centering_pass"))
    if title_required and passing_candidates and not raw_winner_passed:
        fallback_reason = (
            f"标题居中是硬门槛，改选标题居中通过且综合分最高的候选{winner_candidate['index']}。"
        )
        winner_reason = f"{fallback_reason}{winner_reason}" if winner_reason else fallback_reason
    elif not winner_reason:
        winner_reason = str(winner_candidate.get("summary", "")).strip() or "综合表现最好。"

    payload["group_label"] = group.display_name
    payload["page_type"] = group.page_type
    payload["winner_index"] = winner_candidate["index"]
    payload["winner_image_name"] = winner_candidate["image_name"]
    payload["winner_reason"] = winner_reason
    payload["candidates"] = normalized_candidates
    payload["title_centering_required"] = title_required
    payload["title_centering_pass_count"] = len(passing_candidates)
    payload["title_centering_failed_candidate_indexes"] = [
        candidate["index"]
        for candidate in normalized_candidates
        if not bool(candidate.get("title_centering_pass"))
    ]
    payload["all_candidates_fail_title_centering"] = all_failed
    payload["winner_is_provisional"] = all_failed
    return payload


def evaluate_image_group(client: OpenAI, settings: ReviewSettings, group: ImageGroup) -> dict[str, Any]:
    if len(group.image_paths) == 1 and not requires_title_centering(group):
        only_path = group.image_paths[0]
        return {
            "group_label": group.display_name,
            "page_type": group.page_type,
            "auto_selected": True,
            "winner_index": 1,
            "winner_image_name": only_path.name,
            "winner_reason": "该页只有 1 张图片，按规则直接入 publish，不调用 AI 分析。",
            "title_centering_required": False,
            "title_centering_pass_count": 1,
            "title_centering_failed_candidate_indexes": [],
            "all_candidates_fail_title_centering": False,
            "winner_is_provisional": False,
            "candidates": [
                {
                    "index": 1,
                    "image_name": only_path.name,
                    "dimension_scores": {},
                    "title_centering_pass": True,
                    "title_center_offset_percent": 0,
                    "title_centering_note": "该页不适用标题居中硬门槛。",
                    "total_score": 100,
                    "hard_issues": [],
                    "summary": "单图直入 publish。",
                }
            ],
        }

    reference_text = build_reference_text(group, settings.input_dir)
    print(f"正在用豆包评审：{group.display_name}，候选 {len(group.image_paths)} 张，模型 {settings.model}")
    messages = cast(
        Any,
        [
            {"role": "system", "content": build_review_system_prompt()},
            {"role": "user", "content": build_review_user_content(group, reference_text)},
        ],
    )

    try:
        response = client.chat.completions.create(
            model=settings.model,
            messages=messages,
            timeout=settings.timeout_seconds,
            max_tokens=2200,
            temperature=0,
        )
    except Exception as exc:
        if is_timeout_error(exc):
            raise RuntimeError(f"豆包评审超时：{group.display_name}") from exc
        raise RuntimeError(f"豆包评审失败：{group.display_name}，{exc}") from exc

    response_text = extract_chat_text_output(response)
    if not response_text:
        raise RuntimeError(f"豆包没有返回有效评分文本：{group.display_name}")

    payload = parse_json_object(response_text)
    return normalize_review_payload(payload, group)


def move_or_copy_selected_image(source_file: Path, publish_dir: Path, copy_mode: bool) -> Path:
    publish_dir.mkdir(parents=True, exist_ok=True)
    target_file = publish_dir / source_file.name
    if target_file.exists():
        target_file.unlink()

    if copy_mode:
        shutil.copy2(source_file, target_file)
    else:
        shutil.move(str(source_file), str(target_file))

    return target_file


def save_review_reports(publish_dir: Path, report_payload: dict[str, Any]) -> tuple[Path, Path]:
    publish_dir.mkdir(parents=True, exist_ok=True)
    report_file = publish_dir / DEFAULT_REPORT_FILE_NAME
    summary_file = publish_dir / DEFAULT_SUMMARY_FILE_NAME
    report_file.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append(f"评审目录：{report_payload.get('input_dir', '')}")
    lines.append(f"评审模型：{report_payload.get('model', '')}")
    lines.append(
        f"执行模式：{'dry-run（只输出报告，不移动图片）' if report_payload.get('dry_run') else ('复制' if report_payload.get('copy_mode') else '移动')}"
    )
    lines.append("")

    for result in report_payload.get("groups", []):
        lines.append(f"[{result.get('group_label', '')}]")
        lines.append(f"胜出图片：{result.get('winner_image_name', '')}")
        lines.append(f"原因：{result.get('winner_reason', '')}")
        blocked_reason = str(result.get("selection_blocked_reason", "")).strip()
        if blocked_reason:
            lines.append(f"当前状态：未入 publish，原因是 {blocked_reason}")
        if result.get("title_centering_required"):
            if result.get("all_candidates_fail_title_centering"):
                lines.append("标题居中状态：当前候选全部未通过硬门槛")
            else:
                lines.append(
                    f"标题居中状态：通过 {result.get('title_centering_pass_count', 0)}/{len(result.get('candidates', []))}"
                )
        for retry in result.get("title_regeneration_attempts", []):
            lines.append(
                f"补生记录：第{retry.get('round', '')}轮新增 {retry.get('generated_image_name', '')} | Photoshop补处理 {'是' if retry.get('photoshop_postprocessed') else '否'}"
            )
        if result.get("title_regeneration_planned"):
            lines.append("后续动作：正式模式会按原 prompt 补生新图后再复评。")
        for candidate in result.get("candidates", []):
            title_centering_text = ""
            if result.get("title_centering_required"):
                offset = candidate.get("title_center_offset_percent")
                local_offset = candidate.get("local_title_center_offset_percent")
                offset_parts: list[str] = []
                if local_offset is not None:
                    offset_parts.append(f"本地偏移 {local_offset}%")
                if offset is not None:
                    offset_parts.append(f"AI偏移 {offset}%")
                offset_text = f" | {' / '.join(offset_parts)}" if offset_parts else ""
                title_centering_text = f" | 标题居中 {'通过' if candidate.get('title_centering_pass') else '未过'}{offset_text}"
            lines.append(
                f"- 候选{candidate.get('index', '')} {candidate.get('image_name', '')} | 总分 {candidate.get('total_score', 0)}{title_centering_text} | 硬伤 {candidate.get('hard_issues', [])}"
            )
            summary = str(candidate.get("summary", "")).strip()
            if summary:
                lines.append(f"  {summary}")
            title_note = str(candidate.get("title_centering_note", "")).strip()
            if title_note and result.get("title_centering_required"):
                lines.append(f"  标题判定：{title_note}")
        lines.append("")

    summary_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return report_file, summary_file


def find_group_prompt_file(group: ImageGroup, input_dir: Path) -> Path:
    prompt_file = input_dir / f"{group.group_key}_文生图prompt.txt"
    if prompt_file.exists() and prompt_file.is_file():
        return prompt_file
    raise RuntimeError(f"找不到该页对应的文生图 prompt 文件：{group.display_name}")


def resolve_regeneration_image_settings(group: ImageGroup) -> dict[str, Any]:
    if group.page_type == "cover":
        base_settings = get_cover_image_settings()
    elif group.page_type == "page01":
        base_settings = get_image_settings()
    else:
        base_settings = get_tujie_image_settings()
    image_settings = dict(base_settings)
    image_settings["image_count"] = 1
    return image_settings


def get_next_group_image_index(group: ImageGroup, input_dir: Path) -> int:
    max_index = 0
    for path in input_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            continue
        if normalize_group_key(path) != group.group_key:
            continue
        match = re.search(r"_(\d+)$", path.stem)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index + 1


def request_regenerated_image_items(
    client: OpenAI,
    *,
    prompt_text: str,
    image_settings: dict[str, Any],
    stage_name: str,
) -> list[dict[str, str]]:
    request_timeout = get_image_request_timeout_seconds()
    response = None
    for attempt in range(1, DEFAULT_REQUEST_RETRY_COUNT + 1):
        try:
            response = client.images.generate(
                model=image_settings["model"],
                prompt=prompt_text,
                n=1,
                size=image_settings["size"],
                quality=image_settings["quality"],
                timeout=request_timeout,
            )
            break
        except Exception as exc:
            if attempt >= DEFAULT_REQUEST_RETRY_COUNT or not is_timeout_error(exc):
                raise RuntimeError(f"{stage_name}失败：{exc}") from exc
            print(f"{stage_name}超时，正在重试第 {attempt + 1}/{DEFAULT_REQUEST_RETRY_COUNT} 次...")

    image_items = extract_image_items(response)
    if not image_items:
        raise RuntimeError(f"{stage_name}已返回响应，但未发现可保存的图片数据。")
    return image_items


def save_regenerated_image_items(
    group: ImageGroup,
    *,
    save_dir: Path,
    image_items: list[dict[str, str]],
    start_index: int,
) -> list[Path]:
    save_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []

    for offset, item in enumerate(image_items, start=0):
        image_index = start_index + offset
        image_file = save_dir / f"{group.group_key}_{image_index:02d}.png"
        image_file.write_bytes(base64.b64decode(item["image_base64"]))
        saved_paths.append(image_file)

        revised_prompt = str(item.get("revised_prompt", "")).strip()
        if revised_prompt:
            revised_prompt_file = save_dir / f"{group.group_key}_{image_index:02d}_revised_prompt.txt"
            revised_prompt_file.write_text(revised_prompt, encoding="utf-8")

    return saved_paths


def should_postprocess_regenerated_image(group: ImageGroup) -> bool:
    return any(path.suffix.lower() in {".jpg", ".jpeg"} for path in group.image_paths)


def regenerate_group_candidate(
    *,
    group: ImageGroup,
    input_dir: Path,
    regeneration_dir: Path,
    image_client: OpenAI,
) -> dict[str, Any]:
    prompt_file = find_group_prompt_file(group, input_dir)
    prompt_text = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise RuntimeError(f"该页的文生图 prompt 文件为空：{prompt_file.name}")

    image_settings = resolve_regeneration_image_settings(group)
    print(f"{group.display_name} 当前候选全部未通过标题居中硬门槛，开始按原 prompt 补生成 1 张新图...")
    image_items = request_regenerated_image_items(
        client=image_client,
        prompt_text=prompt_text,
        image_settings=image_settings,
        stage_name=f"{group.display_name} 标题居中补生图",
    )

    start_index = get_next_group_image_index(group, regeneration_dir)
    saved_paths = save_regenerated_image_items(
        group,
        save_dir=regeneration_dir,
        image_items=image_items[:1],
        start_index=start_index,
    )
    if not saved_paths:
        raise RuntimeError(f"{group.display_name} 补生图失败：没有保存出新文件。")

    candidate_path = saved_paths[0]
    photoshop_postprocessed = False
    if should_postprocess_regenerated_image(group):
        from tools.apply_photoshop_template_batch import apply_photoshop_template_to_image

        photoshop_postprocessed = True
        candidate_path = Path(apply_photoshop_template_to_image(candidate_path))

    return {
        "candidate_path": candidate_path,
        "generated_image_name": candidate_path.name,
        "prompt_file": str(prompt_file),
        "image_model": image_settings["model"],
        "image_size": image_settings["size"],
        "image_quality": image_settings["quality"],
        "photoshop_postprocessed": photoshop_postprocessed,
    }


def evaluate_group_with_regeneration(
    *,
    review_client: OpenAI,
    image_client: OpenAI | None,
    settings: ReviewSettings,
    group: ImageGroup,
    regeneration_dir: Path,
) -> tuple[ImageGroup, dict[str, Any], OpenAI | None]:
    current_group = group
    regeneration_attempts: list[dict[str, Any]] = []

    while True:
        result = evaluate_image_group(client=review_client, settings=settings, group=current_group)
        result["title_regeneration_attempts"] = list(regeneration_attempts)

        if not requires_title_centering(current_group) or not result.get("all_candidates_fail_title_centering"):
            return current_group, result, image_client

        if settings.dry_run:
            result["title_regeneration_planned"] = True
            dry_run_reason = "当前候选全部未通过标题居中硬门槛，正式模式会先按原 prompt 补生新图再复评。"
            current_reason = str(result.get("winner_reason", "")).strip()
            result["winner_reason"] = f"{dry_run_reason}{current_reason}" if current_reason else dry_run_reason
            return current_group, result, image_client

        if len(regeneration_attempts) >= settings.title_retry_limit:
            raise RuntimeError(
                f"{group.display_name} 连续补生 {settings.title_retry_limit} 轮后，仍没有标题视觉居中的候选图，请人工复查该页 prompt。"
            )

        if image_client is None:
            image_client = build_image_client()

        try:
            generated = regenerate_group_candidate(
                group=current_group,
                input_dir=settings.input_dir,
                regeneration_dir=regeneration_dir,
                image_client=image_client,
            )
        except Exception as exc:
            blocked_reason = str(exc).strip()
            current_reason = str(result.get("winner_reason", "")).strip()
            result["selection_blocked"] = True
            result["selection_blocked_reason"] = blocked_reason
            result["title_regeneration_failed"] = True
            result["winner_reason"] = (
                f"当前候选全部未通过标题居中硬门槛，且补生受阻：{blocked_reason}。未将该页加入 publish。"
                + (f"当前临时最佳候选说明：{current_reason}" if current_reason else "")
            )
            return current_group, result, image_client

        regeneration_attempts.append(
            {
                "round": len(regeneration_attempts) + 1,
                "generated_image_name": generated["generated_image_name"],
                "prompt_file": generated["prompt_file"],
                "image_model": generated["image_model"],
                "image_size": generated["image_size"],
                "image_quality": generated["image_quality"],
                "photoshop_postprocessed": generated["photoshop_postprocessed"],
            }
        )
        current_group = ImageGroup(
            group_key=current_group.group_key,
            page_type=current_group.page_type,
            display_name=current_group.display_name,
            image_paths=tuple(sorted((*current_group.image_paths, generated["candidate_path"]), key=lambda item: item.name.lower())),
        )


def run_publish_selection(settings: ReviewSettings, groups: list[ImageGroup] | None = None) -> dict[str, Any]:
    review_client = build_review_client(settings)
    regeneration_dir = settings.input_dir / DEFAULT_REGEN_WORK_DIR_NAME
    if regeneration_dir.exists():
        shutil.rmtree(regeneration_dir, ignore_errors=True)
    regeneration_dir.mkdir(parents=True, exist_ok=True)

    if groups is None:
        groups = collect_image_groups(settings.input_dir)
    if not groups:
        raise RuntimeError("目标目录里没有找到可评审的图片文件。")

    report_groups: list[dict[str, Any]] = []
    image_client: OpenAI | None = None
    try:
        for group in groups:
            resolved_group, result, image_client = evaluate_group_with_regeneration(
                review_client=review_client,
                image_client=image_client,
                settings=settings,
                group=group,
                regeneration_dir=regeneration_dir,
            )

            if result.get("selection_blocked"):
                result["selected_output_path"] = ""
                report_groups.append(result)
                print(f"未选入 publish：{resolved_group.display_name} -> {result.get('selection_blocked_reason', '')}")
                continue

            winner_image_name = str(result.get("winner_image_name", "")).strip()
            if not winner_image_name:
                raise RuntimeError(f"评审结果缺少胜出图片名：{resolved_group.display_name}")

            winner_source = next((path for path in resolved_group.image_paths if path.name == winner_image_name), None)
            if winner_source is None:
                raise RuntimeError(f"找不到胜出图片文件：{resolved_group.display_name} -> {winner_image_name}")

            if settings.dry_run:
                moved_to = settings.publish_dir / winner_source.name
                print(f"[dry-run] {resolved_group.display_name} -> {winner_source.name}")
            else:
                moved_to = move_or_copy_selected_image(
                    source_file=winner_source,
                    publish_dir=settings.publish_dir,
                    copy_mode=settings.copy_mode,
                )
                print(f"已选中 {resolved_group.display_name} -> {moved_to.name}")
            result["selected_output_path"] = str(moved_to)
            report_groups.append(result)
    finally:
        shutil.rmtree(regeneration_dir, ignore_errors=True)

    report_payload = {
        "input_dir": str(settings.input_dir),
        "publish_dir": str(settings.publish_dir),
        "model": settings.model,
        "dry_run": settings.dry_run,
        "copy_mode": settings.copy_mode,
        "title_retry_limit": settings.title_retry_limit,
        "groups": report_groups,
    }

    report_file, summary_file = save_review_reports(settings.publish_dir, report_payload)
    report_payload["report_file"] = str(report_file)
    report_payload["summary_file"] = str(summary_file)
    return report_payload


def select_publish_images(
    input_dir: str | Path,
    *,
    model: str | None = None,
    dry_run: bool = False,
    copy_mode: bool = False,
    title_retry_limit: int | None = None,
    include_page_types: Sequence[str] | None = None,
) -> dict[str, Any]:
    args = argparse.Namespace(
        input_dir=str(input_dir),
        model=model,
        publish_dir_name=DEFAULT_PUBLISH_DIR_NAME,
        title_retry_limit=title_retry_limit,
        dry_run=dry_run,
        copy=copy_mode,
    )
    settings = resolve_review_settings(args)
    groups = filter_image_groups_by_page_types(
        collect_image_groups(settings.input_dir),
        include_page_types={item for item in include_page_types or ()},
    )
    return run_publish_selection(settings, groups=groups)


def main() -> int:
    args = parse_args()

    try:
        settings = resolve_review_settings(args)
        groups = collect_image_groups(settings.input_dir)
        if not groups:
            print("目标目录里没有找到可评审的图片文件。")
            return 1

        print(f"开始评审目录：{settings.input_dir}")
        print(f"评审模型：{settings.model}")
        print(f"分组数量：{len(groups)}")
        print(f"输出目录：{settings.publish_dir}")
        print(f"执行模式：{'dry-run' if settings.dry_run else ('复制' if settings.copy_mode else '移动')}")
        report_payload = run_publish_selection(settings)
        report_file = Path(str(report_payload["report_file"]))
        summary_file = Path(str(report_payload["summary_file"]))

        if settings.dry_run:
            print(f"评分报告 JSON：{report_file}")
            print(f"评分摘要 TXT：{summary_file}")
            print("dry-run 结束：已完成评分和胜出图选择，未移动文件。")
        else:
            print(f"评分报告 JSON：{report_file}")
            print(f"评分摘要 TXT：{summary_file}")
            selected_count = sum(1 for item in report_payload.get("groups", []) if str(item.get("selected_output_path", "")).strip())
            print(f"publish 入选图片数量：{selected_count}")
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())