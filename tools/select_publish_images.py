from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from image_generator import (  # noqa: E402
    DEFAULT_DOUBAO_BASE_URL,
    DEFAULT_DOUBAO_TEXT_MODEL,
    ensure_runtime_config_loaded,
    get_text_request_timeout_seconds,
    is_timeout_error,
)


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DEFAULT_PUBLISH_DIR_NAME = "publish"
DEFAULT_REPORT_FILE_NAME = "publish_selection_report.json"
DEFAULT_SUMMARY_FILE_NAME = "publish_selection_report.txt"


@dataclass(frozen=True)
class ReviewSettings:
    input_dir: Path
    publish_dir: Path
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
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
    parser.add_argument("--dry-run", action="store_true", help="只生成评分结果，不移动文件。")
    parser.add_argument("--copy", action="store_true", help="复制最佳图到 publish，而不是移动。")
    return parser.parse_args()


def resolve_path(path_text: str | Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (ROOT_DIR / candidate).resolve()


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
    publish_dir_name = str(args.publish_dir_name or DEFAULT_PUBLISH_DIR_NAME).strip() or DEFAULT_PUBLISH_DIR_NAME
    publish_dir = input_dir / publish_dir_name

    return ReviewSettings(
        input_dir=input_dir,
        publish_dir=publish_dir,
        api_key=api_key,
        base_url=base_url,
        model=review_model,
        timeout_seconds=get_text_request_timeout_seconds(),
        dry_run=bool(args.dry_run),
        copy_mode=bool(args.copy),
    )


def build_review_client(settings: ReviewSettings) -> OpenAI:
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout_seconds)


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


def build_group_rubric(group: ImageGroup) -> tuple[str, tuple[str, ...]]:
    if group.page_type == "page01":
        return (
            "页面类型：首图。重点看标题和黄条是否沿中轴居中、主菜照片是否更像真实手机拍摄、哪张更有食欲、哪张整体排版更顺眼，图片里的文案和食材卡是否存在明显逻辑错误、错字或离谱内容。\n"
            "权重建议：标题居中与版心 25，主菜真实感 25，食欲 20，整体排版与信息层级 15，文案逻辑与正确性 15。\n"
            "硬性扣分：标题明显偏左偏右、食材卡和菜本身逻辑冲突、明显乱码、品牌包装或可读商品标签、明显假光和塑料食物感。",
            ("标题居中与版心", "主菜真实感", "食欲", "整体排版与信息层级", "文案逻辑与正确性"),
        )

    if group.page_type == "guide_page":
        return (
            "页面类型：图解02到图解06。重点看标题是否水平居中、有没有品牌文字或可读标签的酱料瓶/锅具/食材包装、哪张更符合人类阅读顺序和卡片层级，另外要严查那种出现刻度图、温度条、进度环或量表却没有具体指标的假信息图。\n"
            "权重建议：标题居中 20，无品牌与无可读标签 25，阅读顺序与版式清晰度 25，文字正确性与可读性 20，信息图有效性 10。\n"
            "硬性扣分：品牌名、包装标签、明显假商标、乱码、标题不居中、出现没有具体数字或指标的刻度图/量表/仪表盘。",
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

    prompt_text = f"""
当前页面：{group.display_name}
候选图片数量：{len(group.image_paths)}

{rubric_text}

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
      "total_score": 0,
      "hard_issues": ["如果没有硬伤就返回空数组"],
      "summary": "一句话总结优缺点"
    }}
  ]
}}
""".strip()

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
    for index, image_path in enumerate(group.image_paths, start=1):
        content.append({"type": "text", "text": f"候选{index}，文件名：{image_path.name}"})
        content.append({"type": "image_url", "image_url": {"url": encode_image_as_data_url(image_path)}})
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


def parse_json_object(text: str) -> dict[str, Any]:
    raw_text = text.strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        start_index = raw_text.find("{")
        end_index = raw_text.rfind("}")
        if start_index < 0 or end_index < 0 or end_index <= start_index:
            raise RuntimeError("豆包返回的评分结果不是有效 JSON。")
        payload = json.loads(raw_text[start_index : end_index + 1])

    if not isinstance(payload, dict):
        raise RuntimeError("豆包返回的评分结果不是 JSON 对象。")
    return payload


def coerce_score(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def evaluate_image_group(client: OpenAI, settings: ReviewSettings, group: ImageGroup) -> dict[str, Any]:
    if len(group.image_paths) == 1:
        only_path = group.image_paths[0]
        return {
            "group_label": group.display_name,
            "page_type": group.page_type,
            "auto_selected": True,
            "winner_index": 1,
            "winner_image_name": only_path.name,
            "winner_reason": "该页只有 1 张图片，按规则直接入 publish，不调用 AI 分析。",
            "candidates": [
                {
                    "index": 1,
                    "image_name": only_path.name,
                    "dimension_scores": {},
                    "total_score": 100,
                    "hard_issues": [],
                    "summary": "单图直入 publish。",
                }
            ],
        }

    reference_text = build_reference_text(group, settings.input_dir)
    print(f"正在用豆包评审：{group.display_name}，候选 {len(group.image_paths)} 张，模型 {settings.model}")

    try:
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "system", "content": build_review_system_prompt()},
                {"role": "user", "content": build_review_user_content(group, reference_text)},
            ],
            timeout=settings.timeout_seconds,
            max_tokens=1800,
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
    winner_index = coerce_score(payload.get("winner_index"))
    if winner_index < 1 or winner_index > len(group.image_paths):
        raise RuntimeError(f"豆包返回的 winner_index 非法：{group.display_name} -> {winner_index}")

    winner_image_path = group.image_paths[winner_index - 1]
    payload["group_label"] = group.display_name
    payload["page_type"] = group.page_type
    payload["winner_index"] = winner_index
    payload["winner_image_name"] = winner_image_path.name

    normalized_candidates: list[dict[str, Any]] = []
    raw_candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    for index, image_path in enumerate(group.image_paths, start=1):
        raw_candidate = next(
            (
                item
                for item in raw_candidates
                if isinstance(item, dict) and coerce_score(item.get("index")) == index
            ),
            {},
        )
        normalized_candidates.append(
            {
                "index": index,
                "image_name": image_path.name,
                "dimension_scores": raw_candidate.get("dimension_scores", {}) if isinstance(raw_candidate, dict) else {},
                "total_score": coerce_score(raw_candidate.get("total_score")) if isinstance(raw_candidate, dict) else 0,
                "hard_issues": raw_candidate.get("hard_issues", []) if isinstance(raw_candidate, dict) else [],
                "summary": raw_candidate.get("summary", "") if isinstance(raw_candidate, dict) else "",
            }
        )
    payload["candidates"] = normalized_candidates
    return payload


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
        for candidate in result.get("candidates", []):
            lines.append(
                f"- 候选{candidate.get('index', '')} {candidate.get('image_name', '')} | 总分 {candidate.get('total_score', 0)} | 硬伤 {candidate.get('hard_issues', [])}"
            )
            summary = str(candidate.get("summary", "")).strip()
            if summary:
                lines.append(f"  {summary}")
        lines.append("")

    summary_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return report_file, summary_file


def select_publish_images(input_dir: str | Path, *, model: str | None = None, dry_run: bool = False, copy_mode: bool = False) -> dict[str, Any]:
    args = argparse.Namespace(
        input_dir=str(input_dir),
        model=model,
        publish_dir_name=DEFAULT_PUBLISH_DIR_NAME,
        dry_run=dry_run,
        copy=copy_mode,
    )
    settings = resolve_review_settings(args)
    client = build_review_client(settings)
    groups = collect_image_groups(settings.input_dir)
    if not groups:
        raise RuntimeError("目标目录里没有找到可评审的图片文件。")

    report_groups: list[dict[str, Any]] = []
    for group in groups:
        result = evaluate_image_group(client=client, settings=settings, group=group)
        winner_image_name = str(result.get("winner_image_name", "")).strip()
        if not winner_image_name:
            raise RuntimeError(f"评审结果缺少胜出图片名：{group.display_name}")

        winner_source = next((path for path in group.image_paths if path.name == winner_image_name), None)
        if winner_source is None:
            raise RuntimeError(f"找不到胜出图片文件：{group.display_name} -> {winner_image_name}")

        if settings.dry_run:
            moved_to = settings.publish_dir / winner_source.name
        else:
            moved_to = move_or_copy_selected_image(
                source_file=winner_source,
                publish_dir=settings.publish_dir,
                copy_mode=settings.copy_mode,
            )
        result["selected_output_path"] = str(moved_to)
        report_groups.append(result)

    report_payload = {
        "input_dir": str(settings.input_dir),
        "publish_dir": str(settings.publish_dir),
        "model": settings.model,
        "dry_run": settings.dry_run,
        "copy_mode": settings.copy_mode,
        "groups": report_groups,
    }

    report_file, summary_file = save_review_reports(settings.publish_dir, report_payload)
    report_payload["report_file"] = str(report_file)
    report_payload["summary_file"] = str(summary_file)

    return report_payload


def main() -> int:
    args = parse_args()

    try:
        settings = resolve_review_settings(args)
        client = build_review_client(settings)
        groups = collect_image_groups(settings.input_dir)
        if not groups:
            print("目标目录里没有找到可评审的图片文件。")
            return 1

        print(f"开始评审目录：{settings.input_dir}")
        print(f"评审模型：{settings.model}")
        print(f"分组数量：{len(groups)}")
        print(f"输出目录：{settings.publish_dir}")
        print(f"执行模式：{'dry-run' if settings.dry_run else ('复制' if settings.copy_mode else '移动')}")

        report_groups: list[dict[str, Any]] = []
        for group in groups:
            result = evaluate_image_group(client=client, settings=settings, group=group)
            winner_image_name = str(result.get("winner_image_name", "")).strip()
            winner_source = next((path for path in group.image_paths if path.name == winner_image_name), None)
            if winner_source is None:
                raise RuntimeError(f"找不到胜出图片文件：{group.display_name} -> {winner_image_name}")

            if settings.dry_run:
                selected_path = settings.publish_dir / winner_source.name
                print(f"[dry-run] {group.display_name} -> {winner_source.name}")
            else:
                selected_path = move_or_copy_selected_image(
                    source_file=winner_source,
                    publish_dir=settings.publish_dir,
                    copy_mode=settings.copy_mode,
                )
                print(f"已选中 {group.display_name} -> {selected_path.name}")

            result["selected_output_path"] = str(selected_path)
            report_groups.append(result)

        report_payload = {
            "input_dir": str(settings.input_dir),
            "publish_dir": str(settings.publish_dir),
            "model": settings.model,
            "dry_run": settings.dry_run,
            "copy_mode": settings.copy_mode,
            "groups": report_groups,
        }

        report_file, summary_file = save_review_reports(settings.publish_dir, report_payload)
        report_payload["report_file"] = str(report_file)
        report_payload["summary_file"] = str(summary_file)

        if settings.dry_run:
            print(f"评分报告 JSON：{report_file}")
            print(f"评分摘要 TXT：{summary_file}")
            print("dry-run 结束：已完成评分和胜出图选择，未移动文件。")
        else:
            print(f"评分报告 JSON：{report_file}")
            print(f"评分摘要 TXT：{summary_file}")
            print(f"publish 入选图片数量：{len(report_groups)}")
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())