from __future__ import annotations

import sys
import re
import time
from dataclasses import dataclass
from datetime import datetime
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
    DEFAULT_CDP_READY_TIMEOUT_MS,
    DEFAULT_CDP_URL,
    DEFAULT_TYPING_DELAY_MS,
    PublishSettings,
    click_locator,
    connect_cdp_browser,
    connect_cdp_browser_resilient,
    ensure_cdp_browser_available,
    find_optional_locator,
    normalize_schedule_at,
    resolve_page_by_keyword,
    type_text_humanly,
    wait_for_locator,
    wait_for_manual_login_continue,
)
from tools.weixin_mp_publish import confirm_windows_open_dialog, paste_text_to_clipboard  # noqa: E402


DEFAULT_URL_KEYWORD = "cp.kuaishou.com"
DEFAULT_KUAISHOU_HOME_URL = "https://cp.kuaishou.com/"
DEFAULT_KUAISHOU_GRAPHIC_PUBLISH_URL = "https://cp.kuaishou.com/article/publish/video"
DEFAULT_AFTER_PUBLISH_WORK_WAIT_MS = 15_000
DEFAULT_AFTER_GRAPHIC_TAB_WAIT_MS = 5_000
DEFAULT_AFTER_UPLOAD_BUTTON_WAIT_MS = 3_000
DEFAULT_AFTER_MAIN_UPLOAD_WAIT_MS = 5_000
DEFAULT_AFTER_OPEN_COVER_WAIT_MS = 3_000
DEFAULT_AFTER_COVER_UPLOAD_WAIT_MS = 3_000
DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS = 2_000
DEFAULT_AFTER_AUTHOR_SELECT_WAIT_MS = 1_500
DEFAULT_AFTER_LOCATION_OPEN_WAIT_MS = 1_500
DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS = 1_000
DEFAULT_AFTER_TOPIC_CONFIRM_WAIT_MS = DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS  # 兼容旧名
DEFAULT_BETWEEN_TOPICS_WAIT_MS = 2_000
DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS = 3_500
DEFAULT_PUBLISH_LOCATION = "成都市"
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "kuaishou_publish_last_error.png"
DEFAULT_UPLOAD_STEP_SCREENSHOT = ROOT_DIR / "tools" / "kuaishou_publish_upload_step.png"
EXPECTED_TOPIC_COUNT = 4
KUAISHOU_MUSIC_SEARCH_KEYWORD = "好美味"
KUAISHOU_MUSIC_TITLE = "好美味(美食BGM)"


@dataclass(frozen=True)
class KuaishouPublishAssets:
    output_dir: Path
    final_dir: Path
    image_paths: tuple[Path, ...]
    cover_path: Path
    title_text: str
    description_body: str
    topic_tags: tuple[str, ...]
    title_file: Path
    description_file: Path


@dataclass(frozen=True)
class KuaishouPublishSettings:
    output_dir: Path
    cdp_url: str
    url_keyword: str
    chrome_path: Path | None
    automation_profile_dir: Path
    auto_launch_browser: bool
    cdp_ready_timeout_ms: int
    typing_delay_ms: int
    after_publish_work_wait_ms: int
    after_graphic_tab_wait_ms: int
    after_upload_button_wait_ms: int
    after_main_upload_wait_ms: int
    after_open_cover_wait_ms: int
    after_cover_upload_wait_ms: int
    after_cover_confirm_wait_ms: int
    after_author_select_wait_ms: int
    after_location_open_wait_ms: int
    after_topic_paste_wait_ms: int
    between_topics_wait_ms: int
    publish_location: str
    windows_open_dialog_wait_ms: int
    upload_step_screenshot: Path
    debug_screenshot: Path
    schedule_at: str | None
    dry_run: bool


def publish_work_button_locators(page: Page) -> tuple:
    return (
        page.locator("div.publish-button"),
        page.locator(".publish-button"),
        page.get_by_text("发布作品", exact=True),
    )


def graphic_tab_locators(page: Page) -> tuple:
    return (
        page.locator("#rc-tabs-0-tab-2"),
        page.get_by_role("tab", name="上传图文"),
        page.locator(".ant-tabs-tab-btn").filter(has_text="上传图文"),
    )


def graphic_tab_panel_locators(page: Page) -> tuple:
    return (
        page.locator("#rc-tabs-0-panel-2.ant-tabs-tabpane-active"),
        page.locator("#rc-tabs-0-panel-2"),
        page.locator(".ant-tabs-tabpane-active").filter(has=page.locator("button").filter(has_text="上传图片")),
    )


def draft_recovery_banner_locators(page: Page) -> tuple:
    return (
        page.get_by_text("还有上次未发布的图集", exact=False),
        page.get_by_text("是否继续编辑", exact=False),
    )


def discard_draft_button_locators(page: Page) -> tuple:
    return (
        page.get_by_role("button", name="放弃"),
        page.get_by_text("放弃", exact=True),
    )


def continue_edit_draft_button_locators(page: Page) -> tuple:
    return (
        page.get_by_role("button", name="继续编辑"),
        page.get_by_text("继续编辑", exact=True),
    )


def main_upload_button_locators(page: Page) -> tuple:
    return (
        page.locator("button._upload-btn_ysbff_57"),
        page.locator("section._upload-container button").filter(has_text="上传图片"),
        page.get_by_role("button", name="上传图片"),
        page.locator("button").filter(has_text="上传图片").first,
    )


def main_upload_file_input_locators(page: Page) -> tuple:
    return (
        page.locator("section._upload-container input[type='file'][multiple]"),
        page.locator("input[type='file'][multiple][accept*='jpg']"),
    )


def description_editor_locators(page: Page) -> tuple:
    return (
        page.locator("#work-description-edit"),
        page.locator("[contenteditable='true']#work-description-edit"),
        page.locator("div._description_eho7l_59[contenteditable='true']"),
    )


def edit_cover_button_locators(page: Page) -> tuple:
    return (
        page.locator("div._button_3a31q_1").filter(has_text="编辑封面"),
        page.get_by_text("编辑封面", exact=True),
    )


def upload_cover_entry_locators(page: Page) -> tuple:
    return (
        page.locator(".ant-modal").get_by_text("上传封面", exact=True),
        page.locator("div._header-title-item_2t3fe_27").filter(has_text="上传封面"),
        page.get_by_text("上传封面", exact=True),
    )


def cover_modal_upload_button_locators(page: Page) -> tuple:
    return (
        page.locator(".ant-modal button._upload-btn_d1qhn_85"),
        page.locator(".ant-modal button").filter(has_text="上传图片"),
        page.locator(".ant-modal").get_by_role("button", name="上传图片"),
    )


def cover_modal_file_input_locators(page: Page) -> tuple:
    return (
        page.locator(".ant-modal input[type='file']"),
        page.locator("div._cropper-upload-upload input[type='file']"),
    )


def cover_finish_button_locators(page: Page) -> tuple:
    return (
        page.locator(".ant-modal button").filter(has_text="完成"),
        page.get_by_role("button", name="完成"),
    )


def author_statement_select_locators(page: Page) -> tuple:
    return (
        page.locator("#rc_select_0"),
        page.locator("input#rc_select_0"),
        page.locator(".ant-select").filter(has=page.locator("#rc_select_0")).first,
        page.get_by_text("作者声明", exact=False).locator("..").locator(".ant-select").first,
    )


def author_statement_option_locators(page: Page) -> tuple:
    return (
        page.locator("#rc_select_0_list_2"),
        page.locator("#rc_select_0_list").get_by_text("个人观点，仅供参考", exact=True),
        page.get_by_text("个人观点，仅供参考", exact=True),
        page.get_by_text("个人观点", exact=False),
    )


def location_select_locators(page: Page) -> tuple:
    return (
        page.locator("#rc_select_1"),
        page.locator("input#rc_select_1"),
        page.locator("label").filter(has_text="添加地点").locator("..").locator(".ant-select").first,
        page.get_by_text("请选择所在地区", exact=False),
    )


def location_option_locators(page: Page, location: str) -> tuple:
    return (
        page.locator("#rc_select_1_list_0"),
        page.locator("#rc_select_1_list").get_by_text(location, exact=True).first,
        page.locator(".ant-cascader-menu").get_by_text(location, exact=True).first,
        page.locator(".ant-select-item-option-content").filter(has_text=location).first,
        page.get_by_text(location, exact=True),
    )


def _kuaishou_page_priority(url: str) -> int:
    if "/article/publish" in url or ("/publish" in url and "/manage/" not in url):
        return 0
    if "cp.kuaishou.com" in url:
        return 1
    return 2


def find_kuaishou_page(browser, url_keyword: str) -> Page:
    return resolve_kuaishou_page(browser, url_keyword)


