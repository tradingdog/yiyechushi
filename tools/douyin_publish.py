from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

try:
    from playwright.sync_api import Browser, Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ImportError as exc:
    raise SystemExit("未安装 playwright，请先执行 pip install -r requirements.txt。") from exc


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from image_generator import ensure_runtime_config_loaded, get_required_publish_topics, split_description_body_and_tags  # noqa: E402


DEFAULT_OUTPUT_DIR = ROOT_DIR / "output" / "20260525_043309_葱香海参酿"
DEFAULT_PUBLISH_DIR_NAME = "publish"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_URL_KEYWORD = "creator.douyin.com"
DEFAULT_CREATOR_HOME_URL = "https://creator.douyin.com/creator-micro/home"
DEFAULT_TYPING_DELAY_MS = 120
DEFAULT_AFTER_UPLOAD_WAIT_MS = 10_000
DEFAULT_AFTER_OPEN_COVER_WAIT_MS = 5_000
DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS = 10_000
DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS = 2_000
DEFAULT_DEBUG_SCREENSHOT = ROOT_DIR / "tools" / "douyin_publish_last_error.png"
DEFAULT_AUTOMATION_PROFILE_DIR = ROOT_DIR / "tools" / "chrome_automation_profile"
DEFAULT_CDP_READY_TIMEOUT_MS = 20_000
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
LocatorWaitState = Literal["attached", "detached", "hidden", "visible"]


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
    chrome_path: Path | None
    automation_profile_dir: Path
    auto_launch_browser: bool
    creator_home_url: str
    cdp_ready_timeout_ms: int
    typing_delay_ms: int
    after_upload_wait_ms: int
    after_open_cover_wait_ms: int
    after_cover_confirm_wait_ms: int
    after_declaration_open_wait_ms: int
    auto_submit_publish: bool
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
        description="接管已开启远程调试的 Chrome 抖音创作者页，自动上传 publish 图片并填写标题、描述、封面与自主声明，默认停在待人工发布状态。",
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
        "--chrome-path",
        help="Chrome.exe 路径；不传时脚本会按本机常见路径自动查找。",
    )
    parser.add_argument(
        "--automation-profile-dir",
        default=str(DEFAULT_AUTOMATION_PROFILE_DIR),
        help=f"自动化 Chrome 独立资料目录，默认 {DEFAULT_AUTOMATION_PROFILE_DIR}",
    )
    parser.add_argument(
        "--creator-home-url",
        default=DEFAULT_CREATOR_HOME_URL,
        help=f"自动拉起浏览器时默认打开的抖音创作者页，默认 {DEFAULT_CREATOR_HOME_URL}",
    )
    parser.add_argument(
        "--cdp-ready-timeout-ms",
        type=int,
        default=DEFAULT_CDP_READY_TIMEOUT_MS,
        help=f"自动拉起 Chrome 后等待 CDP 就绪的超时时长，默认 {DEFAULT_CDP_READY_TIMEOUT_MS}ms",
    )
    parser.add_argument(
        "--no-auto-launch-browser",
        action="store_true",
        help="当 9222 不可用时不自动拉起可接管的 Chrome 浏览器，而是直接报错退出。",
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
        "--auto-submit-publish",
        action="store_true",
        help="显式开启后，脚本才会自动点击最终发布按钮；默认保留给人工审核后手动发布。",
    )
    parser.add_argument(
        "--debug-screenshot",
        default=str(DEFAULT_DEBUG_SCREENSHOT),
        help=f"运行失败时保存页面截图的位置，默认 {DEFAULT_DEBUG_SCREENSHOT}",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验本地文件与参数，不连接 Chrome。")
    return parser.parse_args()


def default_chrome_candidates() -> tuple[Path, ...]:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "")).expanduser()
    candidates = [
        local_app_data / "Google/Chrome/Application/chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    unique_candidates: list[Path] = []
    for candidate in candidates:
        if candidate and candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return tuple(unique_candidates)


def find_default_chrome_path() -> Path | None:
    for candidate in default_chrome_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def resolve_settings(args: argparse.Namespace) -> PublishSettings:
    ensure_runtime_config_loaded()

    output_dir = resolve_path(args.output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        raise RuntimeError(f"输出目录不存在：{output_dir}")

    chrome_path_text = str(args.chrome_path or "").strip()
    chrome_path = resolve_path(chrome_path_text) if chrome_path_text else find_default_chrome_path()

    return PublishSettings(
        output_dir=output_dir,
        cdp_url=str(args.cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
        url_keyword=str(args.url_keyword or DEFAULT_URL_KEYWORD).strip() or DEFAULT_URL_KEYWORD,
        chrome_path=chrome_path,
        automation_profile_dir=resolve_path(args.automation_profile_dir),
        auto_launch_browser=not bool(args.no_auto_launch_browser),
        creator_home_url=str(args.creator_home_url or DEFAULT_CREATOR_HOME_URL).strip() or DEFAULT_CREATOR_HOME_URL,
        cdp_ready_timeout_ms=parse_non_negative_int(args.cdp_ready_timeout_ms, field_name="cdp-ready-timeout-ms", default=DEFAULT_CDP_READY_TIMEOUT_MS),
        typing_delay_ms=parse_non_negative_int(args.typing_delay_ms, field_name="typing-delay-ms", default=DEFAULT_TYPING_DELAY_MS),
        after_upload_wait_ms=parse_non_negative_int(args.after_upload_wait_ms, field_name="after-upload-wait-ms", default=DEFAULT_AFTER_UPLOAD_WAIT_MS),
        after_open_cover_wait_ms=parse_non_negative_int(args.after_open_cover_wait_ms, field_name="after-open-cover-wait-ms", default=DEFAULT_AFTER_OPEN_COVER_WAIT_MS),
        after_cover_confirm_wait_ms=parse_non_negative_int(args.after_cover_confirm_wait_ms, field_name="after-cover-confirm-wait-ms", default=DEFAULT_AFTER_COVER_CONFIRM_WAIT_MS),
        after_declaration_open_wait_ms=parse_non_negative_int(args.after_declaration_open_wait_ms, field_name="after-declaration-open-wait-ms", default=DEFAULT_AFTER_DECLARATION_OPEN_WAIT_MS),
        auto_submit_publish=bool(args.auto_submit_publish),
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


def merge_publish_topic_tags(topic_tags: Sequence[str]) -> tuple[str, ...]:
    merged_tags: list[str] = []
    seen: set[str] = set()

    for topic in [*get_required_publish_topics(), *topic_tags]:
        normalized_topic = str(topic or "").strip()
        if not normalized_topic or normalized_topic in seen:
            continue
        seen.add(normalized_topic)
        merged_tags.append(normalized_topic)
        if len(merged_tags) >= 5:
            break

    return tuple(merged_tags)


def resolve_publish_assets(settings: PublishSettings) -> PublishAssets:
    publish_dir = settings.output_dir / DEFAULT_PUBLISH_DIR_NAME
    if not publish_dir.exists() or not publish_dir.is_dir():
        raise RuntimeError(f"publish 目录不存在：{publish_dir}")

    title_file = find_single_file(settings.output_dir, "_抖音图文标题.txt")
    description_file = find_single_file(settings.output_dir, "_抖音图文描述.txt")
    title_text = read_utf8_text(title_file)
    description_text = read_utf8_text(description_file)
    description_body, topic_tags = split_description_body_and_tags(description_text)
    merged_topic_tags = merge_publish_topic_tags(topic_tags)

    if not title_text:
        raise RuntimeError(f"抖音图文标题为空：{title_file}")
    if not description_body:
        raise RuntimeError(f"抖音图文描述正文为空：{description_file}")
    if len(merged_topic_tags) != 5:
        raise RuntimeError(f"抖音图文描述最后一行必须正好有 5 个话题，当前为 {len(merged_topic_tags)} 个：{description_file}")

    if tuple(topic_tags) != merged_topic_tags:
        normalized_description_text = f"{description_body}\n{' '.join(merged_topic_tags)}".strip()
        description_file.write_text(normalized_description_text, encoding="utf-8")
        print(f"已按当前 config 同步描述文件话题：{description_file}")

    return PublishAssets(
        output_dir=settings.output_dir,
        publish_dir=publish_dir,
        image_paths=collect_publish_images(publish_dir),
        cover_path=resolve_cover_path(publish_dir),
        title_text=title_text,
        description_body=description_body,
        topic_tags=merged_topic_tags,
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
        print(f"已识别上传图片：{image_path}")


def cdp_version_url(cdp_url: str) -> str:
    return cdp_url.rstrip("/") + "/json/version"


def is_cdp_endpoint_ready(cdp_url: str, timeout_seconds: float = 1.5) -> bool:
    try:
        with urlopen(cdp_version_url(cdp_url), timeout=timeout_seconds) as response:
            return response.status == 200
    except (URLError, OSError, ValueError):
        return False


def extract_cdp_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(f"暂时只支持 http/https 形式的 CDP 地址：{cdp_url}")
    if not parsed.port:
        raise RuntimeError(f"CDP 地址里缺少端口：{cdp_url}")
    return parsed.port


def ensure_cdp_browser_available(settings: PublishSettings) -> None:
    if is_cdp_endpoint_ready(settings.cdp_url):
        return

    if not settings.auto_launch_browser:
        raise RuntimeError(
            "无法连接到 Chrome 远程调试端口，且当前已关闭自动拉起浏览器。"
            f"\n当前连接地址：{settings.cdp_url}"
        )

    if settings.chrome_path is None:
        searched_paths = "\n".join(str(path) for path in default_chrome_candidates())
        raise RuntimeError(
            "未找到可自动拉起的 Chrome.exe，请手动通过 --chrome-path 指定路径。"
            f"\n已尝试这些位置：\n{searched_paths}"
        )

    settings.automation_profile_dir.mkdir(parents=True, exist_ok=True)
    cdp_port = extract_cdp_port(settings.cdp_url)
    launch_command = [
        str(settings.chrome_path),
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={settings.automation_profile_dir}",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        settings.creator_home_url,
    ]
    subprocess.Popen(launch_command)
    print("检测到当前没有可接管的 Chrome 调试端口，已自动拉起独立的自动化 Chrome 窗口。")
    print(f"自动化 Chrome 路径：{settings.chrome_path}")
    print(f"自动化资料目录：{settings.automation_profile_dir}")
    print("说明：这是独立的自动化 Chrome，不会直接接管你当前已经打开的普通 Chrome 窗口。")
    print("如果这个自动化窗口是首次打开，请先在其中完成抖音登录；后续脚本会复用这个资料目录。")

    deadline = time.time() + settings.cdp_ready_timeout_ms / 1000
    while time.time() < deadline:
        if is_cdp_endpoint_ready(settings.cdp_url):
            print(f"Chrome 调试端口已就绪：{settings.cdp_url}")
            return
        time.sleep(0.5)

    raise RuntimeError(
        "脚本已经尝试自动拉起可接管的 Chrome，但调试端口仍未就绪。"
        f"\n当前连接地址：{settings.cdp_url}"
        f"\nChrome 路径：{settings.chrome_path}"
        f"\n自动化资料目录：{settings.automation_profile_dir}"
    )


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


def is_login_required_page(page: Page) -> bool:
    login_keywords = (
        "扫码登录",
        "验证码登录",
        "密码登录",
        "登录/注册",
        "创作者登录",
    )
    for keyword in login_keywords:
        try:
            if page.get_by_text(keyword, exact=False).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def wait_for_locator(
    page: Page,
    locators: Sequence[Locator],
    *,
    description: str,
    state: LocatorWaitState = "visible",
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
    state: LocatorWaitState = "visible",
    timeout_ms: int = 3_000,
) -> Locator | None:
    try:
        return wait_for_locator(page, locators, description="可选节点", state=state, timeout_ms=timeout_ms)
    except RuntimeError:
        return None


def click_locator(
    page: Page,
    locators: Sequence[Locator],
    *,
    description: str,
    timeout_ms: int = 30_000,
    force: bool = False,
) -> Locator:
    locator = wait_for_locator(page, locators, description=description, timeout_ms=timeout_ms)
    locator.click(force=force)
    print(f"已点击：{description}")
    return locator


def click_locator_via_dom(
    page: Page,
    locators: Sequence[Locator],
    *,
    description: str,
    timeout_ms: int = 30_000,
) -> Locator:
    locator = wait_for_locator(page, locators, description=description, timeout_ms=timeout_ms)
    locator.evaluate("el => el.click()")
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
        page.locator("[contenteditable='true'][data-placeholder*='添加作品描述']"),
        page.locator("div[contenteditable='true'][data-placeholder*='添加作品描述']"),
        page.locator("[contenteditable][data-placeholder*='添加作品描述']"),
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


def select_cover_tab_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("tab", name="选择封面"),
        page.locator("div[role='tab']").filter(has_text="选择封面"),
    )


def cover_upload_input_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("input[type='file']:not([multiple])"),
    )


def cover_crop_confirm_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("div.semi-modal-wrap[role='modal']:visible button.primary-cECiOJ.dialog-b5CBy1").filter(has_text="确定"),
        page.locator("div[role='modal']:visible button.dialog-b5CBy1").filter(has_text="确定"),
        page.locator("div.semi-modal-wrap[role='modal']:visible button").filter(has_text="确定"),
    )


def cover_crop_modal_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("div.semi-modal-wrap[role='modal']:visible").filter(has_text="裁剪封面"),
        page.locator("div[role='modal']:visible").filter(has_text="裁剪封面"),
    )


def cover_save_confirm_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("button.submit-wycsGi").filter(has_text="确定"),
        page.locator("div.operation-faNu0S button").filter(has_text="确定"),
    )


def cover_apply_confirm_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.locator("button.submit-eXWVUP").filter(has_text="确定"),
        page.locator("div.operation-zqnRB8 button").filter(has_text="确定"),
    )


def final_publish_button_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_role("button", name="发布", exact=True),
        page.locator("button.fixed-J9O8Yw").filter(has_text="发布"),
        page.locator("button.primary-cECiOJ.fixed-J9O8Yw").filter(has_text="发布"),
    )


def publish_success_locators(page: Page) -> tuple[Locator, ...]:
    return (
        page.get_by_text("审核中", exact=False),
        page.get_by_text("作品管理", exact=False),
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
        page.locator("label").filter(has_text="内容为个人观点或见解"),
        page.get_by_text("内容为个人观点或见解", exact=False),
        page.locator("label").filter(has_text="内容为个人观点或臆测"),
        page.get_by_text("内容为个人观点或臆测", exact=False),
    )


def publish_editor_resume_locators(page: Page) -> tuple[Locator, ...]:
    return declaration_select_locators(page) + title_input_locators(page)


def ensure_publish_editor_ready(page: Page) -> None:
    if find_optional_locator(page, title_input_locators(page), timeout_ms=2_000):
        print("当前已在抖音图文发布页。")
        return

    if find_optional_locator(page, main_upload_input_locators(page), state="attached", timeout_ms=2_000):
        print("当前已在图文上传页，已检测到多图上传控件。")
        return

    click_locator(page, publish_button_locators(page), description="顶部发布按钮")
    click_locator(page, publish_graphic_menu_locators(page), description="发布图文菜单项")
    page.wait_for_load_state("domcontentloaded")

    if find_optional_locator(page, title_input_locators(page), timeout_ms=5_000):
        print("已进入图文发布页。")
        return

    if find_optional_locator(page, main_upload_input_locators(page), state="attached", timeout_ms=5_000):
        print("已进入图文上传页，已检测到多图上传控件。")
        return

    # 这里不要再点击“上传图文”入口，否则会弹出 Windows 原生文件对话框，
    # 直接阻塞后续的 set_input_files 自动上传链路。

    wait_for_locator(page, main_upload_input_locators(page), description="图文上传 input", state="attached", timeout_ms=30_000)
    print("图文上传页已就绪。")


def upload_publish_images(page: Page, assets: PublishAssets, settings: PublishSettings) -> None:
    upload_input = find_optional_locator(
        page,
        main_upload_input_locators(page),
        state="attached",
        timeout_ms=5_000,
    )
    if upload_input is None:
        if find_optional_locator(page, title_input_locators(page), timeout_ms=2_000):
            print("当前页面已存在标题输入框，视为图片已经上传到编辑页，跳过重复投喂。")
            return
        raise RuntimeError("等待 图文上传 input 超时。")

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

    wait_for_locator(page, cover_crop_modal_locators(page), description="封面裁剪弹窗", timeout_ms=30_000)
    page.wait_for_timeout(1_500)
    crop_modal_closed = False
    for attempt_index in range(3):
        click_locator_via_dom(
            page,
            cover_crop_confirm_locators(page),
            description="封面裁剪确定按钮",
            timeout_ms=30_000,
        )
        try:
            wait_for_locator(
                page,
                cover_crop_modal_locators(page),
                description="封面裁剪弹窗关闭",
                state="hidden",
                timeout_ms=4_000,
            )
            crop_modal_closed = True
            break
        except RuntimeError:
            if attempt_index == 2:
                break
            page.wait_for_timeout(1_500)

    if not crop_modal_closed:
        raise RuntimeError("封面裁剪弹窗确认后仍未关闭。")

    click_locator_via_dom(page, cover_save_confirm_locators(page), description="上传封面确认按钮", timeout_ms=30_000)
    page.wait_for_timeout(1_500)

    click_locator_via_dom(page, select_cover_tab_locators(page), description="选择封面标签", timeout_ms=30_000)
    page.wait_for_timeout(1_000)

    editor_resumed = False
    for attempt_index in range(3):
        click_locator_via_dom(page, cover_apply_confirm_locators(page), description="封面应用确认按钮", timeout_ms=30_000)
        if find_optional_locator(page, publish_editor_resume_locators(page), timeout_ms=4_000):
            editor_resumed = True
            break
        if attempt_index == 2:
            break
        page.wait_for_timeout(1_500)

    if not editor_resumed:
        raise RuntimeError("封面应用确认后，主编辑区仍未恢复。")

    page.wait_for_timeout(settings.after_cover_confirm_wait_ms)


def submit_declaration(page: Page, settings: PublishSettings) -> None:
    click_locator(page, declaration_select_locators(page), description="自主声明下拉框", timeout_ms=30_000)
    page.wait_for_timeout(settings.after_declaration_open_wait_ms)
    click_locator(page, personal_view_radio_locators(page), description="个人观点声明选项", timeout_ms=10_000)
    click_locator(page, confirm_button_locators(page), description="自主声明确认按钮", timeout_ms=30_000)


def submit_final_publish(page: Page) -> None:
    click_locator(page, final_publish_button_locators(page), description="最终发布按钮", timeout_ms=30_000)
    try:
        page.wait_for_url("**/content/manage**", timeout=30_000)
    except PlaywrightTimeoutError:
        wait_for_locator(page, publish_success_locators(page), description="发布完成结果", timeout_ms=30_000)
    print("已进入作品管理页，发布流程已提交。")


def wait_for_manual_publish_ready(page: Page) -> None:
    wait_for_locator(page, final_publish_button_locators(page), description="最终发布按钮", timeout_ms=30_000)
    print("已完成自动填充与声明处理，当前停在待人工发布状态，请人工审核后手动点击发布。")


def run_publish(settings: PublishSettings, assets: PublishAssets) -> None:
    ensure_cdp_browser_available(settings)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        except Exception as exc:
            error_text = str(exc)
            if "ECONNREFUSED" in error_text or "connect_over_cdp" in error_text:
                raise RuntimeError(
                    "无法连接到 Chrome 远程调试端口。当前错误与 publish 图片无关，脚本已经识别到 "
                    f"{len(assets.image_paths)} 张图文图片和 1 张封面图。\n"
                    f"当前连接地址：{settings.cdp_url}\n"
                    "普通已打开的 Chrome 进程默认不暴露可附着的标签页控制接口，"
                    "所以 Playwright 不能直接接管它。脚本已优先尝试自动拉起独立的可接管 Chrome；"
                    "若仍失败，请检查该自动化 Chrome 是否被安全软件拦截，或改用 --chrome-path 显式指定 Chrome.exe。"
                ) from exc
            raise

        page = find_target_page(browser, settings.url_keyword)

        if is_login_required_page(page):
            raise RuntimeError(
                "当前自动化 Chrome 打开的抖音创作者页还没有登录，所以脚本还不能继续点发布入口。\n"
                "这不是 publish 图片问题，也不是标签页没匹配到；当前页面已经锁定成功，但它属于独立的自动化 Chrome 资料目录。\n"
                "请先在这个自动化 Chrome 窗口里完成一次抖音登录，然后重新运行脚本。"
            )

        try:
            ensure_publish_editor_ready(page)
            upload_publish_images(page, assets, settings)
            input_publish_title(page, assets, settings)
            input_publish_description(page, assets, settings)
            upload_cover(page, assets, settings)
            submit_declaration(page, settings)
            if settings.auto_submit_publish:
                submit_final_publish(page)
            else:
                wait_for_manual_publish_ready(page)
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