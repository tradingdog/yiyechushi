from __future__ import annotations

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
    from playwright.sync_api import Locator, Page, sync_playwright
except ImportError as exc:
    raise SystemExit(
        f"导入发布依赖失败：{exc}\n请执行：{sys.executable} -m pip install -r requirements.txt"
    ) from exc

from tools.douyin_publish import (  # noqa: E402
    DEFAULT_AUTOMATION_PROFILE_DIR,
    DEFAULT_CDP_URL,
    DEFAULT_TYPING_DELAY_MS,
    PublishSettings,
    click_locator,
    click_locator_via_dom,
    connect_cdp_browser,
    connect_cdp_browser_resilient,
    ensure_cdp_browser_available,
    find_optional_locator,
    normalize_schedule_at,
    resolve_page_by_keyword,
    type_text_humanly,
    wait_for_locator,
)
from tools.publish_login import wait_for_panel_login  # noqa: E402
from tools.screen_template_match import match_template_on_page  # noqa: E402
from tools.weixin_mp_publish import confirm_windows_open_dialog, paste_text_to_clipboard  # noqa: E402


DEFAULT_URL_KEYWORD = "channels.weixin.qq.com"
DEFAULT_CHANNELS_HOME_URL = "https://channels.weixin.qq.com/platform"
DEFAULT_LOGIN_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_logged_in_marker.png"
DEFAULT_MENU_CONTENT_MANAGE_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_menu_content_manage.png"
DEFAULT_MENU_GRAPHIC_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_menu_graphic.png"
DEFAULT_PUBLISH_GRAPHIC_BUTTON_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_btn_publish_graphic.png"
DEFAULT_UPLOAD_AREA_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_upload_area.png"
DEFAULT_LOGIN_MATCH_THRESHOLD = 0.9
DEFAULT_TEMPLATE_MATCH_THRESHOLD = 0.9
DEFAULT_STEP_WAIT_MS = 3_000
DEFAULT_AFTER_UPLOAD_WAIT_MS = 8_000
DEFAULT_AFTER_UPLOAD_TITLE_WAIT_MS = 3_000
DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS = 1_000
DEFAULT_BETWEEN_TOPICS_WAIT_MS = 2_000
DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS = 3_500
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "weixin_channels_publish_last_error.png"
DEFAULT_UPLOAD_STEP_SCREENSHOT = ROOT_DIR / "tools" / "weixin_channels_publish_upload_step.png"
DEFAULT_STEP_SCREENSHOT_DIR = ROOT_DIR / "tools" / "weixin_channels_publish_steps"
CHANNELS_MUSIC_SEARCH_KEYWORD = "好美味-释先生"


@dataclass(frozen=True)
class WeixinChannelsPublishAssets:
    output_dir: Path
    final_dir: Path
    image_paths: tuple[Path, ...]
    title_text: str
    description_body: str
    topic_tags: tuple[str, ...]
    title_file: Path
    description_file: Path


@dataclass(frozen=True)
class WeixinChannelsPublishSettings:
    output_dir: Path
    cdp_url: str
    url_keyword: str
    chrome_path: Path | None
    automation_profile_dir: Path
    auto_launch_browser: bool
    cdp_ready_timeout_ms: int
    typing_delay_ms: int
    login_marker_path: Path
    menu_content_manage_marker_path: Path
    menu_graphic_marker_path: Path
    publish_graphic_button_marker_path: Path
    upload_area_marker_path: Path
    login_match_threshold: float
    template_match_threshold: float
    step_wait_ms: int
    after_upload_wait_ms: int
    after_upload_title_wait_ms: int
    after_topic_paste_wait_ms: int
    between_topics_wait_ms: int
    windows_open_dialog_wait_ms: int
    upload_step_screenshot: Path
    step_screenshot_dir: Path
    debug_screenshot: Path
    schedule_at: str | None
    dry_run: bool


def _menu_bar(page: Page) -> Locator:
    return page.locator("#menuBar")


def content_manage_menu_locators(page: Page) -> tuple:
    menu_bar = _menu_bar(page)
    return (
        menu_bar.locator(".finder-ui-desktop-menu__name").filter(has_text="内容管理").first,
        menu_bar.locator("a").filter(has=page.locator(".finder-ui-desktop-menu__name", has_text="内容管理")).first,
        menu_bar.get_by_text("内容管理", exact=True).first,
    )


def graphic_menu_locators(page: Page) -> tuple:
    menu_bar = _menu_bar(page)
    return (
        menu_bar.locator(".finder-ui-desktop-menu__name").filter(has_text="图文").first,
        menu_bar.locator("a").filter(has=page.locator(".finder-ui-desktop-menu__name", has_text="图文")).first,
    )


def publish_graphic_button_locators(page: Page) -> tuple:
    return (
        page.locator("button.weui-desktop-btn.weui-desktop-btn_primary").filter(has_text="发表图文").first,
        page.get_by_role("button", name="发表图文"),
    )


def upload_file_input_locators(page: Page) -> tuple:
    return (
        page.locator(".post-upload-wrap input[type='file'][accept*='image']").first,
        page.locator(".ant-upload input[type='file'][accept*='image']").first,
        page.locator("input[type='file'][accept*='image'][multiple]").first,
    )


def upload_click_target_locators(page: Page) -> tuple:
    return (
        page.locator(".post-upload-wrap .ant-upload-drag").first,
        page.locator(".upload-wrap .ant-upload").first,
        page.locator("span.add-icon.weui-icon-outlined-add").first,
    )


def title_input_locators(page: Page) -> tuple:
    return (
        page.locator("div.form-item").filter(has=page.locator("div.label", has_text="图文标题")).locator(
            "input.weui-desktop-form__input"
        ).first,
        page.locator('input.weui-desktop-form__input[placeholder*="填写标题"]').first,
        page.locator('input.weui-desktop-form__input[placeholder="填写标题，22个字符内"]').first,
        page.get_by_placeholder("填写标题，22个字符内"),
    )


def description_editor_locators(page: Page) -> tuple:
    """与页面 DOM 一致：div.post-desc-box > div.input-editor[contenteditable]。"""
    return (
        page.locator(
            'div.post-desc-box div.input-editor[contenteditable][data-placeholder="添加描述，1000个字符内"]'
        ).first,
        page.locator("div.post-desc-box div.input-editor[contenteditable]").first,
        page.locator('div.input-editor[contenteditable][data-placeholder*="添加描述"]').first,
    )


def click_description_editor(page: Page) -> Locator:
    editor = click_locator(page, description_editor_locators(page), description="图文描述输入框", timeout_ms=15_000)
    editor.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    print("已点击图文描述正文输入框（div.input-editor）。")
    return editor


def _read_editor_text(editor: Locator) -> str:
    try:
        return str(editor.evaluate("el => (el.innerText || el.textContent || '').trim()"))
    except Exception:
        return str(editor.inner_text(timeout=2_000) or "").strip()


def _text_mostly_present(current: str, expected: str, *, min_ratio: float = 0.85) -> bool:
    expected_text = expected.strip()
    current_text = current.strip()
    if not expected_text:
        return True
    if len(current_text) < len(expected_text) * min_ratio:
        return False
    head = expected_text[:12]
    tail = expected_text[-12:] if len(expected_text) >= 12 else expected_text
    return head in current_text and tail in current_text


def move_cursor_to_editor_end(page: Page, editor: Locator) -> None:
    editor.click(force=True)
    page.wait_for_timeout(150)
    page.keyboard.press("Control+End")
    page.wait_for_timeout(200)


def _normalize_topic_tag(topic_tag: str) -> str:
    text = str(topic_tag or "").strip()
    if not text:
        raise ValueError("话题标签不能为空。")
    return text if text.startswith("#") else f"#{text}"


def _topics_missing_in_editor(editor: Locator, topic_tags: Sequence[str]) -> list[str]:
    current = _read_editor_text(editor)
    return [tag for tag in topic_tags if tag not in current]


def _paste_clipboard_with_playwright(
    page: Page,
    editor: Locator,
    *,
    clipboard_settle_s: float = 0.35,
    after_paste_wait_ms: int,
) -> None:
    page.bring_to_front()
    editor.click(force=True)
    page.wait_for_timeout(120)
    time.sleep(clipboard_settle_s)
    page.keyboard.press("Control+v")
    page.wait_for_timeout(max(0, after_paste_wait_ms))


def paste_weixin_channels_topic_tags(
    page: Page,
    editor: Locator,
    topic_tags: Sequence[str],
    settings: WeixinChannelsPublishSettings,
) -> None:
    """视频号话题：优先整行 Playwright 粘贴，缺项再逐个补粘并校验。"""
    normalized = tuple(_normalize_topic_tag(tag) for tag in topic_tags if str(tag or "").strip())
    if not normalized:
        return

    move_cursor_to_editor_end(page, editor)
    page.wait_for_timeout(300)

    topics_line = " ".join(normalized)
    paste_text_to_clipboard(topics_line)
    _paste_clipboard_with_playwright(
        page,
        editor,
        after_paste_wait_ms=settings.after_topic_paste_wait_ms,
    )
    missing = _topics_missing_in_editor(editor, normalized)
    if not missing:
        print(f"已一次性粘贴视频号 {len(normalized)} 个话题（整行）。")
        return

    print(
        f"整行粘贴后仍缺 {len(missing)} 个话题，改为逐个补粘："
        f"{' '.join(missing[:5])}{'…' if len(missing) > 5 else ''}"
    )
    for index, tag in enumerate(missing):
        move_cursor_to_editor_end(page, editor)
        page.keyboard.press("Space")
        page.wait_for_timeout(150)
        paste_text_to_clipboard(tag)
        _paste_clipboard_with_playwright(
            page,
            editor,
            after_paste_wait_ms=settings.after_topic_paste_wait_ms,
        )
        page.wait_for_timeout(max(0, settings.between_topics_wait_ms))
        if tag not in _read_editor_text(editor):
            print(f"话题 {tag} 首次未写入，重试一次。")
            move_cursor_to_editor_end(page, editor)
            page.keyboard.press("Space")
            page.wait_for_timeout(150)
            paste_text_to_clipboard(tag)
            _paste_clipboard_with_playwright(
                page,
                editor,
                after_paste_wait_ms=settings.after_topic_paste_wait_ms,
            )
        print(f"已粘贴视频号话题（补粘 {index + 1}/{len(missing)}）：{tag}")

    still_missing = _topics_missing_in_editor(editor, normalized)
    if still_missing:
        raise RuntimeError(
            f"视频号话题未全部写入，仍缺 {len(still_missing)} 个：{' '.join(still_missing)}"
        )
    print(f"已补粘完成，视频号共 {len(normalized)} 个话题。")


def paste_into_contenteditable(
    page: Page,
    editor: Locator,
    text: str,
    *,
    description: str,
    typing_delay_ms: int,
) -> None:
    editor.click(force=True)
    page.wait_for_timeout(150)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(100)

    paste_text_to_clipboard(text)
    _paste_clipboard_with_playwright(page, editor, clipboard_settle_s=0.2, after_paste_wait_ms=800)
    current = _read_editor_text(editor)
    if _text_mostly_present(current, text):
        print(f"已向图文描述输入框粘贴{description}（长度 {len(current)}/{len(text.strip())}）。")
        return

    print(f"剪贴板粘贴不完整（当前长度 {len(current)}），改用 press_sequentially 输入。")
    editor.click(force=True)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(100)
    editor.press_sequentially(text, delay=max(20, min(typing_delay_ms, 80)))
    page.wait_for_timeout(500)
    current = _read_editor_text(editor)
    if _text_mostly_present(current, text):
        print(f"已向图文描述输入框写入{description}（长度 {len(current)}/{len(text.strip())}）。")
        return

    raise RuntimeError(
        f"{description}输入不完整：期望约 {len(text.strip())} 字，实际 {len(current)} 字；"
        f"内容片段：{current[:60]!r}…{current[-40:]!r}"
    )


def parse_topic_tags(topics_text: str) -> tuple[str, ...]:
    tags = [tag for tag in re.findall(r"#\S+", str(topics_text or ""))]
    return tuple(dict.fromkeys(tags))


def _channels_page_priority(url: str) -> int:
    if "finderNewLifeCreate" in url:
        return 0
    if "finderNewLifePostList" in url:
        return 1
    if "/platform/post" in url:
        return 2
    if re.fullmatch(r"https://channels\.weixin\.qq\.com/platform/?", url.rstrip("/")):
        return 4
    if url.rstrip("/").endswith("/platform"):
        return 3
    return 5


def find_channels_page(browser, url_keyword: str) -> Page:
    return resolve_channels_page(browser, url_keyword)


def resolve_channels_page(browser, url_keyword: str) -> Page:
    return resolve_page_by_keyword(
        browser,
        url_keyword=url_keyword,
        creator_home_url=DEFAULT_CHANNELS_HOME_URL,
        platform_label="微信视频号",
        page_priority=_channels_page_priority,
    )


def close_stray_file_dialog(page: Page) -> None:
    page.bring_to_front()
    page.wait_for_timeout(200)
    pyautogui.press("escape")
    page.wait_for_timeout(300)


def save_step_screenshot(page: Page, screenshot_path: Path, label: str) -> None:
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"已保存{label}截图：{screenshot_path}")


def save_flow_step_screenshot(page: Page, settings: WeixinChannelsPublishSettings, step_id: str, label: str) -> Path:
    safe_label = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", label).strip("_") or "step"
    screenshot_path = settings.step_screenshot_dir / f"{step_id}_{safe_label}.png"
    save_step_screenshot(page, screenshot_path, f"流程步骤 {step_id} {label}")
    return screenshot_path


def is_publish_graphic_entry_visible(page: Page) -> bool:
    return find_optional_locator(page, publish_graphic_button_locators(page), timeout_ms=2_000) is not None


def is_graphic_compose_page(page: Page) -> bool:
    if "finderNewLifeCreate" in page.url:
        return True
    if find_optional_locator(page, upload_click_target_locators(page), timeout_ms=2_000):
        return True
    if find_optional_locator(page, upload_file_input_locators(page), state="attached", timeout_ms=2_000):
        return True
    if _find_visible_title_input(page) is not None:
        return True
    if find_optional_locator(page, description_editor_locators(page), timeout_ms=2_000):
        return True
    try:
        if page.get_by_text("发表动态", exact=True).first.is_visible(timeout=500):
            return True
    except Exception:
        pass
    return False


def _find_visible_title_input(page: Page) -> Locator | None:
    for locator_group in title_input_locators(page):
        try:
            candidate = locator_group.first
            if candidate.is_visible(timeout=300) and candidate.is_enabled(timeout=300):
                return candidate
        except Exception:
            continue
    return None


def wait_for_title_input_ready(page: Page, *, timeout_ms: int) -> Locator:
    attempts = max(1, timeout_ms // 500)
    for attempt in range(attempts):
        title_input = _find_visible_title_input(page)
        if title_input is not None:
            print(f"图文标题输入框已就绪（第 {attempt + 1} 次检测）。")
            return title_input
        page.wait_for_timeout(500)
    raise RuntimeError("上传后未找到可编辑的图文标题输入框。")


def fill_locator_text(
    page: Page,
    locator: Locator,
    text: str,
    *,
    description: str,
    typing_delay_ms: int,
) -> None:
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    page.wait_for_timeout(200)

    try:
        locator.fill("")
        locator.fill(text)
        current = locator.input_value(timeout=2_000)
        if current.strip() == text.strip():
            print(f"已通过 fill 输入{description}。")
            return
    except Exception as exc:
        print(f"fill 输入{description}失败，改用键盘：{exc}")

    locator.click(force=True)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, text, delay_ms=typing_delay_ms)
    current = ""
    try:
        current = locator.input_value(timeout=2_000)
    except Exception:
        pass
    if current.strip() == text.strip():
        print(f"已通过键盘输入{description}。")
        return

    paste_text_to_clipboard(text)
    page.bring_to_front()
    time.sleep(0.15)
    locator.click(force=True)
    pyautogui.hotkey("ctrl", "v")
    page.wait_for_timeout(300)
    try:
        current = locator.input_value(timeout=2_000)
    except Exception:
        current = ""
    if current.strip() != text.strip():
        raise RuntimeError(f"{description}输入失败，期望「{text}」，实际「{current}」。")
    print(f"已通过剪贴板输入{description}。")


def is_channels_login_required(page: Page) -> bool:
    """页面上出现扫码/登录入口时，判定为未登录。"""
    login_keywords = (
        "扫码登录",
        "微信扫一扫",
        "请使用微信扫码",
        "登录视频号助手",
        "扫码关注后登录",
    )
    for keyword in login_keywords:
        try:
            if page.get_by_text(keyword, exact=False).first.is_visible(timeout=600):
                return True
        except Exception:
            continue
    url = (page.url or "").lower()
    if "channels.weixin.qq.com" in url and any(token in url for token in ("/login", "loginpage", "auth")):
        return True
    return False


def is_channels_logged_in_dom(page: Page) -> bool:
    """用平台后台 DOM 判定登录态（比头像模板匹配更稳）。"""
    url = page.url or ""
    if "channels.weixin.qq.com" not in url:
        return False
    if is_channels_login_required(page):
        return False
    if is_graphic_compose_page(page) or is_publish_graphic_entry_visible(page):
        return True
    if find_optional_locator(page, content_manage_menu_locators(page), timeout_ms=1_500) is not None:
        return True
    if find_optional_locator(page, graphic_menu_locators(page), timeout_ms=1_000) is not None:
        return True
    try:
        if page.locator("#menuBar").first.is_visible(timeout=1_000):
            return True
    except Exception:
        pass
    return False


def ensure_channels_logged_in(page: Page, settings: WeixinChannelsPublishSettings) -> None:
    """优先 DOM 判定登录；头像模板仅作补充。避免已登录仍因模板失败反复弹窗刷新。"""
    while True:
        if is_channels_logged_in_dom(page):
            print("已检测到视频号登录态（DOM：平台后台可见）。")
            return

        if settings.login_marker_path.exists():
            matched, score = match_template_on_page(
                page,
                settings.login_marker_path,
                threshold=settings.login_match_threshold,
            )
            if matched:
                print(f"已检测到视频号登录态（模板匹配度 {score:.3f}）。")
                return
            print(
                f"视频号头像模板未命中（匹配度 {score:.3f}，阈值 "
                f"{settings.login_match_threshold}），改用 DOM/登录页判定。"
            )
        elif not settings.login_marker_path.exists():
            print(f"登录识别模板不存在，仅用 DOM 判定：{settings.login_marker_path}")

        if not is_channels_login_required(page) and "channels.weixin.qq.com/platform" in (page.url or ""):
            # 平台页可能仍在加载侧栏，稍等再判一次，避免误弹登录框。
            page.wait_for_timeout(1_200)
            if is_channels_logged_in_dom(page):
                print("已检测到视频号登录态（DOM：平台页加载后）。")
                return

        wait_for_panel_login(
            platform_label="微信视频号",
            confirm_kind="enter",
            hint=(
                "未检测到视频号平台后台登录态。若浏览器已登录，请点「已完成登录，继续」；"
                "若仍在扫码页，请先扫码登录。"
            ),
        )
        # 用户确认后先立刻再判 DOM，已登录则无需刷新（刷新会再次打乱页面）。
        if is_channels_logged_in_dom(page):
            print("已检测到视频号登录态（用户确认后 DOM 复核通过）。")
            return
        try:
            page.goto(DEFAULT_CHANNELS_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)


def _click_sidebar_menu(page: Page, locators: tuple, *, description: str) -> None:
    try:
        click_locator(page, locators, description=description, timeout_ms=15_000)
    except Exception:
        click_locator_via_dom(page, locators, description=description, timeout_ms=15_000)


def open_graphic_publish_editor(page: Page, settings: WeixinChannelsPublishSettings) -> None:
    """登录优先 DOM 判定；导航与发表优先用 #menuBar / 按钮 DOM。"""
    page.bring_to_front()
    ensure_channels_logged_in(page, settings)
    save_flow_step_screenshot(page, settings, "01", "登录检测通过")

    if is_graphic_compose_page(page):
        print("当前已在发表图文编辑页（发表动态），跳过导航。")
        save_flow_step_screenshot(page, settings, "04", "已在发表编辑页")
        return

    if is_publish_graphic_entry_visible(page):
        print("当前在图文管理列表页，直接点击「发表图文」。")
        save_flow_step_screenshot(page, settings, "02", "已在图文管理列表")
        click_locator(page, publish_graphic_button_locators(page), description="发表图文", timeout_ms=20_000)
        page.wait_for_timeout(settings.step_wait_ms)
        save_flow_step_screenshot(page, settings, "04", "点击发表图文后")
        return

    _click_sidebar_menu(page, content_manage_menu_locators(page), description="内容管理")
    page.wait_for_timeout(settings.step_wait_ms)
    save_flow_step_screenshot(page, settings, "02", "点击内容管理后")

    _click_sidebar_menu(page, graphic_menu_locators(page), description="图文")
    page.wait_for_timeout(settings.step_wait_ms)
    save_flow_step_screenshot(page, settings, "03", "点击图文后")

    if is_graphic_compose_page(page):
        print("点击「图文」后已进入发表编辑页，无需再点「发表图文」。")
        save_flow_step_screenshot(page, settings, "04", "点击图文后进入编辑页")
        return

    if is_publish_graphic_entry_visible(page):
        click_locator(page, publish_graphic_button_locators(page), description="发表图文", timeout_ms=20_000)
        page.wait_for_timeout(settings.step_wait_ms)
        save_flow_step_screenshot(page, settings, "04", "点击发表图文后")
        return

    raise RuntimeError(
        "点击「图文」后未进入发表编辑页，也未找到「发表图文」按钮。"
        f"当前 URL：{page.url}"
    )


def _upload_via_dom_input(page: Page, image_paths: Sequence[Path]) -> bool:
    upload_input = find_optional_locator(page, upload_file_input_locators(page), state="attached", timeout_ms=5_000)
    if upload_input is None:
        return False

    resolved_paths = [str(path.resolve()) for path in image_paths]
    upload_input.set_input_files(resolved_paths)
    print(f"已通过 file input 投喂 {len(resolved_paths)} 张图片。")
    for path in image_paths:
        print(f"  - {path.name}")
    return True


def upload_graphic_images(page: Page, assets: WeixinChannelsPublishAssets, settings: WeixinChannelsPublishSettings) -> None:
    print(f"准备上传 {len(assets.image_paths)} 张图文（01/02/03）。")
    page.bring_to_front()
    save_flow_step_screenshot(page, settings, "05a", "上传前")

    if _upload_via_dom_input(page, assets.image_paths):
        page.wait_for_timeout(settings.after_upload_wait_ms)
        close_stray_file_dialog(page)
        wait_for_title_input_ready(page, timeout_ms=60_000)
        page.wait_for_timeout(settings.after_upload_title_wait_ms)
        save_flow_step_screenshot(page, settings, "05b", "上传后")
        save_step_screenshot(page, settings.upload_step_screenshot, "图文上传汇总")
        print(f"视频号图文上传完成，共 {len(assets.image_paths)} 张。")
        return

    resolved_paths = [str(path.resolve()) for path in assets.image_paths]
    uploaded = False
    try:
        with page.expect_file_chooser(timeout=12_000) as chooser_info:
            click_locator(page, upload_click_target_locators(page), description="上传区域", timeout_ms=15_000)
        chooser_info.value.set_files(resolved_paths)
        uploaded = True
        print(f"已通过 file chooser 上传 {len(resolved_paths)} 张。")
    except Exception as exc:
        print(f"上传区 file chooser 不可用，改用 Windows 对话框：{exc}")
        click_locator(page, upload_click_target_locators(page), description="上传区域", timeout_ms=15_000)
        page.wait_for_timeout(500)
        confirm_windows_open_dialog(
            assets.image_paths,
            wait_ms=settings.windows_open_dialog_wait_ms,
            focus_page=page,
        )
        uploaded = True

    if not uploaded:
        raise RuntimeError("视频号图文上传失败。")

    close_stray_file_dialog(page)
    page.wait_for_timeout(settings.after_upload_wait_ms)
    wait_for_title_input_ready(page, timeout_ms=60_000)
    page.wait_for_timeout(settings.after_upload_title_wait_ms)
    save_flow_step_screenshot(page, settings, "05b", "上传后")
    save_step_screenshot(page, settings.upload_step_screenshot, "图文上传汇总")
    print(f"视频号图文上传完成，共 {len(assets.image_paths)} 张。")


def fill_title(page: Page, assets: WeixinChannelsPublishAssets, settings: WeixinChannelsPublishSettings) -> None:
    title_input = wait_for_title_input_ready(page, timeout_ms=30_000)
    fill_locator_text(
        page,
        title_input,
        assets.title_text,
        description="图文标题",
        typing_delay_ms=settings.typing_delay_ms,
    )
    save_flow_step_screenshot(page, settings, "06", "标题输入后")
    print(f"已输入视频号标题：{assets.title_text}")


def fill_description_and_topics(
    page: Page,
    assets: WeixinChannelsPublishAssets,
    settings: WeixinChannelsPublishSettings,
) -> None:
    editor = click_description_editor(page)
    paste_into_contenteditable(
        page,
        editor,
        assets.description_body,
        description="图文描述正文",
        typing_delay_ms=settings.typing_delay_ms,
    )
    move_cursor_to_editor_end(page, editor)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    print(f"已输入视频号描述正文，长度 {len(assets.description_body)}。")

    if not assets.topic_tags:
        print("未配置话题标签，跳过话题粘贴。")
        return

    page.wait_for_timeout(500)
    move_cursor_to_editor_end(page, editor)
    paste_weixin_channels_topic_tags(page, editor, assets.topic_tags, settings)
    save_flow_step_screenshot(page, settings, "07", "描述与话题输入后")


def select_weixin_channels_music(page: Page, settings: WeixinChannelsPublishSettings) -> None:
    music_input = find_optional_locator(
        page,
        (
            page.get_by_placeholder("搜索音乐"),
            page.locator("input[placeholder*='搜索']"),
            page.locator("label").filter(has_text="音乐").locator("..").locator("input").first,
            page.locator("input").filter(has=page.get_by_text("音乐", exact=False)),
        ),
        timeout_ms=10_000,
    )
    if music_input is None:
        print("未找到视频号音乐搜索框，跳过音乐设置。")
        return
    music_input.scroll_into_view_if_needed()
    music_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, CHANNELS_MUSIC_SEARCH_KEYWORD, delay_ms=settings.typing_delay_ms)
    page.wait_for_timeout(1_200)
    first_result = find_optional_locator(
        page,
        (
            page.locator("div").filter(has_text="好美味").filter(has_text="释先生").first,
            page.get_by_text("好美味", exact=False).first,
        ),
        timeout_ms=10_000,
    )
    if first_result is None:
        raise RuntimeError(f"视频号音乐搜索结果为空：{CHANNELS_MUSIC_SEARCH_KEYWORD}")
    first_result.click()
    page.wait_for_timeout(600)
    print(f"已选择视频号音乐：{CHANNELS_MUSIC_SEARCH_KEYWORD}")


def scroll_to_schedule_section(page: Page) -> None:
    page.evaluate(
        """() => {
          document.querySelectorAll('*').forEach(el => {
            if (el.scrollHeight > el.clientHeight + 100) el.scrollTop = el.scrollHeight;
          });
        }"""
    )
    page.wait_for_timeout(500)


def scheduled_radio_locator(page: Page) -> Locator:
    return page.locator('input.weui-desktop-form__radio[value="1"]').first


def scheduled_label_locator(page: Page) -> Locator:
    return page.locator("label.weui-desktop-form__check-label").filter(
        has=page.locator('input.weui-desktop-form__radio[value="1"]')
    ).first


def schedule_datetime_input_locator(page: Page) -> Locator:
    for candidate in (
        page.locator("dl.weui-desktop-picker__date-time input.weui-desktop-form__input"),
        page.locator("dl.weui-desktop-picker__date-time input"),
    ):
        if candidate.count():
            return candidate.first
    raise RuntimeError("未找到视频号定时发表时间输入框。")


def schedule_picker_scope(page: Page) -> Locator:
    return page.locator("dl.weui-desktop-picker__date-time, dl.weui-desktop-picker__focus").first


def click_scheduled_publish_mode(page: Page) -> None:
    label = scheduled_label_locator(page)
    if label.count():
        label.click()
    else:
        scheduled_radio_locator(page).click(force=True)
    page.wait_for_timeout(400)


def open_schedule_picker(page: Page) -> None:
    dt_input = schedule_datetime_input_locator(page)
    dt_input.click()
    page.wait_for_timeout(500)
    if page.locator(".weui-desktop-picker__panel_day").count():
        return
    for candidate in (
        page.locator("dl.weui-desktop-picker__date-time .weui-desktop-icon__date"),
        page.locator("dl.weui-desktop-picker__date-time dt"),
    ):
        if candidate.count():
            candidate.first.click()
            page.wait_for_timeout(500)
            break
    page.locator(".weui-desktop-picker__panel_day").first.wait_for(state="visible", timeout=10_000)


def pick_schedule_day(page: Page, day: int) -> None:
    day_panel = page.locator(".weui-desktop-picker__panel_day").first
    day_panel.wait_for(state="visible", timeout=10_000)
    day_text = str(day)
    for selector in (
        day_panel.locator("a").filter(has_text=day_text),
        day_panel.locator("td").filter(has_text=day_text),
    ):
        for index in range(selector.count()):
            cell = selector.nth(index)
            if cell.inner_text().strip() != day_text:
                continue
            cell.click()
            page.wait_for_timeout(300)
            return
    raise RuntimeError(f"视频号日期选择器里未找到 {day} 日。")


def click_schedule_clock_icon(page: Page) -> None:
    scope = schedule_picker_scope(page)
    inner_time_input = scope.locator('input.weui-desktop-form__input[placeholder*="时间"]').first
    if inner_time_input.count():
        inner_time_input.click()
        page.wait_for_timeout(300)
    clock = scope.locator("i.weui-desktop-icon__time").first
    clock.wait_for(state="visible", timeout=10_000)
    clock.click(force=True)
    page.wait_for_timeout(500)


def pick_schedule_time_column(page: Page, panel_class_suffix: str, target: str, *, description: str) -> None:
    scope = schedule_picker_scope(page)
    panel = scope.locator(f".weui-desktop-picker__time__panel.weui-desktop-picker__time__{panel_class_suffix}").first
    panel.wait_for(state="attached", timeout=10_000)
    padded = str(int(target)).zfill(2)
    items = panel.locator("li")
    for index in range(items.count()):
        item = items.nth(index)
        text = item.inner_text().strip()
        if text in {padded, str(int(target))}:
            item.scroll_into_view_if_needed()
            item.click(force=True)
            page.wait_for_timeout(200)
            return
    raise RuntimeError(f"未找到视频号{description}选项：{target}。")


def schedule_picker_open_locator(page: Page) -> Locator:
    return page.locator(
        ".weui-desktop-picker__panel_day:visible, .weui-desktop-picker__time__panel:visible"
    )


def is_schedule_picker_open(page: Page) -> bool:
    return schedule_picker_open_locator(page).count() > 0


def dismiss_schedule_picker(page: Page) -> None:
    if not is_schedule_picker_open(page):
        return

    dt_input = schedule_datetime_input_locator(page)
    box = dt_input.bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] + 60, box["y"] + box["height"] / 2)
        page.wait_for_timeout(400)

    if is_schedule_picker_open(page):
        for candidate in (
            page.locator(".post-time-wrap .label").first,
            page.locator(".post-time-wrap dt").first,
            page.locator("div.form-item").filter(has=page.locator("div.label", has_text="发表时间")).first,
        ):
            if not candidate.count():
                continue
            label_box = candidate.bounding_box()
            if label_box:
                page.mouse.click(label_box["x"] + 8, label_box["y"] + label_box["height"] / 2)
                page.wait_for_timeout(400)
                break

    if is_schedule_picker_open(page):
        scope = schedule_picker_scope(page)
        scope_box = scope.bounding_box()
        if scope_box:
            page.mouse.click(max(24, scope_box["x"] - 80), scope_box["y"] - 40)
            page.wait_for_timeout(400)

    if is_schedule_picker_open(page):
        title_input = find_optional_locator(page, title_input_locators(page), timeout_ms=5_000)
        if title_input is not None:
            title_box = title_input.bounding_box()
            if title_box:
                page.mouse.click(title_box["x"] + title_box["width"] / 2, title_box["y"] + title_box["height"] / 2)
                page.wait_for_timeout(400)

    if is_schedule_picker_open(page):
        raise RuntimeError("视频号定时发表弹层未能关闭，请检查空白确认点击逻辑。")


def set_weixin_channels_scheduled_publish_time(
    page: Page,
    schedule_at: str,
    settings: WeixinChannelsPublishSettings,
) -> None:
    day = int(schedule_at[8:10])
    hour = str(int(schedule_at[11:13]))
    minute = str(int(schedule_at[14:16]))
    scroll_to_schedule_section(page)
    click_scheduled_publish_mode(page)
    open_schedule_picker(page)
    pick_schedule_day(page, day)
    click_schedule_clock_icon(page)
    pick_schedule_time_column(page, "hour", hour, description="小时")
    pick_schedule_time_column(page, "minute", minute, description="分钟")
    dismiss_schedule_picker(page)

    current_value = schedule_datetime_input_locator(page).input_value().strip()
    if not current_value.startswith(schedule_at):
        raise RuntimeError(f"视频号定时发表时间未生效，期望 {schedule_at}，当前 {current_value or '空'}。")
    if not scheduled_radio_locator(page).is_checked():
        raise RuntimeError("视频号定时发表模式未保持选中状态。")
    print(f"已设置视频号定时发表时间：{current_value}")
    save_flow_step_screenshot(page, settings, "08", "定时发表设置后")


def to_cdp_settings(settings: WeixinChannelsPublishSettings) -> PublishSettings:
    return PublishSettings(
        output_dir=settings.output_dir,
        cdp_url=settings.cdp_url,
        url_keyword=settings.url_keyword,
        chrome_path=settings.chrome_path,
        automation_profile_dir=settings.automation_profile_dir,
        auto_launch_browser=settings.auto_launch_browser,
        creator_home_url=DEFAULT_CHANNELS_HOME_URL,
        cdp_ready_timeout_ms=settings.cdp_ready_timeout_ms,
        typing_delay_ms=settings.typing_delay_ms,
        after_upload_wait_ms=0,
        after_open_cover_wait_ms=0,
        after_cover_confirm_wait_ms=0,
        after_declaration_open_wait_ms=0,
        auto_submit_publish=False,
        schedule_at=settings.schedule_at,
        debug_screenshot=settings.debug_screenshot,
        dry_run=settings.dry_run,
    )


def run_weixin_channels_publish(
    settings: WeixinChannelsPublishSettings,
    assets: WeixinChannelsPublishAssets,
) -> None:
    if not settings.dry_run:
        ensure_cdp_browser_available(to_cdp_settings(settings))

    with sync_playwright() as playwright:
        browser = connect_cdp_browser_resilient(playwright, to_cdp_settings(settings))
        page = resolve_channels_page(browser, settings.url_keyword)

        try:
            open_graphic_publish_editor(page, settings)
            upload_graphic_images(page, assets, settings)
            fill_title(page, assets, settings)
            fill_description_and_topics(page, assets, settings)
            try:
                select_weixin_channels_music(page, settings)
            except Exception as music_exc:
                print(f"视频号音乐选择失败（继续其余步骤）：{music_exc}")
            if settings.schedule_at:
                set_weixin_channels_scheduled_publish_time(page, settings.schedule_at, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise
