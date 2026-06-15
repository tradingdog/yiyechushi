from __future__ import annotations

import argparse
import ctypes
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.runtime_deps import ensure_project_runtime_dependencies  # noqa: E402

ensure_project_runtime_dependencies()

from script_logging import setup_script_logging

if __name__ == "__main__":
    setup_script_logging(__file__)

try:
    import pyautogui
    from playwright.sync_api import Browser, Locator, Page, sync_playwright
except ImportError as exc:
    raise SystemExit(
        f"导入发布依赖失败：{exc}\n请执行：{sys.executable} -m pip install -r requirements.txt"
    ) from exc

from tools.douyin_publish import (  # noqa: E402
    DEFAULT_AUTOMATION_PROFILE_DIR,
    DEFAULT_CDP_READY_TIMEOUT_MS,
    DEFAULT_CDP_URL,
    DEFAULT_TYPING_DELAY_MS,
    PublishSettings,
    click_locator,
    click_locator_via_dom,
    ensure_cdp_browser_available,
    find_default_chrome_path,
    find_optional_locator,
    resolve_page_by_keyword,
    resolve_path,
    type_text_humanly,
    wait_for_locator,
)
from tools.publish_login import wait_for_panel_login  # noqa: E402
from tools.screen_template_match import match_template_on_page  # noqa: E402
from tools.win_foreground import activate_chrome_window_for_page  # noqa: E402


DEFAULT_URL_KEYWORD = "mp.weixin.qq.com"
DEFAULT_WEIXIN_HOME_URL = "https://mp.weixin.qq.com/"
DEFAULT_LOGIN_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_mp_logged_in_marker.png"
DEFAULT_LOGIN_MATCH_THRESHOLD = 0.9
DEFAULT_AFTER_HOME_WAIT_MS = 5_000
DEFAULT_AFTER_TIETU_WAIT_MS = 5_000
DEFAULT_AFTER_EDITOR_WAIT_MS = 10_000
DEFAULT_UPLOAD_HOVER_MS = 3_000
DEFAULT_UPLOAD_MENU_X = 623
DEFAULT_UPLOAD_MENU_Y = 354
DEFAULT_UPLOAD_CLICK_X = 616
DEFAULT_UPLOAD_CLICK_Y = 380
DEFAULT_UPLOAD2_MENU_X = 865
DEFAULT_UPLOAD2_MENU_Y = 729
DEFAULT_UPLOAD2_CLICK_X = 858
DEFAULT_UPLOAD2_CLICK_Y = 768
DEFAULT_UPLOAD3_MENU_X = 883
DEFAULT_UPLOAD3_MENU_Y = 728
DEFAULT_UPLOAD3_CLICK_X = 881
DEFAULT_UPLOAD3_CLICK_Y = 768
DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS = 1_500
DEFAULT_AFTER_COVER_EDITOR_WAIT_MS = 1_200
DEFAULT_COVER_CROP_DRAG_START_X = 662
DEFAULT_COVER_CROP_DRAG_START_Y = 555
DEFAULT_COVER_CROP_DRAG_END_Y = 410
DEFAULT_DEBUG_SCREENSHOT_WEIXIN = ROOT_DIR / "tools" / "weixin_mp_publish_last_error.png"


@dataclass(frozen=True)
class WeixinPublishAssets:
    output_dir: Path
    final_dir: Path
    image_paths: tuple[Path, ...]
    title_text: str
    description_body: str
    description_topics: str
    title_file: Path
    description_file: Path


@dataclass(frozen=True)
class WeixinPublishSettings:
    output_dir: Path
    cdp_url: str
    url_keyword: str
    chrome_path: Path | None
    automation_profile_dir: Path
    auto_launch_browser: bool
    weixin_home_url: str
    cdp_ready_timeout_ms: int
    typing_delay_ms: int
    login_marker_path: Path
    login_match_threshold: float
    after_home_wait_ms: int
    after_tietu_wait_ms: int
    after_editor_wait_ms: int
    upload_hover_ms: int
    upload_menu_x: int
    upload_menu_y: int
    upload_click_x: int
    upload_click_y: int
    upload2_menu_x: int
    upload2_menu_y: int
    upload2_click_x: int
    upload2_click_y: int
    upload3_menu_x: int
    upload3_menu_y: int
    upload3_click_x: int
    upload3_click_y: int
    windows_open_dialog_wait_ms: int
    after_cover_editor_wait_ms: int
    cover_crop_drag_start_x: int
    cover_crop_drag_start_y: int
    cover_crop_drag_end_y: int
    debug_screenshot: Path
    dry_run: bool


def home_menu_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator('a.weui-desktop-menu__link[title="首页"]'),
        page.locator("span.weui-desktop-menu__name").filter(has_text="首页"),
        page.get_by_text("首页", exact=True),
    )


def tietu_menu_item_locator(page: Page) -> Locator:
    return page.locator("div.new-creation__menu-item").filter(
        has=page.locator("div.new-creation__menu-title", has_text="贴图")
    )


def tietu_entry_locators(page: Page) -> tuple[Locator, ...]:
    tietu_item = tietu_menu_item_locator(page)
    return (
        tietu_item.locator("div.new-creation__menu-content"),
        tietu_item,
        page.locator("div.new-creation__menu-content").filter(
            has=page.locator("div.new-creation__menu-title", has_text="贴图")
        ),
    )


def is_tietu_editor_url(url: str) -> bool:
    return "type=77" in url or "createType=8" in url


def assert_tietu_editor_page(page: Page) -> None:
    if is_tietu_editor_url(page.url):
        return
    raise RuntimeError(
        f"当前页面不是贴图编辑页，可能误点了「文章」或其它入口：{page.url}"
    )


def title_editor_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator('div[name="title"] .ProseMirror'),
        page.locator(".title-editor__input .ProseMirror"),
        page.locator('.ProseMirror[data-placeholder="请在这里输入标题"]'),
    )


def description_editor_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator(".share-text__input .ProseMirror"),
        page.locator(".js_pmEditorArea .ProseMirror"),
        page.locator(".share-text__area .ProseMirror"),
    )


def declaration_not_added_locators(page: Page) -> tuple[Locator, ...]:
    claim_source = page.locator("#js_claim_source_area")
    return (
        claim_source.locator("span.lbl_content_desc_default").filter(has_text="未添加"),
        claim_source.locator(".js_claim_source_desc").filter(has_text="未添加"),
        claim_source.get_by_text("未添加", exact=True),
    )


def personal_view_option_locators(page: Page) -> tuple[Locator, ...]:
    dialog = page.locator("div.weui-desktop-dialog:visible")
    return (
        dialog.get_by_text("个人观点", exact=False),
        dialog.locator("label").filter(has_text="个人观点"),
        dialog.locator("label").filter(has_text="个人"),
        dialog.get_by_text("内容为个人观点或见解", exact=False),
        page.get_by_text("个人观点", exact=True),
        page.get_by_text("个人观点", exact=False),
        page.get_by_text("内容为个人观点或见解", exact=False),
        page.locator("label").filter(has_text="个人观点"),
        page.locator(".weui-desktop-form__check-label").filter(has_text="个人"),
    )


def dialog_confirm_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("button.weui-desktop-btn_primary").filter(has_text="确认"),
        page.locator("div.weui-desktop-dialog__ft button.weui-desktop-btn_primary"),
        page.get_by_role("button", name="确认"),
    )


def save_draft_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("#js_submit button"),
        page.locator("#js_submit .send_wording").filter(has_text="保存为草稿"),
        page.get_by_text("保存为草稿", exact=True),
    )


def modify_cover_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("a.js_modifyCover"),
        page.locator("a.common_edit.js_modifyCover"),
        page.locator("a.weui-desktop-icon-btn.js_modifyCover"),
    )


def cover_preview_hover_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator(".share_cover").first,
        page.locator(".js_cover_area").first,
        page.locator("#js_cover_area").first,
        page.locator("a.js_modifyCover").first.locator(
            "xpath=ancestor::*[contains(@class,'cover') or contains(@class,'share')][1]"
        ),
    )


def cover_crop_confirm_button_locators(page: Page) -> tuple[Locator, ...]:
    dialog = page.locator("div.weui-desktop-dialog:visible")
    return (
        dialog.locator("button.weui-desktop-btn_primary").filter(has_text="确认"),
        dialog.locator("div.weui-desktop-dialog__ft button.weui-desktop-btn_primary"),
        dialog.get_by_role("button", name="确认"),
        page.locator("button.weui-desktop-btn_primary").filter(has_text="确认"),
        page.get_by_role("button", name="确认"),
    )


def _cover_crop_dialog(page: Page) -> Locator:
    return page.locator("div.weui-desktop-dialog:visible").last


def forward_card_preview_card_locators(page: Page) -> tuple[Locator, ...]:
    """3:4 裁剪弹窗内第二个预览卡片（转发卡片），非默认公众号列表第一张。"""
    dialog = _cover_crop_dialog(page)
    three_four = dialog.locator("div.cover-preview-con[edit-cover-type='3_4']")
    return (
        three_four.locator("div.cover-preview-card").nth(1),
        three_four.locator("div.cover-preview-card").last,
        dialog.locator("div.cover-preview-con[edit-cover-type='3_4'] div.cover-preview-card").nth(1),
        page.locator("div.cover-preview-con[edit-cover-type='3_4'] div.cover-preview-card").nth(1),
        three_four.locator("img.card-cover-img").nth(1),
        page.locator("div.cover-preview-con[edit-cover-type='3_4'] img.card-cover-img").nth(1),
    )


def forward_card_cover_locators(page: Page) -> tuple[Locator, ...]:
    return forward_card_preview_card_locators(page)


def _forward_card_cover_selected_state(page: Page) -> dict:
    return page.evaluate(
        """
        () => {
          const con = document.querySelector('div.cover-preview-con[edit-cover-type="3_4"]');
          if (!con) return { ok: false, reason: 'no_3_4_container' };
          const cards = con.querySelectorAll('div.cover-preview-card');
          if (cards.length < 2) return { ok: false, reason: 'card_count_' + cards.length };
          const second = cards[1];
          const cls = (second.className || '').toString();
          const active = /\\bactive\\b|selected|current|checked/i.test(cls)
            || second.getAttribute('aria-selected') === 'true';
          if (active) return { ok: true, cls };
          const inner = second.querySelector('.card-content, .card-cover-con');
          const innerCls = inner ? (inner.className || '').toString() : '';
          const innerActive = /\\bactive\\b|selected|current/i.test(innerCls);
          return { ok: innerActive, cls, innerCls, reason: 'not_active_yet' };
        }
        """
    )


def _assert_forward_card_cover_selected(page: Page, *, timeout_ms: int = 3_000) -> None:
    deadline = time.time() + timeout_ms / 1000
    state: dict = {"ok": False, "reason": "timeout"}
    while time.time() < deadline:
        state = _forward_card_cover_selected_state(page)
        if state.get("ok"):
            print(f"已确认选中 3:4 转发卡片预览（class={state.get('cls')!r}）。")
            return
        page.wait_for_timeout(200)
    print(f"WARN: 未能确认转发卡片已选中（state={state}），继续执行裁剪拖拽。")


