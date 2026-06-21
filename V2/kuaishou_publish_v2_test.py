from __future__ import annotations

import argparse
import re
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
    normalize_schedule_at,
    read_utf8_text,
    resolve_path,
)
from tools.kuaishou_publish import (  # noqa: E402
    DEFAULT_AFTER_AUTHOR_SELECT_WAIT_MS,
    DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS,
    DEFAULT_AFTER_COVER_UPLOAD_WAIT_MS,
    DEFAULT_AFTER_GRAPHIC_TAB_WAIT_MS,
    DEFAULT_AFTER_LOCATION_OPEN_WAIT_MS,
    DEFAULT_AFTER_MAIN_UPLOAD_WAIT_MS,
    DEFAULT_AFTER_OPEN_COVER_WAIT_MS,
    DEFAULT_AFTER_PUBLISH_WORK_WAIT_MS,
    DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS,
    DEFAULT_AFTER_UPLOAD_BUTTON_WAIT_MS,
    DEFAULT_BETWEEN_TOPICS_WAIT_MS,
    DEFAULT_DEBUG_SCREENSHOT,
    DEFAULT_PUBLISH_LOCATION,
    DEFAULT_UPLOAD_STEP_SCREENSHOT,
    DEFAULT_URL_KEYWORD,
    DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS,
    EXPECTED_TOPIC_COUNT,
    KuaishouPublishAssets,
    KuaishouPublishSettings,
    run_kuaishou_publish,
)
from publish_final_assets import (  # noqa: E402
    resolve_publish_cover_image,
    resolve_publish_final_dir,
    resolve_publish_image_triplet,
)


DEFAULT_V2_OUTPUT_DIR = V2_DIR / "output" / "20260604_210303_葱香陈皮羊排"
DEFAULT_FINAL_DIR_NAME = "publish/final"
KUAISHOU_TITLE_SUFFIX = "_快手标题.txt"
KUAISHOU_DESCRIPTION_SUFFIX = "_快手图文描述.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "V2 快手发布测试脚本：读取 output 子目录里的快手标题/图文描述，"
            "上传 final 内 01/02/03 三张图与 04 封面；作品描述填标题、正文，"
            "4 个话题用剪贴板逐个 Ctrl+V 粘贴（第 2 个起先空格）。"
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
    parser.add_argument("--no-auto-launch-browser", action="store_true", help="不自动拉起 Chrome，连不上 CDP 时直接失败。")
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument(
        "--after-topic-paste-wait-ms",
        type=int,
        default=DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS,
        help="每个话题粘贴后的等待毫秒数。",
    )
    parser.add_argument("--between-topics-wait-ms", type=int, default=DEFAULT_BETWEEN_TOPICS_WAIT_MS)
    parser.add_argument("--publish-location", default=DEFAULT_PUBLISH_LOCATION)
    parser.add_argument("--windows-open-dialog-wait-ms", type=int, default=DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS)
    parser.add_argument("--upload-step-screenshot", default=str(DEFAULT_UPLOAD_STEP_SCREENSHOT))
    parser.add_argument("--debug-screenshot", default=str(DEFAULT_DEBUG_SCREENSHOT))
    parser.add_argument(
        "--schedule-at",
        default="",
        help="定时发布时间，格式 yyyy-MM-dd HH:mm；传入后会自动勾选「定时发布」并填写时间。",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _dish_name_from_output_dir(output_dir: Path) -> str:
    match = re.match(r"\d{8}_\d{6}_(.+)", output_dir.name)
    if match:
        return match.group(1).strip()
    return output_dir.name.strip()


def _resolve_kuaishou_title(output_dir: Path) -> tuple[str, Path | None]:
    for suffix in (KUAISHOU_TITLE_SUFFIX, "_图文标题.txt"):
        matches = sorted(output_dir.glob(f"*{suffix}"))
        if matches:
            title_file = matches[-1]
            title_text = read_utf8_text(title_file)
            if title_text:
                if suffix != KUAISHOU_TITLE_SUFFIX:
                    print(f"未找到快手专用标题文件，回退使用：{title_file.name}")
                return title_text, title_file

    dish_name = _dish_name_from_output_dir(output_dir)
    if dish_name:
        print(f"未找到快手标题文件，回退使用目录菜名：{dish_name}")
        return dish_name, None

    raise RuntimeError(f"未找到快手标题文件，且无法从目录名解析菜名：{output_dir}")


def _resolve_kuaishou_description_parts(description_text: str, description_file: Path) -> tuple[str, tuple[str, ...]]:
    description_body, topic_tags = split_description_body_and_tags(description_text)
    if not description_body:
        lines = [line.strip() for line in description_text.splitlines() if line.strip()]
        if lines and not lines[0].replace(" ", "").startswith("#"):
            description_body = lines[0]
    if not description_body:
        raise RuntimeError(f"未从 {description_file.name} 中解析到快手描述正文（第 1 行）。")
    if not topic_tags:
        raise RuntimeError(f"未从 {description_file.name} 中解析到快手话题（第 2 行）。")
    if len(topic_tags) != EXPECTED_TOPIC_COUNT:
        raise RuntimeError(
            f"快手话题必须为 {EXPECTED_TOPIC_COUNT} 个，当前为 {len(topic_tags)} 个：{description_file}"
        )
    return description_body, tuple(topic_tags)


def resolve_settings(args: argparse.Namespace) -> KuaishouPublishSettings:
    output_dir = resolve_path(args.output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"输出目录不存在：{output_dir}")

    chrome_path_text = str(args.chrome_path or "").strip()
    chrome_path = resolve_path(chrome_path_text) if chrome_path_text else find_default_chrome_path()

    return KuaishouPublishSettings(
        output_dir=output_dir,
        cdp_url=str(args.cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
        url_keyword=str(args.url_keyword or DEFAULT_URL_KEYWORD).strip() or DEFAULT_URL_KEYWORD,
        chrome_path=chrome_path,
        automation_profile_dir=resolve_path(args.automation_profile_dir),
        auto_launch_browser=not bool(args.no_auto_launch_browser),
        cdp_ready_timeout_ms=max(0, int(args.cdp_ready_timeout_ms)),
        typing_delay_ms=max(0, int(args.typing_delay_ms)),
        after_publish_work_wait_ms=DEFAULT_AFTER_PUBLISH_WORK_WAIT_MS,
        after_graphic_tab_wait_ms=DEFAULT_AFTER_GRAPHIC_TAB_WAIT_MS,
        after_upload_button_wait_ms=DEFAULT_AFTER_UPLOAD_BUTTON_WAIT_MS,
        after_main_upload_wait_ms=DEFAULT_AFTER_MAIN_UPLOAD_WAIT_MS,
        after_open_cover_wait_ms=DEFAULT_AFTER_OPEN_COVER_WAIT_MS,
        after_cover_upload_wait_ms=DEFAULT_AFTER_COVER_UPLOAD_WAIT_MS,
        after_cover_confirm_wait_ms=DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS,
        after_author_select_wait_ms=DEFAULT_AFTER_AUTHOR_SELECT_WAIT_MS,
        after_location_open_wait_ms=DEFAULT_AFTER_LOCATION_OPEN_WAIT_MS,
        after_topic_paste_wait_ms=max(0, int(args.after_topic_paste_wait_ms)),
        between_topics_wait_ms=max(0, int(args.between_topics_wait_ms)),
        publish_location=str(args.publish_location or DEFAULT_PUBLISH_LOCATION).strip() or DEFAULT_PUBLISH_LOCATION,
        windows_open_dialog_wait_ms=max(0, int(args.windows_open_dialog_wait_ms)),
        upload_step_screenshot=resolve_path(args.upload_step_screenshot),
        debug_screenshot=resolve_path(args.debug_screenshot),
        schedule_at=normalize_schedule_at(getattr(args, "schedule_at", "")),
        dry_run=bool(args.dry_run),
    )


def resolve_kuaishou_assets(args: argparse.Namespace) -> KuaishouPublishAssets:
    settings = resolve_settings(args)
    output_dir = settings.output_dir
    final_dir = resolve_publish_final_dir(
        output_dir,
        final_dir_text=str(args.final_dir or ""),
        require_cover=True,
    )

    description_file = find_single_file(output_dir, KUAISHOU_DESCRIPTION_SUFFIX)
    title_text, title_file = _resolve_kuaishou_title(output_dir)
    description_text = read_utf8_text(description_file)
    description_body, topic_tags = _resolve_kuaishou_description_parts(description_text, description_file)

    poster_image, detail_image, recipe_image = resolve_publish_image_triplet(final_dir)
    cover_image = resolve_publish_cover_image(final_dir)

    return KuaishouPublishAssets(
        output_dir=output_dir,
        final_dir=final_dir,
        image_paths=(poster_image, detail_image, recipe_image),
        cover_path=cover_image,
        title_text=title_text,
        description_body=description_body,
        topic_tags=topic_tags,
        title_file=title_file or description_file,
        description_file=description_file,
    )


def log_assets(assets: KuaishouPublishAssets) -> None:
    print(f"输出目录：{assets.output_dir}")
    print(f"final 目录：{assets.final_dir}")
    if assets.title_file.name.endswith(KUAISHOU_TITLE_SUFFIX) or assets.title_file.name.endswith("_图文标题.txt"):
        print(f"标题文件：{assets.title_file}")
    else:
        print(f"标题来源：输出目录菜名（无独立标题文件）")
    print(f"描述文件：{assets.description_file}")
    print(f"标题：{assets.title_text}")
    print(f"描述正文长度：{len(assets.description_body)}，话题数量：{len(assets.topic_tags)}")
    for image_path in assets.image_paths:
        print(f"已识别上传图片：{image_path}")
    print(f"已识别封面图：{assets.cover_path}")


def main() -> int:
    args = parse_args()
    try:
        settings = resolve_settings(args)
        assets = resolve_kuaishou_assets(args)
        log_assets(assets)
        print("快手上传顺序：01 海报 -> 02 细节图 -> 03 菜谱图；封面单独上传。")
        if settings.dry_run:
            print("dry-run 校验完成，未连接 Chrome。")
            return 0
        run_kuaishou_publish(settings, assets)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}")
        return 1

    print("V2 快手发布测试动作已执行完成，请回到浏览器检查发布页状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
