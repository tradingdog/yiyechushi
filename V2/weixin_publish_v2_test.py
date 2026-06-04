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
    DEFAULT_AUTOMATION_PROFILE_DIR,
    DEFAULT_CDP_READY_TIMEOUT_MS,
    DEFAULT_CDP_URL,
    DEFAULT_TYPING_DELAY_MS,
    find_default_chrome_path,
    find_single_file,
    read_utf8_text,
    resolve_path,
)
from tools.weixin_mp_publish import (  # noqa: E402
    DEFAULT_AFTER_EDITOR_WAIT_MS,
    DEFAULT_AFTER_HOME_WAIT_MS,
    DEFAULT_AFTER_TIETU_WAIT_MS,
    DEFAULT_LOGIN_MARKER,
    DEFAULT_LOGIN_MATCH_THRESHOLD,
    DEFAULT_UPLOAD_HOVER_MS,
    DEFAULT_UPLOAD2_CLICK_X,
    DEFAULT_UPLOAD2_CLICK_Y,
    DEFAULT_UPLOAD2_MENU_X,
    DEFAULT_UPLOAD2_MENU_Y,
    DEFAULT_UPLOAD3_CLICK_X,
    DEFAULT_UPLOAD3_CLICK_Y,
    DEFAULT_UPLOAD3_MENU_X,
    DEFAULT_UPLOAD3_MENU_Y,
    DEFAULT_UPLOAD_CLICK_X,
    DEFAULT_UPLOAD_CLICK_Y,
    DEFAULT_UPLOAD_MENU_X,
    DEFAULT_UPLOAD_MENU_Y,
    DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS,
    DEFAULT_URL_KEYWORD,
    DEFAULT_WEIXIN_HOME_URL,
    DEFAULT_DEBUG_SCREENSHOT_WEIXIN,
    WeixinPublishAssets,
    WeixinPublishSettings,
    run_weixin_publish,
)
from publish_final_assets import (  # noqa: E402
    resolve_publish_image_triplet,
    split_wechat_description_parts,
)


DEFAULT_V2_OUTPUT_DIR = V2_DIR / "output" / "20260604_210303_葱香陈皮羊排"
DEFAULT_FINAL_DIR_NAME = "publish/final"
WECHAT_TITLE_SUFFIX = "_微信视频号和公众号标题.txt"
WECHAT_DESCRIPTION_SUFFIX = "_微信视频号和公众号图文描述.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "V2 公众号发布测试脚本：读取 output 子目录里的公众号标题/描述，"
            "并按 publish/final 内 01/02/03 三张图执行公众号贴图草稿流程。"
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
    parser.add_argument("--weixin-home-url", default=DEFAULT_WEIXIN_HOME_URL)
    parser.add_argument("--cdp-ready-timeout-ms", type=int, default=DEFAULT_CDP_READY_TIMEOUT_MS)
    parser.add_argument("--no-auto-launch-browser", action="store_true")
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--login-marker", default=str(DEFAULT_LOGIN_MARKER))
    parser.add_argument("--login-match-threshold", type=float, default=DEFAULT_LOGIN_MATCH_THRESHOLD)
    parser.add_argument("--after-home-wait-ms", type=int, default=DEFAULT_AFTER_HOME_WAIT_MS)
    parser.add_argument("--after-tietu-wait-ms", type=int, default=DEFAULT_AFTER_TIETU_WAIT_MS)
    parser.add_argument("--after-editor-wait-ms", type=int, default=DEFAULT_AFTER_EDITOR_WAIT_MS)
    parser.add_argument("--upload-hover-ms", type=int, default=DEFAULT_UPLOAD_HOVER_MS)
    parser.add_argument("--upload-menu-x", type=int, default=DEFAULT_UPLOAD_MENU_X)
    parser.add_argument("--upload-menu-y", type=int, default=DEFAULT_UPLOAD_MENU_Y)
    parser.add_argument("--upload-click-x", type=int, default=DEFAULT_UPLOAD_CLICK_X)
    parser.add_argument("--upload-click-y", type=int, default=DEFAULT_UPLOAD_CLICK_Y)
    parser.add_argument("--windows-open-dialog-wait-ms", type=int, default=DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS)
    parser.add_argument("--debug-screenshot", default=str(DEFAULT_DEBUG_SCREENSHOT_WEIXIN))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _find_wechat_title_file(output_dir: Path) -> Path:
    for suffix in (WECHAT_TITLE_SUFFIX, "_图文标题.txt"):
        matches = sorted(output_dir.glob(f"*{suffix}"))
        if matches:
            if suffix != WECHAT_TITLE_SUFFIX:
                print(f"未找到公众号专用标题文件，回退使用：{matches[-1].name}")
            return matches[-1]
    raise RuntimeError(f"未找到公众号标题文件：{output_dir}")


def resolve_final_dir(output_dir: Path, final_dir_text: str) -> Path:
    if final_dir_text.strip():
        final_dir = resolve_path(final_dir_text.strip())
    else:
        final_dir = output_dir / DEFAULT_FINAL_DIR_NAME
    if not final_dir.exists() or not final_dir.is_dir():
        raise RuntimeError(f"final 图片目录不存在：{final_dir}")
    return final_dir


def resolve_settings(args: argparse.Namespace) -> WeixinPublishSettings:
    output_dir = resolve_path(args.output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"输出目录不存在：{output_dir}")

    chrome_path_text = str(args.chrome_path or "").strip()
    chrome_path = resolve_path(chrome_path_text) if chrome_path_text else find_default_chrome_path()

    return WeixinPublishSettings(
        output_dir=output_dir,
        cdp_url=str(args.cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
        url_keyword=str(args.url_keyword or DEFAULT_URL_KEYWORD).strip() or DEFAULT_URL_KEYWORD,
        chrome_path=chrome_path,
        automation_profile_dir=resolve_path(args.automation_profile_dir),
        auto_launch_browser=not bool(args.no_auto_launch_browser),
        weixin_home_url=str(args.weixin_home_url or DEFAULT_WEIXIN_HOME_URL).strip() or DEFAULT_WEIXIN_HOME_URL,
        cdp_ready_timeout_ms=max(0, int(args.cdp_ready_timeout_ms)),
        typing_delay_ms=max(0, int(args.typing_delay_ms)),
        login_marker_path=resolve_path(args.login_marker),
        login_match_threshold=float(args.login_match_threshold),
        after_home_wait_ms=max(0, int(args.after_home_wait_ms)),
        after_tietu_wait_ms=max(0, int(args.after_tietu_wait_ms)),
        after_editor_wait_ms=max(0, int(args.after_editor_wait_ms)),
        upload_hover_ms=max(0, int(args.upload_hover_ms)),
        upload_menu_x=int(args.upload_menu_x),
        upload_menu_y=int(args.upload_menu_y),
        upload_click_x=int(args.upload_click_x),
        upload_click_y=int(args.upload_click_y),
        upload2_menu_x=DEFAULT_UPLOAD2_MENU_X,
        upload2_menu_y=DEFAULT_UPLOAD2_MENU_Y,
        upload2_click_x=DEFAULT_UPLOAD2_CLICK_X,
        upload2_click_y=DEFAULT_UPLOAD2_CLICK_Y,
        upload3_menu_x=DEFAULT_UPLOAD3_MENU_X,
        upload3_menu_y=DEFAULT_UPLOAD3_MENU_Y,
        upload3_click_x=DEFAULT_UPLOAD3_CLICK_X,
        upload3_click_y=DEFAULT_UPLOAD3_CLICK_Y,
        windows_open_dialog_wait_ms=max(0, int(args.windows_open_dialog_wait_ms)),
        debug_screenshot=resolve_path(args.debug_screenshot),
        dry_run=bool(args.dry_run),
    )


def resolve_weixin_assets(args: argparse.Namespace) -> WeixinPublishAssets:
    settings = resolve_settings(args)
    output_dir = settings.output_dir
    final_dir = resolve_final_dir(output_dir, str(args.final_dir or ""))

    title_file = _find_wechat_title_file(output_dir)
    description_file = find_single_file(output_dir, WECHAT_DESCRIPTION_SUFFIX)
    title_text = read_utf8_text(title_file)
    description_body, description_topics = split_wechat_description_parts(read_utf8_text(description_file))

    if not title_text:
        raise RuntimeError(f"公众号标题为空：{title_file}")
    if not description_body:
        raise RuntimeError(f"公众号描述正文为空：{description_file}")
    if not description_topics:
        raise RuntimeError(f"公众号描述话题为空：{description_file}")

    poster_image, detail_image, recipe_image = resolve_publish_image_triplet(final_dir)

    return WeixinPublishAssets(
        output_dir=output_dir,
        final_dir=final_dir,
        image_paths=(poster_image, detail_image, recipe_image),
        title_text=title_text,
        description_body=description_body,
        description_topics=description_topics,
        title_file=title_file,
        description_file=description_file,
    )


def log_assets(assets: WeixinPublishAssets) -> None:
    print(f"输出目录：{assets.output_dir}")
    print(f"final 目录：{assets.final_dir}")
    print(f"标题文件：{assets.title_file}")
    print(f"描述文件：{assets.description_file}")
    print(f"描述正文长度：{len(assets.description_body)}，话题长度：{len(assets.description_topics)}")
    for image_path in assets.image_paths:
        print(f"已识别上传图片：{image_path}")


def main() -> int:
    args = parse_args()
    try:
        settings = resolve_settings(args)
        assets = resolve_weixin_assets(args)
        log_assets(assets)
        print("公众号上传顺序：01 海报 -> 02 细节图 -> 03 菜谱图（不含封面）。")
        if settings.dry_run:
            print("dry-run 校验完成，未连接 Chrome。")
            return 0
        run_weixin_publish(settings, assets)
    except Exception as exc:  # noqa: BLE001
        print(f"运行失败：{exc}")
        return 1

    print("V2 公众号发布测试动作已执行完成，请回到浏览器检查草稿状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
