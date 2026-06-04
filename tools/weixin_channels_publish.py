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
    ensure_cdp_browser_available,
    find_optional_locator,
    type_text_humanly,
    wait_for_locator,
)
from tools.kuaishou_publish import paste_topic_tags_via_clipboard  # noqa: E402
from tools.screen_template_match import match_template_on_page  # noqa: E402
from tools.weixin_mp_publish import confirm_windows_open_dialog, paste_text_to_clipboard  # noqa: E402


DEFAULT_URL_KEYWORD = "channels.weixin.qq.com"
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
    page.bring_to_front()
    time.sleep(0.15)
    editor.click(force=True)
    pyautogui.hotkey("ctrl", "v")
    page.wait_for_timeout(500)
    current = _read_editor_text(editor)
    snippet = text.strip()[:12]
    if snippet and snippet in current:
        print(f"已向图文描述输入框粘贴{description}。")
        return

    print(f"剪贴板粘贴未检测到正文（当前长度 {len(current)}），改用逐字输入。")
    editor.click(force=True)
    type_text_humanly(page, text, delay_ms=typing_delay_ms)
    page.wait_for_timeout(300)
    current = _read_editor_text(editor)
    if snippet and snippet not in current:
        raise RuntimeError(f"{description}输入失败，编辑器内容：{current[:80]!r}")
    print(f"已向图文描述输入框写入{description}。")


def parse_topic_tags(topics_text: str) -> tuple[str, ...]:
    tags = [tag for tag in re.findall(r"#\S+", str(topics_text or ""))]
    return tuple(dict.fromkeys(tags))


def find_channels_page(browser, url_keyword: str) -> Page:
    matched_pages = [
        page
        for context in browser.contexts
        for page in context.pages
        if url_keyword in page.url
    ]
    if not matched_pages:
        raise RuntimeError(
            f"未在已打开的 Chrome 标签页里找到包含 {url_keyword} 的页面。"
            "请先自行打开微信视频号助手并登录，然后重新运行脚本。"
        )
    page = matched_pages[-1]
    page.bring_to_front()
    page.wait_for_load_state("domcontentloaded")
    print(f"已锁定视频号页面：{page.url}")
    return page


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
    if find_optional_locator(page, upload_file_input_locators(page), state="attached", timeout_ms=1_500):
        return True
    if _find_visible_title_input(page) is not None:
        return True
    if find_optional_locator(page, description_editor_locators(page), timeout_ms=1_500):
        return True
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


def ensure_channels_logged_in(page: Page, settings: WeixinChannelsPublishSettings) -> None:
    if not settings.login_marker_path.exists():
        raise RuntimeError(f"登录识别模板不存在：{settings.login_marker_path}")

    while True:
        matched, score = match_template_on_page(
            page,
            settings.login_marker_path,
            threshold=settings.login_match_threshold,
        )
        if matched:
            print(f"已检测到视频号登录态（匹配度 {score:.3f}）。")
            return

        print(
            "未在页面中匹配到视频号登录标识（阈值 "
            f"{settings.login_match_threshold}）。可能已退出登录，请重新扫码登录。"
        )
        input("完成扫码登录后按回车继续：")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)


def _click_sidebar_menu(page: Page, locators: tuple, *, description: str) -> None:
    try:
        click_locator(page, locators, description=description, timeout_ms=15_000)
    except Exception:
        click_locator_via_dom(page, locators, description=description, timeout_ms=15_000)


def open_graphic_publish_editor(page: Page, settings: WeixinChannelsPublishSettings) -> None:
    """按用户步骤：仅图1做登录匹配；导航与发表优先用 #menuBar / 按钮 DOM。"""
    page.bring_to_front()
    ensure_channels_logged_in(page, settings)
    save_flow_step_screenshot(page, settings, "01", "登录检测通过")

    if is_graphic_compose_page(page):
        print("当前已在发表图文编辑页，跳过导航。")
        save_flow_step_screenshot(page, settings, "04", "已在发表编辑页")
        return

    if is_publish_graphic_entry_visible(page):
        print("当前已在图文管理列表页，跳过侧边栏「内容管理/图文」。")
        save_flow_step_screenshot(page, settings, "02", "已在图文管理列表")
    else:
        _click_sidebar_menu(page, content_manage_menu_locators(page), description="内容管理")
        page.wait_for_timeout(settings.step_wait_ms)
        save_flow_step_screenshot(page, settings, "02", "点击内容管理后")

        _click_sidebar_menu(page, graphic_menu_locators(page), description="图文")
        page.wait_for_timeout(settings.step_wait_ms)
        save_flow_step_screenshot(page, settings, "03", "点击图文后")

    click_locator(page, publish_graphic_button_locators(page), description="发表图文", timeout_ms=20_000)
    page.wait_for_timeout(settings.step_wait_ms)
    save_flow_step_screenshot(page, settings, "04", "点击发表图文后")


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
    editor.click(force=True)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    print(f"已输入视频号描述正文，长度 {len(assets.description_body)}。")

    if not assets.topic_tags:
        print("未配置话题标签，跳过话题粘贴。")
        return

    editor.click()
    page.wait_for_timeout(200)
    paste_topic_tags_via_clipboard(
        page,
        editor,
        assets.topic_tags,
        after_paste_wait_ms=settings.after_topic_paste_wait_ms,
        between_topics_wait_ms=settings.between_topics_wait_ms,
        platform_label="视频号",
    )
    save_flow_step_screenshot(page, settings, "07", "描述与话题输入后")
    print(f"已粘贴视频号 {len(assets.topic_tags)} 个话题。")


def to_cdp_settings(settings: WeixinChannelsPublishSettings) -> PublishSettings:
    return PublishSettings(
        output_dir=settings.output_dir,
        cdp_url=settings.cdp_url,
        url_keyword=settings.url_keyword,
        chrome_path=settings.chrome_path,
        automation_profile_dir=settings.automation_profile_dir,
        auto_launch_browser=settings.auto_launch_browser,
        creator_home_url=f"https://{DEFAULT_URL_KEYWORD}/",
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


def run_weixin_channels_publish(
    settings: WeixinChannelsPublishSettings,
    assets: WeixinChannelsPublishAssets,
) -> None:
    if settings.auto_launch_browser:
        ensure_cdp_browser_available(to_cdp_settings(settings))
    elif not settings.dry_run:
        from tools.douyin_publish import is_cdp_endpoint_ready

        if not is_cdp_endpoint_ready(settings.cdp_url):
            raise RuntimeError(
                f"无法连接到 Chrome 远程调试端口：{settings.cdp_url}\n"
                "请先自行打开 Chrome 并登录 channels.weixin.qq.com，再运行本脚本。"
            )

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        page = find_channels_page(browser, settings.url_keyword)

        try:
            open_graphic_publish_editor(page, settings)
            upload_graphic_images(page, assets, settings)
            fill_title(page, assets, settings)
            fill_description_and_topics(page, assets, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise
