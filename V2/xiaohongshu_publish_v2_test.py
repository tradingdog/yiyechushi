from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
V2_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

from tools.runtime_deps import ensure_project_runtime_dependencies  # noqa: E402

ensure_project_runtime_dependencies()

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

from image_generator import split_description_body_and_tags  # noqa: E402
from tools.douyin_publish import (  # noqa: E402
    DEFAULT_AUTOMATION_PROFILE_DIR,
    DEFAULT_CDP_READY_TIMEOUT_MS,
    DEFAULT_CDP_URL,
    DEFAULT_TYPING_DELAY_MS,
    find_default_chrome_path,
    find_single_file,
    read_utf8_text,
    resolve_path,
)
from tools.xiaohongshu_publish import (  # noqa: E402
    DEFAULT_AFTER_CHECKBOX_WAIT_MS,
    DEFAULT_AFTER_DECLARE_ORIGINAL_WAIT_MS,
    DEFAULT_AFTER_GRAPHIC_MENU_WAIT_MS,
    DEFAULT_AFTER_LOCATION_INPUT_WAIT_MS,
    DEFAULT_AFTER_ORIGINAL_DIALOG_WAIT_MS,
    DEFAULT_AFTER_TOPIC_CONFIRM_WAIT_MS,
    DEFAULT_AFTER_UPLOAD_WAIT_MS,
    DEFAULT_BETWEEN_TOPICS_WAIT_MS,
    DEFAULT_DEBUG_SCREENSHOT,
    DEFAULT_LOGIN_MARKER,
    DEFAULT_LOGIN_MATCH_THRESHOLD,
    DEFAULT_PUBLISH_GRAPHIC_CLICK_X,
    DEFAULT_PUBLISH_GRAPHIC_CLICK_Y,
    DEFAULT_PUBLISH_LOCATION,
    DEFAULT_PUBLISH_MENU_HOVER_X,
    DEFAULT_PUBLISH_MENU_HOVER_Y,
    DEFAULT_TOPIC_TYPING_DELAY_MS,
    DEFAULT_UPLOAD_STEP_SCREENSHOT,
    DEFAULT_URL_KEYWORD,
    DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS,
    XiaohongshuPublishAssets,
    XiaohongshuPublishSettings,
    run_xiaohongshu_publish,
)
from publish_final_assets import resolve_publish_image_triplet  # noqa: E402