def _click_forward_card_preview(page: Page, card: Locator) -> None:
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    try:
        card.click(timeout=3_000, force=True)
        print("已点击：3:4转发卡片预览（第二个 cover-preview-card）。")
        return
    except Exception as exc1:
        try:
            card.evaluate("el => el.click()")
            print("已通过 DOM 点击：3:4转发卡片预览（第二个 cover-preview-card）。")
            return
        except Exception as exc2:
            box = card.bounding_box()
            if box is None:
                raise RuntimeError("无法点击 3:4 转发卡片预览：Playwright 与 DOM 点击均失败。") from exc2
            prepare_weixin_chrome_page(page)
            center_x = int(box["x"] + box["width"] / 2)
            center_y = int(box["y"] + box["height"] / 2)
            pyautogui.moveTo(center_x, center_y, duration=0.25)
            pyautogui.click()
            print(f"已通过屏幕坐标 ({center_x}, {center_y}) 点击 3:4 转发卡片预览。")


def select_forward_card_cover_preview(page: Page, *, timeout_ms: int = 30_000) -> Locator:
    card = wait_for_locator(
        page,
        forward_card_preview_card_locators(page),
        description="3:4转发卡片预览",
        timeout_ms=timeout_ms,
    )
    _click_forward_card_preview(page, card)
    page.wait_for_timeout(300)
    if not _forward_card_cover_selected_state(page).get("ok"):
        print("转发卡片可能未选中，重试点击第二个预览卡片。")
        _click_forward_card_preview(page, card)
        page.wait_for_timeout(300)
    _assert_forward_card_cover_selected(page)
    return card


def fill_contenteditable(page: Page, locators: Sequence[Locator], *, text: str, description: str, settings: WeixinPublishSettings) -> None:
    editor = wait_for_locator(page, locators, description=description, timeout_ms=30_000)
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, text, delay_ms=settings.typing_delay_ms)
    print(f"已输入{description}。")


def fill_wechat_description(page: Page, assets: WeixinPublishAssets, settings: WeixinPublishSettings) -> None:
    editor = wait_for_locator(page, description_editor_locators(page), description="公众号描述", timeout_ms=30_000)
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, assets.description_body, delay_ms=settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    if assets.description_topics:
        type_text_humanly(page, assets.description_topics, delay_ms=settings.typing_delay_ms)
    print("已输入公众号描述正文与话题。")


def goto_weixin_home(page: Page) -> None:
    if "cgi-bin/home" in page.url:
        return

    home_entry = find_optional_locator(page, home_menu_locators(page), timeout_ms=3_000)
    if home_entry is not None:
        home_entry.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1_000)
        print("已从当前页切回公众号首页。")
        return

    token_match = re.search(r"token=(\d+)", page.url)
    if token_match is not None:
        home_url = (
            "https://mp.weixin.qq.com/cgi-bin/home"
            f"?t=home/index&token={token_match.group(1)}&lang=zh_CN"
        )
        page.goto(home_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1_000)
        print(f"已跳转到公众号首页：{page.url}")
        return

    page.goto(DEFAULT_WEIXIN_HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1_000)
    print(f"已尝试打开公众号首页：{page.url}")


def prepare_weixin_chrome_page(page: Page) -> None:
    activate_chrome_window_for_page(page, extra_hints=(DEFAULT_URL_KEYWORD,), maximize=True)


def ensure_weixin_mp_logged_in(page: Page, settings: WeixinPublishSettings) -> None:
    if not settings.login_marker_path.exists():
        raise RuntimeError(f"登录识别模板不存在：{settings.login_marker_path}")

    while True:
        goto_weixin_home(page)
        prepare_weixin_chrome_page(page)
        matched, score = match_template_on_page(
            page,
            settings.login_marker_path,
            threshold=settings.login_match_threshold,
        )
        if matched:
            print(f"已检测到公众号后台登录态（匹配度 {score:.3f}）。")
            return

        wait_for_panel_login(
            platform_label="微信公众号",
            confirm_kind="y",
            hint=(
                "未在页面中匹配到公众号后台登录标识（阈值 "
                f"{settings.login_match_threshold}）。请在浏览器中扫码登录公众号。"
            ),
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)
        prepare_weixin_chrome_page(page)


def open_home_page(page: Page, settings: WeixinPublishSettings) -> None:
    click_locator(page, home_menu_locators(page), description="公众号首页", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_home_wait_ms)
    print("已进入公众号首页。")


def open_tietu_editor(browser: Browser, page: Page, settings: WeixinPublishSettings) -> Page:
    existing_pages = {id(item) for item in page.context.pages}
    click_locator(page, tietu_entry_locators(page), description="贴图入口", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_tietu_wait_ms)

    editor_page = page
    deadline = time.time() + 15
    while time.time() < deadline:
        for candidate in page.context.pages:
            if id(candidate) not in existing_pages:
                editor_page = candidate
                break
        if editor_page.url != page.url:
            break
        page.wait_for_timeout(300)

    prepare_weixin_chrome_page(editor_page)
    editor_page.wait_for_load_state("domcontentloaded")
    editor_page.wait_for_timeout(settings.after_editor_wait_ms)
    assert_tietu_editor_page(editor_page)
    print(f"已进入贴图编辑页：{editor_page.url}")
    return editor_page


def paste_text_to_clipboard(text: str) -> None:
    """通过 Win32 Unicode 剪贴板写入，避免 PowerShell 在中文路径下出现乱码。"""
    try:
        import pyperclip

        pyperclip.copy(text)
        return
    except Exception:
        pass

    cf_unicode = 13
    gmem_moveable = 0x0002
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.EmptyClipboard.restype = ctypes.c_int
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.restype = ctypes.c_int

    if not user32.OpenClipboard(None):
        raise RuntimeError("无法打开 Windows 剪贴板。")
    try:
        if not user32.EmptyClipboard():
            raise RuntimeError("无法清空 Windows 剪贴板。")

        payload = (text + "\0").encode("utf-16-le")
        h_global = kernel32.GlobalAlloc(gmem_moveable, len(payload))
        if not h_global:
            raise RuntimeError("GlobalAlloc 失败。")

        locked = kernel32.GlobalLock(h_global)
        if not locked:
            kernel32.GlobalFree(h_global)
            raise RuntimeError("GlobalLock 失败。")
        try:
            ctypes.memmove(locked, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(h_global)

        if not user32.SetClipboardData(cf_unicode, h_global):
            kernel32.GlobalFree(h_global)
            raise RuntimeError("SetClipboardData 失败。")
    finally:
        user32.CloseClipboard()


def confirm_windows_open_dialog(image_paths: Sequence[Path], *, wait_ms: int, focus_page: Page | None = None) -> None:
    if focus_page is not None:
        prepare_weixin_chrome_page(focus_page)

    time.sleep(wait_ms / 1000)
    resolved_paths = [path.resolve() for path in image_paths]

    # 不用 Alt+D：会与豆包等软件的「Alt+D 语音通话」快捷键冲突。
    # 直接在「文件名」框粘贴完整路径（单张）或带引号的多路径（多张）。
    pyautogui.hotkey("alt", "n")
    time.sleep(0.4)
    if len(resolved_paths) == 1:
        payload = str(resolved_paths[0])
    else:
        payload = " ".join(f'"{path}"' for path in resolved_paths)
    paste_text_to_clipboard(payload)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyautogui.press("enter")
    print(f"已在 Windows 打开对话框确认 {len(resolved_paths)} 张图片。")
    for path in image_paths:
        print(f"  - {path.name}")

    if focus_page is not None:
        prepare_weixin_chrome_page(focus_page)


def upload_screen_coords_for_index(settings: WeixinPublishSettings, index: int) -> tuple[int, int, int, int]:
    coord_map = {
        1: (
            settings.upload_menu_x,
            settings.upload_menu_y,
            settings.upload_click_x,
            settings.upload_click_y,
        ),
        2: (
            settings.upload2_menu_x,
            settings.upload2_menu_y,
            settings.upload2_click_x,
            settings.upload2_click_y,
        ),
        3: (
            settings.upload3_menu_x,
            settings.upload3_menu_y,
            settings.upload3_click_x,
            settings.upload3_click_y,
        ),
    }
    if index not in coord_map:
        raise RuntimeError(f"未配置第 {index} 张图片的上传坐标。")
    return coord_map[index]


def open_local_upload_menu_at(
    page: Page,
    *,
    hover_x: int,
    hover_y: int,
    click_x: int,
    click_y: int,
    hover_ms: int,
) -> None:
    prepare_weixin_chrome_page(page)
    pyautogui.moveTo(hover_x, hover_y, duration=0.4)
    time.sleep(hover_ms / 1000)
    pyautogui.moveTo(click_x, click_y, duration=0.3)
    pyautogui.click()


def upload_local_images(page: Page, assets: WeixinPublishAssets, settings: WeixinPublishSettings) -> None:
    prepare_weixin_chrome_page(page)

    for index, image_path in enumerate(assets.image_paths, start=1):
        hover_x, hover_y, click_x, click_y = upload_screen_coords_for_index(settings, index)
        print(f"开始上传第 {index} 张：{image_path.name}")
        open_local_upload_menu_at(
            page,
            hover_x=hover_x,
            hover_y=hover_y,
            click_x=click_x,
            click_y=click_y,
            hover_ms=settings.upload_hover_ms,
        )
        print(
            f"已在 ({hover_x}, {hover_y}) 悬停 {settings.upload_hover_ms}ms，"
            f"并点击 ({click_x}, {click_y}) 打开本地上传。"
        )
        confirm_windows_open_dialog(
            [image_path],
            wait_ms=settings.windows_open_dialog_wait_ms,
            focus_page=page,
        )
        page.wait_for_timeout(2_500)

    print(f"公众号贴图上传完成，共 {len(assets.image_paths)} 张。")


def dismiss_blocking_dialogs(page: Page) -> None:
    for locator in (
        page.locator("div.weui-desktop-dialog:visible button").filter(has_text="取消"),
        page.get_by_role("button", name="取消"),
    ):
        try:
            button = locator.first
            if button.is_visible(timeout=500):
                button.click()
                page.wait_for_timeout(500)
                print("已关闭遮挡弹窗。")
                return
        except Exception:
            continue


def submit_declaration(page: Page) -> None:
    dismiss_blocking_dialogs(page)
    claim_source = page.locator("#js_claim_source_area")
    claim_source.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    not_added = wait_for_locator(page, declaration_not_added_locators(page), description="创作来源声明未添加", timeout_ms=30_000)
    not_added.scroll_into_view_if_needed()
    not_added.click()
    print("已点击：声明未添加")
    page.wait_for_timeout(1_500)
    personal_option = wait_for_locator(page, personal_view_option_locators(page), description="个人观点声明", timeout_ms=15_000)
    personal_option.scroll_into_view_if_needed()
    personal_option.click()
    print("已点击：个人观点声明")
    click_locator_via_dom(page, dialog_confirm_button_locators(page), description="声明确认按钮", timeout_ms=30_000)
    print("已完成个人观点声明。")


def save_as_draft(page: Page) -> None:
    click_locator_via_dom(page, save_draft_button_locators(page), description="保存为草稿", timeout_ms=30_000)
    page.wait_for_timeout(2_000)
    print("已点击保存为草稿。")


def find_cover_hover_target(page: Page) -> Locator:
    for locator in cover_preview_hover_locators(page):
        try:
            if locator.count() == 0:
                continue
            box = locator.bounding_box()
            if box and box.get("width", 0) > 80 and box.get("height", 0) > 80:
                print(f"已定位封面悬停区域：{box}")
                return locator
        except Exception:
            continue
    raise RuntimeError("未找到公众号封面悬停区域（.share_cover）。")


def wait_cover_crop_icon_visible(page: Page, timeout_ms: int = 5_000) -> Locator:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for locator in modify_cover_button_locators(page):
            try:
                item = locator.first
                if item.count() and item.is_visible():
                    return item
            except Exception:
                pass
        page.wait_for_timeout(200)
    raise RuntimeError("悬停封面后仍未出现裁剪图标（js_modifyCover）。")


def open_cover_crop_modal(page: Page) -> None:
    prepare_weixin_chrome_page(page)
    hover_target = find_cover_hover_target(page)
    hover_target.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    hover_target.hover()
    print("已悬停封面预览区，等待裁剪图标出现。")
    page.wait_for_timeout(600)
    crop_icon = wait_cover_crop_icon_visible(page)
    crop_icon.click(force=True)
    print("已点击封面裁剪图标。")
    page.wait_for_timeout(1_200)


def drag_cover_crop_up(page: Page, settings: WeixinPublishSettings) -> None:
    prepare_weixin_chrome_page(page)
    start_x = settings.cover_crop_drag_start_x
    start_y = settings.cover_crop_drag_start_y
    end_y = settings.cover_crop_drag_end_y
    pyautogui.moveTo(start_x, start_y, duration=0.3)
    time.sleep(0.15)
    pyautogui.mouseDown()
    time.sleep(0.12)
    pyautogui.moveTo(start_x, end_y, duration=0.55)
    pyautogui.mouseUp()
    print(
        f"已拖动封面裁剪框：({start_x}, {start_y}) → ({start_x}, {end_y})。"
    )


def adjust_weixin_cover_crop(page: Page, settings: WeixinPublishSettings) -> None:
    open_cover_crop_modal(page)
    select_forward_card_cover_preview(page)
    page.wait_for_timeout(settings.after_cover_editor_wait_ms)
    drag_cover_crop_up(page, settings)
    page.wait_for_timeout(500)
    click_locator_via_dom(page, cover_crop_confirm_button_locators(page), description="封面裁剪确认", timeout_ms=30_000)
    page.wait_for_timeout(1_000)
    print("已完成公众号封面裁剪调整（含确认）。")


def to_cdp_settings(settings: WeixinPublishSettings) -> PublishSettings:
    return PublishSettings(
        output_dir=settings.output_dir,
        cdp_url=settings.cdp_url,
        url_keyword=settings.url_keyword,
        chrome_path=settings.chrome_path,
        automation_profile_dir=settings.automation_profile_dir,
        auto_launch_browser=settings.auto_launch_browser,
        creator_home_url=settings.weixin_home_url,
        cdp_ready_timeout_ms=settings.cdp_ready_timeout_ms,
        typing_delay_ms=settings.typing_delay_ms,
        after_upload_wait_ms=0,
        after_open_cover_wait_ms=0,
        after_cover_confirm_wait_ms=0,
        after_declaration_open_wait_ms=0,
        auto_submit_publish=False,
        debug_screenshot=settings.debug_screenshot,
        dry_run=settings.dry_run,
    )


def resolve_weixin_mp_page(browser: Browser, settings: WeixinPublishSettings) -> Page:
    page = resolve_page_by_keyword(
        browser,
        url_keyword=settings.url_keyword,
        creator_home_url=settings.weixin_home_url,
        platform_label="微信公众号",
    )
    prepare_weixin_chrome_page(page)
    return page


def run_weixin_publish(settings: WeixinPublishSettings, assets: WeixinPublishAssets) -> None:
    if not settings.dry_run:
        ensure_cdp_browser_available(to_cdp_settings(settings))

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        page = resolve_weixin_mp_page(browser, settings)
        editor_page = page

        try:
            ensure_weixin_mp_logged_in(page, settings)
            open_home_page(page, settings)
            editor_page = open_tietu_editor(browser, page, settings)
            upload_local_images(editor_page, assets, settings)
            fill_contenteditable(
                editor_page,
                title_editor_locators(editor_page),
                text=assets.title_text,
                description="公众号标题",
                settings=settings,
            )
            fill_wechat_description(editor_page, assets, settings)
            submit_declaration(editor_page)
            save_as_draft(editor_page)
            if not settings.dry_run:
                adjust_weixin_cover_crop(editor_page, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                editor_page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise
