from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from playwright.sync_api import Page


def read_image_bgr(image_path: Path) -> np.ndarray:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"无法读取模板图片：{image_path}")
    return image


def capture_page_bgr(page: Page) -> np.ndarray:
    screenshot_bytes = page.screenshot()
    decoded = cv2.imdecode(np.frombuffer(screenshot_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise RuntimeError("无法解码页面截图。")
    return decoded


def match_template_on_bgr(
    screen_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    threshold: float,
) -> tuple[bool, float]:
    if screen_bgr.size == 0 or template_bgr.size == 0:
        return False, 0.0
    if template_bgr.shape[0] > screen_bgr.shape[0] or template_bgr.shape[1] > screen_bgr.shape[1]:
        return False, 0.0

    result = cv2.matchTemplate(screen_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, _max_loc = cv2.minMaxLoc(result)
    return max_val >= threshold, float(max_val)


def locate_template_center_on_bgr(
    screen_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    threshold: float,
) -> tuple[bool, float, tuple[int, int] | None]:
    matched, score = match_template_on_bgr(screen_bgr, template_bgr, threshold=threshold)
    if not matched:
        return False, score, None

    result = cv2.matchTemplate(screen_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _min_val, _max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    template_h, template_w = template_bgr.shape[:2]
    center_x = int(max_loc[0] + template_w / 2)
    center_y = int(max_loc[1] + template_h / 2)
    return True, float(score), (center_x, center_y)


def match_template_on_page(page: Page, template_path: Path, *, threshold: float = 0.9) -> tuple[bool, float]:
    template_bgr = read_image_bgr(template_path)
    screen_bgr = capture_page_bgr(page)
    matched, score, _center = locate_template_center_on_bgr(screen_bgr, template_bgr, threshold=threshold)
    return matched, score


def click_template_on_page(
    page: Page,
    template_path: Path,
    *,
    threshold: float = 0.9,
    description: str = "",
) -> tuple[bool, float]:
    if not template_path.exists():
        raise RuntimeError(f"模板图片不存在：{template_path}")

    page.bring_to_front()
    page.wait_for_timeout(300)
    template_bgr = read_image_bgr(template_path)
    screen_bgr = capture_page_bgr(page)
    matched, score, center = locate_template_center_on_bgr(screen_bgr, template_bgr, threshold=threshold)
    if not matched or center is None:
        label = description or template_path.name
        raise RuntimeError(f"未在页面中匹配到「{label}」（阈值 {threshold}，最高匹配度 {score:.3f}）。")

    page.mouse.click(center[0], center[1])
    label = description or template_path.name
    print(f"已点击模板「{label}」（匹配度 {score:.3f}，坐标 {center[0]}, {center[1]}）。")
    return True, score
