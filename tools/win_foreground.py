from __future__ import annotations

import ctypes
import time
from typing import Sequence

from playwright.sync_api import Page

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_chrome_window_hwnd(*, title_hints: Sequence[str] = ()) -> int | None:
    candidates: list[tuple[int, int, str]] = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if _window_class(hwnd) != "Chrome_WidgetWin_1":
            return True
        title = _window_text(hwnd)
        if not title:
            return True

        score = 0
        normalized_hints = [hint.strip() for hint in title_hints if hint and hint.strip()]
        if normalized_hints:
            for hint in normalized_hints:
                if hint in title:
                    score += 100 + len(hint)
                elif hint.lower() in title.lower():
                    score += 50 + len(hint)
            if score == 0:
                return True
        else:
            score = 1

        candidates.append((score, int(hwnd), title))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def is_window_maximized(hwnd: int) -> bool:
    return bool(user32.IsZoomed(hwnd))


def maximize_window(hwnd: int) -> None:
    sw_maximize = 3
    if is_window_maximized(hwnd):
        return
    user32.ShowWindow(hwnd, sw_maximize)
    time.sleep(0.4)


def maximize_page_window_via_cdp(page: Page) -> bool:
    try:
        client = page.context.new_cdp_session(page)
        target_info = client.send("Target.getTargetInfo")
        target_id = target_info.get("targetInfo", {}).get("targetId")
        if not target_id:
            return False
        window_info = client.send("Browser.getWindowForTarget", {"targetId": target_id})
        window_id = window_info.get("windowId")
        if window_id is None:
            return False
        client.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "maximized"}},
        )
        return True
    except Exception:
        return False


def ensure_chrome_maximized(page: Page, hwnd: int) -> bool:
    """CDP + Win32 + Win+↑ 多重兜底，确认 Chrome 已最大化。"""
    maximize_page_window_via_cdp(page)
    page.wait_for_timeout(350)
    if not is_window_maximized(hwnd):
        maximize_window(hwnd)
    if not is_window_maximized(hwnd):
        force_foreground_window(hwnd)
        try:
            import pyautogui

            pyautogui.hotkey("win", "up")
            time.sleep(0.45)
        except Exception:
            pass
    maximized = is_window_maximized(hwnd)
    if maximized:
        print("Chrome 窗口已最大化。")
    else:
        print("警告：Chrome 窗口未能确认最大化，模板/坐标匹配可能失败。")
    return maximized


def force_foreground_window(hwnd: int) -> None:
    sw_restore = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, sw_restore)
    if user32.GetForegroundWindow() == hwnd:
        return

    foreground = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(foreground, None)
    cur_tid = kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def activate_chrome_window_for_page(
    page: Page,
    *,
    extra_hints: Sequence[str] = (),
    maximize: bool = True,
) -> bool:
    """Playwright 切 tab + Win32 将 Chrome 宿主窗口置顶；maximize 时全屏以匹配 pyautogui 标定坐标。"""
    page.bring_to_front()
    page.wait_for_timeout(200)

    hints: list[str] = []
    try:
        title = (page.title() or "").strip()
        if title:
            hints.append(title)
            if len(title) > 10:
                hints.append(title[:10])
    except Exception:
        pass
    for item in extra_hints:
        text = str(item).strip()
        if text and text not in hints:
            hints.append(text)
    for fallback in ("微信公众平台", "微信", "mp.weixin"):
        if fallback not in hints:
            hints.append(fallback)

    hwnd = find_chrome_window_hwnd(title_hints=hints)
    if hwnd is None:
        hwnd = find_chrome_window_hwnd(title_hints=())
    if hwnd is None:
        print("未能定位 Chrome 窗口，pyautogui 可能落在其它窗口。")
        return False

    force_foreground_window(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        try:
            import pyautogui

            pyautogui.press("alt")
            time.sleep(0.05)
            force_foreground_window(hwnd)
        except Exception:
            pass

    if maximize:
        ensure_chrome_maximized(page, hwnd)

    time.sleep(0.2)
    print(f"已激活 Chrome 窗口至最前端：{_window_text(hwnd)}")
    return True
