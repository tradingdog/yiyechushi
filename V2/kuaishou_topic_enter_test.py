"""
临时脚本：仅测试快手作品描述里「话题变蓝」的按键方式。

用法（请先打开 Chrome 9222，并进入图文编辑页，或让脚本打开 publish/video）：
  python V2/kuaishou_topic_enter_test.py
  python V2/kuaishou_topic_enter_test.py --method main_enter
  python V2/kuaishou_topic_enter_test.py --method all
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.runtime_deps import ensure_project_runtime_dependencies  # noqa: E402

ensure_project_runtime_dependencies()

from script_logging import setup_script_logging  # noqa: E402

if __name__ == "__main__":
    setup_script_logging(__file__)

import pyautogui  # noqa: E402
from playwright.sync_api import Locator, Page, sync_playwright  # noqa: E402

from tools.douyin_publish import (  # noqa: E402
    DEFAULT_CDP_URL,
    find_optional_locator,
    type_text_humanly,
    wait_for_locator,
)
from tools.kuaishou_publish import description_editor_locators, type_hashtag_topic_humanly  # noqa: E402


DEFAULT_PUBLISH_VIDEO_URL = "https://cp.kuaishou.com/article/publish/video"
DEFAULT_TOPIC_TEXT = "美食教程"
DEFAULT_TYPING_DELAY_MS = 180
DEFAULT_AFTER_TYPE_WAIT_MS = 1_500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="快手话题变蓝：单独测试回车确认方式")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--url", default=DEFAULT_PUBLISH_VIDEO_URL, help="目标页面 URL（含此关键字即可）")
    parser.add_argument("--topic", default=DEFAULT_TOPIC_TEXT, help="不含 # 的话题文字")
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--after-type-wait-ms", type=int, default=DEFAULT_AFTER_TYPE_WAIT_MS)
    parser.add_argument(
        "--insert-text",
        action="store_true",
        help="用 insert_text 输入（对照组，通常无法弹出话题下拉）",
    )
    parser.add_argument(
        "--method",
        default="main_enter",
        choices=(
            "main_enter",
            "numpad_enter",
            "keydown_enter",
            "arrow_enter",
            "click_first",
            "pyautogui_enter",
            "all",
        ),
        help=(
            "main_enter/pyautogui_enter=系统主键盘 Enter；numpad_enter=小键盘 Enter（对照）；"
            "keydown_enter=Playwright Enter；arrow_enter=下箭头+Enter；"
            "click_first=点下拉首项；all=依次全试"
        ),
    )
    return parser.parse_args()


def topic_suggestion_first_locators(page: Page, topic_text: str) -> tuple:
    hash_label = f"#{topic_text}"
    return (
        page.locator(".ant-select-dropdown:visible").get_by_text(hash_label, exact=False).first,
        page.locator("[class*='popover']:visible").get_by_text(hash_label, exact=False).first,
        page.locator(".d-popover:visible").get_by_text(hash_label, exact=False).first,
        page.locator("[class*='suggest']:visible").filter(has_text="人浏览").first,
        page.locator("[class*='mention']:visible [class*='item']").first,
        page.get_by_role("listbox").locator("[role='option']").first,
    )


def find_publish_video_page(browser, url_keyword: str) -> Page:
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


def focus_editor(page: Page) -> Locator:
    editor = wait_for_locator(page, description_editor_locators(page), description="作品描述输入框", timeout_ms=60_000)
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(300)
    print("已清空并聚焦作品描述输入框。")
    return editor


def type_topic_prefix(
    editor: Locator,
    topic_text: str,
    *,
    typing_delay_ms: int,
    use_insert_text: bool = False,
) -> None:
    if use_insert_text:
        editor.click()
        editor.page.keyboard.insert_text("#")
        editor.page.wait_for_timeout(typing_delay_ms)
        type_text_humanly(editor.page, topic_text, delay_ms=typing_delay_ms)
        mode = "insert_text"
    else:
        type_hashtag_topic_humanly(editor, topic_text, delay_ms=typing_delay_ms)
        mode = "press_sequentially"
    print(f"已逐字输入：#{topic_text}（{mode}）")


def confirm_main_enter(page: Page) -> None:
    """与正式发布脚本一致：系统级主键盘 Enter（字母区右侧，非小键盘）。"""
    confirm_pyautogui_enter(page)


def confirm_numpad_enter(page: Page) -> None:
    page.keyboard.press("NumpadEnter")
    print("已发送：NumpadEnter（小键盘回车）")


def confirm_keydown_enter(page: Page) -> None:
    page.keyboard.down("Enter")
    page.wait_for_timeout(80)
    page.keyboard.up("Enter")
    print("已发送：keydown + keyup Enter（模拟短按主回车）")


def confirm_arrow_enter(page: Page) -> None:
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")
    print("已发送：ArrowDown + Enter")


def confirm_click_first(page: Page, topic_text: str) -> None:
    suggestion = find_optional_locator(
        page,
        topic_suggestion_first_locators(page, topic_text),
        timeout_ms=4_000,
    )
    if suggestion is None:
        raise RuntimeError("未找到话题下拉首项，无法点击确认。")
    suggestion.click()
    print("已点击：话题下拉首项")


def confirm_pyautogui_enter(page: Page) -> None:
    page.bring_to_front()
    time.sleep(0.2)
    pyautogui.press("enter")
    print("已发送：pyautogui 系统级 enter（主键盘）")


def run_one_method(
    page: Page,
    method: str,
    topic_text: str,
    *,
    typing_delay_ms: int,
    after_type_wait_ms: int,
    use_insert_text: bool,
) -> None:
    print(f"\n========== 测试方式：{method} ==========")
    editor = focus_editor(page)
    type_topic_prefix(editor, topic_text, typing_delay_ms=typing_delay_ms, use_insert_text=use_insert_text)
    page.wait_for_timeout(after_type_wait_ms)
    print(f"已等待 {after_type_wait_ms}ms，准备确认话题…")

    if method == "main_enter":
        confirm_main_enter(page)
    elif method == "numpad_enter":
        confirm_numpad_enter(page)
    elif method == "keydown_enter":
        confirm_keydown_enter(page)
    elif method == "arrow_enter":
        confirm_arrow_enter(page)
    elif method == "click_first":
        confirm_click_first(page, topic_text)
    elif method == "pyautogui_enter":
        confirm_pyautogui_enter(page)
    else:
        raise RuntimeError(f"未知方式：{method}")

    page.wait_for_timeout(2_000)
    print("请查看编辑器里该话题是否已变蓝。按回车继续…")
    input()


def main() -> int:
    args = parse_args()
    topic_text = str(args.topic or "").strip().lstrip("#")
    if not topic_text:
        print("话题不能为空。")
        return 1

    methods = (
        "main_enter",
        "numpad_enter",
        "keydown_enter",
        "arrow_enter",
        "click_first",
        "pyautogui_enter",
    )
    if args.method == "all":
        run_methods = methods
    else:
        run_methods = (args.method,)

    print("说明：press_sequentially 输入 #话题 → 等下拉 → 主键盘 Enter（Shift 上方，勿用小键盘 Enter）。")
    print("默认 main_enter（pyautogui 系统主回车）；可用 --method all 对比。")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(str(args.cdp_url))
        page = find_publish_video_page(browser, str(args.url))
        print(f"已锁定页面：{page.url}")

        for method in run_methods:
            run_one_method(
                page,
                method,
                topic_text,
                typing_delay_ms=max(0, int(args.typing_delay_ms)),
                after_type_wait_ms=max(0, int(args.after_type_wait_ms)),
                use_insert_text=bool(args.insert_text),
            )

    print("测试结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
