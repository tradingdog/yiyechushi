from __future__ import annotations

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
    from playwright.sync_api import Page, sync_playwright
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
    find_optional_locator,
    resolve_page_by_keyword,
    type_text_humanly,
    wait_for_locator,
)
from tools.screen_template_match import match_template_on_page  # noqa: E402
from tools.weixin_mp_publish import confirm_windows_open_dialog  # noqa: E402


DEFAULT_URL_KEYWORD = "creator.xiaohongshu.com"
DEFAULT_XIAOHONGSHU_HOME_URL = "https://creator.xiaohongshu.com/"
DEFAULT_LOGIN_MARKER = ROOT_DIR / "V2" / "assets" / "xiaohongshu_publish_note_marker.png"
DEFAULT_LOGIN_MATCH_THRESHOLD = 0.9
DEFAULT_PUBLISH_MENU_HOVER_X = 105
DEFAULT_PUBLISH_MENU_HOVER_Y = 222
DEFAULT_PUBLISH_GRAPHIC_CLICK_X = 94
DEFAULT_PUBLISH_GRAPHIC_CLICK_Y = 318
DEFAULT_AFTER_GRAPHIC_MENU_WAIT_MS = 5_000
DEFAULT_AFTER_UPLOAD_WAIT_MS = 8_000
DEFAULT_AFTER_ORIGINAL_DIALOG_WAIT_MS = 3_000
DEFAULT_AFTER_CHECKBOX_WAIT_MS = 2_000
DEFAULT_AFTER_DECLARE_ORIGINAL_WAIT_MS = 2_000
DEFAULT_AFTER_LOCATION_INPUT_WAIT_MS = 2_000
DEFAULT_AFTER_TOPIC_CONFIRM_WAIT_MS = 1_000
DEFAULT_BETWEEN_TOPICS_WAIT_MS = 2_000
DEFAULT_TOPIC_TYPING_DELAY_MS = 180
DEFAULT_PUBLISH_LOCATION = "成都市"
DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS = 2_500
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "xiaohongshu_publish_last_error.png"
DEFAULT_UPLOAD_STEP_SCREENSHOT = ROOT_DIR / "tools" / "xiaohongshu_publish_upload_step.png"


@dataclass(frozen=True)
class XiaohongshuPublishAssets:
    output_dir: Path
    final_dir: Path
    image_paths: tuple[Path, ...]
    title_text: str
    description_body: str
    topic_tags: tuple[str, ...]
    title_file: Path
    description_file: Path


@dataclass(frozen=True)
class XiaohongshuPublishSettings:
    output_dir: Path
    cdp_url: str
    url_keyword: str
    chrome_path: Path | None
    automation_profile_dir: Path
    auto_launch_browser: bool
    cdp_ready_timeout_ms: int
    typing_delay_ms: int
    topic_typing_delay_ms: int
    login_marker_path: Path
    login_match_threshold: float
    publish_menu_hover_x: int
    publish_menu_hover_y: int
    publish_graphic_click_x: int
    publish_graphic_click_y: int
    after_graphic_menu_wait_ms: int
    after_upload_wait_ms: int
    after_original_dialog_wait_ms: int
    after_checkbox_wait_ms: int
    after_declare_original_wait_ms: int
    after_location_input_wait_ms: int
    after_topic_confirm_wait_ms: int
    between_topics_wait_ms: int
    publish_location: str
    windows_open_dialog_wait_ms: int
    upload_step_screenshot: Path
    debug_screenshot: Path
    dry_run: bool


def upload_image_button_locators(page: Page) -> tuple:
    return (
        page.locator("button.upload-button"),
        page.locator("button.bg-red.upload-button"),
        page.get_by_role("button", name="上传图片"),
        page.get_by_text("上传图片", exact=True),
    )


def upload_file_input_locators(page: Page) -> tuple:
    return (
        page.locator("input.upload-input[type='file']"),
        page.locator("input[type='file'][multiple]"),
        page.locator("input[type='file'][accept*='jpg']"),
    )


def uploaded_image_preview_locators(page: Page) -> tuple:
    return (
        page.locator(".img-preview-item"),
        page.locator(".upload-wrapper .upload-item"),
        page.locator(".upload-list .upload-item"),
        page.locator("[class*='upload'] [class*='preview']"),
        page.locator(".crop-wrapper"),
    )


def title_input_locators(page: Page) -> tuple:
    return (
        page.get_by_placeholder("填写标题会有更多赞哦"),
        page.locator("input.d-text[type='text']").first,
        page.locator(".edit-container input[type='text']"),
    )


def note_editor_locators(page: Page) -> tuple:
    return (
        page.locator("div.tiptap.ProseMirror[contenteditable='true']"),
        page.locator(".editor-content .ProseMirror"),
        page.locator("[contenteditable='true'][role='textbox']"),
    )


def location_dropdown_first_option_locators(page: Page, location: str) -> tuple:
    return (
        page.locator(".d-popover:visible .d-option").first,
        page.locator(".d-popover:visible [class*='select-option']").first,
        page.locator(".d-popover:visible").get_by_text(location, exact=True).first,
        page.locator("[class*='dropdown']:visible").locator("div").filter(has_text=location).first,
        page.locator(".d-popover:visible .d-grid-item").first,
    )


def original_declaration_switch_locators(page: Page) -> tuple:
    return (
        page.locator(".custom-switch-wrapper").filter(has_text="原创声明").locator(".d-switch"),
        page.locator("div").filter(has_text="原创声明").locator(".d-switch").first,
        page.locator(".custom-switch-wrapper:has-text('原创声明') .d-switch"),
    )


def original_agreement_checkbox_locators(page: Page) -> tuple:
    return (
        page.locator(".footerLeft input[type='checkbox']"),
        page.locator("input[type='checkbox']").last,
        page.locator(".d-checkbox-simulator").last,
    )


def declare_original_button_locators(page: Page) -> tuple:
    return (
        page.locator("button.bg-red").filter(has_text="声明原创"),
        page.get_by_role("button", name="声明原创"),
        page.get_by_text("声明原创", exact=True),
    )


def location_select_locators(page: Page) -> tuple:
    return (
        page.locator(".address-card-select .d-select-placeholder").filter(has_text="添加地点"),
        page.locator(".d-select-placeholder").filter(has_text="添加地点"),
        page.get_by_text("添加地点", exact=True),
    )


def _page_priority(url: str) -> int:
    if "/new/home" in url:
        return 0
    if "publish" in url or "/new/note" in url:
        return 1
    if "note-manager" in url:
        return 4
    return 2


def find_xiaohongshu_page(browser, url_keyword: str) -> Page:
    return resolve_xiaohongshu_page(browser, url_keyword)


def resolve_xiaohongshu_page(browser, url_keyword: str) -> Page:
    page = resolve_page_by_keyword(
        browser,
        url_keyword=url_keyword,
        creator_home_url=DEFAULT_XIAOHONGSHU_HOME_URL,
        platform_label="小红书",
        page_priority=_page_priority,
    )
    if "note-manager" in page.url:
        print("提示：当前锁定的是笔记管理页，后续步骤会尝试进入图文发布编辑页。")
    return page


def save_step_screenshot(page: Page, screenshot_path: Path, label: str) -> None:
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"已保存{label}截图：{screenshot_path}")


def count_uploaded_image_previews(page: Page) -> int:
    max_count = 0
    for locator_group in uploaded_image_preview_locators(page):
        try:
            count = locator_group.count()
        except Exception:
            continue
        max_count = max(max_count, count)
    return max_count


def ensure_xiaohongshu_logged_in(page: Page, settings: XiaohongshuPublishSettings) -> None:
    if not settings.login_marker_path.exists():
        raise RuntimeError(f"登录识别模板不存在：{settings.login_marker_path}")

    while True:
        matched, score = match_template_on_page(
            page,
            settings.login_marker_path,
            threshold=settings.login_match_threshold,
        )
        if matched:
            print(f"已检测到小红书创作者登录态（匹配度 {score:.3f}）。")
            return

        print(
            "未在页面中匹配到「发布笔记」按钮（阈值 "
            f"{settings.login_match_threshold}）。请在小红书创作者中心完成登录。"
        )
        answer = input("登录完成后请输入 y 继续：").strip().lower()
        if answer != "y":
            print("请输入 y 继续。")
            continue

        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)


