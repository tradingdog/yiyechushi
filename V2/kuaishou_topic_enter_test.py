"""
临时脚本：测试快手作品描述里「剪贴板粘贴话题变蓝」。

用法（请先打开 Chrome 9222，并进入快手图文编辑页）：
  python V2/kuaishou_topic_enter_test.py
  python V2/kuaishou_topic_enter_test.py --topics "#美食教程 #美食分享"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.runtime_deps import ensure_project_runtime_dependencies  # noqa: E402

ensure_project_runtime_dependencies()

from script_logging import setup_script_logging  # noqa: E402

if __name__ == "__main__":
    setup_script_logging(__file__)

from playwright.sync_api import sync_playwright  # noqa: E402

from tools.douyin_publish import DEFAULT_CDP_URL, wait_for_locator  # noqa: E402
from tools.kuaishou_publish import (  # noqa: E402
    DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS,
    DEFAULT_BETWEEN_TOPICS_WAIT_MS,
    KuaishouPublishSettings,
    description_editor_locators,
    paste_topic_tags_via_clipboard,
)


DEFAULT_PUBLISH_VIDEO_URL = "https://cp.kuaishou.com/article/publish/video"
DEFAULT_TOPICS = ("#美食教程", "#美食分享")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="快手话题：剪贴板逐个粘贴测试")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--url", default=DEFAULT_PUBLISH_VIDEO_URL)
    parser.add_argument(
        "--topics",
        default=" ".join(DEFAULT_TOPICS),
        help='空格分隔，如 "#美食教程 #美食分享"',
    )
    parser.add_argument("--after-paste-wait-ms", type=int, default=DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS)
    parser.add_argument("--between-topics-wait-ms", type=int, default=DEFAULT_BETWEEN_TOPICS_WAIT_MS)
    return parser.parse_args()


def parse_topic_tags(raw: str) -> tuple[str, ...]:
    tags = []
    for part in str(raw or "").split():
        text = part.strip()
        if not text:
            continue
        tags.append(text if text.startswith("#") else f"#{text}")
    if not tags:
        raise ValueError("至少需要一个话题，例如：#美食教程 #美食分享")
    return tuple(tags)


def find_publish_video_page(browser, url_keyword: str):
    matched = [
        page
        for context in browser.contexts
        for page in context.pages
        if url_keyword.rstrip("/") in page.url or "publish/video" in page.url
    ]
    if matched:
        page = matched[-1]
        page.bring_to_front()
        return page

    page = browser.contexts[0].pages[0] if browser.contexts and browser.contexts[0].pages else None
    if page is None:
        raise RuntimeError("Chrome 里没有可用标签页。")
    page.bring_to_front()
    page.goto(url_keyword, wait_until="domcontentloaded")
    page.wait_for_timeout(2_000)
    print(f"已打开页面：{page.url}")
    return page


def build_test_settings(args: argparse.Namespace) -> KuaishouPublishSettings:
    return KuaishouPublishSettings(
        output_dir=ROOT_DIR,
        cdp_url=str(args.cdp_url),
        url_keyword="cp.kuaishou.com",
        chrome_path=None,
        automation_profile_dir=ROOT_DIR / "tools" / "chrome_automation_profile",
        auto_launch_browser=False,
        cdp_ready_timeout_ms=30_000,
        typing_delay_ms=0,
        after_publish_work_wait_ms=0,
        after_graphic_tab_wait_ms=0,
        after_upload_button_wait_ms=0,
        after_main_upload_wait_ms=0,
        after_open_cover_wait_ms=0,
        after_cover_upload_wait_ms=0,
        after_cover_confirm_wait_ms=0,
        after_author_select_wait_ms=0,
        after_location_open_wait_ms=0,
        after_topic_paste_wait_ms=max(0, int(args.after_paste_wait_ms)),
        between_topics_wait_ms=max(0, int(args.between_topics_wait_ms)),
        publish_location="成都市",
        windows_open_dialog_wait_ms=0,
        upload_step_screenshot=ROOT_DIR / "tools" / "kuaishou_publish_upload_step.png",
        debug_screenshot=ROOT_DIR / "tools" / "kuaishou_publish_last_error.png",
        schedule_at=None,
        dry_run=False,
    )


def main() -> int:
    args = parse_args()
    try:
        topic_tags = parse_topic_tags(args.topics)
    except ValueError as exc:
        print(exc)
        return 1

    print("说明：每个话题单独复制到剪贴板，在作品描述框 Ctrl+V 粘贴；第 2 个起先按空格再粘贴。")
    print(f"待测话题：{' '.join(topic_tags)}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(str(args.cdp_url))
        page = find_publish_video_page(browser, str(args.url))
        print(f"已锁定页面：{page.url}")

        editor = wait_for_locator(page, description_editor_locators(page), description="作品描述输入框", timeout_ms=60_000)
        editor.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(300)
        print("已清空并聚焦作品描述输入框。")

        settings = build_test_settings(args)
        paste_topic_tags_via_clipboard(
            page,
            editor,
            topic_tags,
            after_paste_wait_ms=settings.after_topic_paste_wait_ms,
            between_topics_wait_ms=settings.between_topics_wait_ms,
        )

        page.wait_for_timeout(1_500)
        html = editor.evaluate("el => el.innerHTML")
        print(f"粘贴后编辑器 HTML 片段：{html[:500]}")
        print("请目视检查话题是否均已变蓝。确认后按回车结束…")
        input()

    print("测试结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
