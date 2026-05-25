from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

try:
    from playwright.sync_api import Browser, Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ImportError as exc:
    raise SystemExit("未安装 playwright，请先执行 pip install -r requirements.txt。") from exc


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from image_generator import ensure_runtime_config_loaded, split_description_body_and_tags  # noqa: E402


DEFAULT_OUTPUT_DIR = ROOT_DIR / "output" / "20260525_043309_葱香海参酿"
DEFAULT_PUBLISH_DIR_NAME = "publish"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_URL_KEYWORD = "creator.douyin.com"
DEFAULT_TYPING_DELAY_MS = 120
DEFAULT_AFTER_UPLOAD_WAIT_MS = 10_000
DEFAULT_AFTER_OPEN_COVER_WAIT_MS = 5_000
DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS = 10_000
DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS = 2_000
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "douyin_publish_last_error.png"
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class PublishAssets:
    output_dir: Path
    publish_dir: Path
    image_paths: tuple[Path, ...]
    cover_path: Path
    title_text: str
    description_body: str
    topic_tags: tuple[str, ...]
    title_file: Path
    description_file: Path


@dataclass(frozen=True)
class PublishSettings:
    output_dir: Path
    cdp_url: str
    url_keyword: str
    typing_delay_ms: int
    after_upload_wait_ms: int
    after_open_cover_wait_ms: int
    after_cover_confirm_wait_ms: int
    after_declaration_open_wait_ms: int
    debug_screenshot: Path
    dry_run: bool


def resolve_path(path_text: str | Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (ROOT_DIR / candidate).resolve()


def parse_non_negative_int(value: object, *, field_name: str, default: int) -> int:
    raw_value = str(value or "").strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{field_name} 必须是大于等于 0 的整数。") from exc

    if parsed < 0:
        raise RuntimeError(f"{field_name} 必须是大于等于 0 的整数。")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="接管已开启远程调试的 Chrome 抖音创作者页，自动上传 publish 图片并填写标题、描述、封面与自主声明。",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"本次输出目录，默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--cdp-url",
        default=DEFAULT_CDP_URL,
        help=f"Chrome 远程调试地址，默认 {DEFAULT_CDP_URL}",
    )
    parser.add_argument(
        "--url-keyword",
        default=DEFAULT_URL_KEYWORD,
        help=f"用于锁定已打开抖音创作者标签页的 URL 关键字，默认 {DEFAULT_URL_KEYWORD}",
    )
    parser.add_argument(
        "--typing-delay-ms",
        type=int,
        default=DEFAULT_TYPING_DELAY_MS,
        help=f"标题和描述逐字输入间隔，默认 {DEFAULT_TYPING_DELAY_MS}ms",
    )
    parser.add_argument(
        "--after-upload-wait-ms",
        type=int,
        default=DEFAULT_AFTER_UPLOAD_WAIT_MS,
        help=f"上传图文后额外等待时长，默认 {DEFAULT_AFTER_UPLOAD_WAIT_MS}ms",
    )
    parser.add_argument(
        "--after-open-cover-wait-ms",
        type=int,
        default=DEFAULT_AFTER_OPEN_COVER_WAIT_MS,
        help=f"点击编辑封面后额外等待时长，默认 {DEFAULT_AFTER_OPEN_COVER_WAIT_MS}ms",
    )
    parser.add_argument(
        "--after-cover-confirm-wait-ms",
        type=int,
        default=DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS,
        help=f"封面两次确认后额外等待时长，默认 {DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS}ms",
    )
    parser.add_argument(
        "--after-declaration-open-wait-ms",
        type=int,
        default=DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS,
        help=f"打开自主声明下拉框后额外等待时长，默认 {DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS}ms",
    )
    parser.add_argument(
        "--debug-screenshot",
        default=str(DEFAULT_DEBUG_SCREENSHOT),
        help=f"运行失败时保存页面截图的位置，默认 {DEFAULT_DEBUG_SCREENSHOT}",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验本地文件与参数，不连接 Chrome。")
    return parser.parse_args()


def resolve_settings(args: argparse.Namespace) -> PublishSettings:
    ensure_runtime_config_loaded()

    output_dir = resolve_path(args.output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"输出目录不存在：{output_dir}")

    return PublishSettings(
        output_dir=output_dir,
        cdp_url=str(args.cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
        url_keyword=str(args.url_keyword or DEFAULT_URL_KEYWORD).strip() or DEFAULT_URL_KEYWORD,
        typing_delay_ms=parse_non_negative_int(args.typing_delay_ms, field_name="typing-delay-ms", default=DEFAULT_TYPING_DELAY_MS),
        after_upload_wait_ms=parse_non_negative_int(args.after_upload_wait_ms, field_name="after-upload-wait-ms", default=DEFAULT_AFTER_UPLOAD_WAIT_MS),
        after_open_cover_wait_ms=parse_non_negative_int(args.after_open_cover_wait_ms, field_name="after-open-cover-wait-ms", default=DEFAULT_AFTER_OPEN_COVER_WAIT_MS),
        after_cover_confirm_wait_ms=parse_non_negative_int(args.after_cover_confirm_wait_ms, field_name="after-cover-confirm-wait-ms", default=DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS),
        after_declaration_open_wait_ms=parse_non_negative_int(args.after_declaration_open_wait_ms, field_name="after-declaration-open-wait-ms", default=DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS),
        debug_screenshot=resolve_path(args.debug_screenshot),
        dry_run=bool(args.dry_run),
    )


def read_utf8_text(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8").strip()


def find_single_file(output_dir: Path, suffix: str) -> Path:
    matches = sorted(output_dir.glob(f"*{suffix}"))
    if not matches:
        raise RuntimeError(f"未找到 {suffix} 文件：{output_dir}")
    return matches[-1]


def resolve_cover_path(publish_dir: Path) -> Path:
    direct_cover = publish_dir / "cover.jpg"
    if direct_cover.exists() and direct_cover.is_file():
        return direct_cover

    candidates = sorted(
        path
        for path in publish_dir.iterdir()
        if path.is_file() and path.stem.lower() == "cover" and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )
    if not candidates:
        raise RuntimeError(f"publish 目录里未找到 cover 图片：{publish_dir}")
    return candidates[-1]


def collect_publish_images(publish_dir: Path) -> tuple[Path, ...]:
    image_paths = sorted(
        path
        for path in publish_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        and path.stem.lower() != "cover"
    )
    if not image_paths:
        raise RuntimeError(f"publish 目录里没有可上传的图文图片：{publish_dir}")
    return tuple(image_paths)


def resolve_publish_assets(settings: PublishSettings) -> PublishAssets:
    publish_dir = settings.output_dir / DEFAULT_PUBLISH_DIR_NAME
    if not publish_dir.exists() or not publish_dir.is_dir():
        raise RuntimeError(f"publish 目录不存在：{publish_dir}")

    title_file = find_single_file(settings.output_dir, "_抖音图文标题.txt")
    description_file = find_single_file(settings.output_dir, "_抖音图文描述.txt")
    title_text = read_utf8_text(title_file)
    description_text = read_utf8_text(description_file)
    description_body, topic_tags = split_description_body_and_tags(description_text)

    if not title_text:
        raise RuntimeError(f"抖音图文标题为空：{title_file}")
    if not description_body:
        raise RuntimeError(f"抖音图文描述正文为空：{description_file}")
    if len(topic_tags) != 5:
        raise RuntimeError(f"抖音图文描述最后一行必须正好有 5 个话题，当前为 {len(topic_tags)} 个：{description_file}")

    return PublishAssets(
        output_dir=settings.output_dir,
        publish_dir=publish_dir,
        image_paths=collect_publish_images(publish_dir),
        cover_path=resolve_cover_path(publish_dir),
        title_text=title_text,
        description_body=description_body,
        topic_tags=tuple(topic_tags),
        title_file=title_file,
        description_file=description_file,
    )


def log_assets(assets: PublishAssets) -> None:
    print(f"输出目录：{assets.output_dir}")
    print(f"图文上传目录：{assets.publish_dir}")
    print(f"标题文件：{assets.title_file}")
    print(f"描述文件：{assets.description_file}")
    print(f"封面文件：{assets.cover_path}")
    print(f"图文图片数量：{len(assets.image_paths)}")
    for image_path in assets.image_paths:
        print(f"待上传图片：{image_path}")


def find_target_page(browser: Browser, url_keyword: str) -> Page:
    matched_pages: list[Page] = []
    for context in browser.contexts:
        matched_pages.extend(page for page in context.pages if url_keyword in page.url)

    if not matched_pages:
        raise RuntimeError(
            f"未在已打开的 Chrome 标签页里找到包含 {url_keyword} 的页面。"
            "请先用带远程调试端口的方式打开 Chrome，并确保抖音创作者页已经处于登录状态。"
        )

    page = matched_pages[-1]
    page.bring_to_front()
    page.wait_for_load_state("domcontentloaded")
    print(f"已锁定抖音页面：{page.url}")
    return page


def wait_for_locator(
    page: Page,
    locators: Sequence[Locator],
    *,
    description: str,
    state: str = "visible",
    timeout_ms: int = 30_000,
) -> Locator:
    deadline_attempts = max(1, timeout_ms // 500)
    last_error: Exception | None = None

    for _ in range(deadline_attempts):
        for locator in locators:
            try:
                locator.first.wait_for(state=state, timeout=500)
                return locator.first
            except PlaywrightTimeoutError as exc:
                last_error = exc
        page.wait_for_timeout(200)

    raise RuntimeError(f"等待 {description} 超时。") from last_error


def find_optional_locator(
    page: Page,
    locators: Sequence[Locator],
    *,
    state: str = "visible",
    timeout_ms: int = 3_000,
) -> Locator | None:
    try:
        return wait_for_locator(page, locators, description="可选节点", state=state, timeout_ms=timeout_ms)
    except RuntimeError:
        return None


def click_locator(page: Page, locators: Sequence[Locator], *, description: str, timeout_ms: int = 30_000) -> Locator:
    locator = wait_for_locator(page, locators, description=description, timeout_ms=timeout_ms)
    locator.click()
    print(f"已点击：{description}")
    return locator


def clear_and_focus(page: Page, locator: Locator, *, description: str) -> None:
    locator.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    print(f"已聚焦：{description}")


def type_text_humanly(page: Page, text: str, *, delay_ms: int) -> None:
    for char in text:
        if char == "\n":
            page.keyboard.press("Enter")
        else:
            page.keyboard.insert_text(char)
        page.wait_for_timeout(delay_ms)


def title_input_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("input[placeholder='添加作品标题']"),
        page.get_by_placeholder("添加作品标题"),
    )


def description_editor_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("[contenteditable='true'][data-placeholder='添加作品描述']"),
        page.locator("div[contenteditable='true'][data-placeholder='添加作品描述']"),
    )


def publish_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("button", name="高质量发布"),
        page.get_by_role("button", name="发布"),
        page.locator("button").filter(has_text="高质量发布"),
        page.locator("button").filter(has_text="发布"),
    )


def publish_graphic_menu_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("menuitem", name="发布图文"),
        page.locator("li[role='menuitem']").filter(has_text="发布图文"),
        page.locator("div[role='menuitem']").filter(has_text="发布图文"),
    )


def upload_graphic_entry_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("tab", name="上传图文"),
        page.locator("div[role='tab']").filter(has_text="上传图文"),
        page.locator("span").filter(has_text="上传图文"),
    )


def main_upload_input_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("input[type='file'][multiple]"),
    )


def edit_cover_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("button", name="编辑封面"),
        page.locator("button").filter(has_text="编辑封面"),
        page.locator("span").filter(has_text="编辑封面"),
    )


def upload_cover_tab_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("tab", name="上传封面"),
        page.locator("div[role='tab']").filter(has_text="上传封面"),
    )


def cover_upload_input_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("input[type='file']:not([multiple])"),
    )


def confirm_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("button", name="确定"),
        page.get_by_role("button", name="确认"),
        page.locator("button").filter(has_text="确定"),
        page.locator("button").filter(has_text="确认"),
    )


def declaration_select_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_text("请选择自主声明", exact=True),
        page.locator("div").filter(has_text="请选择自主声明"),
        page.locator("span").filter(has_text="请选择自主声明"),
    )


def personal_view_radio_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("label").filter(has_text="内容为个人观点或臆测"),
        page.get_by_text("内容为个人观点或臆测", exact=False),
    )


def ensure_publish_editor_ready(page: Page) -> None:
    if find_optional_locator(page, title_input_locators(page), timeout_ms=2_000):
        print("当前已在抖音图文发布页。")
        return

    click_locator(page, publish_button_locators(page), description="顶部发布按钮")
    click_locator(page, publish_graphic_menu_locators(page), description="发布图文菜单项")
    page.wait_for_load_state("domcontentloaded")

    if find_optional_locator(page, title_input_locators(page), timeout_ms=5_000):
        print("已进入图文发布页。")
        return

    upload_entry = find_optional_locator(page, upload_graphic_entry_locators(page), timeout_ms=8_000)
    if upload_entry is not None:
        upload_entry.click()
        print("已点击：上传图文入口")

    wait_for_locator(page, title_input_locators(page), description="图文标题输入框", timeout_ms=30_000)
    print("图文发布页已就绪。")