def resolve_kuaishou_page(browser, url_keyword: str) -> Page:
    return resolve_page_by_keyword(
        browser,
        url_keyword=url_keyword,
        creator_home_url=DEFAULT_KUAISHOU_HOME_URL,
        platform_label="快手",
        page_priority=_kuaishou_page_priority,
    )


def is_kuaishou_login_required(page: Page) -> bool:
    if find_optional_locator(page, publish_work_button_locators(page), timeout_ms=2_000) is not None:
        return False
    login_keywords = ("扫码登录", "密码登录", "立即登录", "登录/注册", "手机号登录")
    for keyword in login_keywords:
        try:
            if page.get_by_text(keyword, exact=False).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def ensure_kuaishou_logged_in(page: Page) -> None:
    while is_kuaishou_login_required(page):
        wait_for_manual_login_continue(platform_label="快手创作者中心")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
    print("快手创作者中心登录态已就绪。")


def close_stray_file_dialog(page: Page) -> None:
    page.bring_to_front()
    page.wait_for_timeout(200)
    pyautogui.press("escape")
    page.wait_for_timeout(300)


def save_step_screenshot(page: Page, screenshot_path: Path, label: str) -> None:
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"已保存{label}截图：{screenshot_path}")


def _upload_via_dom_input(page: Page, locators: tuple, image_paths: Sequence[Path], *, label: str) -> bool:
    upload_input = find_optional_locator(page, locators, state="attached", timeout_ms=5_000)
    if upload_input is None:
        return False

    resolved_paths = [str(path.resolve()) for path in image_paths]
    upload_input.set_input_files(resolved_paths)
    print(f"{label}已通过 file input 投喂 {len(resolved_paths)} 张：")
    for path in image_paths:
        print(f"  - {path.name}")
    return True


def _upload_via_file_chooser(page: Page, button_locators: tuple, image_paths: Sequence[Path], *, label: str) -> None:
    resolved_paths = [str(path.resolve()) for path in image_paths]
    with page.expect_file_chooser(timeout=30_000) as chooser_info:
        click_locator(page, button_locators, description=f"{label}上传按钮", timeout_ms=30_000)
    chooser_info.value.set_files(resolved_paths)
    print(f"{label}已通过 file chooser 投喂 {len(resolved_paths)} 张。")


def _upload_via_windows_dialog(page: Page, button_locators: tuple, image_paths: Sequence[Path], settings: KuaishouPublishSettings, *, label: str) -> None:
    click_locator(page, button_locators, description=f"{label}上传按钮", timeout_ms=30_000)
    page.bring_to_front()
    page.wait_for_timeout(settings.after_upload_button_wait_ms)
    confirm_windows_open_dialog(
        image_paths,
        wait_ms=settings.windows_open_dialog_wait_ms,
        focus_page=page,
    )


def upload_files_with_fallback(
    page: Page,
    *,
    file_input_locators: tuple,
    button_locators: tuple,
    image_paths: Sequence[Path],
    settings: KuaishouPublishSettings,
    label: str,
) -> None:
    if _upload_via_dom_input(page, file_input_locators, image_paths, label=label):
        return

    try:
        _upload_via_file_chooser(page, button_locators, image_paths, label=label)
    except Exception as exc:
        print(f"{label} file chooser 失败，回退 Windows 对话框：{exc}")
        _upload_via_windows_dialog(page, button_locators, image_paths, settings, label=label)


def dismiss_draft_recovery_if_present(page: Page) -> None:
    banner = find_optional_locator(page, draft_recovery_banner_locators(page), timeout_ms=2_000)
    if banner is None:
        return

    discard_button = find_optional_locator(page, discard_draft_button_locators(page), timeout_ms=2_000)
    if discard_button is not None:
        discard_button.click()
        print("已点击：放弃未发布图集草稿")
        page.wait_for_timeout(1_500)
        return

    print("检测到未发布图集提示，但未找到「放弃」按钮，尝试继续。")


def wait_for_graphic_upload_panel(page: Page, *, timeout_ms: int = 30_000) -> None:
    wait_for_locator(page, main_upload_button_locators(page), description="上传图文面板-上传图片按钮", timeout_ms=timeout_ms)
    print("已确认：上传图文面板已打开（可见「上传图片」按钮）。")