def open_graphic_publish_editor(page: Page, settings: XiaohongshuPublishSettings) -> None:
    page.bring_to_front()
    page.wait_for_timeout(500)
    pyautogui.moveTo(settings.publish_menu_hover_x, settings.publish_menu_hover_y, duration=0.4)
    time.sleep(0.8)
    pyautogui.moveTo(settings.publish_graphic_click_x, settings.publish_graphic_click_y, duration=0.3)
    pyautogui.click()
    print(
        f"已在 ({settings.publish_menu_hover_x}, {settings.publish_menu_hover_y}) 悬停，"
        f"并点击 ({settings.publish_graphic_click_x}, {settings.publish_graphic_click_y}) 发布图文。"
    )
    page.wait_for_timeout(settings.after_graphic_menu_wait_ms)


def _upload_via_dom_input(page: Page, image_paths: Sequence[Path]) -> bool:
    upload_input = find_optional_locator(
        page,
        upload_file_input_locators(page),
        state="attached",
        timeout_ms=5_000,
    )
    if upload_input is None:
        return False

    resolved_paths = [str(path.resolve()) for path in image_paths]
    upload_input.set_input_files(resolved_paths)
    print(f"已通过页面 file input 投喂 {len(resolved_paths)} 张图片。")
    for path in image_paths:
        print(f"  - {path.name}")
    return True


def _upload_via_file_chooser(page: Page, image_paths: Sequence[Path]) -> None:
    resolved_paths = [str(path.resolve()) for path in image_paths]
    with page.expect_file_chooser(timeout=30_000) as chooser_info:
        click_locator(page, upload_image_button_locators(page), description="上传图片按钮", timeout_ms=30_000)
    chooser_info.value.set_files(resolved_paths)
    print(f"已通过 file chooser 投喂 {len(resolved_paths)} 张图片。")
    for path in image_paths:
        print(f"  - {path.name}")


def _upload_via_windows_dialog(page: Page, image_paths: Sequence[Path], settings: XiaohongshuPublishSettings) -> None:
    click_locator(page, upload_image_button_locators(page), description="上传图片按钮", timeout_ms=30_000)
    page.wait_for_timeout(500)
    confirm_windows_open_dialog(image_paths, wait_ms=settings.windows_open_dialog_wait_ms)


def _upload_images_one_by_one(page: Page, image_paths: Sequence[Path], settings: XiaohongshuPublishSettings) -> None:
    for index, image_path in enumerate(image_paths, start=1):
        print(f"开始单张补传第 {index} 张：{image_path.name}")
        if _upload_via_dom_input(page, [image_path]):
            page.wait_for_timeout(2_500)
            continue
        click_locator(page, upload_image_button_locators(page), description="上传图片按钮", timeout_ms=30_000)
        page.wait_for_timeout(500)
        confirm_windows_open_dialog([image_path], wait_ms=settings.windows_open_dialog_wait_ms)
        page.wait_for_timeout(2_500)


def upload_note_images(page: Page, assets: XiaohongshuPublishAssets, settings: XiaohongshuPublishSettings) -> None:
    expected_count = len(assets.image_paths)
    print(f"准备上传 {expected_count} 张图片（顺序 01/02/03）。")

    uploaded = _upload_via_dom_input(page, assets.image_paths)
    if not uploaded:
        try:
            _upload_via_file_chooser(page, assets.image_paths)
        except Exception as exc:
            print(f"file chooser 上传失败，回退 Windows 对话框：{exc}")
            _upload_via_windows_dialog(page, assets.image_paths, settings)

    page.wait_for_timeout(settings.after_upload_wait_ms)
    save_step_screenshot(page, settings.upload_step_screenshot, "上传后")

    preview_count = count_uploaded_image_previews(page)
    print(f"上传后页面预览图数量（启发式）：{preview_count}，目标 {expected_count}。")

    if 0 < preview_count < expected_count:
        print("预览数量不足，尝试按单张方式补传。")
        _upload_images_one_by_one(page, assets.image_paths, settings)
        page.wait_for_timeout(settings.after_upload_wait_ms)
        save_step_screenshot(page, settings.upload_step_screenshot, "补传后")
        preview_count = count_uploaded_image_previews(page)
        print(f"补传后页面预览图数量（启发式）：{preview_count}。")
    elif preview_count == 0:
        print("未能从 DOM 统计预览数量，请人工对照上传截图确认是否为 3 张。")

    if 0 < preview_count < expected_count:
        raise RuntimeError(
            f"小红书图片上传后仅识别到约 {preview_count} 张预览，期望 {expected_count} 张。"
            f"请查看截图：{settings.upload_step_screenshot}"
        )

    print(f"小红书笔记图片上传完成，共 {expected_count} 张。")


def fill_note_title(page: Page, assets: XiaohongshuPublishAssets, settings: XiaohongshuPublishSettings) -> None:
    title_input = wait_for_locator(page, title_input_locators(page), description="笔记标题输入框", timeout_ms=30_000)
    title_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, assets.title_text, delay_ms=settings.typing_delay_ms)
    print("已输入小红书笔记标题。")


def confirm_topic_with_enter(page: Page, topic_tag: str, settings: XiaohongshuPublishSettings) -> None:
    page.wait_for_timeout(settings.after_topic_confirm_wait_ms)
    page.keyboard.press("Enter")
    print(f"已等待 {settings.after_topic_confirm_wait_ms}ms 后回车确认话题：{topic_tag}")


def fill_note_content(page: Page, assets: XiaohongshuPublishAssets, settings: XiaohongshuPublishSettings) -> None:
    editor = wait_for_locator(page, note_editor_locators(page), description="笔记正文输入框", timeout_ms=30_000)
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")

    type_text_humanly(page, assets.description_body, delay_ms=settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    print("已输入小红书正文，并空一行准备输入话题。")

    for topic_tag in assets.topic_tags:
        topic_text = topic_tag.lstrip("#")
        page.keyboard.insert_text("#")
        page.wait_for_timeout(settings.topic_typing_delay_ms)
        type_text_humanly(page, topic_text, delay_ms=settings.topic_typing_delay_ms)
        confirm_topic_with_enter(page, topic_tag, settings)
        page.wait_for_timeout(settings.between_topics_wait_ms)

    print(f"已输入小红书 {len(assets.topic_tags)} 个话题（每个等待后回车确认）。")


def submit_original_declaration(page: Page, settings: XiaohongshuPublishSettings) -> None:
    switch = wait_for_locator(page, original_declaration_switch_locators(page), description="原创声明开关", timeout_ms=30_000)
    switch.scroll_into_view_if_needed()
    switch.click()
    print("已点击：原创声明开关")
    page.wait_for_timeout(settings.after_original_dialog_wait_ms)

    checkbox = wait_for_locator(page, original_agreement_checkbox_locators(page), description="原创声明同意复选框", timeout_ms=15_000)
    checkbox.scroll_into_view_if_needed()
    checkbox.click()
    print("已勾选：原创声明须知")
    page.wait_for_timeout(settings.after_checkbox_wait_ms)

    click_locator_via_dom(page, declare_original_button_locators(page), description="声明原创按钮", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_declare_original_wait_ms)
    print("已完成原创声明。")


def select_publish_location(page: Page, settings: XiaohongshuPublishSettings) -> None:
    location_input = wait_for_locator(page, location_select_locators(page), description="添加地点", timeout_ms=30_000)
    location_input.scroll_into_view_if_needed()
    location_input.click()
    type_text_humanly(page, settings.publish_location, delay_ms=settings.typing_delay_ms)
    page.wait_for_timeout(settings.after_location_input_wait_ms)

    first_option = wait_for_locator(
        page,
        location_dropdown_first_option_locators(page, settings.publish_location),
        description="地点下拉首项",
        timeout_ms=10_000,
    )
    first_option.scroll_into_view_if_needed()
    first_option.click()
    print(f"已点击地点下拉首项：{settings.publish_location}")


def to_cdp_settings(settings: XiaohongshuPublishSettings) -> PublishSettings:
    return PublishSettings(
        output_dir=settings.output_dir,
        cdp_url=settings.cdp_url,
        url_keyword=settings.url_keyword,
        chrome_path=settings.chrome_path,
        automation_profile_dir=settings.automation_profile_dir,
        auto_launch_browser=settings.auto_launch_browser,
        creator_home_url=DEFAULT_XIAOHONGSHU_HOME_URL,
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


def run_xiaohongshu_publish(settings: XiaohongshuPublishSettings, assets: XiaohongshuPublishAssets) -> None:
    if not settings.dry_run:
        ensure_cdp_browser_available(to_cdp_settings(settings))

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        page = resolve_xiaohongshu_page(browser, settings.url_keyword)

        try:
            ensure_xiaohongshu_logged_in(page, settings)
            open_graphic_publish_editor(page, settings)
            upload_note_images(page, assets, settings)
            fill_note_title(page, assets, settings)
            fill_note_content(page, assets, settings)
            submit_original_declaration(page, settings)
            select_publish_location(page, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise
