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

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

from tools.douyin_publish import (  # noqa: E402
    DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS,
    DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS,
    DEFAULT_AFTER_OPEN_COVER_WAIT_MS,
    DEFAULT_AFTER_UPLOAD_WAIT_MS,
    DEFAULT_AUTOMATION_PROFILE_DIR,
    DEFAULT_CDP_READY_TIMEOUT_MS,
    DEFAULT_CDP_URL,
    DEFAULT_CREATOR_HOME_URL,
    DEFAULT_DEBUG_SCREENSHOT,
    DEFAULT_TYPING_DELAY_MS,
    DEFAULT_URL_KEYWORD,
    PublishAssets,
    find_single_file,
    log_assets,
    merge_publish_topic_tags,
    read_utf8_text,
    resolve_path,
    resolve_settings,
    run_publish,
)
from image_generator import split_description_body_and_tags  # noqa: E402
from publish_final_assets import (  # noqa: E402
    resolve_publish_cover_image,
    resolve_publish_image_triplet,
)


DEFAULT_V2_OUTPUT_DIR = V2_DIR / "output" / "20260604_210303_葱香陈皮羊排"
DEFAULT_FINAL_DIR_NAME = "publish/final"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "V2 抖音发布测试脚本：读取 output 子目录里的标题/抖音描述，"
            "并按 publish/final 内的海报图、细节图、菜谱图、封面图执行旧版抖音发布流程。"
        )
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(DEFAULT_V2_OUTPUT_DIR),
        help=f"V2 单次输出目录，默认 {DEFAULT_V2_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--final-dir",
        default="",
        help="final 图片目录；不传时使用 output_dir/publish/final。",
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help=f"Chrome 远程调试地址，默认 {DEFAULT_CDP_URL}")
    parser.add_argument("--url-keyword", default=DEFAULT_URL_KEYWORD, help=f"抖音创作者页 URL 关键字，默认 {DEFAULT_URL_KEYWORD}")
    parser.add_argument("--chrome-path", help="Chrome.exe 路径；不传时按旧脚本逻辑自动查找。")
    parser.add_argument(
        "--automation-profile-dir",
        default=str(DEFAULT_AUTOMATION_PROFILE_DIR),
        help=f"自动化 Chrome 资料目录，默认 {DEFAULT_AUTOMATION_PROFILE_DIR}",
    )
    parser.add_argument("--creator-home-url", default=DEFAULT_CREATOR_HOME_URL, help=f"创作者首页，默认 {DEFAULT_CREATOR_HOME_URL}")
    parser.add_argument("--cdp-ready-timeout-ms", type=int, default=DEFAULT_CDP_READY_TIMEOUT_MS)
    parser.add_argument("--no-auto-launch-browser", action="store_true", help="不自动拉起 Chrome，连不上 CDP 时直接失败。")
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--after-upload-wait-ms", type=int, default=DEFAULT_AFTER_UPLOAD_WAIT_MS)
    parser.add_argument("--after-open-cover-wait-ms", type=int, default=DEFAULT_AFTER_OPEN_COVER_WAIT_MS)
    parser.add_argument("--after-cover-confirm-wait-ms", type=int, default=DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS)
    parser.add_argument("--after-declaration-open-wait-ms", type=int, default=DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS)
    parser.add_argument("--auto-submit-publish", action="store_true", help="显式开启后才自动点击最终发布；默认停在人工发布前。")
    parser.add_argument("--debug-screenshot", default=str(DEFAULT_DEBUG_SCREENSHOT))
    parser.add_argument("--dry-run", action="store_true", help="只校验 V2 文件与顺序，不连接 Chrome。")
    return parser.parse_args()


def resolve_final_dir(output_dir: Path, final_dir_text: str) -> Path:
    if final_dir_text.strip():
        final_dir = resolve_path(final_dir_text.strip())
    else:
        final_dir = output_dir / DEFAULT_FINAL_DIR_NAME
    if not final_dir.exists() or not final_dir.is_dir():
        raise RuntimeError(f"final 图片目录不存在：{final_dir}")
    return final_dir


def resolve_v2_publish_assets(args: argparse.Namespace) -> PublishAssets:
    settings = resolve_settings(args)
    output_dir = settings.output_dir
    final_dir = resolve_final_dir(output_dir, str(args.final_dir or ""))

    title_file = None
    for suffix in ("_图文标题.txt", "_抖音标题.txt", "_抖音图文标题.txt"):
        try:
            title_file = find_single_file(output_dir, suffix)
            break
        except RuntimeError:
            continue
    if title_file is None:
        raise RuntimeError(f"未找到标题文件（已尝试 _图文标题 / _抖音标题 / _抖音图文标题）：{output_dir}")
    description_file = find_single_file(output_dir, "_抖音图文描述.txt")

    title_text = read_utf8_text(title_file)
    description_text = read_utf8_text(description_file)
    description_body, topic_tags = split_description_body_and_tags(description_text)
    merged_topic_tags = merge_publish_topic_tags(topic_tags)
    if not title_text:
        raise RuntimeError(f"图文标题为空：{title_file}")
    if not description_body:
        raise RuntimeError(f"抖音图文描述正文为空：{description_file}")
    if len(merged_topic_tags) != 5:
        raise RuntimeError(f"抖音图文描述最后必须正好 5 个话题，当前 {len(merged_topic_tags)} 个：{description_file}")

    poster_image, detail_image, recipe_image = resolve_publish_image_triplet(final_dir)
    cover_image = resolve_publish_cover_image(final_dir)

    return PublishAssets(
        output_dir=output_dir,
        publish_dir=final_dir,
        image_paths=(poster_image, detail_image, recipe_image),
        cover_path=cover_image,
        title_text=title_text,
        description_body=description_body,
        topic_tags=merged_topic_tags,
        title_file=title_file,
        description_file=description_file,
    )


def main() -> int:
    args = parse_args()
    try:
        settings = resolve_settings(args)
        assets = resolve_v2_publish_assets(args)
        log_assets(assets)
        print("V2 上传顺序：海报图 -> 细节图 -> 菜谱图；封面单独上传封面图。")
        if settings.dry_run:
            print("dry-run 校验完成，未连接 Chrome。")
            return 0
        run_publish(settings, assets)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}")
        return 1

    print("V2 抖音发布测试动作已执行完成，请回到浏览器检查页面状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
