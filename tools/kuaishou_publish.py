from __future__ import annotations

import sys
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
    ensure_cdp_browser_available,
    find_optional_locator,
    type_text_humanly,
    wait_for_locator,
)
from tools.weixin_mp_publish import confirm_windows_open_dialog  # noqa: E402


DEFAULT_URL_KEYWORD = "cp.kuaishou.com"
DEFAULT_AFTER_PUBLISH_WORK_WAIT_MS = 15_000
DEFAULT_AFTER_GRAPHIC_TAB_WAIT_MS = 5_000
DEFAULT_AFTER_UPLOAD_BUTTON_WAIT_MS = 3_000
DEFAULT_AFTER_MAIN_UPLOAD_WAIT_MS = 5_000
DEFAULT_AFTER_OPEN_COVER_WAIT_MS = 3_000
DEFAULT_AFTER_COVER_UPLOAD_WAIT_MS = 3_000
DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS = 2_000
DEFAULT_AFTER_AUTHOR_SELECT_WAIT_MS = 1_500
DEFAULT_AFTER_LOCATION_OPEN_WAIT_MS = 1_500
DEFAULT_AFTER_TOPIC_CONFIRM_WAIT_MS = 1_000
DEFAULT_BETWEEN_TOPICS_WAIT_MS = 2_000
DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS = 3_500
DEFAULT_TOPIC_TYPING_DELAY_MS = 150
DEFAULT_PUBLISH_LOCATION = "成都市"
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "kuaishou_publish_last_error.png"
DEFAULT_UPLOAD_STEP_SCREENSHOT = ROOT_DIR / "tools" / "kuaishou_publish_upload_step.png"
EXPECTED_TOPIC_COUNT = 4


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
    topic_typing_delay_ms: int
    after_publish_work_wait_ms: int
    after_graphic_tab_wait_ms: int
    after_upload_button_wait_ms: int
    after_main_upload_wait_ms: int
    after_open_cover_wait_ms: int
    after_cover_upload_wait_ms: int
    after_cover_confirm_wait_ms: int
    after_author_select_wait_ms: int
    after_location_open_wait_ms: int
    after_topic_confirm_wait_ms: int
    between_topics_wait_ms: int
    publish_location: str
    windows_open_dialog_wait_ms: int
    upload_step_screenshot: Path
    debug_screenshot: Path
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
    matched_pages = [
        page
        for context in browser.contexts
        for page in context.pages
        if url_keyword in page.url
    ]
    if not matched_pages:
        raise RuntimeError(
            f"未在已打开的 Chrome 标签页里找到包含 {url_keyword} 的页面。"
            "请先自行打开快手创作者中心并登录，然后重新运行脚本。"
        )
    page = min(matched_pages, key=lambda item: (_kuaishou_page_priority(item.url), item.url))
    page.bring_to_front()
    page.wait_for_load_state("domcontentloaded")
    print(f"已锁定快手页面：{page.url}")
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


def open_graphic_publish_flow(page: Page, settings: KuaishouPublishSettings) -> None:
    click_locator(page, publish_work_button_locators(page), description="发布作品", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_publish_work_wait_ms)

    upload_panel_ready = find_optional_locator(page, main_upload_button_locators(page), timeout_ms=3_000)
    editor_ready = find_optional_locator(page, description_editor_locators(page), timeout_ms=1_500)
    if upload_panel_ready is None and editor_ready is None:
        print("发布作品后未立即出现上传区，等待页面加载…")
        page.wait_for_timeout(3_000)

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


def confirm_topic_with_enter(page: Page, topic_tag: str, settings: KuaishouPublishSettings) -> None:
    page.wait_for_timeout(settings.after_topic_confirm_wait_ms)
    page.keyboard.press("Enter")
    print(f"已等待 {settings.after_topic_confirm_wait_ms}ms 后回车确认话题：{topic_tag}")


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
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)
    print(f"已输入快手作品描述正文（第 1 行），长度 {len(assets.description_body)}。")

    for topic_tag in assets.topic_tags:
        topic_text = topic_tag.lstrip("#")
        page.keyboard.insert_text("#")
        page.wait_for_timeout(settings.topic_typing_delay_ms)
        type_text_humanly(page, topic_text, delay_ms=settings.topic_typing_delay_ms)
        confirm_topic_with_enter(page, topic_tag, settings)
        page.wait_for_timeout(settings.between_topics_wait_ms)

    print(f"已输入快手 {len(assets.topic_tags)} 个话题。")


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


def to_cdp_settings(settings: KuaishouPublishSettings) -> PublishSettings:
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


def run_kuaishou_publish(settings: KuaishouPublishSettings, assets: KuaishouPublishAssets) -> None:
    if settings.auto_launch_browser:
        ensure_cdp_browser_available(to_cdp_settings(settings))
    elif not settings.dry_run:
        from tools.douyin_publish import is_cdp_endpoint_ready

        if not is_cdp_endpoint_ready(settings.cdp_url):
            raise RuntimeError(
                f"无法连接到 Chrome 远程调试端口：{settings.cdp_url}\n"
                "请先自行打开 Chrome 并登录 cp.kuaishou.com，再运行本脚本。"
            )

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        page = find_kuaishou_page(browser, settings.url_keyword)

        try:
            open_graphic_publish_flow(page, settings)
            upload_main_images(page, assets, settings)
            fill_work_description(page, assets, settings)
            upload_cover_image(page, assets, settings)
            select_author_statement(page, settings)
            select_publish_location(page, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise
