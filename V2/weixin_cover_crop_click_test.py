"""测试公众号贴图编辑页：悬停封面后点击裁剪图标。

用法（Chrome 已开 9222 且停留在贴图编辑页）：
  .venv\\Scripts\\python.exe V2\\weixin_cover_crop_click_test.py
  .venv\\Scripts\\python.exe V2\\weixin_cover_crop_click_test.py --probe-only
  .venv\\Scripts\\python.exe V2\\weixin_cover_crop_click_test.py --try-click
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

from playwright.sync_api import sync_playwright

from tools.douyin_publish import DEFAULT_CDP_URL, DEFAULT_URL_KEYWORD
from tools.win_foreground import activate_chrome_window_for_page
from tools.weixin_mp_publish import (
    DEFAULT_URL_KEYWORD as WEIXIN_KEYWORD,
    forward_card_preview_card_locators,
    is_tietu_editor_url,
    modify_cover_button_locators,
    open_cover_crop_modal,
    select_forward_card_cover_preview,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公众号封面裁剪图标点击探测/测试")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--url-keyword", default=WEIXIN_KEYWORD)
    parser.add_argument("--probe-only", action="store_true", help="只探测 DOM，不点击")
    parser.add_argument("--try-click", action="store_true", help="探测后尝试打开裁剪弹窗")
    parser.add_argument("--try-select-forward", action="store_true", help="打开弹窗后点击第二个 3:4 转发卡片")
    return parser.parse_args()


def find_weixin_editor_page(browser, url_keyword: str):
    pages = []
    for context in browser.contexts:
        for page in context.pages:
            if url_keyword in page.url and "appmsg_edit" in page.url:
                pages.append(page)
    if not pages:
        for context in browser.contexts:
            for page in context.pages:
                if url_keyword in page.url:
                    pages.append(page)
    if not pages:
        raise RuntimeError(f"未找到含 {url_keyword} 的页面，请先在 Chrome 打开贴图编辑页。")
    page = pages[-1]
    page.bring_to_front()
    return page


def probe_locator(page, locator, label: str) -> None:
    count = locator.count()
    print(f"\n=== {label} (count={count}) ===")
    for index in range(min(count, 8)):
        item = locator.nth(index)
        try:
            box = item.bounding_box()
        except Exception as exc:
            box = None
            print(f"  [{index}] bounding_box error: {exc}")
            continue
        visible = False
        try:
            visible = item.is_visible()
        except Exception:
            pass
        text = ""
        try:
            text = (item.inner_text(timeout=500) or "").strip().replace("\n", " ")[:80]
        except Exception:
            pass
        cls = ""
        try:
            cls = item.evaluate("el => el.className || ''")
        except Exception:
            pass
        print(
            f"  [{index}] visible={visible} box={box} class={cls!r} text={text!r}"
        )


def probe_cover_regions(page) -> None:
    selectors = {
        "js_modifyCover": page.locator("a.js_modifyCover, .js_modifyCover"),
        "modify_cover_locators": page.locator("a.js_modifyCover, a.common_edit.js_modifyCover"),
        "cover_img_wrap": page.locator(".js_cover_preview, .cover__preview, .share_cover, .appmsg_cover"),
        "upload_img_card": page.locator(".weui-desktop-upload__img, .appmsg_edit_img_item, .js_img_item"),
        "editor_img_area": page.locator(".share-image__list img, .appmsg_edit_container img").first,
        "crop_icon_title": page.locator('[title*="裁剪"], [aria-label*="裁剪"]'),
        "icon_btn_edit": page.locator("a.weui-desktop-icon-btn, .weui-desktop-icon-btn"),
    }
    for label, locator in selectors.items():
        probe_locator(page, locator, label)

    print("\n=== 页面 URL / 标题 ===")
    print(page.url)
    print(page.title())


def find_cover_hover_target(page):
    candidates = [
        ("first_upload_img", page.locator(".weui-desktop-upload__img").first),
        ("js_modifyCover_parent", page.locator("a.js_modifyCover").first.locator("xpath=ancestor::*[contains(@class,'cover') or contains(@class,'share')][1]")),
        ("share_image_item", page.locator(".share-image__item, .share-image__list > *").first),
        ("large_editor_img", page.locator("img").nth(2)),
        ("cover_preview_area", page.locator(".share_cover, .js_cover_area, #js_cover_area").first),
    ]
    for label, locator in candidates:
        try:
            if locator.count() == 0:
                print(f"候选悬停目标 {label}: count=0")
                continue
            box = locator.bounding_box()
            if box and box.get("width", 0) > 80 and box.get("height", 0) > 80:
                print(f"选用悬停目标 {label}: box={box}")
                return label, locator
            print(f"候选悬停目标 {label}: box 太小或为空 {box}")
        except Exception as exc:
            print(f"候选悬停目标 {label}: error={exc}")
    raise RuntimeError("未找到合适的封面悬停区域。")


def wait_crop_icon_visible(page, timeout_ms: int = 5000) -> tuple[str, object]:
    deadline = time.time() + timeout_ms / 1000
    locators = {
        "js_modifyCover": page.locator("a.js_modifyCover, .js_modifyCover").first,
        "modify_locators": modify_cover_button_locators(page)[0],
        "title_crop": page.locator('[title="裁剪"], [title*="裁剪"]').first,
    }
    while time.time() < deadline:
        for label, locator in locators.items():
            try:
                if locator.count() and locator.is_visible():
                    print(f"裁剪图标已可见：{label}")
                    return label, locator
            except Exception:
                pass
        page.wait_for_timeout(200)
    raise RuntimeError("悬停后仍未看到裁剪图标。")


def probe_forward_cards(page) -> None:
    cards = page.locator("div.cover-preview-con[edit-cover-type='3_4'] div.cover-preview-card")
    probe_locator(page, cards, "cover_preview_card_3_4")
    probe_locator(page, forward_card_preview_card_locators(page)[0], "forward_card_target_nth1")


def try_open_crop_modal(page, *, select_forward: bool = False) -> None:
    open_cover_crop_modal(page)
    probe_forward_cards(page)
    if select_forward:
        select_forward_card_cover_preview(page)
        probe_forward_cards(page)
        print("SUCCESS: 已点击第二个 3:4 转发卡片预览。")
        return
    matched = False
    for locator in forward_card_preview_card_locators(page):
        try:
            if locator.count() and locator.first.is_visible():
                matched = True
                break
        except Exception:
            continue
    if matched:
        print("SUCCESS: 裁剪弹窗已打开（检测到第二个 3:4 转发卡片预览）。")
    else:
        print("WARN: 点击后未检测到 3:4 转发卡片预览，可能未进入裁剪弹窗。")
        out = Path(ROOT_DIR / "tools" / "weixin_cover_crop_click_test_last.png")
        page.screenshot(path=str(out), full_page=True)
        print(f"已保存截图：{out}")


def main() -> None:
    args = parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        page = find_weixin_editor_page(browser, args.url_keyword)
        activate_chrome_window_for_page(page, extra_hints=(args.url_keyword,), maximize=True)

        print(f"已连接页面：{page.url}")
        print(f"是否贴图编辑页：{is_tietu_editor_url(page.url)}")
        probe_cover_regions(page)

        if args.probe_only:
            return
        if args.try_click or args.try_select_forward:
            try_open_crop_modal(page, select_forward=args.try_select_forward)
            return

        print("\n提示：加 --try-click 打开裁剪弹窗；加 --try-select-forward 再点第二个转发卡片；加 --probe-only 仅探测 DOM。")


if __name__ == "__main__":
    main()
