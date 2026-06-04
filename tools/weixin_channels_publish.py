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
    ensure_cdp_browser_available,
    find_optional_locator,
    type_text_humanly,
    wait_for_locator,
)
from tools.kuaishou_publish import paste_topic_tags_via_clipboard  # noqa: E402
from tools.screen_template_match import click_template_on_page, match_template_on_page  # noqa: E402
from tools.weixin_mp_publish import confirm_windows_open_dialog  # noqa: E402


DEFAULT_URL_KEYWORD = "channels.weixin.qq.com"
DEFAULT_LOGIN_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_logged_in_marker.png"
DEFAULT_MENU_CONTENT_MANAGE_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_menu_content_manage.png"
DEFAULT_MENU_GRAPHIC_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_menu_graphic.png"
DEFAULT_PUBLISH_GRAPHIC_BUTTON_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_btn_publish_graphic.png"
DEFAULT_UPLOAD_AREA_MARKER = ROOT_DIR / "V2" / "assets" / "weixin_channels_upload_area.png"
DEFAULT_LOGIN_MATCH_THRESHOLD = 0.9
DEFAULT_TEMPLATE_MATCH_THRESHOLD = 0.9
DEFAULT_STEP_WAIT_MS = 3_000
DEFAULT_AFTER_UPLOAD_WAIT_MS = 5_000
DEFAULT_AFTER_TOPIC_PASTE_WAIT_MS = 1_000
DEFAULT_BETWEEN_TOPICS_WAIT_MS = 2_000
DEFAULT_WINDOWS_OPEN_DIALOG_WAIT_MS = 3_500
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "weixin_channels_publish_last_error.png"
DEFAULT_UPLOAD_STEP_SCREENSHOT = ROOT_DIR / "tools" / "weixin_channels_publish_upload_step.png"


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
    after_topic_paste_wait_ms: int
    between_topics_wait_ms: int
    windows_open_dialog_wait_ms: int
    upload_step_screenshot: Path
    debug_screenshot: Path
    dry_run: bool


def content_manage_menu_locators(page: Page) -> tuple:
    return (
        page.locator("#menuBar .finder-ui-desktop-menu__name").filter(has_text="内容管理").first,
        page.get_by_text("内容管理", exact=True).first,
    )


def graphic_menu_locators(page: Page) -> tuple:
    return (
        page.locator("#menuBar .finder-ui-desktop-menu__name").filter(has_text="图文").first,
        page.get_by_text("图文", exact=True).first,
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
        page.locator('input.weui-desktop-form__input[placeholder="填写标题，22个字符内"]').first,
        page.get_by_placeholder("填写标题，22个字符内"),
    )


def description_editor_locators(page: Page) -> tuple:
    return (
        page.locator("div.input-editor[contenteditable='true']").first,
        page.locator('div[data-placeholder="添加描述，1000个字符内"]').first,
    )


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


def _click_template_or_dom(
    page: Page,
    *,
    template_path: Path,
    dom_locators: tuple,
    description: str,
    settings: WeixinChannelsPublishSettings,
) -> None:
    try:
        click_template_on_page(
            page,
            template_path,
            threshold=settings.template_match_threshold,
            description=description,
        )
        return
    except Exception as exc:
        print(f"模板点击「{description}」失败，尝试 DOM：{exc}")
    click_locator(page, dom_locators, description=description, timeout_ms=15_000)


def open_graphic_publish_editor(page: Page, settings: WeixinChannelsPublishSettings) -> None:
    page.bring_to_front()
    ensure_channels_logged_in(page, settings)

    _click_template_or_dom(
        page,
        template_path=settings.menu_content_manage_marker_path,
        dom_locators=content_manage_menu_locators(page),
        description="内容管理",
        settings=settings,
    )
    page.wait_for_timeout(settings.step_wait_ms)

    _click_template_or_dom(
        page,
        template_path=settings.menu_graphic_marker_path,
        dom_locators=graphic_menu_locators(page),
        description="图文",
        settings=settings,
    )
    page.wait_for_timeout(settings.step_wait_ms)

    _click_template_or_dom(
        page,
        template_path=settings.publish_graphic_button_marker_path,
        dom_locators=publish_graphic_button_locators(page),
        description="发表图文",
        settings=settings,
    )
    page.wait_for_timeout(settings.step_wait_ms)


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

    if _upload_via_dom_input(page, assets.image_paths):
        page.wait_for_timeout(settings.after_upload_wait_ms)
        close_stray_file_dialog(page)
        save_step_screenshot(page, settings.upload_step_screenshot, "图文上传后")
        print(f"视频号图文上传完成，共 {len(assets.image_paths)} 张。")
        return

    resolved_paths = [str(path.resolve()) for path in assets.image_paths]
    uploaded = False
    try:
        with page.expect_file_chooser(timeout=12_000) as chooser_info:
            click_template_on_page(
                page,
                settings.upload_area_marker_path,
                threshold=settings.template_match_threshold,
                description="上传区域",
            )
        chooser_info.value.set_files(resolved_paths)
        uploaded = True
        print(f"已通过 file chooser 上传 {len(resolved_paths)} 张。")
    except Exception as exc:
        print(f"上传区 file chooser 不可用：{exc}")
        try:
            click_template_on_page(
                page,
                settings.upload_area_marker_path,
                threshold=settings.template_match_threshold,
                description="上传区域",
            )
        except Exception:
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
    save_step_screenshot(page, settings.upload_step_screenshot, "图文上传后")
    print(f"视频号图文上传完成，共 {len(assets.image_paths)} 张。")


def fill_title(page: Page, assets: WeixinChannelsPublishAssets, settings: WeixinChannelsPublishSettings) -> None:
    title_input = wait_for_locator(page, title_input_locators(page), description="图文标题输入框", timeout_ms=30_000)
    title_input.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, assets.title_text, delay_ms=settings.typing_delay_ms)
    print(f"已输入视频号标题：{assets.title_text}")


def fill_description_and_topics(
    page: Page,
    assets: WeixinChannelsPublishAssets,
    settings: WeixinChannelsPublishSettings,
) -> None:
    editor = wait_for_locator(page, description_editor_locators(page), description="图文描述输入框", timeout_ms=30_000)
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    type_text_humanly(page, assets.description_body, delay_ms=settings.typing_delay_ms)
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