DEFAULT_V2_OUTPUT_DIR = V2_DIR / "output" / "20260604_210303_葱香陈皮羊排"
DEFAULT_FINAL_DIR_NAME = "publish/final"
XIAOHONGSHU_TITLE_SUFFIX = "_小红书标题.txt"
XIAOHONGSHU_DESCRIPTION_SUFFIX = "_小红书图文描述.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "V2 小红书发布测试脚本：读取 output 子目录里的小红书标题/图文描述，"
            "并按 publish/final 内 01/02/03 三张图执行小红书图文发布流程。"
        )
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(DEFAULT_V2_OUTPUT_DIR),
        help=f"V2 单次输出目录，默认 {DEFAULT_V2_OUTPUT_DIR}",
    )
    parser.add_argument("--final-dir", default="", help="final 图片目录；不传时使用 output_dir/publish/final。")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--url-keyword", default=DEFAULT_URL_KEYWORD)
    parser.add_argument("--chrome-path", default="")
    parser.add_argument("--automation-profile-dir", default=str(DEFAULT_AUTOMATION_PROFILE_DIR))
    parser.add_argument("--cdp-ready-timeout-ms", type=int, default=DEFAULT_CDP_READY_TIMEOUT_MS)
    parser.add_argument("--allow-auto-launch-browser", action="store_true", help="允许脚本自动拉起 Chrome（默认不自动拉起）。")
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--topic-typing-delay-ms", type=int, default=DEFAULT_TOPIC_TYPING_DELAY_MS)
    parser.add_argument("--between-topics-wait-ms", type=int, default=DEFAULT_BETWEEN_TOPICS_WAIT_MS)
    parser.add_argument("--after-upload-wait-ms", type=int, default=DEFAULT_AFTER_UPLOAD_WAIT_MS)
    parser.add_argument("--login-marker", default=str(DEFAULT_LOGIN_MARKER))
    parser.add_argument("--login-match-threshold", type=float, default=DEFAULT_LOGIN_MATCH_THRESHOLD)
    parser.add_argument("--publish-menu-hover-x", type=int, default=DEFAULT_PUBLISH_MENU_HOVER_X)
    parser.add_argument("--publish-menu-hover-y", type=int, default=DEFAULT_PUBLISH_MENU_HOVER_Y)
    parser.add_argument("--publish-graphic-click-x", type=int, default=DEFAULT_PUBLISH_GRAPHIC_CLICK_X)
    parser.add_argument("--publish-graphic-click-y", type=int, default=DEFAULT_PUBLISH_GRAPHIC_CLICK_Y)
    parser.add_argument("--after-graphic-menu-wait-ms", type=int, default=DEFAULT_AFTER_GRAPHIC_MENU_WAIT_MS)
    parser.add_argument("--publish-location", default=DEFAULT_PUBLISH_LOCATION)
    parser.add_argument("--windows-open-dialog-wait-ms", type=int, default=DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS)
    parser.add_argument("--upload-step-screenshot", default=str(DEFAULT_UPLOAD_STEP_SCREENSHOT))
    parser.add_argument("--debug-screenshot", default=str(DEFAULT_DEBUG_SCREENSHOT))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _find_xiaohongshu_title_file(output_dir: Path) -> Path:
    for suffix in (XIAOHONGSHU_TITLE_SUFFIX, "_图文标题.txt"):
        matches = sorted(output_dir.glob(f"*{suffix}"))
        if matches:
            if suffix != XIAOHONGSHU_TITLE_SUFFIX:
                print(f"未找到小红书专用标题文件，回退使用：{matches[-1].name}")
            return matches[-1]
    raise RuntimeError(f"未找到小红书标题文件：{output_dir}")


def _resolve_topic_tags(description_text: str, description_file: Path) -> tuple[str, ...]:
    _body, tags = split_description_body_and_tags(description_text)
    if not tags:
        raise RuntimeError(f"未从 {description_file.name} 中解析到小红书话题行。")
    return tuple(tags)


def resolve_final_dir(output_dir: Path, final_dir_text: str) -> Path:
    if final_dir_text.strip():
        final_dir = resolve_path(final_dir_text.strip())
    else:
        final_dir = output_dir / DEFAULT_FINAL_DIR_NAME
    if not final_dir.exists() or not final_dir.is_dir():
        raise RuntimeError(f"final 图片目录不存在：{final_dir}")
    return final_dir


def resolve_settings(args: argparse.Namespace) -> XiaohongshuPublishSettings:
    output_dir = resolve_path(args.output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"输出目录不存在：{output_dir}")

    chrome_path_text = str(args.chrome_path or "").strip()
    chrome_path = resolve_path(chrome_path_text) if chrome_path_text else find_default_chrome_path()

    return XiaohongshuPublishSettings(
        output_dir=output_dir,
        cdp_url=str(args.cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
        url_keyword=str(args.url_keyword or DEFAULT_URL_KEYWORD).strip() or DEFAULT_URL_KEYWORD,
        chrome_path=chrome_path,
        automation_profile_dir=resolve_path(args.automation_profile_dir),
        auto_launch_browser=bool(args.allow_auto_launch_browser),
        cdp_ready_timeout_ms=max(0, int(args.cdp_ready_timeout_ms)),
        typing_delay_ms=max(0, int(args.typing_delay_ms)),
        topic_typing_delay_ms=max(0, int(args.topic_typing_delay_ms)),
        login_marker_path=resolve_path(args.login_marker),
        login_match_threshold=float(args.login_match_threshold),
        publish_menu_hover_x=int(args.publish_menu_hover_x),
        publish_menu_hover_y=int(args.publish_menu_hover_y),
        publish_graphic_click_x=int(args.publish_graphic_click_x),
        publish_graphic_click_y=int(args.publish_graphic_click_y),
        after_graphic_menu_wait_ms=max(0, int(args.after_graphic_menu_wait_ms)),
        after_upload_wait_ms=max(0, int(args.after_upload_wait_ms)),
        after_original_dialog_wait_ms=DEFAULT_AFTER_ORIGINAL_DIALOG_WAIT_MS,
        after_checkbox_wait_ms=DEFAULT_AFTER_CHECKBOX_WAIT_MS,
        after_declare_original_wait_ms=DEFAULT_AFTER_DECLARE_ORIGINAL_WAIT_MS,
        after_location_input_wait_ms=DEFAULT_AFTER_LOCATION_INPUT_WAIT_MS,
        after_topic_confirm_wait_ms=DEFAULT_AFTER_TOPIC_CONFIRM_WAIT_MS,
        between_topics_wait_ms=max(0, int(args.between_topics_wait_ms)),
        publish_location=str(args.publish_location or DEFAULT_PUBLISH_LOCATION).strip() or DEFAULT_PUBLISH_LOCATION,
        windows_open_dialog_wait_ms=max(0, int(args.windows_open_dialog_wait_ms)),
        upload_step_screenshot=resolve_path(args.upload_step_screenshot),
        debug_screenshot=resolve_path(args.debug_screenshot),
        dry_run=bool(args.dry_run),
    )


def resolve_xiaohongshu_assets(args: argparse.Namespace) -> XiaohongshuPublishAssets:
    settings = resolve_settings(args)
    output_dir = settings.output_dir
    final_dir = resolve_final_dir(output_dir, str(args.final_dir or ""))

    title_file = _find_xiaohongshu_title_file(output_dir)
    description_file = find_single_file(output_dir, XIAOHONGSHU_DESCRIPTION_SUFFIX)
    title_text = read_utf8_text(title_file)
    description_text = read_utf8_text(description_file)
    description_body, _inline_tags = split_description_body_and_tags(description_text)
    topic_tags = _resolve_topic_tags(description_text, description_file)

    if not title_text:
        raise RuntimeError(f"小红书标题为空：{title_file}")
    if not description_body:
        raise RuntimeError(f"小红书描述正文为空：{description_file}")

    poster_image, detail_image, recipe_image = resolve_publish_image_triplet(final_dir)

    return XiaohongshuPublishAssets(
        output_dir=output_dir,
        final_dir=final_dir,
        image_paths=(poster_image, detail_image, recipe_image),
        title_text=title_text,
        description_body=description_body,
        topic_tags=topic_tags,
        title_file=title_file,
        description_file=description_file,
    )


def log_assets(assets: XiaohongshuPublishAssets) -> None:
    print(f"输出目录：{assets.output_dir}")
    print(f"final 目录：{assets.final_dir}")
    print(f"标题文件：{assets.title_file}")
    print(f"描述文件：{assets.description_file}")
    print(f"正文长度：{len(assets.description_body)}，话题数量：{len(assets.topic_tags)}")
    for image_path in assets.image_paths:
        print(f"已识别上传图片：{image_path}")


def main() -> int:
    args = parse_args()
    try:
        settings = resolve_settings(args)
        assets = resolve_xiaohongshu_assets(args)
        log_assets(assets)
        print("小红书上传顺序：01 海报 -> 02 细节图 -> 03 菜谱图（不含封面）。")
        if settings.dry_run:
            print("dry-run 校验完成，未连接 Chrome。")
            return 0
        run_xiaohongshu_publish(settings, assets)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}")
        return 1

    print("V2 小红书发布测试动作已执行完成，请回到浏览器检查笔记编辑状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