def activate_graphic_tab(page: Page, settings: KuaishouPublishSettings) -> None:
    tab = wait_for_locator(page, graphic_tab_locators(page), description="上传图文标签", timeout_ms=30_000)
    tab.scroll_into_view_if_needed()
    selected = tab.get_attribute("aria-selected")
    if selected != "true":
        tab.click(force=True)
        page.wait_for_timeout(800)
        tab.click(force=True)
        print("已点击：上传图文（切换到图文发布）")
    else:
        tab.click(force=True)
        print("已点击：上传图文（已选中，再次确认）")
    page.wait_for_timeout(settings.after_graphic_tab_wait_ms)

    dismiss_draft_recovery_if_present(page)
    wait_for_graphic_upload_panel(page)


def post_upload_advance_locators(page: Page) -> tuple:
    return (
        *continue_edit_draft_button_locators(page),
        page.get_by_role("button", name="下一步"),
        page.get_by_role("button", name="继续"),
        page.get_by_text("下一步", exact=True),
        page.get_by_text("继续编辑", exact=True),
    )


def ensure_graphic_editor_ready(page: Page, settings: KuaishouPublishSettings) -> None:
    deadline_ms = 90_000
    attempts = max(1, deadline_ms // 3_000)

    for attempt in range(attempts):
        editor = find_optional_locator(page, description_editor_locators(page), timeout_ms=1_500)
        if editor is not None:
            print("已进入快手图文编辑页。")
            return

        continue_button = find_optional_locator(page, continue_edit_draft_button_locators(page), timeout_ms=1_500)
        if continue_button is not None:
            continue_button.click()
            print("已点击：继续编辑（进入图文编辑页）")
            page.wait_for_timeout(2_000)
            continue

        for locator_group in post_upload_advance_locators(page):
            try:
                button = locator_group.first
                if button.is_visible(timeout=500):
                    button.click()
                    print("已点击：上传后进入编辑的按钮")
                    page.wait_for_timeout(2_000)
                    break
            except Exception:
                continue

        if attempt % 4 == 3:
            dismiss_draft_recovery_if_present(page)

        page.wait_for_timeout(3_000)

    wait_for_locator(page, description_editor_locators(page), description="作品描述输入框", timeout_ms=15_000)
    print("作品描述输入框已就绪。")


def recover_kuaishou_app_load_failure(page: Page, *, max_retries: int = 3) -> None:
    for attempt in range(1, max_retries + 1):
        load_failed = find_optional_locator(
            page,
            (
                page.get_by_text("应用加载失败", exact=False),
                page.get_by_text("请刷新重试", exact=False),
            ),
            timeout_ms=1_500,
        )
        if load_failed is None:
            return
        print(f"检测到快手「应用加载失败」，刷新重试（{attempt}/{max_retries}）…")
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3_000)
    raise RuntimeError("快手发布页反复出现「应用加载失败」，请手动刷新浏览器后重试。")


def open_graphic_publish_flow(page: Page, settings: KuaishouPublishSettings) -> None:
    if "/article/publish" not in page.url:
        try:
            page.goto(DEFAULT_KUAISHOU_GRAPHIC_PUBLISH_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            page.goto(DEFAULT_KUAISHOU_GRAPHIC_PUBLISH_URL, wait_until="commit", timeout=60_000)
        page.wait_for_timeout(2_000)
        print(f"已直达快手图文发布页：{page.url}")
    recover_kuaishou_app_load_failure(page)

    upload_panel_ready = find_optional_locator(page, main_upload_button_locators(page), timeout_ms=3_000)
    editor_ready = find_optional_locator(page, description_editor_locators(page), timeout_ms=1_500)
    if upload_panel_ready is None and editor_ready is None:
        click_locator(page, publish_work_button_locators(page), description="发布作品", timeout_ms=30_000)
        page.wait_for_timeout(settings.after_publish_work_wait_ms)
        recover_kuaishou_app_load_failure(page)
        upload_panel_ready = find_optional_locator(page, main_upload_button_locators(page), timeout_ms=5_000)
        editor_ready = find_optional_locator(page, description_editor_locators(page), timeout_ms=2_000)
        if upload_panel_ready is None and editor_ready is None:
            print("发布作品后未立即出现上传区，等待页面加载…")
            page.wait_for_timeout(3_000)

    if editor_ready is not None:
        print("当前已在快手图文编辑页，跳过上传图文标签切换。")
        return

    activate_graphic_tab(page, settings)


def upload_main_images(page: Page, assets: KuaishouPublishAssets, settings: KuaishouPublishSettings) -> None:
    print(f"准备上传 {len(assets.image_paths)} 张图文（01/02/03）。")
    page.bring_to_front()
    dismiss_draft_recovery_if_present(page)
    wait_for_graphic_upload_panel(page)

    upload_button = wait_for_locator(page, main_upload_button_locators(page), description="上传图片按钮", timeout_ms=30_000)
    upload_button.scroll_into_view_if_needed()

    resolved_paths = [str(path.resolve()) for path in assets.image_paths]
    uploaded = False
    try:
        with page.expect_file_chooser(timeout=12_000) as chooser_info:
            upload_button.click()
            print("已点击：上传图片（等待 file chooser）")
        chooser_info.value.set_files(resolved_paths)
        uploaded = True
        print(f"图文已通过 file chooser 上传 {len(resolved_paths)} 张。")
        for path in assets.image_paths:
            print(f"  - {path.name}")
    except Exception as exc:
        print(f"file chooser 不可用，改用 Windows 对话框：{exc}")
        upload_button.click()
        print("已点击：上传图片（打开 Windows 对话框）")
        page.wait_for_timeout(settings.after_upload_button_wait_ms)
        confirm_windows_open_dialog(
            assets.image_paths,
            wait_ms=settings.windows_open_dialog_wait_ms,
            focus_page=page,
        )
        uploaded = True

    if not uploaded:
        raise RuntimeError("快手图文上传失败：未能通过 file chooser 或 Windows 对话框完成上传。")

    close_stray_file_dialog(page)
    page.wait_for_timeout(settings.after_main_upload_wait_ms)
    save_step_screenshot(page, settings.upload_step_screenshot, "图文上传后")
    ensure_graphic_editor_ready(page, settings)
    print(f"快手图文上传完成，共 {len(assets.image_paths)} 张。")


def _normalize_topic_tag(topic_tag: str) -> str:
    text = str(topic_tag or "").strip()
    if not text:
        raise ValueError("话题标签不能为空。")
    return text if text.startswith("#") else f"#{text}"


def paste_topic_tags_via_clipboard(
    page: Page,
    editor: Locator,
    topic_tags: Sequence[str],
    *,
    after_paste_wait_ms: int,
    between_topics_wait_ms: int,
    platform_label: str = "快手",
    cursor_at_end: bool = False,
) -> None:
    """逐个复制话题到剪贴板后 Ctrl+V 粘贴；第 2 个起先按空格再粘贴。"""
    if not topic_tags:
        return

    page.bring_to_front()
    editor.click()
    page.wait_for_timeout(200)
    if cursor_at_end:
        page.keyboard.press("Control+End")
        page.wait_for_timeout(200)

    for index, topic_tag in enumerate(topic_tags):
        tag = _normalize_topic_tag(topic_tag)
        paste_text_to_clipboard(tag)
        time.sleep(0.15)
        if index > 0:
            pyautogui.press("space")
            time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        page.wait_for_timeout(max(0, after_paste_wait_ms))
        print(f"已粘贴{platform_label}话题（{'首项' if index == 0 else '空格后'}）：{tag}")
        if index < len(topic_tags) - 1:
            page.wait_for_timeout(max(0, between_topics_wait_ms))


def fill_work_description(page: Page, assets: KuaishouPublishAssets, settings: KuaishouPublishSettings) -> None:
    editor = wait_for_locator(page, description_editor_locators(page), description="作品描述输入框", timeout_ms=30_000)
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")

    type_text_humanly(page, assets.title_text, delay_ms=settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    print(f"已输入快手作品描述标题：{assets.title_text}")

    type_text_humanly(page, assets.description_body, delay_ms=settings.typing_delay_ms)
    for _ in range(4):
        page.keyboard.press("Enter")
        page.wait_for_timeout(settings.typing_delay_ms)
    print(f"已输入快手作品描述正文（第 1 行），长度 {len(assets.description_body)}。")

    editor.click()
    page.wait_for_timeout(200)
    paste_topic_tags_via_clipboard(
        page,
        editor,
        assets.topic_tags,
        after_paste_wait_ms=settings.after_topic_paste_wait_ms,
        between_topics_wait_ms=settings.between_topics_wait_ms,
    )
    print(f"已用剪贴板粘贴快手 {len(assets.topic_tags)} 个话题（Ctrl+V，第 2 个起先空格）。")


def upload_cover_image(page: Page, assets: KuaishouPublishAssets, settings: KuaishouPublishSettings) -> None:
    click_locator(page, edit_cover_button_locators(page), description="编辑封面", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_open_cover_wait_ms)

    upload_cover_entry = find_optional_locator(page, upload_cover_entry_locators(page), timeout_ms=5_000)
    if upload_cover_entry is not None:
        upload_cover_entry.click()
        print("已点击：上传封面")
        page.wait_for_timeout(500)

    page.bring_to_front()
    if _upload_via_dom_input(page, cover_modal_file_input_locators(page), [assets.cover_path], label="封面"):
        page.wait_for_timeout(settings.after_cover_upload_wait_ms)
    else:
        cover_button = wait_for_locator(page, cover_modal_upload_button_locators(page), description="封面上传图片", timeout_ms=30_000)
        try:
            with page.expect_file_chooser(timeout=10_000) as chooser_info:
                cover_button.click()
            chooser_info.value.set_files([str(assets.cover_path.resolve())])
            print(f"封面已通过 file chooser 上传：{assets.cover_path.name}")
        except Exception as exc:
            print(f"封面上传 file chooser 失败，改用 Windows 对话框：{exc}")
            _upload_via_windows_dialog(
                page,
                cover_modal_upload_button_locators(page),
                [assets.cover_path],
                settings,
                label="封面",
            )
        page.wait_for_timeout(settings.after_cover_upload_wait_ms)

    click_locator(page, cover_finish_button_locators(page), description="完成", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_cover_confirm_wait_ms)
    print(f"封面已上传并确认：{assets.cover_path.name}")


def select_author_statement(page: Page, settings: KuaishouPublishSettings) -> None:
    select_box = wait_for_locator(page, author_statement_select_locators(page), description="作者声明", timeout_ms=30_000)
    select_box.scroll_into_view_if_needed()
    select_box.click(force=True)
    page.wait_for_timeout(settings.after_author_select_wait_ms)

    option = wait_for_locator(
        page,
        author_statement_option_locators(page),
        description="个人观点",
        timeout_ms=10_000,
    )
    option.click()
    page.wait_for_timeout(settings.after_author_select_wait_ms)
    print("已选择作者声明：个人观点，仅供参考")


def select_publish_location(page: Page, settings: KuaishouPublishSettings) -> None:
    location_select = wait_for_locator(page, location_select_locators(page), description="所在地区", timeout_ms=30_000)
    location_select.scroll_into_view_if_needed()
    location_select.click(force=True)
    page.wait_for_timeout(settings.after_location_open_wait_ms)

    location_option = wait_for_locator(
        page,
        location_option_locators(page, settings.publish_location),
        description=f"地区选项 {settings.publish_location}",
        timeout_ms=10_000,
    )
    location_option.click()
    print(f"已选择所在地区：{settings.publish_location}")


def scheduled_publish_label_locators(page: Page) -> tuple:
    return (
        page.locator("label").filter(has_text="定时发布"),
        page.get_by_text("定时发布", exact=True).locator("xpath=ancestor-or-self::label[1]"),
    )


def public_visibility_label_locators(page: Page) -> tuple:
    return (
        page.locator("label").filter(has_text="所有人可见"),
        page.get_by_text("所有人可见", exact=True).locator("xpath=ancestor-or-self::label[1]"),
        page.locator("label").filter(has_text="公开"),
    )


def music_add_button_locators(page: Page) -> tuple:
    return (
        page.get_by_text("添加音乐", exact=True),
        page.locator("button").filter(has_text="添加音乐"),
        page.get_by_text("+ 添加音乐", exact=False),
    )


def music_search_input_locators(page: Page) -> tuple:
    return (
        page.get_by_placeholder("搜索音乐"),
        page.locator("input[placeholder*='搜索']"),
        page.locator("input[type='search']"),
    )


def ensure_kuaishou_public_visibility(page: Page) -> None:
    for locators in (public_visibility_label_locators(page),):
        option = find_optional_locator(page, locators, timeout_ms=5_000)
        if option is None:
            continue
        radio = option.locator('input[type="radio"]').first
        if radio.count() and radio.is_checked():
            print("快手查看权限已是「所有人可见」。")
            return
        option.scroll_into_view_if_needed()
        option.click(force=True)
        page.wait_for_timeout(400)
        print("已选择快手查看权限：所有人可见")
        return
    print("未找到「所有人可见」选项，跳过权限设置。")


def select_kuaishou_music(page: Page) -> None:
    add_button = find_optional_locator(page, music_add_button_locators(page), timeout_ms=8_000)
    if add_button is None:
        print("未找到「添加音乐」入口，跳过音乐设置。")
        return
    add_button.scroll_into_view_if_needed()
    add_button.click()
    page.wait_for_timeout(1_200)
    search_input = wait_for_locator(page, music_search_input_locators(page), description="快手音乐搜索框", timeout_ms=15_000)
    search_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, KUAISHOU_MUSIC_SEARCH_KEYWORD, delay_ms=80)
    page.wait_for_timeout(2_000)
    song_item = find_optional_locator(
        page,
        (
            page.get_by_text(KUAISHOU_MUSIC_TITLE, exact=False).first,
            page.locator("div").filter(has_text=KUAISHOU_MUSIC_TITLE).first,
            page.locator("div").filter(has_text=KUAISHOU_MUSIC_SEARCH_KEYWORD).filter(has_text="BGM").first,
            page.locator("div").filter(has_text=KUAISHOU_MUSIC_SEARCH_KEYWORD).filter(has_text="释先生").first,
            page.locator("[class*='music']").filter(has_text=KUAISHOU_MUSIC_SEARCH_KEYWORD).first,
        ),
        timeout_ms=15_000,
    )
    if song_item is None:
        raise RuntimeError(f"未找到快手音乐：{KUAISHOU_MUSIC_TITLE}")
    song_item.scroll_into_view_if_needed()
    song_item.click()
    page.wait_for_timeout(800)
    confirm = find_optional_locator(
        page,
        (
            page.get_by_role("button", name="确定"),
            page.get_by_role("button", name="使用"),
            page.locator("button").filter(has_text="确定"),
        ),
        timeout_ms=5_000,
    )
    if confirm is not None:
        confirm.click()
        page.wait_for_timeout(400)
    print(f"已选择快手音乐：{KUAISHOU_MUSIC_TITLE}")


def scheduled_datetime_input_locators(page: Page) -> tuple:
    return (
        page.locator('input[placeholder="选择日期时间"]'),
        page.get_by_placeholder("选择日期时间"),
    )


def ant_picker_dropdown_locator(page: Page) -> Locator:
    return page.locator(".ant-picker-dropdown:not(.ant-picker-dropdown-hidden)").first


def _parse_kuaishou_schedule_parts(schedule_at: str) -> tuple[int, int, int, int, int]:
    parsed = datetime.strptime(schedule_at, "%Y-%m-%d %H:%M")
    return parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute


def _ant_picker_header_year_month(page: Page) -> tuple[int, int] | None:
    dropdown = ant_picker_dropdown_locator(page)
    if not dropdown.count():
        return None
    header_text = dropdown.locator(".ant-picker-header-view").first.inner_text()
    match = re.search(r"(\d{4}).*?(\d{1,2})", header_text.replace("年", "-").replace("月", ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _click_ant_picker_month_nav(page: Page, *, forward: bool) -> None:
    dropdown = ant_picker_dropdown_locator(page)
    button = dropdown.locator(".ant-picker-header-next-btn" if forward else ".ant-picker-header-prev-btn").first
    if button.count():
        button.click()
        page.wait_for_timeout(250)


def _select_ant_picker_day(page: Page, year: int, month: int, day: int) -> None:
    dropdown = ant_picker_dropdown_locator(page)
    for _ in range(24):
        header = _ant_picker_header_year_month(page)
        if header == (year, month):
            break
        if header is None:
            break
        current_year, current_month = header
        _click_ant_picker_month_nav(page, forward=(year, month) > (current_year, current_month))

    day_cells = dropdown.locator(".ant-picker-cell:not(.ant-picker-cell-disabled) .ant-picker-cell-inner")
    for index in range(day_cells.count()):
        cell = day_cells.nth(index)
        if cell.inner_text().strip() == str(day):
            cell.click()
            page.wait_for_timeout(250)
            return
    raise RuntimeError(f"快手日期选择器里未找到可用日期：{year}-{month:02d}-{day:02d}")


def _select_ant_picker_time_column(page: Page, column_index: int, target_text: str, *, description: str) -> None:
    dropdown = ant_picker_dropdown_locator(page)
    column = dropdown.locator(".ant-picker-time-panel-column").nth(column_index)
    column.wait_for(state="visible", timeout=10_000)
    items = column.locator("li")
    item_count = items.count()
    if item_count == 0:
        raise RuntimeError(f"未找到快手 {description} 选项。")

    target_index = None
    padded = str(target_text).zfill(2)
    for index in range(item_count):
        item_text = items.nth(index).inner_text().strip()
        if item_text in {padded, str(target_text)}:
            target_index = index
            break
    if target_index is None:
        raise RuntimeError(f"未找到快手 {description} 选项：{target_text}")

    items.nth(target_index).scroll_into_view_if_needed()
    items.nth(target_index).click()
    page.wait_for_timeout(200)


def confirm_ant_picker(page: Page) -> None:
    dropdown = ant_picker_dropdown_locator(page)
    confirm_button = dropdown.locator(".ant-picker-footer button.ant-btn-primary").first
    confirm_button.wait_for(state="visible", timeout=10_000)
    confirm_button.click(force=True)
    page.wait_for_timeout(400)
    if ant_picker_dropdown_locator(page).count():
        datetime_input = find_optional_locator(page, scheduled_datetime_input_locators(page), timeout_ms=5_000)
        if datetime_input is not None:
            datetime_input.evaluate("element => element.blur()")
            page.wait_for_timeout(300)


def set_kuaishou_scheduled_publish_time(page: Page, schedule_at: str, settings: KuaishouPublishSettings) -> None:
    year, month, day, hour, minute = _parse_kuaishou_schedule_parts(schedule_at)
    click_locator(page, scheduled_publish_label_locators(page), description="快手定时发布选项", timeout_ms=30_000)
    datetime_input = wait_for_locator(
        page,
        scheduled_datetime_input_locators(page),
        description="快手定时发布日期时间输入框",
        timeout_ms=15_000,
    )
    datetime_input.click()
    page.wait_for_timeout(400)
    wait_for_locator(page, (ant_picker_dropdown_locator(page),), description="快手日期时间选择弹层", timeout_ms=10_000)
    _select_ant_picker_day(page, year, month, day)
    _select_ant_picker_time_column(page, 0, str(hour), description="定时发布小时")
    _select_ant_picker_time_column(page, 1, str(minute), description="定时发布分钟")
    _select_ant_picker_time_column(page, 2, "00", description="定时发布秒")
    confirm_ant_picker(page)

    expected_prefix = f"{schedule_at}:00"
    current_value = datetime_input.input_value().strip()
    if not current_value.startswith(schedule_at):
        raise RuntimeError(f"快手定时发布时间未生效，期望 {expected_prefix}，当前 {current_value or '空'}。")
    if ant_picker_dropdown_locator(page).count():
        raise RuntimeError("快手定时发布弹层仍未关闭，请检查确定按钮。")

    scheduled_mode = page.locator("label").filter(has_text="定时发布").locator('input[type="radio"]').first
    if scheduled_mode.count() and not scheduled_mode.is_checked():
        raise RuntimeError("快手定时发布模式未保持选中状态。")

    print(f"已设置快手定时发布时间：{current_value}")


def to_cdp_settings(settings: KuaishouPublishSettings) -> PublishSettings:
    return PublishSettings(
        output_dir=settings.output_dir,
        cdp_url=settings.cdp_url,
        url_keyword=settings.url_keyword,
        chrome_path=settings.chrome_path,
        automation_profile_dir=settings.automation_profile_dir,
        auto_launch_browser=settings.auto_launch_browser,
        creator_home_url=DEFAULT_KUAISHOU_HOME_URL,
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


def run_kuaishou_publish(settings: KuaishouPublishSettings, assets: KuaishouPublishAssets) -> None:
    if not settings.dry_run:
        ensure_cdp_browser_available(to_cdp_settings(settings))

    with sync_playwright() as playwright:
        browser = connect_cdp_browser_resilient(playwright, to_cdp_settings(settings))
        page = resolve_kuaishou_page(browser, settings.url_keyword)

        try:
            ensure_kuaishou_logged_in(page)
            open_graphic_publish_flow(page, settings)
            upload_main_images(page, assets, settings)
            fill_work_description(page, assets, settings)
            upload_cover_image(page, assets, settings)
            select_author_statement(page, settings)
            select_publish_location(page, settings)
            try:
                select_kuaishou_music(page)
            except Exception as music_exc:
                print(f"快手音乐选择失败（继续其余步骤）：{music_exc}")
            ensure_kuaishou_public_visibility(page)
            if settings.schedule_at:
                set_kuaishou_scheduled_publish_time(page, settings.schedule_at, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise
