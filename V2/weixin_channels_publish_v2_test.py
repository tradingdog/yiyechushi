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

from script_logging import setup_script_logging  # noqa: E402

if __name__ == "__main__":
    setup_script_logging(__file__)

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
from tools.weixin_channels_publish import (  # noqa: E402
    DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS,
    DEFAULT_AFTER_UPLOAD_WAIT_MS,
    DEFAULT_AFTER_UPLOAD_TITLE_WAIT_MS,
    DEFAULT_BETWEEN_TOPICS_WAIT_MS,
    DEFAULT_DEBUG_SCREENSHOT,
    DEFAULT_LOGIN_MARKER,
    DEFAULT_LOGIN_MATCH_THRESHOLD,
    DEFAULT_MENU_CONTENT_MANAGE_MARKER,
    DEFAULT_MENU_GRAPHIC_MARKER,
    DEFAULT_PUBLISH_GRAPHIC_BUTTON_MARKER,
    DEFAULT_STEP_WAIT_MS,
    DEFAULT_TEMPLATE_MATCH_THRESHOLD,
    DEFAULT_UPLOAD_AREA_MARKER,
    DEFAULT_STEP_SCREENSHOT_DIR,
    DEFAULT_UPLOAD_STEP_SCREENSHOT,
    DEFAULT_URL_KEYWORD,
    DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS,
    WeixinChannelsPublishAssets,
    WeixinChannelsPublishSettings,
    parse_topic_tags,
    run_weixin_channels_publish,
)
from publish_final_assets import (  # noqa: E402
    resolve_publish_final_dir,
    resolve_publish_image_triplet,
    split_wechat_description_parts,
)


DEFAULT_V2_OUTPUT_DIR = V2_DIR / "output" / "20260604_210303_葱香陈皮羊排"
DEFAULT_FINAL_DIR_NAME = "publish/final"
WECHAT_TITLE_SUFFIXES = (
    "_微信视频号标题.txt",
    "_微信视频号和公众号标题.txt",
    "_图文标题.txt",
)
WECHAT_DESCRIPTION_SUFFIXES = (
    "_微信视频号图文描述.txt",
    "_微信视频号和公众号图文描述.txt",
)
WECHAT_TOPIC_SUFFIXES = (
    "_微信视频号话题.txt",
    "_微信视频号和公众号话题.txt",
)
MAX_CHANNELS_TOPIC_COUNT = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "V2 微信视频号发布测试：锁定 channels.weixin.qq.com，"
            "上传 01/02/03 三张图，填写通用图文标题与公众号描述/话题。"
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
    parser.add_argument("--no-auto-launch-browser", action="store_true", help="不自动拉起 Chrome，连不上 CDP 时直接失败。")
    parser.add_argument("--cdp-ready-timeout-ms", type=int, default=DEFAULT_CDP_READY_TIMEOUT_MS)
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--login-marker", default=str(DEFAULT_LOGIN_MARKER))
    parser.add_argument("--login-match-threshold", type=float, default=DEFAULT_LOGIN_MATCH_THRESHOLD)
    parser.add_argument("--step-wait-ms", type=int, default=DEFAULT_STEP_WAIT_MS)
    parser.add_argument("--after-topic-paste-wait-ms", type=int, default=DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS)
    parser.add_argument("--between-topics-wait-ms", type=int, default=DEFAULT_BETWEEN_TOPICS_WAIT_MS)
    parser.add_argument("--windows-open-dialog-wait-ms", type=int, default=DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS)
    parser.add_argument("--upload-step-screenshot", default=str(DEFAULT_UPLOAD_STEP_SCREENSHOT))
    parser.add_argument("--debug-screenshot", default=str(DEFAULT_DEBUG_SCREENSHOT))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _find_output_file(output_dir: Path, suffixes: tuple[str, ...], label: str) -> Path:
    for suffix in suffixes:
        matches = sorted(output_dir.glob(f"*{suffix}"))
        if matches:
            if suffix != suffixes[0]:
                print(f"未找到{label}首选文件，回退使用：{matches[-1].name}")
            return matches[-1]
    raise RuntimeError(f"未找到{label}文件（已尝试：{', '.join(suffixes)}）：{output_dir}")


def _resolve_topic_tags(output_dir: Path, fallback_topics_line: str) -> tuple[str, ...]:
    topic_tags: tuple[str, ...] = ()
    topic_files = [path for suffix in WECHAT_TOPIC_SUFFIXES for path in sorted(output_dir.glob(f"*{suffix}"))]
    if topic_files:
        topic_tags = parse_topic_tags(read_utf8_text(topic_files[-1]))
        if topic_tags:
            print(f"话题来自专用文件（{len(topic_tags)} 个）：{topic_files[-1].name}")
    if not topic_tags:
        topic_tags = parse_topic_tags(fallback_topics_line)
        if topic_tags:
            print(f"话题来自图文描述第 2 行（{len(topic_tags)} 个）。")
    if len(topic_tags) > MAX_CHANNELS_TOPIC_COUNT:
        print(f"视频号话题超过 {MAX_CHANNELS_TOPIC_COUNT} 个，已截断为前 {MAX_CHANNELS_TOPIC_COUNT} 个。")
        topic_tags = topic_tags[:MAX_CHANNELS_TOPIC_COUNT]
    return topic_tags


def resolve_settings(args: argparse.Namespace) -> WeixinChannelsPublishSettings:
    output_dir = resolve_path(args.output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"输出目录不存在：{output_dir}")

    chrome_path_text = str(args.chrome_path or "").strip()
    chrome_path = resolve_path(chrome_path_text) if chrome_path_text else find_default_chrome_path()

    return WeixinChannelsPublishSettings(
        output_dir=output_dir,
        cdp_url=str(args.cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
        url_keyword=str(args.url_keyword or DEFAULT_URL_KEYWORD).strip() or DEFAULT_URL_KEYWORD,
        chrome_path=chrome_path,
        automation_profile_dir=resolve_path(args.automation_profile_dir),
        auto_launch_browser=not bool(args.no_auto_launch_browser),
        cdp_ready_timeout_ms=max(0, int(args.cdp_ready_timeout_ms)),
        typing_delay_ms=max(0, int(args.typing_delay_ms)),
        login_marker_path=resolve_path(args.login_marker),
        menu_content_manage_marker_path=DEFAULT_MENU_CONTENT_MANAGE_MARKER,
        menu_graphic_marker_path=DEFAULT_MENU_GRAPHIC_MARKER,
        publish_graphic_button_marker_path=DEFAULT_PUBLISH_GRAPHIC_BUTTON_MARKER,
        upload_area_marker_path=DEFAULT_UPLOAD_AREA_MARKER,
        login_match_threshold=float(args.login_match_threshold),
        template_match_threshold=DEFAULT_TEMPLATE_MATCH_THRESHOLD,
        step_wait_ms=max(0, int(args.step_wait_ms)),
        after_upload_wait_ms=DEFAULT_AFTER_UPLOAD_WAIT_MS,
        after_upload_title_wait_ms=DEFAULT_AFTER_UPLOAD_TITLE_WAIT_MS,
        after_topic_paste_wait_ms=max(0, int(args.after_topic_paste_wait_ms)),
        between_topics_wait_ms=max(0, int(args.between_topics_wait_ms)),
        windows_open_dialog_wait_ms=max(0, int(args.windows_open_dialog_wait_ms)),
        upload_step_screenshot=resolve_path(args.upload_step_screenshot),
        step_screenshot_dir=DEFAULT_STEP_SCREENSHOT_DIR,
        debug_screenshot=resolve_path(args.debug_screenshot),
        dry_run=bool(args.dry_run),
    )


def resolve_channels_assets(args: argparse.Namespace) -> WeixinChannelsPublishAssets:
    settings = resolve_settings(args)
    output_dir = settings.output_dir
    final_dir = resolve_publish_final_dir(output_dir, final_dir_text=str(args.final_dir or ""))

    title_file = _find_output_file(output_dir, WECHAT_TITLE_SUFFIXES, "视频号标题")
    description_file = _find_output_file(output_dir, WECHAT_DESCRIPTION_SUFFIXES, "视频号图文描述")
    title_text = read_utf8_text(title_file)
    description_body, topics_line = split_wechat_description_parts(read_utf8_text(description_file))
    topic_tags = _resolve_topic_tags(output_dir, topics_line)

    if not title_text:
        raise RuntimeError(f"标题为空：{title_file}")
    if not description_body:
        raise RuntimeError(f"描述正文为空：{description_file}")
    if not topic_tags:
        raise RuntimeError(f"未解析到话题标签：{description_file}")

    poster_image, detail_image, recipe_image = resolve_publish_image_triplet(final_dir)

    return WeixinChannelsPublishAssets(
        output_dir=output_dir,
        final_dir=final_dir,
        image_paths=(poster_image, detail_image, recipe_image),
        title_text=title_text,
        description_body=description_body,
        topic_tags=topic_tags,
        title_file=title_file,
        description_file=description_file,
    )


def log_assets(assets: WeixinChannelsPublishAssets) -> None:
    print(f"输出目录：{assets.output_dir}")
    print(f"final 目录：{assets.final_dir}")
    print(f"标题文件：{assets.title_file}")
    print(f"描述文件：{assets.description_file}")
    print(f"描述正文长度：{len(assets.description_body)}，话题：{' '.join(assets.topic_tags)}")
    for image_path in assets.image_paths:
        print(f"已识别上传图片：{image_path}")


def main() -> int:
    args = parse_args()
    try:
        settings = resolve_settings(args)
        assets = resolve_channels_assets(args)
        log_assets(assets)
        print("视频号上传顺序：01 海报 -> 02 细节图 -> 03 菜谱图（不含封面）。")
        if settings.dry_run:
            print("dry-run 校验完成，未连接 Chrome。")
            return 0
        run_weixin_channels_publish(settings, assets)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}")
        return 1

    print("V2 微信视频号发布测试已执行完成，请回到浏览器检查页面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