def upload_publish_images(page: Page, assets: PublishAssets, settings: PublishSettings) -> None:
    upload_input = wait_for_locator(
        page,
        main_upload_input_locators(page),
        description="图文上传 input",
        state="attached",
        timeout_ms=30_000,
    )
    upload_input.set_input_files([str(path) for path in assets.image_paths])
    print(f"已投喂图文图片，共 {len(assets.image_paths)} 张。")
    page.wait_for_timeout(settings.after_upload_wait_ms)


def input_publish_title(page: Page, assets: PublishAssets, settings: PublishSettings) -> None:
    title_input = wait_for_locator(page, title_input_locators(page), description="标题输入框", timeout_ms=30_000)
    clear_and_focus(page, title_input, description="标题输入框")
    type_text_humanly(page, assets.title_text, delay_ms=settings.typing_delay_ms)
    print("已输入图文标题。")


def input_publish_description(page: Page, assets: PublishAssets, settings: PublishSettings) -> None:
    editor = wait_for_locator(page, description_editor_locators(page), description="描述输入框", timeout_ms=30_000)
    clear_and_focus(page, editor, description="描述输入框")
    type_text_humanly(page, assets.description_body, delay_ms=settings.typing_delay_ms)
    page.keyboard.press("Enter")
    page.wait_for_timeout(settings.typing_delay_ms)

    for topic_tag in assets.topic_tags:
        topic_text = topic_tag.lstrip("#")
        page.keyboard.insert_text("#")
        page.wait_for_timeout(settings.typing_delay_ms)
        type_text_humanly(page, topic_text, delay_ms=settings.typing_delay_ms)
        page.keyboard.press("Space")
        page.wait_for_timeout(settings.typing_delay_ms)
        page.keyboard.press("Space")
        page.wait_for_timeout(settings.typing_delay_ms)

    print("已按逐字方式输入描述与 5 个话题。")


def upload_cover(page: Page, assets: PublishAssets, settings: PublishSettings) -> None:
    click_locator(page, edit_cover_button_locators(page), description="编辑封面按钮")
    page.wait_for_timeout(settings.after_open_cover_wait_ms)

    upload_cover_tab = find_optional_locator(page, upload_cover_tab_locators(page), timeout_ms=5_000)
    if upload_cover_tab is not None:
        upload_cover_tab.click()
        print("已点击：上传封面标签")

    cover_input = wait_for_locator(
        page,
        cover_upload_input_locators(page),
        description="封面上传 input",
        state="attached",
        timeout_ms=30_000,
    )
    cover_input.set_input_files(str(assets.cover_path))
    print("已上传封面图。")

    click_locator(page, confirm_button_locators(page), description="封面裁剪确定按钮", timeout_ms=30_000)
    page.wait_for_timeout(800)
    click_locator(page, confirm_button_locators(page), description="封面保存确认按钮", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_cover_confirm_wait_ms)


def submit_declaration(page: Page, settings: PublishSettings) -> None:
    click_locator(page, declaration_select_locators(page), description="自主声明下拉框", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_declaration_open_wait_ms)
    click_locator(page, personal_view_radio_locators(page), description="个人观点声明选项", timeout_ms=10_000)
    click_locator(page, confirm_button_locators(page), description="自主声明确认按钮", timeout_ms=30_000)


def run_publish(settings: PublishSettings, assets: PublishAssets) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        page = find_target_page(browser, settings.url_keyword)

        try:
            ensure_publish_editor_ready(page)
            upload_publish_images(page, assets, settings)
            input_publish_title(page, assets, settings)
            input_publish_description(page, assets, settings)
            upload_cover(page, assets, settings)
            submit_declaration(page, settings)
        except Exception:
            settings.debug_screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(settings.debug_screenshot), full_page=True)
                print(f"已保存失败截图：{settings.debug_screenshot}")
            except Exception:
                pass
            raise


def main() -> int:
    try:
        settings = resolve_settings(parse_args())
        assets = resolve_publish_assets(settings)
        log_assets(assets)
        if settings.dry_run:
            print("dry-run 校验完成，未连接 Chrome。")
            return 0

        run_publish(settings, assets)
    except Exception as exc:
        print(f"运行失败：{exc}")
        return 1

    print("抖音图文发布动作已执行完成，请回到浏览器检查页面状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())