from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from custom_image_service import (
    cancel_custom_image_work,
    custom_image_status_snapshot,
    handle_custom_image_generate,
    init_custom_image_service,
    is_custom_image_path,
)
from mode2_flow import (
    run_anchor_poster_regenerate,
    run_publish_image_replace,
    run_supplement_for_output_dir,
    run_v2_mode2,
)
from publish_final_assets import resolve_publish_final_dir
from idea_batch import run_idea_batch, DISH_POOL_DIR
from image_gen_profile import (
    apply_image_gen_controls,
    image_gen_options_for_frontend,
    resolve_image_size,
)
from image_gen_profile import (
    apply_image_gen_controls,
    image_gen_options_for_frontend,
    resolve_image_size,
)
from v2_core import (
    IDEA_FILE,
    OUTPUT_DIR,
    archive_dish_folder,
    build_openai_image_client,
    build_run_output_dir,
    clear_work_cancel,
    ensure_runtime_config_loaded,
    generate_images_by_prompt,
    get_cover_image_count,
    get_image_settings,
    get_timestamp,
    load_dish_idea_record_from_dir,
    raise_if_work_cancelled,
    request_work_cancel,
    save_generated_images,
    save_text_output,
)


def _panel_port() -> int:
    raw = os.getenv("V2_PANEL_PORT", "8765").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 8765


HOST = os.getenv("V2_PANEL_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _panel_port()
PANEL_VERSION = "v1.42"
PANEL_IMAGE_GEN_PREFS: dict[str, str] = {
    "image_provider": "official",
    "image_aspect_ratio": "2:3",
    "image_resolution_tier": "1k",
    "image_quality": "high",
}
DISH_ARCHIVE_DIR = ROOT_DIR / "dish_archive"
FAVORITES_FILE = ROOT_DIR / "dish_favorites.json"
DISH_MEAL_TAGS_FILE = ROOT_DIR / "dish_meal_tags.json"
PUBLISH_PLAN_FILE = ROOT_DIR / "publish_plan.json"
VALID_MEAL_TAGS = ("breakfast", "lunch", "dinner", "late_night")
MEAL_TAG_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "late_night": "夜宵",
}
PLAN_SLOT_KEYS = ("morning", "noon", "evening")
PLAN_SLOT_LABELS = {"morning": "早", "noon": "中", "evening": "晚"}
VALID_HISTORY_SORTS = {"favorite", "created_desc", "created_asc", "name", "image_first"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SILENT_HTTP_LOG_PATHS = {
    "/api/run_status",
    "/api/publish_status",
    "/api/custom_image/status",
    "/api/file",
}
REPO_ROOT = ROOT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.project_python import reexec_in_project_venv_if_needed, resolve_project_python

PUBLISH_PLATFORMS: dict[str, dict[str, str]] = {
    "douyin": {"label": "抖音", "script": "douyin_publish_v2_test.py"},
    "kuaishou": {"label": "快手", "script": "kuaishou_publish_v2_test.py"},
    "xiaohongshu": {"label": "小红书", "script": "xiaohongshu_publish_v2_test.py"},
    "weixin_mp": {"label": "微信公众号", "script": "weixin_publish_v2_test.py"},
    "weixin_channels": {"label": "微信视频号", "script": "weixin_channels_publish_v2_test.py"},
}
LOGIN_WAIT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("登录完成后请输入 y 继续", "y"),
    ("完成扫码登录后按回车继续", "enter"),
    ("按回车继续", "enter"),
)

RUN_LOCK = threading.RLock()
RUNNING = False
LAST_RESULT: dict[str, Any] | None = None
LAST_ERROR = ""
LAST_STARTED_AT = 0.0
LAST_FINISHED_AT = 0.0
RUN_LOG_LINES: list[str] = []
MAX_LOG_LINES = 8000
TASK_QUEUE: list[dict[str, Any]] = []
CURRENT_TASK: dict[str, Any] | None = None
TASK_SEQ = 0
RUN_META_FILE_NAME = "_run_meta.json"
RUN_LOG_FILE_NAME = "_run_log.txt"
PUBLISH_LOG_FILE_NAME = "_publish_log.txt"

PUBLISH_LOCK = threading.Lock()
PUBLISH_RUNNING = False
PUBLISH_OUTPUT_DIR = ""
PUBLISH_LOG_LINES: list[str] = []
PUBLISH_ERROR = ""
PUBLISH_CURRENT_PLATFORM = ""
PUBLISH_QUEUE: list[str] = []
PUBLISH_LOGIN_REQUIRED = False
PUBLISH_LOGIN_MESSAGE = ""
PUBLISH_LOGIN_CONFIRM_KIND = "enter"
PUBLISH_LOGIN_EVENT: threading.Event | None = None
MAX_PUBLISH_LOG_LINES = 2000
PANEL_SERVER: ThreadingHTTPServer | None = None
ACTIVE_PUBLISH_PROCS: list[subprocess.Popen] = []


def append_run_log(text: str) -> None:
    line = text.rstrip("\r\n")
    if not line:
        return
    with RUN_LOCK:
        maybe_update_live_task_from_log(line)
        RUN_LOG_LINES.append(line)
        if len(RUN_LOG_LINES) > MAX_LOG_LINES:
            del RUN_LOG_LINES[: len(RUN_LOG_LINES) - MAX_LOG_LINES]


def maybe_update_live_task_from_log(line: str) -> None:
    """从运行日志回填当前任务的 live 目录与菜名，供任务条跳转使用。"""
    global CURRENT_TASK
    if CURRENT_TASK is None:
        return
    output_marker = "输出目录："
    if output_marker in line:
        path_text = line.split(output_marker, 1)[1].strip()
        if path_text:
            CURRENT_TASK["live_output_dir"] = path_text
            if not str(CURRENT_TASK.get("dish_name", "")).strip():
                CURRENT_TASK["dish_name"] = infer_dish_name_from_folder(Path(path_text).name)
        return
    target_marker = "指定造菜：复用已有目录 "
    if target_marker in line:
        path_text = line.split(target_marker, 1)[1].strip()
        if path_text:
            CURRENT_TASK["live_output_dir"] = path_text
            if not str(CURRENT_TASK.get("dish_name", "")).strip():
                CURRENT_TASK["dish_name"] = infer_dish_name_from_folder(Path(path_text).name)


def append_publish_log(text: str) -> None:
    line = text.rstrip("\r\n")
    if not line:
        return
    with PUBLISH_LOCK:
        PUBLISH_LOG_LINES.append(line)
        if len(PUBLISH_LOG_LINES) > MAX_PUBLISH_LOG_LINES:
            del PUBLISH_LOG_LINES[: len(PUBLISH_LOG_LINES) - MAX_PUBLISH_LOG_LINES]


def load_dish_favorites() -> dict[str, str]:
    if not FAVORITES_FILE.exists():
        return {}
    try:
        payload = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    favorites = payload.get("paths")
    if not isinstance(favorites, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in favorites.items() if str(key).strip()}


def save_dish_favorites(favorites: dict[str, str]) -> None:
    FAVORITES_FILE.write_text(
        json.dumps({"paths": favorites}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def remove_dish_favorite(path_text: str) -> None:
    path_key = str(Path(path_text).resolve())
    favorites = load_dish_favorites()
    if path_key not in favorites:
        return
    favorites.pop(path_key, None)
    save_dish_favorites(favorites)


def toggle_dish_favorite(raw_path: str) -> dict[str, Any]:
    folder = resolve_output_path(raw_path)
    path_key = str(folder.resolve())
    favorites = load_dish_favorites()
    if path_key in favorites:
        favorites.pop(path_key, None)
        favorited = False
        favorited_at = ""
    else:
        favorited_at = time.strftime("%Y-%m-%d %H:%M:%S")
        favorites[path_key] = favorited_at
        favorited = True
    save_dish_favorites(favorites)
    return {
        "path": path_key,
        "favorited": favorited,
        "favorited_at": favorited_at,
    }


def normalize_meal_tags(raw_tags: Any) -> list[str]:
    if not isinstance(raw_tags, list):
        return []
    normalized: list[str] = []
    for item in raw_tags:
        tag = str(item or "").strip()
        if tag in VALID_MEAL_TAGS and tag not in normalized:
            normalized.append(tag)
    return normalized


def load_dish_meal_tags() -> dict[str, list[str]]:
    if not DISH_MEAL_TAGS_FILE.exists():
        return {}
    try:
        payload = json.loads(DISH_MEAL_TAGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, value in paths.items():
        path_key = str(key).strip()
        if not path_key:
            continue
        tags = normalize_meal_tags(value)
        if tags:
            result[path_key] = tags
    return result


def save_dish_meal_tags(tags_map: dict[str, list[str]]) -> None:
    cleaned: dict[str, list[str]] = {}
    for key, value in tags_map.items():
        path_key = str(key).strip()
        tags = normalize_meal_tags(value)
        if path_key and tags:
            cleaned[path_key] = tags
    DISH_MEAL_TAGS_FILE.write_text(
        json.dumps({"paths": cleaned}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_dish_meal_tags(raw_path: str, raw_tags: Any) -> dict[str, Any]:
    folder = resolve_output_path(raw_path)
    path_key = str(folder.resolve())
    tags_map = load_dish_meal_tags()
    tags = normalize_meal_tags(raw_tags)
    if tags:
        tags_map[path_key] = tags
    else:
        tags_map.pop(path_key, None)
    save_dish_meal_tags(tags_map)
    return {"path": path_key, "meal_tags": tags}


def batch_update_dish_meal_tags(
    raw_paths: list[str],
    *,
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
) -> dict[str, Any]:
    tags_map = load_dish_meal_tags()
    add_list = normalize_meal_tags(add_tags or [])
    remove_set = set(normalize_meal_tags(remove_tags or []))
    updated: list[str] = []
    for raw_path in raw_paths:
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        folder = resolve_output_path(path_text)
        path_key = str(folder.resolve())
        current = set(tags_map.get(path_key, []))
        current.update(add_list)
        current -= remove_set
        tags = normalize_meal_tags(list(current))
        if tags:
            tags_map[path_key] = tags
        else:
            tags_map.pop(path_key, None)
        updated.append(path_key)
    save_dish_meal_tags(tags_map)
    return {"updated": updated, "count": len(updated)}


def remove_dish_meal_tags(path_text: str) -> None:
    path_key = str(Path(path_text).resolve())
    tags_map = load_dish_meal_tags()
    if path_key in tags_map:
        tags_map.pop(path_key, None)
        save_dish_meal_tags(tags_map)


def default_publish_plan_range() -> tuple[str, str]:
    today = time.strftime("%Y-%m-%d")
    end = time.strftime("%Y-%m-%d", time.localtime(time.time() + 6 * 86400))
    return today, end


def normalize_published_dates(raw_dates: Any) -> list[str]:
    if not isinstance(raw_dates, list):
        return []
    dates: list[str] = []
    seen: set[str] = set()
    for raw_date in raw_dates:
        date_text = str(raw_date).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            continue
        if date_text in seen:
            continue
        seen.add(date_text)
        dates.append(date_text)
    return sorted(dates)


def normalize_publish_plan_slots(raw_slots: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw_slots, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for date_key, slot_map in raw_slots.items():
        date_text = str(date_key).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            continue
        if not isinstance(slot_map, dict):
            continue
        normalized_slots: dict[str, str] = {}
        for slot_key in PLAN_SLOT_KEYS:
            path_text = str(slot_map.get(slot_key, "")).strip()
            if not path_text:
                continue
            try:
                normalized_slots[slot_key] = str(resolve_output_path(path_text).resolve())
            except Exception:
                normalized_slots[slot_key] = path_text
        if normalized_slots:
            result[date_text] = normalized_slots
    return result


def load_publish_plan() -> dict[str, Any]:
    if not PUBLISH_PLAN_FILE.exists():
        start, end = default_publish_plan_range()
        return {"start": start, "end": end, "slots": {}, "published_dates": []}
    try:
        payload = json.loads(PUBLISH_PLAN_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    start = str(payload.get("start", "")).strip()
    end = str(payload.get("end", "")).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        start, end = default_publish_plan_range()
    if start > end:
        start, end = end, start
    return {
        "start": start,
        "end": end,
        "slots": normalize_publish_plan_slots(payload.get("slots")),
        "published_dates": normalize_published_dates(payload.get("published_dates")),
    }


def save_publish_plan(plan: dict[str, Any]) -> dict[str, Any]:
    start = str(plan.get("start", "")).strip()
    end = str(plan.get("end", "")).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        raise ValueError("日期范围格式无效。")
    if start > end:
        start, end = end, start
    slots = normalize_publish_plan_slots(plan.get("slots"))
    published_dates = normalize_published_dates(plan.get("published_dates"))
    payload = {"start": start, "end": end, "slots": slots, "published_dates": published_dates}
    PUBLISH_PLAN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def remove_path_from_publish_plan(path_text: str) -> None:
    path_key = str(Path(path_text).resolve())
    plan = load_publish_plan()
    slots = plan.get("slots", {})
    changed = False
    for date_key, slot_map in list(slots.items()):
        for slot_key in PLAN_SLOT_KEYS:
            if slot_map.get(slot_key) == path_key:
                slot_map.pop(slot_key, None)
                changed = True
        if not slot_map:
            slots.pop(date_key, None)
    if changed:
        save_publish_plan({**plan, "slots": slots})


def history_revision() -> str:
    dirs = collect_history_dirs()
    tag_mtime = DISH_MEAL_TAGS_FILE.stat().st_mtime if DISH_MEAL_TAGS_FILE.exists() else 0.0
    plan_mtime = PUBLISH_PLAN_FILE.stat().st_mtime if PUBLISH_PLAN_FILE.exists() else 0.0
    if not dirs:
        return f"0:0:{tag_mtime:.3f}:{plan_mtime:.3f}"
    max_mtime = max(path.stat().st_mtime for path in dirs)
    return f"{len(dirs)}:{max_mtime:.3f}:{tag_mtime:.3f}:{plan_mtime:.3f}"


def publish_status_snapshot(*, log_from: int = 0) -> dict[str, Any]:
    with PUBLISH_LOCK:
        total_logs = len(PUBLISH_LOG_LINES)
        logs = PUBLISH_LOG_LINES[log_from:] if log_from < total_logs else []
        return {
            "running": PUBLISH_RUNNING,
            "output_dir": PUBLISH_OUTPUT_DIR,
            "current_platform": PUBLISH_CURRENT_PLATFORM,
            "queue": list(PUBLISH_QUEUE),
            "error": PUBLISH_ERROR,
            "login_required": PUBLISH_LOGIN_REQUIRED,
            "login_message": PUBLISH_LOGIN_MESSAGE,
            "logs": logs,
            "next_log_index": total_logs,
            "platforms": {key: item["label"] for key, item in PUBLISH_PLATFORMS.items()},
        }


def _wait_for_publish_login(line: str, platform_label: str) -> str | None:
    global PUBLISH_LOGIN_REQUIRED, PUBLISH_LOGIN_MESSAGE, PUBLISH_LOGIN_CONFIRM_KIND, PUBLISH_LOGIN_EVENT
    for pattern, confirm_kind in LOGIN_WAIT_PATTERNS:
        if pattern not in line:
            continue
        with PUBLISH_LOCK:
            PUBLISH_LOGIN_REQUIRED = True
            PUBLISH_LOGIN_MESSAGE = (
                f"请在浏览器中完成「{platform_label}」登录，完成后点击下方「已完成登录，继续」。"
            )
            PUBLISH_LOGIN_CONFIRM_KIND = confirm_kind
            if PUBLISH_LOGIN_EVENT is None:
                PUBLISH_LOGIN_EVENT = threading.Event()
            PUBLISH_LOGIN_EVENT.clear()
        append_publish_log(f"[面板] 等待手动登录：{platform_label}")
        if PUBLISH_LOGIN_EVENT is not None:
            PUBLISH_LOGIN_EVENT.wait(timeout=3600)
        with PUBLISH_LOCK:
            PUBLISH_LOGIN_REQUIRED = False
        return confirm_kind
    return None


def run_single_platform_publish(platform_key: str, output_dir: Path) -> None:
    platform = PUBLISH_PLATFORMS[platform_key]
    script_path = ROOT_DIR / platform["script"]
    if not script_path.exists():
        raise FileNotFoundError(f"发布脚本不存在：{script_path}")
    append_publish_log(f"===== 开始发布：{platform['label']} =====")
    publish_env = os.environ.copy()
    publish_env["PYTHONUNBUFFERED"] = "1"
    publish_env["PYTHONIOENCODING"] = "utf-8"
    publish_env["PYTHONUTF8"] = "1"
    python_exe = resolve_project_python()
    proc = subprocess.Popen(
        [python_exe, str(script_path), str(output_dir)],
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=publish_env,
    )
    ACTIVE_PUBLISH_PROCS.append(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            append_publish_log(line)
            confirm_kind = _wait_for_publish_login(line, platform["label"])
            if confirm_kind and proc.stdin is not None:
                proc.stdin.write("y\n" if confirm_kind == "y" else "\n")
                proc.stdin.flush()
        return_code = proc.wait()
    finally:
        if proc in ACTIVE_PUBLISH_PROCS:
            ACTIVE_PUBLISH_PROCS.remove(proc)
    if return_code != 0:
        raise RuntimeError(f"{platform['label']} 发布脚本退出码 {return_code}，请查看日志。")
    append_publish_log(f"===== 完成发布：{platform['label']} =====")


def publish_worker(output_dir_text: str, platform_keys: list[str]) -> None:
    global PUBLISH_RUNNING, PUBLISH_ERROR, PUBLISH_OUTPUT_DIR, PUBLISH_CURRENT_PLATFORM, PUBLISH_QUEUE
    try:
        output_dir = resolve_output_path(output_dir_text)
        try:
            final_dir = resolve_publish_final_dir(output_dir)
        except RuntimeError as exc:
            raise FileNotFoundError(str(exc)) from exc
        append_publish_log(f"发布图目录：{final_dir}")
        with PUBLISH_LOCK:
            PUBLISH_OUTPUT_DIR = str(output_dir)
            PUBLISH_QUEUE = list(platform_keys)
        failed_platforms: list[str] = []
        for platform_key in platform_keys:
            with PUBLISH_LOCK:
                PUBLISH_CURRENT_PLATFORM = platform_key
                if platform_key in PUBLISH_QUEUE:
                    PUBLISH_QUEUE.remove(platform_key)
            platform_label = PUBLISH_PLATFORMS[platform_key]["label"]
            try:
                run_single_platform_publish(platform_key, output_dir)
            except Exception as exc:  # noqa: BLE001
                failed_platforms.append(platform_label)
                append_publish_log(f"===== {platform_label} 发布失败，继续下一平台 =====")
                append_publish_log(str(exc))
                append_publish_log(traceback.format_exc())
        if failed_platforms:
            summary = f"部分平台发布失败：{', '.join(failed_platforms)}"
            with PUBLISH_LOCK:
                PUBLISH_ERROR = summary
            append_publish_log(f"{summary}。其余平台已继续执行，请在各平台浏览器中核对。")
        else:
            append_publish_log("全部所选平台发布流程已执行完成，请在各平台浏览器中核对。")
        publish_log_file = write_publish_log_file(PUBLISH_OUTPUT_DIR, list(PUBLISH_LOG_LINES))
        if publish_log_file:
            append_publish_log(f"发布日志已保存：{publish_log_file}")
    except Exception as exc:  # noqa: BLE001
        with PUBLISH_LOCK:
            PUBLISH_ERROR = str(exc)
        append_publish_log(f"发布任务中断：{exc}")
        append_publish_log(traceback.format_exc())
        publish_log_dir = PUBLISH_OUTPUT_DIR or output_dir_text
        publish_log_file = write_publish_log_file(publish_log_dir, list(PUBLISH_LOG_LINES))
        if publish_log_file:
            append_publish_log(f"发布日志已保存：{publish_log_file}")
    finally:
        with PUBLISH_LOCK:
            PUBLISH_RUNNING = False
            PUBLISH_CURRENT_PLATFORM = ""
            PUBLISH_QUEUE = []


def start_publish_task(output_dir_text: str, platform_keys: list[str]) -> None:
    global PUBLISH_RUNNING, PUBLISH_ERROR, PUBLISH_LOG_LINES, PUBLISH_LOGIN_EVENT
    if not platform_keys:
        raise ValueError("请至少选择一个发布平台。")
    unknown = [key for key in platform_keys if key not in PUBLISH_PLATFORMS]
    if unknown:
        raise ValueError(f"未知平台：{', '.join(unknown)}")
    with PUBLISH_LOCK:
        if PUBLISH_RUNNING:
            raise RuntimeError("已有发布任务在执行，请等待完成后再试。")
        PUBLISH_RUNNING = True
        PUBLISH_ERROR = ""
        PUBLISH_LOG_LINES = []
        PUBLISH_LOGIN_EVENT = threading.Event()
    worker = threading.Thread(target=publish_worker, args=(output_dir_text, platform_keys), daemon=True)
    worker.start()


def confirm_publish_login() -> None:
    with PUBLISH_LOCK:
        if not PUBLISH_LOGIN_REQUIRED:
            return
        event = PUBLISH_LOGIN_EVENT
    if event is not None:
        event.set()


def _kill_subprocess(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:  # noqa: BLE001
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def force_stop_background_work() -> None:
    global RUNNING, CURRENT_TASK, PUBLISH_RUNNING, PUBLISH_LOGIN_REQUIRED
    with RUN_LOCK:
        TASK_QUEUE.clear()
        RUNNING = False
        CURRENT_TASK = None
    with PUBLISH_LOCK:
        PUBLISH_RUNNING = False
        PUBLISH_LOGIN_REQUIRED = False
        login_event = PUBLISH_LOGIN_EVENT
    if login_event is not None:
        login_event.set()
    for proc in list(ACTIVE_PUBLISH_PROCS):
        _kill_subprocess(proc)


def stop_background_work(*, reason: str = "用户请求停止后台任务") -> dict[str, Any]:
    with RUN_LOCK:
        queue_len = len(TASK_QUEUE)
        task_busy = RUNNING or queue_len > 0
    with PUBLISH_LOCK:
        publish_busy = PUBLISH_RUNNING
    request_work_cancel()
    cancel_custom_image_work()
    force_stop_background_work()
    append_run_log(
        f"[{time.strftime('%H:%M:%S')}] {reason}："
        f"已清空 {queue_len} 个排队任务，并请求中断当前脚本（面板保持运行）。"
    )
    return {
        "ok": True,
        "message": "后台任务停止请求已发送，进行中的脚本将在下一检查点中断。",
        "was_busy": task_busy or publish_busy,
        "cleared_queue": queue_len,
    }


def _spawn_panel_restart_script() -> None:
    bat_path = REPO_ROOT / "restart_v2_web_panel.bat"
    if bat_path.is_file():
        subprocess.Popen(
            ["cmd", "/c", "start", "/min", "", str(bat_path)],
            cwd=str(REPO_ROOT),
        )
        return
    python_exe = resolve_project_python()
    script_path = ROOT_DIR / "web_panel.py"
    subprocess.Popen(
        [python_exe, str(script_path)],
        cwd=str(REPO_ROOT),
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )


def schedule_panel_shutdown(*, restart: bool = False) -> None:
    request_work_cancel()
    cancel_custom_image_work()
    force_stop_background_work()
    append_run_log(f"[{time.strftime('%H:%M:%S')}] 面板{'重启' if restart else '终止'}请求已收到。")

    def _finish() -> None:
        time.sleep(0.35)
        if restart:
            try:
                _spawn_panel_restart_script()
            except Exception as exc:  # noqa: BLE001
                append_run_log(f"[{time.strftime('%H:%M:%S')}] 启动重启脚本失败：{exc}")
        server = PANEL_SERVER
        if server is not None:
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                pass
        os._exit(0)

    threading.Thread(target=_finish, daemon=True).start()


class LiveLogWriter:
    def __init__(self) -> None:
        self._pending = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        merged = self._pending + text
        parts = merged.splitlines(keepends=True)
        self._pending = ""
        for part in parts:
            if part.endswith("\n") or part.endswith("\r"):
                append_run_log(part)
            else:
                self._pending = part
        return len(text)

    def flush(self) -> None:
        if self._pending:
            append_run_log(self._pending)
            self._pending = ""


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>造菜控制台</title>
  <link rel="icon" type="image/png" href="/favicon.png" />
  <link rel="shortcut icon" type="image/png" href="/favicon.ico" />
  <style>
    :root{
      --bg:#0d1117; --panel:#121a27; --panel-soft:#1a2435; --line:#2d3a52; --text:#e6edf7; --sub:#9aa7bd;
      --pri:#020617; --ok:#22c55e; --warn:#f59e0b; --danger:#ef4444;
      --shadow:0 10px 28px rgba(0,0,0,.35);
      --col-left:720px;
      --col-plan:200px;
      --col-gallery:720px;
      --col-mid:400px;
      --col-publish:118px;
      --col-custom:228px;
      --splitter:8px;
    }
    *{box-sizing:border-box}
    .hidden{display:none!important}
    body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:radial-gradient(1200px 600px at 10% -10%, #1b2438 0%, transparent 60%),var(--bg);color:var(--text)}
    .wrap{max-width:100%;margin:0 auto;padding:6px 10px 84px}
    .top{
      position:sticky;top:6px;z-index:30;display:flex;align-items:center;gap:8px;margin-bottom:6px;
      background:rgba(16,24,39,.92);border:1px solid var(--line);border-radius:10px;padding:5px 10px;box-shadow:var(--shadow);backdrop-filter:blur(4px);
      flex-wrap:nowrap;overflow:visible;min-height:40px;
    }
    .top-brand{display:flex;align-items:center;gap:6px;flex-shrink:0}
    .title{font-size:15px;font-weight:700;line-height:1;white-space:nowrap}
    .chips{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .chip{font-size:12px;background:#0b1220;border:1px solid #334155;padding:6px 10px;border-radius:999px;color:#dbe7ff}
    .version-tag{font-size:11px;color:#e2e8f0;background:#1e293b;border:1px solid #475569;border-radius:999px;padding:2px 7px;line-height:1.4}
    .offline-tag{font-size:11px;color:#fecaca;background:#450a0a;border:1px solid #7f1d1d;border-radius:999px;padding:2px 7px;white-space:nowrap}
    .top-toolbar{display:flex;align-items:center;gap:6px;flex:1;min-width:0;flex-wrap:nowrap;overflow-x:auto;overflow-y:visible}
    .top-seg{display:inline-flex;align-items:center;gap:2px;flex-shrink:0}
    .top-seg-btn,.top-actions button{
      padding:4px 8px;border:1px solid #334155;border-radius:6px;background:#0b1220;color:#dbe7ff;font-size:11px;cursor:pointer;white-space:nowrap;line-height:1.3;
    }
    .top-seg-btn.active{
      border-color:#22c55e!important;background:linear-gradient(180deg,#064e3b,#022c22)!important;color:#dcfce7!important;
      box-shadow:0 0 0 2px rgba(34,197,94,.22)!important;
    }
    .top-size-hint{font-size:10px;color:#64748b;white-space:nowrap;flex-shrink:0}
    .top-actions{display:flex;gap:4px;flex-shrink:0;margin-left:auto}
    .top-actions .danger-btn{border-color:#7f1d1d;color:#fecaca;background:#2b1313}
    .top-toolbar .top-seg-btn:disabled{opacity:.35;cursor:not-allowed}
    button{transition:background .15s ease,border-color .15s ease,box-shadow .15s ease,color .15s ease,transform .12s ease}
    button:hover{border-color:#60a5fa!important;box-shadow:0 0 0 2px rgba(96,165,250,.22)}
    button:active{transform:translateY(1px)}
    button.active,.view-tab.active,.text-tab.active,.text-file-btn.active{
      border-color:#22c55e!important;background:linear-gradient(180deg,#064e3b,#022c22)!important;color:#dcfce7!important;
      box-shadow:0 0 0 2px rgba(34,197,94,.22)!important;
    }
    .top-actions button{
      width:auto;
    }
    .layout-scroll{width:100%;overflow-x:auto;overflow-y:hidden;padding-bottom:6px}
    .four-col{
      display:grid;
      width:max-content;
      min-width:100%;
      grid-template-columns:var(--col-left) var(--splitter) var(--col-plan) var(--splitter) var(--col-gallery) var(--splitter) var(--col-mid) var(--splitter) var(--col-publish) var(--splitter) var(--col-custom);
      gap:0;
      align-items:start;
      min-height:calc(100vh - 88px);
    }
    .panel{
      min-width:0;
      background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;box-shadow:var(--shadow);
      height:calc(100vh - 56px);overflow:auto;
    }
    .panel-left{margin-right:6px}
    .panel-plan{margin:0 4px;padding:8px;display:flex;flex-direction:column;gap:6px;min-width:0}
    .panel-plan .panel-title{font-size:14px;margin:0 0 4px}
    .plan-range{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:6px}
    .plan-range input{padding:5px 6px;font-size:11px;border-radius:8px}
    .plan-list{display:flex;flex-direction:column;gap:6px;overflow:auto;flex:1;padding-right:2px}
    .plan-day{border:1px solid #283449;border-radius:8px;padding:6px;background:#0f172a}
    .plan-day.published{border-color:#166534;background:#0f1f17}
    .plan-day-header{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:4px}
    .plan-day-date{font-size:11px;color:#94a3b8;flex:1;min-width:0}
    .plan-day.published .plan-day-date{color:#86efac}
    .plan-day-done{display:flex;align-items:center;gap:4px;flex-shrink:0;cursor:pointer;user-select:none}
    .plan-day-done input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#38bdf8}
    .plan-day-done span{font-size:10px;color:#64748b;line-height:1}
    .plan-day.published .plan-day-done span{color:#86efac}
    .plan-day-slots{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
    .plan-slot{
      min-height:78px;border:1px dashed #334155;border-radius:6px;background:#0b1220;padding:3px;
      display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:2px;font-size:10px;color:#64748b;
    }
    .plan-slot.drag-over{border-color:#60a5fa;background:#172554}
    .plan-slot-label{font-size:9px;color:#64748b;text-align:center;line-height:1;flex:0 0 auto}
    .plan-slot-chip{
      display:flex;flex-direction:column;gap:2px;padding:2px;border-radius:6px;background:#1e293b;border:1px solid #475569;
      color:#e2e8f0;cursor:grab;min-width:0;flex:1;
    }
    .plan-slot-chip.selected{border-color:#22c55e;box-shadow:0 0 0 2px rgba(34,197,94,.28)}
    .plan-slot-chip:active{cursor:grabbing}
    .plan-slot-chip img{width:100%;height:42px;object-fit:cover;border-radius:4px;background:#111827}
    .plan-slot-chip-name{font-size:9px;line-height:1.25;word-break:break-all;text-align:center}
    .plan-slot-chip.empty-img .plan-slot-chip-name{margin-top:8px}
    .meal-filter-row{display:flex;flex-wrap:wrap;gap:5px;margin:0 0 8px}
    .meal-filter-btn{
      padding:4px 9px;font-size:11px;border-radius:999px;border:1px solid #334155;background:#0b1220;color:#cbd5e1;cursor:pointer;
    }
    .batch-edit-bar{
      display:none;flex-wrap:wrap;gap:5px;margin:0 0 8px;padding:6px;border:1px solid #334155;border-radius:8px;background:#0b1220;
    }
    .batch-edit-bar.open{display:flex}
    .batch-edit-bar button{padding:5px 8px;font-size:11px;border-radius:8px;border:1px solid #475569;background:#111827;color:#e2e8f0;cursor:pointer}
    .batch-edit-bar .danger-btn{border-color:#7f1d1d;color:#fecaca}
    .history-cover{position:relative;overflow:visible}
    .history-cover-media{width:100%;height:100%;overflow:hidden;border-radius:7px;background:#111827;display:flex;align-items:center;justify-content:center}
    .history-cover-media img,.history-cover img{-webkit-user-drag:none;user-select:none;pointer-events:none}
    .history-tag-add{
      position:absolute;top:3px;right:3px;z-index:14;width:18px;height:18px;border-radius:999px;border:1px solid #475569;
      background:rgba(15,23,42,.9);color:#e2e8f0;font-size:13px;line-height:16px;padding:0;cursor:pointer;
    }
    .history-tag-badges{display:flex;flex-wrap:wrap;gap:2px;margin-top:3px}
    .history-tag-badge{font-size:9px;padding:1px 4px;border-radius:999px;background:#1e3a5f;color:#bfdbfe;border:1px solid #334155}
    .tag-popover{
      position:absolute;top:22px;right:0;z-index:30;min-width:112px;padding:6px 8px;border:1px solid #475569;border-radius:8px;
      background:#111827;box-shadow:var(--shadow);display:none;
    }
    .history-grid.pool-compact .tag-popover{left:0;right:auto}
    .tag-popover.open{display:block}
    .tag-popover label{display:flex;align-items:center;gap:5px;margin:0 0 4px;font-size:11px;color:#e2e8f0;cursor:pointer}
    .tag-popover input{width:auto;margin:0}
    .history-item.draggable-pool{cursor:grab}
    .history-item.draggable-pool:active{cursor:grabbing}
    .panel-mid{margin:0 6px}
    .panel-right{margin:0 6px}
    .panel-publish{margin-left:4px;display:flex;flex-direction:column;gap:6px;min-width:0;padding:8px}
    .panel-custom{margin-left:4px;display:flex;flex-direction:column;gap:6px;min-width:0;padding:8px;overflow:hidden}
    .panel-publish .panel-title,.panel-custom .panel-title{font-size:14px;margin-bottom:4px;flex:0 0 auto}
    .panel-custom .sec-title{flex:0 0 auto}
    .custom-compose{display:flex;flex-direction:column;gap:8px;flex:0 0 auto}
    .custom-prompt{width:100%;min-height:72px;max-height:140px;resize:vertical;padding:8px;border:1px solid #334155;border-radius:10px;background:#0b1220;color:#e2e8f0;font-size:12px;line-height:1.45}
    .custom-refs{display:flex;flex-wrap:wrap;gap:6px;min-height:24px}
    .custom-ref-chip{position:relative;width:52px;height:52px;border-radius:8px;overflow:hidden;border:1px solid #334155;background:#0b1220;flex:0 0 auto}
    .custom-ref-chip img{width:100%;height:100%;object-fit:cover;display:block}
    .custom-ref-chip button{position:absolute;top:2px;right:2px;width:16px;height:16px;border:none;border-radius:999px;background:rgba(0,0,0,.7);color:#fff;font-size:11px;line-height:16px;padding:0;cursor:pointer}
    .custom-toolbar{display:flex;align-items:center;gap:8px}
    .custom-add-btn{width:36px;height:36px;border-radius:10px;border:1px solid #475569;background:#111827;color:#e2e8f0;font-size:22px;line-height:1;cursor:pointer;flex:0 0 auto}
    .custom-count{width:58px;padding:6px;border:1px solid #334155;border-radius:8px;background:#0b1220;color:#e2e8f0;font-size:12px}
    .custom-generate-btn{padding:8px 10px;border:1px solid #4b5563;border-radius:10px;background:linear-gradient(180deg,#1d4ed8,#1e3a8a);color:#eff6ff;font-weight:700;cursor:pointer;white-space:nowrap;font-size:12px}
    .custom-generate-btn[disabled]{opacity:.6;cursor:not-allowed}
    .custom-status{min-height:20px;font-size:11px;color:var(--sub);line-height:1.4}
    .custom-history{
      flex:1 1 auto;min-height:140px;max-height:none;overflow-y:auto;overflow-x:hidden;
      display:flex;flex-direction:column;gap:8px;padding-right:2px;
    }
    .custom-history-empty{font-size:11px;color:var(--sub);line-height:1.5;padding:8px 4px}
    .custom-job-card{
      display:flex;gap:8px;padding:8px;border:1px solid #334155;border-radius:10px;background:#0b1220;
      align-items:flex-start;flex:0 0 auto;
    }
    .custom-job-thumbs{flex:0 0 auto;display:flex;gap:4px;flex-wrap:wrap;width:72px}
    .custom-job-thumbs.multi{display:grid;grid-template-columns:repeat(2,34px);gap:3px;width:71px}
    .custom-job-thumb{
      width:72px;height:72px;border-radius:8px;overflow:hidden;border:1px solid #283449;background:#111827;
      cursor:pointer;flex:0 0 auto;
    }
    .custom-job-thumbs.multi .custom-job-thumb{width:34px;height:34px}
    .custom-job-thumb img{width:100%;height:100%;object-fit:cover;display:block}
    .custom-job-thumb.pending{
      display:flex;align-items:center;justify-content:center;font-size:10px;color:#94a3b8;text-align:center;
      padding:4px;line-height:1.3;background:linear-gradient(135deg,#0f172a,#1e293b);
    }
    .custom-job-thumb.pending.failed{background:rgba(127,29,29,.35);color:#fecaca;border-color:#7f1d1d}
    .custom-job-error{font-size:11px;color:#fca5a5;line-height:1.4;margin-top:2px;word-break:break-word}
    .custom-job-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}
    .custom-job-prompt{
      font-size:11px;line-height:1.45;color:#e2e8f0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
      overflow:hidden;word-break:break-word;
    }
    .custom-job-meta{font-size:10px;color:#64748b;line-height:1.3}
    .custom-job-actions{display:flex;gap:6px;flex-wrap:wrap}
    .custom-job-open{
      padding:4px 8px;font-size:10px;border-radius:6px;border:1px solid #475569;background:#111827;color:#cbd5e1;
      cursor:pointer;white-space:nowrap;
    }
    .custom-job-open:hover{border-color:#60a5fa;color:#eff6ff}
    .custom-preview-modal{position:fixed;inset:0;background:rgba(2,6,23,.78);display:flex;align-items:center;justify-content:center;z-index:90;padding:24px}
    .custom-preview-modal.hidden{display:none!important}
    .custom-preview-box{position:relative;max-width:min(92vw,980px);max-height:92vh}
    .custom-preview-box img{max-width:100%;max-height:92vh;border-radius:12px;box-shadow:var(--shadow);display:block;cursor:zoom-out}
    .custom-preview-close{position:absolute;top:-10px;right:-10px;width:34px;height:34px;border-radius:999px;border:1px solid #475569;background:#111827;color:#f8fafc;font-size:18px;cursor:pointer}
    .publish-platform-list{display:flex;flex-direction:column;gap:5px;flex:1}
    .publish-platform-item{display:flex;align-items:center;gap:6px;font-size:12px;color:#dbe7ff;border:1px solid #2f3b55;border-radius:8px;padding:6px 8px;background:#0f172a;cursor:pointer;white-space:nowrap}
    .publish-platform-item input{width:auto;margin:0;accent-color:#22c55e;flex:0 0 auto}
    .publish-status{min-height:48px;max-height:120px;overflow:auto;font-size:11px;color:var(--sub);white-space:pre-wrap;line-height:1.4;border:1px solid #2a364f;border-radius:8px;padding:6px;background:#0b1220}
    .btn-publish{width:100%;padding:10px 6px;border:1px solid #4b5563;border-radius:10px;background:linear-gradient(180deg,#14532d,#052e16);color:#dcfce7;font-size:13px;font-weight:700;cursor:pointer}
    .history-search{width:100%;margin:0 0 8px;padding:7px 10px;border:1px solid #334155;border-radius:8px;background:#0b1220;color:#e2e8f0;font-size:12px}
    .gallery-lightbox{position:fixed;inset:0;z-index:88;background:rgba(2,6,23,.84);display:flex;align-items:center;justify-content:center;padding:24px}
    .gallery-lightbox.hidden{display:none!important}
    .gallery-lightbox-box{position:relative;max-width:min(94vw,1200px);max-height:92vh;display:flex;align-items:center;justify-content:center}
    .gallery-lightbox-box img{max-width:100%;max-height:92vh;object-fit:contain;border-radius:12px;box-shadow:var(--shadow);cursor:zoom-out}
    .gallery-lightbox-close{position:absolute;top:-12px;right:-12px;width:34px;height:34px;border-radius:999px;border:1px solid #475569;background:#111827;color:#f8fafc;font-size:18px;cursor:pointer;z-index:2}
    .gallery-lightbox-nav{
      position:fixed;top:50%;transform:translateY(-50%);width:42px;height:42px;border-radius:999px;border:1px solid #475569;
      background:rgba(17,24,39,.92);color:#f8fafc;font-size:22px;cursor:pointer;z-index:89;
    }
    .gallery-lightbox-nav.prev{left:24px}
    .gallery-lightbox-nav.next{right:24px}
    .gallery-lightbox-counter{position:absolute;bottom:-28px;left:50%;transform:translateX(-50%);font-size:12px;color:#cbd5e1;white-space:nowrap}
    .btn-publish[disabled]{opacity:.6;cursor:not-allowed}
    .gallery-filter{width:auto;padding:7px 10px;border:1px solid #3a475f;border-radius:8px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:12px}
    .modal{position:fixed;inset:0;background:rgba(2,6,23,.72);display:flex;align-items:center;justify-content:center;z-index:80;padding:16px}
    .modal.hidden{display:none}
    .modal-box{max-width:460px;width:100%;border:1px solid #334155;border-radius:12px;background:#111827;padding:16px;box-shadow:var(--shadow)}
    .modal-box h3{margin:0 0 10px;font-size:18px}
    .modal-box p{margin:0 0 14px;font-size:13px;line-height:1.6;color:#cbd5e1;white-space:pre-wrap}
    .modal-box button{width:100%;padding:11px;border:1px solid #22c55e;border-radius:9px;background:#14532d;color:#dcfce7;font-weight:700;cursor:pointer}
    .splitter{
      margin:0 2px;border-radius:8px;background:linear-gradient(180deg,#334155,#1f2937);cursor:col-resize;height:calc(100vh - 88px);
      user-select:none;position:relative;flex:0 0 var(--splitter);touch-action:none;
    }
    .splitter::after{
      content:"";position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:3px;height:60px;border-radius:3px;background:#94a3b8;opacity:.55;
    }
    .panel-title{font-size:15px;font-weight:700;margin:0 0 8px}
    .section-card{border:1px solid #283449;background:var(--panel-soft);border-radius:10px;padding:10px}
    .sec-title{margin:0 0 8px;font-size:14px;font-weight:700}
    .sec-desc{margin:0 0 8px;font-size:12px;color:var(--sub)}
    .mode{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}
    .supplement-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px}
    .supplement-grid label{display:flex;align-items:center;gap:6px;margin:0;font-size:13px;color:var(--text);cursor:pointer}
    .supplement-grid input[type=checkbox]{width:auto;margin:0}
    .mode-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:stretch}
    .mode2-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
    .mode2-item{border:1px solid #283449;border-radius:8px;padding:8px;background:#0f172a}
    .mode2-item h4{margin:0 0 6px;font-size:12px;color:#cbd5e1}
    .mode button{
      width:100%;padding:10px;border:1px solid #3a475f;border-radius:9px;background:#111b2f;color:#dbe7ff;cursor:pointer;font-weight:700;
    }
    label{display:block;font-size:12px;color:var(--sub);margin:0 0 6px}
    input,textarea,select,button{font-family:inherit;outline:none}
    input,textarea,select{
      width:100%;border:1px solid #3a475f;border-radius:9px;padding:9px 10px;font-size:14px;background:#0d1628;color:var(--text);
    }
    input:focus,textarea:focus,select:focus{border-color:#60a5fa;box-shadow:0 0 0 3px rgba(96,165,250,.2)}
    textarea{min-height:98px;resize:vertical;line-height:1.45}
    .input-disabled{opacity:.58}
    .param-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
    .param-item input[type=number]{padding:7px 8px}
    .slider{width:100%;margin-top:6px}
    .preset-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
    .preset-row button,.sub-actions button{
      width:100%;padding:9px;border:1px solid #3a475f;border-radius:9px;background:#101a2a;color:#dbe7ff;cursor:pointer;
    }
    .run-wrap{position:sticky;bottom:0;background:linear-gradient(180deg,rgba(18,24,38,0),rgba(18,24,38,.95) 35%,rgba(18,24,38,1));padding-top:10px}
    .btn-primary{
      width:100%;padding:13px 12px;border:1px solid #4b5563;border-radius:10px;background:linear-gradient(180deg,#111827,#030712);
      color:#fff;font-size:16px;font-weight:700;cursor:pointer;
    }
    .btn-primary[disabled]{opacity:.65;cursor:not-allowed}
    .status{margin-top:8px;font-size:12px;color:var(--sub);white-space:pre-wrap;line-height:1.4}
    .ok{color:var(--ok)} .warn{color:var(--warn)} .danger{color:var(--danger)}
    .result-card{
      background:transparent;border:none;padding:0;box-shadow:none;
      display:flex;flex-direction:column;height:100%;
    }
    .result-stage{
      position:relative;border:1px solid #2a364f;border-radius:12px;min-height:420px;height:calc(100vh - 310px);max-height:780px;
      background:#0b1220;padding:10px;display:flex;align-items:center;justify-content:center;overflow:hidden;
    }
    .gallery-main{width:100%;height:100%;object-fit:contain;border-radius:10px;display:block}
    .gallery-main.hidden{display:none}
    .empty-image-state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#9fb0cc}
    .empty-image-icon{width:56px;height:56px;border-radius:999px;background:#1e293b;display:flex;align-items:center;justify-content:center;font-size:22px}
    .empty-image-title{font-size:14px;font-weight:700}
    .empty-image-sub{font-size:12px}
    .result-overlay{
      margin-top:10px;border:1px solid #334155;background:#111a2d;border-radius:10px;padding:8px 10px;font-size:12px;
      display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;
    }
    .overlay-line{display:flex;gap:8px;min-width:0}
    .overlay-k{color:#94a3b8;width:48px;flex:0 0 auto}
    .overlay-v{color:#e2e8f0;word-break:break-all}
    .skeleton{
      position:absolute;inset:10px;border-radius:10px;
      background:linear-gradient(100deg, rgba(30,41,59,.45) 20%, rgba(71,85,105,.5) 38%, rgba(30,41,59,.45) 56%);
      background-size:220% 100%;animation:shine 1.2s linear infinite;
    }
    .run-badge{
      position:absolute;right:16px;top:16px;border:1px solid #334155;background:rgba(15,23,42,.88);border-radius:999px;
      padding:5px 10px;font-size:12px;color:#e2e8f0;display:none;
    }
    @keyframes shine { from { background-position:200% 0; } to { background-position:-20% 0; } }
    .right-tabs{display:flex;gap:8px;margin-bottom:10px}
    .view-tab{width:auto;padding:8px 12px;border:1px solid #3a475f;border-radius:999px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-weight:700}
    .content-pane.hidden{display:none}
    .result-actions{display:flex;gap:8px;margin-top:10px}
    .result-actions button{width:auto;padding:8px 10px;border:1px solid #3a475f;border-radius:8px;background:#101a2a;color:#dbe7ff;cursor:pointer}
    .gallery-strip{
      border:1px solid #2a364f;border-radius:10px;background:#0b1220;padding:8px 8px 6px;margin-bottom:10px;
    }
    .gallery-tools{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
    .gallery-actions{display:flex;gap:8px}
    .gallery-actions button{width:auto;padding:7px 10px;border:1px solid #3a475f;border-radius:8px;background:#101a2a;color:#dbe7ff;cursor:pointer}
    .gallery-actions button.primary-anchor{border-color:#2d6a4f;background:#14532d;color:#ecfdf5}
    .gallery-actions button.primary-anchor:disabled{opacity:.45;cursor:not-allowed}
    .gallery-actions button.replace-mode-active{border-color:#ca8a04;background:#422006;color:#fef9c3}
    .gallery-actions button.hidden{display:none}
    .thumb.selected{outline:2px solid #22c55e;outline-offset:1px}
    .anchor-poster-hint{font-size:11px;color:var(--sub);margin:0 0 6px;line-height:1.45}
    .gallery-index{font-size:12px;color:var(--sub)}
    .thumbs{
      display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;padding:2px 0 8px;
      scrollbar-width:thin;min-height:72px;align-items:flex-start;
    }
    .thumb{flex:0 0 auto;width:86px;height:64px;border:1px solid #344155;border-radius:8px;padding:2px;background:#0f172a;cursor:pointer;display:block}
    .thumb img{width:100%;height:100%;object-fit:cover;border-radius:6px}
    .thumb.active{border-color:#60a5fa;box-shadow:0 0 0 2px rgba(96,165,250,.25)}
    .history-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
    .history-head .panel-title{margin:0}
    .history-head-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
    .history-sort{
      width:auto;min-width:132px;padding:6px 8px;border:1px solid #3a475f;border-radius:8px;
      background:#101a2a;color:#dbe7ff;font-size:12px;cursor:pointer;
    }
    .history-head-actions button{
      width:auto;padding:5px 9px;border:1px solid #475569;border-radius:7px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:11px;
    }
    .history-head-actions .danger-btn{border-color:#7f1d1d;color:#fecaca;background:#2b1313}
    .history-head-actions .hidden{display:none}
    .history-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;align-content:start}
    .history-grid.cols-1{grid-template-columns:1fr}
    .history-grid.cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}
    .history-grid.cols-3{grid-template-columns:repeat(3,minmax(0,1fr))}
    .history-grid.cols-auto{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
    .history-grid.pool-compact .history-item{
      grid-template-columns:1fr;grid-template-areas:"cover" "meta" "ops";padding:6px;gap:4px;
    }
    .history-grid.pool-compact .history-cover{width:100%;height:68px;border-radius:8px}
    .history-grid.pool-compact .history-meta{text-align:left}
    .history-grid.pool-compact .history-name{font-size:11px;-webkit-line-clamp:2}
    .history-grid.pool-compact .history-time{font-size:11px;margin-top:2px}
    .history-grid.pool-compact .history-ops{justify-content:stretch;gap:4px;padding-top:4px}
    .history-grid.pool-compact .history-ops button{flex:1;padding:4px 2px;font-size:10px}
    .history-grid.pool-compact .history-check{position:absolute;top:4px;left:4px;z-index:3;background:rgba(15,23,42,.82);border-radius:4px;padding:2px}
    .history-grid.pool-compact.batch-mode .history-check{display:flex}
    .history-empty{color:var(--sub);font-size:12px;grid-column:1/-1}
    .history-item{
      position:relative;border:1px solid #2f3b55;border-radius:10px;background:#0f172a;overflow:visible;cursor:pointer;
      transition:transform .15s ease,border-color .15s ease;
      display:grid;grid-template-columns:18px 52px 1fr;grid-template-areas:"check cover meta" "ops ops ops";gap:6px;align-items:center;padding:8px;
    }
    .history-check{grid-area:check;display:none;align-items:flex-start;padding-top:2px}
    .history-check input{width:14px;height:14px;margin:0;accent-color:#22c55e;cursor:pointer}
    .history-grid.batch-mode .history-check{display:flex}
    .history-grid.batch-mode .history-ops{display:none}
    .history-item.batch-selected{border-color:#f59e0b;box-shadow:0 0 0 2px rgba(245,158,11,.22)}
    .history-item:hover{transform:translateY(-1px);border-color:#60a5fa;box-shadow:0 0 0 2px rgba(96,165,250,.18)}
    .history-item.active{border-color:#22c55e;box-shadow:0 0 0 2px rgba(34,197,94,.22)}
    .history-item.task-running{border-color:#f59e0b;box-shadow:0 0 0 2px rgba(245,158,11,.28)}
    .history-item.task-queued{border-color:#6366f1;box-shadow:0 0 0 1px rgba(99,102,241,.22)}
    .history-task-badge{
      position:absolute;top:5px;left:5px;z-index:2;padding:1px 6px;border-radius:999px;
      font-size:10px;font-weight:700;line-height:1.4;color:#fff;pointer-events:none;
    }
    .history-task-badge.running{background:#d97706}
    .history-task-badge.queued{background:#4f46e5}
    .task-bar{
      position:relative;z-index:28;
      margin:0 0 10px;padding:10px 12px;border:1px solid #334155;border-radius:12px;
      background:linear-gradient(180deg,#111b2e 0%,#0d1524 100%);box-shadow:var(--shadow);
    }
    .task-bar.hidden{display:none}
    .task-bar-main{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
    .task-bar-running{flex:1;min-width:220px;font-size:13px;line-height:1.5;color:#e2e8f0}
    .task-bar-running strong{color:#fbbf24}
    .task-bar-running .sub{color:#94a3b8;font-size:12px;margin-top:2px}
    .task-bar-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .task-bar-actions button{
      width:auto;padding:6px 10px;border:1px solid #475569;border-radius:8px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:12px;
    }
    .task-bar-actions button.primary{border-color:#2563eb;background:#1d4ed8;color:#eff6ff}
    .task-bar-queue{margin-top:8px;padding-top:8px;border-top:1px solid #283548;font-size:12px;color:#cbd5e1}
    .task-bar-queue.hidden{display:none}
    .task-bar-queue-item{padding:4px 0;display:flex;gap:8px;align-items:baseline}
    .task-bar-queue-item .tag{color:#a5b4fc;min-width:52px}
    .task-bar-publish{margin-top:8px;padding-top:8px;border-top:1px dashed #334155;font-size:12px;color:#86efac}
    .task-bar-publish.hidden{display:none}
    .view-context-bar{
      display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;font-size:12px;margin-bottom:8px;
      padding:7px 10px;border:1px solid #2a364f;border-radius:8px;background:#0b1220;color:#94a3b8;
    }
    .view-context-bar strong{color:#e2e8f0;font-weight:700}
    .running-context.active{color:#fbbf24}
    .running-context.publish{color:#86efac}
    .history-cover{grid-area:cover;width:52px;height:52px;background:transparent;border-radius:7px;display:flex;align-items:center;justify-content:center;overflow:visible}
    .history-cover img{width:100%;height:100%;object-fit:cover}
    .history-meta{grid-area:meta;padding:0;min-width:0}
    .history-meta-top{display:flex;align-items:flex-start;justify-content:space-between;gap:4px}
    .history-fav{
      width:22px;height:22px;padding:0;border:none;background:transparent;color:#64748b;
      font-size:15px;cursor:pointer;line-height:1;flex:0 0 auto;
    }
    .history-fav.active{color:#ef4444}
    .history-fav:hover{color:#f87171}
    .history-name{font-size:12px;font-weight:700;line-height:1.3;word-break:break-all;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;flex:1;min-width:0}
    .history-time{font-size:12px;color:var(--sub);margin-top:4px}
    .history-ops{
      grid-area:ops;position:static;display:flex;gap:6px;justify-content:flex-end;padding-top:6px;
      border-top:1px solid #283548;
    }
    .history-ops button{
      width:auto;padding:5px 8px;border:1px solid #475569;border-radius:7px;background:rgba(15,23,42,.95);color:#dbeafe;cursor:pointer;font-size:11px;
    }
    .history-ops .del{border-color:#7f1d1d;color:#fecaca;background:rgba(69,10,10,.85)}
    .load-more{margin-top:10px;text-align:center;font-size:12px;color:var(--sub)}
    .load-more button{width:auto;padding:7px 10px;border:1px solid #3a475f;border-radius:8px;background:#111b2f;color:#dbe7ff;cursor:pointer}
    .text-panel{border:1px solid #2a364f;border-radius:12px;background:#0b1220;padding:10px;min-height:calc(100vh - 260px)}
    .text-empty{display:flex;align-items:center;justify-content:center;min-height:260px;color:#9fb0cc;font-size:13px;text-align:center;line-height:1.7}
    .text-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
    .text-tab,.text-file-btn{width:auto;padding:7px 10px;border:1px solid #3a475f;border-radius:999px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:12px}
    .text-file-list{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #26344b}
    .text-file-meta{font-size:12px;color:#94a3b8;margin-bottom:8px;word-break:break-all}
    .text-editor-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;flex-wrap:wrap}
    .text-editor-hint{font-size:12px;color:#94a3b8}
    .text-editor-hint.dirty{color:#fcd34d}
    .text-editor-actions{display:flex;gap:8px;align-items:center}
    .text-editor-actions button{width:auto;padding:7px 12px;border:1px solid #3a475f;border-radius:8px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:12px}
    .text-editor-actions button.primary{border-color:#2563eb;background:#1d4ed8;color:#eff6ff}
    .text-editor-actions button:disabled{opacity:.45;cursor:not-allowed}
    .text-content,.text-editor{margin:0;width:100%;min-height:360px;max-height:calc(100vh - 400px);overflow:auto;white-space:pre-wrap;word-break:break-word;border:1px solid #26344b;border-radius:10px;background:#08111f;padding:12px;color:#e5edf8;font-size:13px;line-height:1.65;box-sizing:border-box;resize:vertical;font-family:ui-monospace,Consolas,monospace}
    .text-editor:focus{outline:none;border-color:#3b82f6;box-shadow:0 0 0 1px rgba(59,130,246,.35)}
    .text-editor.readonly{background:#0a101c;color:#cbd5e1;cursor:default;resize:none}
    .log-toggle{
      position:fixed;right:16px;bottom:16px;z-index:55;padding:9px 12px;border-radius:999px;border:1px solid #475569;
      background:#0f172a;color:#e2e8f0;box-shadow:var(--shadow);cursor:pointer;font-size:12px;
    }
    .log-drawer{
      position:fixed;left:10px;right:10px;bottom:10px;height:0;opacity:0;pointer-events:none;z-index:54;
      border:1px solid #334155;border-radius:12px;background:#111827;box-shadow:var(--shadow);overflow:hidden;transition:all .2s ease;
    }
    .log-drawer.open{height:420px;opacity:1;pointer-events:auto;resize:vertical;min-height:280px;max-height:85vh}
    .log-head{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid #334155}
    .log-head-title{font-size:13px;font-weight:700}
    .log-head-actions{display:flex;gap:8px}
    .log-head-actions button{width:auto;padding:6px 9px;border:1px solid #475569;border-radius:8px;background:#0b1220;color:#dbe7ff;cursor:pointer;font-size:12px}
    .log-panel{
      height:calc(100% - 44px);margin:0;padding:10px;overflow:auto;font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.45;
      white-space:pre-wrap;word-break:break-word;color:#d1d9e9;
    }
    @media(max-width:1200px){
      .four-col{grid-template-columns:1fr}
      .splitter{display:none}
      .panel{height:auto}
      .panel-left,.panel-plan,.panel-mid,.panel-right,.panel-publish,.panel-custom{margin:0 0 10px}
      .history-grid{max-height:none}
    }
    @media(max-width:860px){
      .param-grid{grid-template-columns:1fr}
      .thumbs{padding-bottom:6px}
      .result-overlay{grid-template-columns:1fr}
      .result-stage{height:420px;max-height:none}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="top-brand">
        <div class="title">造菜控制台</div>
        <span class="version-tag">__PANEL_VERSION__</span>
        <span id="panelOfflineTag" class="offline-tag hidden">断开</span>
      </div>
      <div class="top-toolbar">
        <div class="top-seg" id="providerSeg">
          <button id="imgProviderOfficial" type="button" class="top-seg-btn active" title="OpenAI 官方 gpt-image-2">官方</button>
          <button id="imgProviderSilkroad" type="button" class="top-seg-btn" title="丝路网关">丝路</button>
        </div>
        <div class="top-seg" id="qualitySeg">
          <button type="button" class="top-seg-btn" data-quality="low" title="标准画质">标</button>
          <button type="button" class="top-seg-btn" data-quality="medium" title="中等画质">中</button>
          <button type="button" class="top-seg-btn active" data-quality="high" title="高清画质">高</button>
          <button type="button" class="top-seg-btn" data-quality="auto" title="自动画质">自动</button>
        </div>
        <div class="top-seg" id="tierSeg">
          <button type="button" class="top-seg-btn active" data-tier="1k" title="1K 分辨率">1K</button>
          <button type="button" class="top-seg-btn" data-tier="2k" title="2K 分辨率">2K</button>
          <button type="button" class="top-seg-btn" data-tier="4k" title="4K 分辨率">4K</button>
        </div>
        <div class="top-seg" id="ratioSeg">
          <button type="button" class="top-seg-btn active" data-ratio="2:3" title="2:3 竖版">2:3</button>
          <button type="button" class="top-seg-btn" data-ratio="3:4" title="3:4 竖版">3:4</button>
          <button type="button" class="top-seg-btn" data-ratio="9:16" title="9:16 竖版">9:16</button>
          <button type="button" class="top-seg-btn" data-ratio="1:1" title="1:1 方形">1:1</button>
          <button type="button" class="top-seg-btn" data-ratio="4:5" title="4:5 竖版">4:5</button>
          <button type="button" class="top-seg-btn" data-ratio="3:2" title="3:2 横版">3:2</button>
          <button type="button" class="top-seg-btn" data-ratio="16:9" title="16:9 横版">16:9</button>
          <button type="button" class="top-seg-btn" data-ratio="4:3" title="4:3 横版">4:3</button>
        </div>
        <span id="imageSizePreview" class="top-size-hint">1024×1536</span>
      </div>
      <div class="top-actions">
        <button id="openOutputTopBtn" type="button" title="打开输出目录">目录</button>
        <button id="copyOutputTopBtn" type="button" title="复制输出路径">路径</button>
        <button id="refreshTopBtn" type="button" title="刷新菜品池">刷新</button>
        <button id="stopWorkBtn" type="button" class="danger-btn" title="停止后台任务（不关闭面板）">停止</button>
        <button id="restartPanelBtn" type="button" title="重启面板">重启</button>
        <button id="shutdownPanelBtn" type="button" class="danger-btn" title="终止面板">终止</button>
      </div>
    </div>

    <div id="taskBar" class="task-bar hidden">
      <div class="task-bar-main">
        <div class="task-bar-running" id="taskBarRunning">后台空闲</div>
        <div class="task-bar-actions">
          <button id="taskBarJumpBtn" type="button" class="primary hidden">切到运行中菜品</button>
          <button id="taskBarQueueToggle" type="button" class="hidden">展开队列</button>
        </div>
      </div>
      <div id="taskBarQueue" class="task-bar-queue hidden"></div>
      <div id="taskBarPublish" class="task-bar-publish hidden"></div>
    </div>

    <div class="layout-scroll">
    <div id="fourColLayout" class="four-col">
      <section class="panel panel-left">
        <div class="history-head">
          <h3 class="panel-title">菜品池</h3>
          <div class="history-head-actions">
            <select id="historyColsSelect" class="history-sort" title="菜品池列数">
              <option value="1">单列</option>
              <option value="2">双列</option>
              <option value="3" selected>三列</option>
              <option value="auto">自动</option>
            </select>
            <select id="historySortSelect" class="history-sort" title="菜品池排序">
              <option value="favorite">收藏优先</option>
              <option value="image_first">已生图优先</option>
              <option value="created_desc">创建时间（新→旧）</option>
              <option value="created_asc">创建时间（旧→新）</option>
              <option value="name">菜名 A→Z</option>
            </select>
            <button id="batchEditBtn" type="button">批量编辑</button>
          </div>
        </div>
        <div id="mealFilterRow" class="meal-filter-row">
          <button type="button" class="meal-filter-btn active" data-meal-filter="">全部</button>
          <button type="button" class="meal-filter-btn" data-meal-filter="breakfast">早餐</button>
          <button type="button" class="meal-filter-btn" data-meal-filter="lunch">午餐</button>
          <button type="button" class="meal-filter-btn" data-meal-filter="dinner">晚餐</button>
          <button type="button" class="meal-filter-btn" data-meal-filter="late_night">夜宵</button>
        </div>
        <input id="historySearch" class="history-search" type="search" placeholder="搜索菜名…" autocomplete="off" />
        <div id="batchEditBar" class="batch-edit-bar">
          <button id="batchRemoveBtn" type="button" class="danger-btn">批量移出</button>
          <button type="button" data-batch-add="breakfast">+早餐</button>
          <button type="button" data-batch-add="lunch">+午餐</button>
          <button type="button" data-batch-add="dinner">+晚餐</button>
          <button type="button" data-batch-add="late_night">+夜宵</button>
          <button type="button" data-batch-remove="breakfast">-早餐</button>
          <button type="button" data-batch-remove="lunch">-午餐</button>
          <button type="button" data-batch-remove="dinner">-晚餐</button>
          <button type="button" data-batch-remove="late_night">-夜宵</button>
        </div>
        <div id="history" class="history-grid"></div>
        <div id="historyLoadMore" class="load-more">
          <button id="loadMoreBtn" type="button">载入更多</button>
        </div>
      </section>

      <div id="splitterLeft" class="splitter" title="拖拽调整菜品池宽度"></div>

      <aside class="panel panel-plan">
        <h3 class="panel-title">发布计划</h3>
        <div class="plan-range">
          <input id="planStartDate" type="date" title="开始日期" />
          <input id="planEndDate" type="date" title="结束日期" />
        </div>
        <div id="planList" class="plan-list"></div>
      </aside>

      <div id="splitterPlan" class="splitter" title="拖拽调整发布计划宽度"></div>

      <section class="panel panel-right">
        <div class="result-card">
          <div class="right-tabs">
            <button id="imageTabBtn" class="view-tab active" type="button">看图栏</button>
            <button id="textTabBtn" class="view-tab" type="button">文字栏</button>
          </div>
          <div id="imagePane" class="content-pane">
            <div class="view-context-bar">
              <span>正在查看：<strong id="viewingDishLabel">未选择</strong></span>
              <span id="runningContextLabel" class="running-context">后台：空闲</span>
            </div>
            <div class="gallery-strip">
              <div class="gallery-tools">
                <div class="gallery-actions">
                  <button id="prevImgBtn" type="button">上一张</button>
                  <button id="nextImgBtn" type="button">下一张</button>
                  <button id="filterPublishBtn" class="gallery-filter" type="button">发布图</button>
                  <button id="filterAllBtn" class="gallery-filter active" type="button">非发布图</button>
                  <button id="publishReplaceBtn" class="gallery-filter" type="button" title="从非发布图中多选替换 publish/final 对应槽位">替换</button>
                  <button id="publishReplaceExecBtn" class="primary-anchor hidden" type="button">执行替换</button>
                  <button id="anchorPosterRegenBtn" class="primary-anchor" type="button" title="将当前图设为海报，存档旧 publish/final 后补生细节/菜谱/封面并 PS 合成">设为海报并补生</button>
                </div>
                <div id="galleryIndex" class="gallery-index">0 / 0</div>
              </div>
              <div id="anchorPosterHint" class="anchor-poster-hint">非发布图中可「替换」已选发布图（多选后执行），或选中海报后「设为海报并补生」其余组图。</div>
              <div id="thumbs" class="thumbs"></div>
            </div>
            <div class="result-stage">
              <div id="runSkeleton" class="skeleton" style="display:none"></div>
              <div id="runBadge" class="run-badge">处理中 0%</div>
              <img id="resultImg" class="gallery-main" alt="暂无图片" />
              <div id="emptyImageState" class="empty-image-state">
                <div class="empty-image-icon">图</div>
                <div class="empty-image-title">暂未生成图片</div>
                <div class="empty-image-sub">运行任务后在这里查看结果，双击可放大查看。</div>
              </div>
            </div>
          </div>
          <div id="textPane" class="content-pane hidden">
            <div id="textPanel" class="text-panel">
              <div class="text-empty">点击左侧某个菜品后，这里会显示它的造菜信息、平台文案、提示词和其他 txt 文案。</div>
            </div>
          </div>
          <div class="result-overlay">
            <div class="overlay-line"><div class="overlay-k">菜名</div><div id="rDish" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">参考菜</div><div id="rRef" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">菜系</div><div id="rRegion" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">目录</div><div id="rOut" class="overlay-v mono">-</div></div>
            <div class="overlay-line"><div class="overlay-k">流程</div><div id="rWorkflow" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">主图首选</div><div id="rBestMain" class="overlay-v mono">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">封面首选</div><div id="rBestCover" class="overlay-v mono">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">选图方式</div><div id="rPickMode" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">PS合成</div><div id="rPsStatus" class="overlay-v">暂无</div></div>
          </div>
          <div class="result-actions">
            <button id="openOutputBtn" type="button">打开输出目录</button>
            <button id="openPublishBtn" type="button">打开 publish</button>
            <button id="copyOutputBtn" type="button">复制路径</button>
          </div>
          <div id="resultMsg" class="status"></div>
        </div>
      </section>

      <div id="splitterGallery" class="splitter" title="拖拽调整看图栏宽度"></div>

      <aside class="panel panel-mid">
        <div class="section-card">
          <h3 class="sec-title">造菜方式</h3>
          <div class="mode">
            <button id="modeAutoBtn" class="active" type="button">自动</button>
            <button id="modeFileBtn" type="button">手动</button>
            <button id="modeTargetBtn" type="button">指定</button>
            <button id="modeSupplementBtn" type="button">补生</button>
            <button id="modeIdeaBtn" type="button">生菜</button>
          </div>
          <div id="targetModeHint" class="sec-desc" style="display:none;margin-top:8px">在左侧菜品池选中一项后，将自动读取菜名与描述，并在原目录内生成图片与文案。</div>
          <div id="supplementModeHint" class="sec-desc" style="display:none;margin-top:8px">在左侧菜品池选中一项后，勾选要补生的图片或文案，再点「开始运行」。</div>
          <div id="cuisineCard" style="margin-top:8px">
            <label>自动菜系</label>
            <select id="cuisineMode">
              <option value="1" selected>中华料理</option>
              <option value="0">全部随机</option>
              <option value="2">新马泰</option>
              <option value="3">日韩</option>
              <option value="4">西餐</option>
              <option value="5">中东北非</option>
              <option value="6">东欧</option>
              <option value="7">拉美</option>
            </select>
            <div class="sec-desc">「自动」与「生菜」批量模式生效。</div>
          </div>
          <label id="dishNameLabel" style="margin-top:8px;display:none">手动菜名</label>
          <input id="dishName" placeholder="例如：蒜香煎嫩鸡胸肉" style="display:none" />
        </div>

        <div id="ideaCard" class="section-card" style="display:none">
          <h3 class="sec-title">生菜</h3>
          <div class="sec-desc">仅调用豆包写入造菜信息 txt，不生成图片。留空菜名则自动批量；填写菜名则每次 1 条。</div>
          <label>生成数量</label>
          <input id="ideaCount" type="number" min="1" max="30" step="1" value="3" />
          <label style="margin-top:8px">手动菜名（可选）</label>
          <input id="ideaDishName" placeholder="填写则按手动生成 1 条，留空则自动批量" />
        </div>

        <div id="supplementCard" class="section-card" style="display:none">
          <h3 class="sec-title">补生内容</h3>
          <div class="sec-desc">勾选需要重新生成的图片或文案。补生图片时会使用下方四组图的数量与画质配置。</div>
          <div class="supplement-grid">
            <label><input type="checkbox" id="suppPoster" value="poster" />海报图</label>
            <label><input type="checkbox" id="suppDetail" value="detail" />细节图</label>
            <label><input type="checkbox" id="suppRecipe" value="recipe" />菜谱图</label>
            <label><input type="checkbox" id="suppCover" value="cover" />封面图</label>
            <label><input type="checkbox" id="suppCopy" value="copy" />各平台标题与正文</label>
            <label><input type="checkbox" id="suppPhotoshop" value="photoshop" />PS 合成</label>
          </div>
        </div>

        <div class="section-card">
          <h3 class="sec-title">参数调节</h3>
          <div class="param-grid">
            <div class="param-item">
              <label>创意强度</label>
              <input id="temperature" type="number" step="0.1" min="0" max="1.5" />
              <input id="temperatureSlider" class="slider" type="range" min="0" max="15" step="1" />
            </div>
          </div>
          <div id="groupParams">
            <div class="sec-desc" style="margin-top:8px">四组图（海报 / 细节 / 菜谱 / 封面）各自独立配置画质与数量。</div>
            <div class="mode2-grid">
              <div class="mode2-item">
                <h4>海报图</h4>
                <label>数量</label><input id="posterCount" type="number" min="1" max="4" step="1" value="2" />
                <label style="margin-top:6px">画质</label>
                <select id="posterQuality"><option value="low">标准</option><option value="medium">中等</option><option value="high" selected>高清</option><option value="auto">自动</option></select>
              </div>
              <div class="mode2-item">
                <h4>细节图</h4>
                <label>数量</label><input id="detailCount" type="number" min="1" max="4" step="1" value="2" />
                <label style="margin-top:6px">画质</label>
                <select id="detailQuality"><option value="low">标准</option><option value="medium">中等</option><option value="high" selected>高清</option><option value="auto">自动</option></select>
              </div>
              <div class="mode2-item">
                <h4>菜谱图</h4>
                <label>数量</label><input id="recipeCount" type="number" min="1" max="4" step="1" value="2" />
                <label style="margin-top:6px">画质</label>
                <select id="recipeQuality"><option value="low">标准</option><option value="medium">中等</option><option value="high" selected>高清</option><option value="auto">自动</option></select>
              </div>
              <div class="mode2-item">
                <h4>封面图</h4>
                <label>数量</label><input id="coverMode2Count" type="number" min="1" max="4" step="1" value="2" />
                <label style="margin-top:6px">画质</label>
                <select id="coverMode2Quality"><option value="low">标准</option><option value="medium">中等</option><option value="high" selected>高清</option><option value="auto">自动</option></select>
              </div>
            </div>
          </div>
        </div>

        <div id="notesCard" class="section-card">
          <h3 class="sec-title">补充说明</h3>
          <textarea id="dishNotes" placeholder="可写关键做法、口味倾向、你想强调的卖点"></textarea>
        </div>

        <div class="run-wrap">
          <button id="runBtn" class="btn-primary" type="button">开始运行</button>
          <div id="status" class="status">就绪。先在左侧配置参数，再点击开始运行。</div>
        </div>
      </aside>

      <div id="splitterMid" class="splitter" title="拖拽调整造菜栏宽度"></div>

      <aside class="panel panel-publish">
        <h3 class="panel-title">发布平台</h3>
        <div id="publishPlatforms" class="publish-platform-list">
          <label class="publish-platform-item"><input type="checkbox" value="douyin" checked />抖音</label>
          <label class="publish-platform-item"><input type="checkbox" value="kuaishou" checked />快手</label>
          <label class="publish-platform-item"><input type="checkbox" value="xiaohongshu" checked />小红书</label>
          <label class="publish-platform-item"><input type="checkbox" value="weixin_mp" checked />公众号</label>
          <label class="publish-platform-item"><input type="checkbox" value="weixin_channels" checked />视频号</label>
        </div>
        <div id="publishStatus" class="publish-status">选中左侧菜品后，勾选平台并点击发布。</div>
        <button id="publishBtn" class="btn-publish" type="button">发布</button>
      </aside>

      <div id="splitterPublish" class="splitter" title="拖拽调整发布栏宽度"></div>

      <aside class="panel panel-custom">
        <h3 class="panel-title">自定义生图</h3>
        <div class="custom-compose">
          <textarea id="customPrompt" class="custom-prompt" placeholder="输入生图提示词…"></textarea>
          <div id="customRefs" class="custom-refs"></div>
          <div class="custom-toolbar">
            <button id="customAddBtn" class="custom-add-btn" type="button" title="上传参考图">+</button>
            <input id="customFileInput" type="file" accept="image/*" multiple class="hidden" />
            <select id="customCount" class="custom-count" title="生成数量">
              <option value="1">1 张</option>
              <option value="2" selected>2 张</option>
              <option value="3">3 张</option>
              <option value="4">4 张</option>
            </select>
            <button id="customGenerateBtn" class="custom-generate-btn" type="button">生成</button>
          </div>
          <div id="customStatus" class="custom-status">顶栏选择生图模型；图片保存在 V2/custom_image_gen/images/</div>
        </div>
        <h4 class="sec-title" style="margin:6px 0 4px">历史记录</h4>
        <div id="customHistory" class="custom-history"></div>
      </aside>
    </div>
    </div>
  </div>

  <div id="galleryLightbox" class="gallery-lightbox hidden">
    <div class="gallery-lightbox-box">
      <button id="galleryLightboxClose" class="gallery-lightbox-close" type="button" aria-label="关闭">×</button>
      <button id="galleryLightboxPrev" class="gallery-lightbox-nav prev" type="button" aria-label="上一张">‹</button>
      <img id="galleryLightboxImg" alt="大图预览" />
      <button id="galleryLightboxNext" class="gallery-lightbox-nav next" type="button" aria-label="下一张">›</button>
      <div id="galleryLightboxCounter" class="gallery-lightbox-counter">1 / 1</div>
    </div>
  </div>

  <div id="customPreviewModal" class="custom-preview-modal hidden">
    <div class="custom-preview-box">
      <button id="customPreviewClose" class="custom-preview-close" type="button" aria-label="关闭">×</button>
      <img id="customPreviewImg" alt="预览图" />
    </div>
  </div>

  <div id="loginModal" class="modal hidden">
    <div class="modal-box">
      <h3>需要手动登录</h3>
      <p id="loginModalMsg">请在浏览器中完成平台登录后，点击下方按钮继续。</p>
      <button id="loginModalConfirm" type="button">已完成登录，继续</button>
    </div>
  </div>

  <button id="logToggleBtn" class="log-toggle" type="button">日志</button>
  <div id="logDrawer" class="log-drawer">
    <div class="log-head">
      <div class="log-head-title">实时日志</div>
      <div class="log-head-actions">
        <button id="logCopyBtn" type="button">复制日志</button>
        <button id="logReloadBtn" type="button">刷新全部</button>
        <button id="logFilterBtn" type="button">只看错误：关</button>
        <button id="logClearBtn" type="button">清空显示</button>
        <button id="logCloseBtn" type="button">收起</button>
      </div>
    </div>
    <pre id="logPanel" class="log-panel">等待任务启动...</pre>
  </div>

  <script>
    const state = {
      mode: "auto",
      galleryImages: [],
      galleryIndex: 0,
      currentImagePath: "",
      currentOutputPath: "",
      currentResult: null,
      logNextIndex: 0,
      pollTimer: null,
      autoRefreshTimer: null,
      logOnlyErrors: false,
      historyOffset: 0,
      historyLimit: 30,
      historyHasMore: true,
      historyLoading: false,
      historySnapshot: "",
      historyRevision: "",
      historySort: "favorite",
      suppressHistoryAutoReload: 0,
      submittingTask: false,
      running: false,
      runningPercent: 0,
      runningElapsed: 0,
      runningTaskPath: "",
      taskQueueSnapshot: {running: null, queued: []},
      taskQueueExpanded: false,
      publishSnapshot: {running: false, platform: "", output_dir: ""},
      colLeft: 720,
      colPlan: 200,
      colGallery: 720,
      colMid: 400,
      colPublish: 118,
      colCustom: 228,
      historyMealFilter: "",
      historyItemCache: {},
      mealTagLabels: {breakfast:"早餐", lunch:"午餐", dinner:"晚餐", late_night:"夜宵"},
      publishPlan: {start:"", end:"", slots:{}, published_dates:[]},
      tagPopoverPath: "",
      customImagesDir: "",
      historyPoolCols: "3",
      historySearchQuery: "",
      galleryLightboxOpen: false,
      galleryLightboxIndex: 0,
      lightboxSource: "gallery",
      activeRightTab: "image",
      selectedHistoryPath: "",
      galleryFilter: "all",
      galleryReplaceMode: false,
      galleryReplaceSelected: [],
      galleryAllImages: [],
      galleryNonPublishImages: [],
      galleryPublishImages: [],
      textAssets: null,
      activeTextCategory: "",
      activeTextFilePath: "",
      textEditorDrafts: {},
      textEditorDirty: false,
      textEditorEditing: false,
      textSaveStatus: "",
      publishLogIndex: 0,
      publishPolling: false,
      publishPollTimer: null,
      panelOnline: true,
      panelPingFailStreak: 0,
      batchEditMode: false,
      batchEditSelected: new Set(),
      selectedHistoryItem: null,
      selectionSource: "pool",
      customRefs: [],
      customPolling: false,
      customRunning: false,
      customLogNextIndex: 0,
      customJobsSnapshot: "",
      customTilesSnapshot: "",
      imageSizeTable: {}
    };
    const $ = (id) => document.getElementById(id);
    const QUALITY_INDEX = { low: 0, medium: 1, high: 2, auto: 3 };
    const INDEX_QUALITY = ["low", "medium", "high", "auto"];
    const LAYOUT_STORAGE_KEY = "v2_panel_layout_v3";
    const LAYOUT_STORAGE_KEY_V2 = "v2_panel_layout_v2";
    const MEAL_TAG_KEYS = ["breakfast", "lunch", "dinner", "late_night"];
    const PLAN_SLOT_KEYS = ["morning", "noon", "evening"];
    const PLAN_SLOT_LABELS = {morning:"早", noon:"中", evening:"晚"};
    const DRAG_DISH_MIME = "application/x-dish-path";
    const LAYOUT_STORAGE_KEY_V1 = "v2_panel_layout_v1";

    function readDragDishPath(event){
      const dt = event?.dataTransfer;
      if(!dt){ return ""; }
      const custom = dt.getData(DRAG_DISH_MIME);
      if(custom){ return custom; }
      const plain = dt.getData("text/plain");
      if(plain && (/[\\\\/]/.test(plain) || plain.includes(":"))){ return plain; }
      return "";
    }
    const HISTORY_SORT_STORAGE_KEY = "v2_history_sort_v1";
    const HISTORY_COLS_STORAGE_KEY = "v2_history_pool_cols_v1";
    const IMAGE_GEN_PREFS_STORAGE_KEY = "v2_image_gen_prefs_v1";

    function setSegGroupValue(groupId, attr, value){
      document.querySelectorAll(`#${groupId} .top-seg-btn`).forEach((btn) => {
        btn.classList.toggle("active", (btn.dataset[attr] || "") === value);
      });
    }

    function readSegGroupValue(groupId, attr, fallback){
      const active = document.querySelector(`#${groupId} .top-seg-btn.active`);
      return (active?.dataset[attr] || fallback || "").trim();
    }

    function collectImageGenControls(){
      const provider = $("imgProviderOfficial").classList.contains("active") ? "official" : "silkroad";
      return {
        image_provider: provider,
        image_quality: readSegGroupValue("qualitySeg", "quality", "high"),
        image_resolution_tier: readSegGroupValue("tierSeg", "tier", "1k"),
        image_aspect_ratio: readSegGroupValue("ratioSeg", "ratio", "2:3")
      };
    }

    function lookupImageSize(provider, aspectRatio, tier){
      const table = state.imageSizeTable || {};
      const ratioRow = table[aspectRatio] || {};
      let size = ratioRow[tier] || ratioRow["1k"] || "1024x1536";
      if(provider === "silkroad" && tier !== "1k"){
        size = ratioRow["1k"] || size;
      }
      return size;
    }

    function updateImageSizePreview(){
      const controls = collectImageGenControls();
      const size = lookupImageSize(controls.image_provider, controls.image_aspect_ratio, controls.image_resolution_tier);
      const preview = $("imageSizePreview");
      if(preview){
        preview.textContent = size.replace("x", "×");
      }
      const silkroad = controls.image_provider === "silkroad";
      document.querySelectorAll("#tierSeg .top-seg-btn").forEach((btn) => {
        const tier = btn.dataset.tier || "";
        btn.disabled = silkroad && tier !== "1k";
      });
      if(silkroad && controls.image_resolution_tier !== "1k"){
        setTierValue("1k");
      }
    }

    function setImageProvider(provider){
      const official = provider !== "silkroad";
      $("imgProviderOfficial").classList.toggle("active", official);
      $("imgProviderSilkroad").classList.toggle("active", !official);
      updateImageSizePreview();
    }

    function setImageQuality(quality){
      setSegGroupValue("qualitySeg", "quality", quality || "high");
      updateImageSizePreview();
    }

    function setTierValue(tier){
      setSegGroupValue("tierSeg", "tier", (tier || "1k").toLowerCase());
      updateImageSizePreview();
    }

    function setImageAspectRatio(ratio){
      setSegGroupValue("ratioSeg", "ratio", ratio || "2:3");
      updateImageSizePreview();
    }

    function applyImageGenControlsToUi(prefs){
      const data = prefs || {};
      setImageProvider((data.image_provider || data.provider || "official") === "silkroad" ? "silkroad" : "official");
      if(data.image_quality){ setImageQuality(data.image_quality); }
      if(data.image_resolution_tier){ setTierValue(data.image_resolution_tier); }
      if(data.image_aspect_ratio){ setImageAspectRatio(data.image_aspect_ratio); }
      updateImageSizePreview();
    }

    function loadImageGenPrefsFromStorage(){
      try{
        const raw = localStorage.getItem(IMAGE_GEN_PREFS_STORAGE_KEY);
        if(!raw){ return null; }
        return JSON.parse(raw);
      }catch{
        return null;
      }
    }

    async function persistImageGenPrefs(){
      const prefs = collectImageGenControls();
      try{
        localStorage.setItem(IMAGE_GEN_PREFS_STORAGE_KEY, JSON.stringify(prefs));
      }catch{}
      try{
        await fetch("/api/image_gen_prefs", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(prefs)
        });
      }catch{}
      updateImageSizePreview();
    }

    function bindSegGroup(groupId, attr, onPick){
      document.querySelectorAll(`#${groupId} .top-seg-btn`).forEach((btn) => {
        btn.onclick = () => {
          if(btn.disabled){ return; }
          onPick(btn.dataset[attr] || "");
          persistImageGenPrefs();
        };
      });
    }

    function bindImageGenControls(){
      $("imgProviderOfficial").onclick = () => { setImageProvider("official"); persistImageGenPrefs(); };
      $("imgProviderSilkroad").onclick = () => { setImageProvider("silkroad"); persistImageGenPrefs(); };
      bindSegGroup("qualitySeg", "quality", (value) => setImageQuality(value));
      bindSegGroup("tierSeg", "tier", (value) => setTierValue(value));
      bindSegGroup("ratioSeg", "ratio", (value) => setImageAspectRatio(value));
    }

    function 应用菜品池列数(mode){
      const grid = $("history");
      if(!grid){ return; }
      const cols = String(mode || "3");
      state.historyPoolCols = cols;
      grid.classList.remove("cols-1", "cols-2", "cols-3", "cols-auto", "pool-compact");
      if(cols === "1"){
        grid.classList.add("cols-1");
      }else{
        grid.classList.add("pool-compact", `cols-${cols}`);
      }
      const minLeftByCols = { "1": 300, "2": 380, "3": 540, "auto": 360 };
      const needLeft = minLeftByCols[cols] || 380;
      if(state.colLeft < needLeft){
        应用面板栏宽({left: needLeft});
        保存面板栏宽();
      }
      try{
        localStorage.setItem(HISTORY_COLS_STORAGE_KEY, cols);
      }catch{}
    }

    function 画质文案(value){
      if(value === "low"){ return "标准清晰"; }
      if(value === "medium"){ return "中等清晰"; }
      if(value === "high"){ return "高清细节"; }
      if(value === "auto"){ return "自动选择"; }
      return value || "-";
    }

    function fileUrl(path){
      return "/api/file?path=" + encodeURIComponent(path);
    }

    function 菜系文案(code){
      const map = {
        "0": "全部随机",
        "1": "中华料理",
        "2": "新马泰",
        "3": "日韩",
        "4": "西餐",
        "5": "中东北非",
        "6": "东欧",
        "7": "拉美"
      };
      return map[String(code)] || code || "-";
    }

    function applyTargetDishFromItem(item){
      if(!item){ return; }
      $("dishName").value = item.dish_name || "";
      $("dishNotes").value = item.idea_notes || "";
    }

    function setMode(mode){
      state.mode = mode;
      $("modeAutoBtn").classList.toggle("active", mode === "auto");
      $("modeFileBtn").classList.toggle("active", mode === "file");
      $("modeTargetBtn").classList.toggle("active", mode === "target");
      $("modeSupplementBtn").classList.toggle("active", mode === "supplement");
      $("modeIdeaBtn").classList.toggle("active", mode === "idea");
      const manual = mode === "file";
      const target = mode === "target";
      const supplement = mode === "supplement";
      const idea = mode === "idea";
      $("cuisineCard").style.display = (mode === "auto" || idea) ? "block" : "none";
      $("targetModeHint").style.display = target ? "block" : "none";
      $("supplementModeHint").style.display = supplement ? "block" : "none";
      $("ideaCard").style.display = idea ? "block" : "none";
      $("supplementCard").style.display = supplement ? "block" : "none";
      $("groupParams").style.display = idea ? "none" : "block";
      $("dishNameLabel").style.display = manual ? "block" : "none";
      $("dishName").style.display = manual ? "block" : "none";
      $("dishName").disabled = target;
      $("dishName").classList.toggle("input-disabled", target);
      $("dishNotes").disabled = target;
      $("dishNotes").classList.toggle("input-disabled", target);
      $("notesCard").style.display = (manual || target) ? "block" : "none";
      if(target && state.selectedHistoryItem){
        applyTargetDishFromItem(state.selectedHistoryItem);
      }
      syncIdeaCountUi();
    }

    function syncIdeaCountUi(){
      if(state.mode !== "idea"){ return; }
      const ideaDishName = ($("ideaDishName").value || "").trim();
      $("ideaCount").disabled = Boolean(ideaDishName);
      $("ideaCount").classList.toggle("input-disabled", Boolean(ideaDishName));
      if(ideaDishName){ $("ideaCount").value = "1"; }
    }

    function collectSupplementTargets(){
      return ["suppPoster", "suppDetail", "suppRecipe", "suppCover", "suppCopy", "suppPhotoshop"]
        .filter((id) => $(id)?.checked)
        .map((id) => $(id).value);
    }

    function setBatchEditMode(enabled){
      state.batchEditMode = Boolean(enabled);
      state.batchEditSelected = new Set();
      $("history").classList.toggle("batch-mode", state.batchEditMode);
      $("batchEditBtn").textContent = state.batchEditMode ? "取消批量" : "批量编辑";
      $("batchEditBar")?.classList.toggle("open", state.batchEditMode);
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
        el.classList.remove("batch-selected");
        el.draggable = !state.batchEditMode;
        const checkbox = el.querySelector(".history-check input");
        if(checkbox){ checkbox.checked = false; }
      });
    }

    function updateBatchEditUi(){
      const count = state.batchEditSelected.size;
      const removeBtn = $("batchRemoveBtn");
      if(removeBtn){
        removeBtn.disabled = count < 1;
        removeBtn.textContent = count > 0 ? `批量移出（${count}）` : "批量移出";
      }
    }

    function toggleBatchEditPath(path, checked){
      if(!path){ return; }
      if(checked){ state.batchEditSelected.add(path); }
      else{ state.batchEditSelected.delete(path); }
      updateBatchEditUi();
    }

    function mealTagLabel(tag){
      return state.mealTagLabels[tag] || tag;
    }

    function renderMealTagBadges(tags){
      return (tags || []).map((tag) => `<span class="history-tag-badge">${mealTagLabel(tag)}</span>`).join("");
    }

    function closeAllTagPopovers(){
      Array.from(document.querySelectorAll(".tag-popover.open")).forEach((el) => el.classList.remove("open"));
      state.tagPopoverPath = "";
    }

    async function saveMealTags(path, tags){
      const res = await fetch("/api/dish_tags", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path, tags}),
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "保存标签失败"); }
      if(state.historyItemCache[path]){ state.historyItemCache[path].meal_tags = data.meal_tags || []; }
      const card = Array.from($("history").querySelectorAll(".history-item")).find((el) => el.dataset.path === path);
      if(card){
        card.dataset.mealTags = JSON.stringify(data.meal_tags || []);
        const badges = card.querySelector(".history-tag-badges");
        if(badges){ badges.innerHTML = renderMealTagBadges(data.meal_tags || []); }
        MEAL_TAG_KEYS.forEach((tag) => {
          const input = card.querySelector(`.tag-popover input[data-tag="${tag}"]`);
          if(input){ input.checked = (data.meal_tags || []).includes(tag); }
        });
      }
      applyHistorySearchFilter();
      return data;
    }

    async function batchUpdateMealTags(paths, {add=[], remove=[]}){
      const list = (paths || []).filter(Boolean);
      if(!list.length){ setStatus("请先勾选菜品。", "warn"); return; }
      const res = await fetch("/api/dish_tags/batch", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({paths: list, add, remove}),
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "批量更新标签失败"); }
      await loadHistory(true);
      setStatus(`已更新 ${data.count || list.length} 个菜品的标签。`, "ok");
    }

    function getDishLabel(path){
      const cached = state.historyItemCache[path];
      if(cached?.dish_name){ return cached.dish_name; }
      const base = String(path || "").split(/[\\\\/]/).pop() || "菜品";
      const parts = base.split("_");
      return parts.length >= 3 ? parts.slice(2).join("_") : base;
    }

    function getDishPreviewImage(path){
      const item = state.historyItemCache[path];
      if(item?.preview_image){ return item.preview_image; }
      const images = item?.images || [];
      return images.length ? images[0] : "";
    }

    async function hydratePublishPlanCache(){
      const paths = [...collectPlanAssignedPaths()];
      const missing = paths.filter((path) => !getDishPreviewImage(path));
      if(!missing.length){ return; }
      await Promise.all(missing.map(async (path) => {
        try{
          const res = await fetch(`/api/dish_detail?path=${encodeURIComponent(path)}`);
          const detail = await res.json();
          if(res.ok){
            state.historyItemCache[path] = {...(state.historyItemCache[path] || {}), ...detail, path};
          }
        }catch{}
      }));
    }

    function dirnamePath(path){
      const parts = String(path || "").split(/[/\\\\]/);
      parts.pop();
      return parts.join("/");
    }

    function collectPlanAssignedPaths(){
      const paths = new Set();
      Object.values(state.publishPlan?.slots || {}).forEach((daySlots) => {
        PLAN_SLOT_KEYS.forEach((slot) => {
          const p = daySlots?.[slot];
          if(p){ paths.add(normalizeHistoryPath(p)); }
        });
      });
      return paths;
    }

    function listPlanDateRange(){
      const start = state.publishPlan.start || "";
      const end = state.publishPlan.end || start;
      if(!start || !end){ return []; }
      const dates = [];
      const cur = new Date(start + "T00:00:00");
      const endDate = new Date(end + "T00:00:00");
      while(cur <= endDate){
        dates.push(cur.toISOString().slice(0, 10));
        cur.setDate(cur.getDate() + 1);
      }
      return dates;
    }

    async function savePublishPlan(plan){
      const res = await fetch("/api/publish_plan", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify(plan),
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "保存发布计划失败"); }
      state.publishPlan = data;
      renderPublishPlan();
      applyHistorySearchFilter();
      reconcileSelectionSource();
      return data;
    }

    function clearPlanSlotInMemory(plan, date, slot){
      if(!plan.slots?.[date]){ return; }
      delete plan.slots[date][slot];
      if(!Object.keys(plan.slots[date]).length){ delete plan.slots[date]; }
    }

    function removePathFromPlanMemory(plan, path, exceptDate="", exceptSlot=""){
      Object.keys(plan.slots || {}).forEach((date) => {
        PLAN_SLOT_KEYS.forEach((slot) => {
          if(plan.slots[date][slot] === path){
            if(date === exceptDate && slot === exceptSlot){ return; }
            clearPlanSlotInMemory(plan, date, slot);
          }
        });
      });
    }

    async function assignPlanSlot(date, slot, path, fromDate="", fromSlot=""){
      const plan = JSON.parse(JSON.stringify(state.publishPlan || {start:"", end:"", slots:{}}));
      if(fromDate && fromSlot){ clearPlanSlotInMemory(plan, fromDate, fromSlot); }
      if(path){
        Object.keys(plan.slots || {}).forEach((d) => {
          PLAN_SLOT_KEYS.forEach((s) => {
            if(d === date && s === slot){ return; }
            if(plan.slots[d]?.[s] === path){ clearPlanSlotInMemory(plan, d, s); }
          });
        });
        if(!plan.slots[date]){ plan.slots[date] = {}; }
        plan.slots[date][slot] = path;
      }else{
        clearPlanSlotInMemory(plan, date, slot);
      }
      await savePublishPlan(plan);
    }

    function createPlanSlotChip(path, date, slot){
      const chip = document.createElement("div");
      chip.className = "plan-slot-chip";
      chip.draggable = true;
      chip.dataset.path = path;
      const preview = getDishPreviewImage(path);
      if(preview){
        const img = document.createElement("img");
        img.src = fileUrl(preview);
        img.alt = getDishLabel(path);
        img.draggable = false;
        chip.appendChild(img);
      }else{
        chip.classList.add("empty-img");
      }
      const nameEl = document.createElement("div");
      nameEl.className = "plan-slot-chip-name";
      nameEl.textContent = getDishLabel(path);
      chip.appendChild(nameEl);
      chip.addEventListener("dragstart", (event) => {
        event.dataTransfer.setData(DRAG_DISH_MIME, path);
        event.dataTransfer.setData("text/plain", path);
        event.dataTransfer.setData("application/x-plan-from-date", date);
        event.dataTransfer.setData("application/x-plan-from-slot", slot);
        event.dataTransfer.effectAllowed = "move";
      });
      chip.addEventListener("click", async (event) => {
        event.stopPropagation();
        await selectDishByPath(path, "plan");
      });
      chip.addEventListener("dblclick", async (event) => {
        event.stopPropagation();
        event.preventDefault();
        await openPlanDishLightbox(path);
      });
      return chip;
    }

    async function openPlanDishLightbox(path){
      if(!path){ return; }
      let item = state.historyItemCache[path];
      if(!item?.images?.length){
        try{
          const res = await fetch(`/api/dish_detail?path=${encodeURIComponent(path)}`);
          const detail = await res.json();
          if(res.ok){
            item = {...(item || {}), ...detail, path};
            state.historyItemCache[path] = item;
          }
        }catch{}
      }
      const images = (item?.images || []).filter(Boolean);
      if(!images.length && item?.preview_image){ images.push(item.preview_image); }
      if(!images.length){
        setStatus("该菜品暂无可预览图片。", "warn");
        return;
      }
      state.lightboxSource = "plan";
      state.galleryImages = images;
      openGalleryLightbox(0);
    }

    function bindPlanSlotDrop(slotEl, date, slot){
      slotEl.addEventListener("dragover", (event) => {
        event.preventDefault();
        slotEl.classList.add("drag-over");
      });
      slotEl.addEventListener("dragleave", () => slotEl.classList.remove("drag-over"));
      slotEl.addEventListener("drop", async (event) => {
        event.preventDefault();
        slotEl.classList.remove("drag-over");
        const path = readDragDishPath(event);
        if(!path){ return; }
        const fromDate = event.dataTransfer.getData("application/x-plan-from-date");
        const fromSlot = event.dataTransfer.getData("application/x-plan-from-slot");
        try{
          await assignPlanSlot(date, slot, path, fromDate, fromSlot);
        }catch(err){
          setStatus("更新发布计划失败：" + err.message, "warn");
        }
      });
    }

    async function togglePlanDatePublished(date, checked){
      const plan = JSON.parse(JSON.stringify(state.publishPlan || {start:"", end:"", slots:{}, published_dates:[]}));
      const published = new Set(plan.published_dates || []);
      if(checked){ published.add(date); }
      else{ published.delete(date); }
      plan.published_dates = Array.from(published).sort();
      await savePublishPlan(plan);
    }

    function renderPublishPlan(){
      const box = $("planList");
      if(!box){ return; }
      box.innerHTML = "";
      const dates = listPlanDateRange();
      if(!dates.length){
        box.innerHTML = '<div class="history-empty">请选择日期范围</div>';
        return;
      }
      dates.forEach((date) => {
        const publishedDates = new Set(state.publishPlan?.published_dates || []);
        const isPublished = publishedDates.has(date);
        const day = document.createElement("div");
        day.className = "plan-day" + (isPublished ? " published" : "");
        const header = document.createElement("div");
        header.className = "plan-day-header";
        const dateEl = document.createElement("div");
        dateEl.className = "plan-day-date";
        dateEl.textContent = date;
        const doneWrap = document.createElement("label");
        doneWrap.className = "plan-day-done";
        doneWrap.title = isPublished ? "点击取消「已发布」标记" : "点击标记为已发布";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = isPublished;
        checkbox.addEventListener("click", (event) => event.stopPropagation());
        checkbox.addEventListener("change", async () => {
          try{
            await togglePlanDatePublished(date, checkbox.checked);
          }catch(err){
            checkbox.checked = !checkbox.checked;
            setStatus("更新发布状态失败：" + err.message, "warn");
          }
        });
        const doneLabel = document.createElement("span");
        doneLabel.textContent = "已发";
        doneWrap.appendChild(checkbox);
        doneWrap.appendChild(doneLabel);
        header.appendChild(dateEl);
        header.appendChild(doneWrap);
        const slotsWrap = document.createElement("div");
        slotsWrap.className = "plan-day-slots";
        day.appendChild(header);
        day.appendChild(slotsWrap);
        PLAN_SLOT_KEYS.forEach((slot) => {
          const slotEl = document.createElement("div");
          slotEl.className = "plan-slot";
          slotEl.dataset.date = date;
          slotEl.dataset.slot = slot;
          const path = state.publishPlan?.slots?.[date]?.[slot] || "";
          slotEl.innerHTML = `<div class="plan-slot-label">${PLAN_SLOT_LABELS[slot]}</div>`;
          if(path){
            slotEl.appendChild(createPlanSlotChip(path, date, slot));
          }
          bindPlanSlotDrop(slotEl, date, slot);
          slotsWrap.appendChild(slotEl);
        });
        box.appendChild(day);
      });
      markSelectedDishUi();
    }

    function initPublishPlanUi(){
      const plan = state.publishPlan || {};
      if($("planStartDate")){ $("planStartDate").value = plan.start || ""; }
      if($("planEndDate")){ $("planEndDate").value = plan.end || ""; }
      renderPublishPlan();
    }

    function bindHistoryPoolDropZone(){
      const grid = $("history");
      if(!grid || grid.dataset.dropBound === "1"){ return; }
      grid.dataset.dropBound = "1";
      grid.addEventListener("dragover", (event) => {
        if(event.dataTransfer.types.includes(DRAG_DISH_MIME)){
          event.preventDefault();
        }
      });
      grid.addEventListener("drop", async (event) => {
        const fromDate = event.dataTransfer.getData("application/x-plan-from-date");
        const fromSlot = event.dataTransfer.getData("application/x-plan-from-slot");
        if(!fromDate || !fromSlot){ return; }
        event.preventDefault();
        try{
          await assignPlanSlot(fromDate, fromSlot, "", fromDate, fromSlot);
          setStatus("已从发布计划移回菜品池。", "ok");
        }catch(err){
          setStatus("移回菜品池失败：" + err.message, "warn");
        }
      });
    }

    function setStatus(text, level=""){
      $("status").textContent = text;
      $("status").className = "status" + (level ? (" " + level) : "");
    }

    function setRunState(running, percent=0){
      state.running = running;
      state.runningPercent = percent;
      const runPath = normalizeHistoryPath(state.runningTaskPath || "");
      const viewPath = normalizeHistoryPath(state.selectedHistoryPath || "");
      const showOnPreview = running && runPath && viewPath && runPath === viewPath;
      const hasImage = Boolean(state.currentImagePath);
      $("runSkeleton").style.display = (showOnPreview && !hasImage) ? "block" : "none";
      $("runBadge").style.display = showOnPreview ? "block" : "none";
      $("runBadge").textContent = `处理中 ${percent}%`;
      if(running){
        $("runBtn").textContent = `运行中 ${percent}%（可继续排队）`;
        $("runBtn").disabled = false;
      }else{
        $("runBtn").textContent = "开始运行";
        $("runBtn").disabled = false;
        state.runningTaskPath = "";
      }
      updateViewContextBar();
      applyHistoryTaskBadges();
    }

    function updateViewContextBar(){
      const viewing = state.selectedHistoryItem?.dish_name
        || (state.selectedHistoryPath ? state.selectedHistoryPath.split(/[\\\\/]/).pop() : "")
        || "未选择";
      $("viewingDishLabel").textContent = viewing;
      const run = state.taskQueueSnapshot?.running;
      const ctx = $("runningContextLabel");
      if(state.running && run){
        const elapsed = state.runningElapsed ? ` · 已等 ${state.runningElapsed}s` : "";
        ctx.textContent = `后台运行：${run.summary}${elapsed}${state.runningPercent ? ` · ${state.runningPercent}%` : ""}`;
        ctx.className = "running-context active";
        return;
      }
      if(state.publishSnapshot?.running){
        const dish = state.publishSnapshot.dish_name || "当前菜品";
        const platform = state.publishSnapshot.platform || "发布中";
        ctx.textContent = `后台发布：${dish} → ${platform}`;
        ctx.className = "running-context publish";
        return;
      }
      if(state.customRunning){
        ctx.textContent = "后台自定义生图运行中…";
        ctx.className = "running-context active";
        return;
      }
      ctx.textContent = "后台：空闲";
      ctx.className = "running-context";
    }

    function renderTaskBar(){
      const snap = state.taskQueueSnapshot || {running: null, queued: []};
      const queued = snap.queued || [];
      const running = snap.running;
      const publishing = Boolean(state.publishSnapshot?.running);
      const hasWork = Boolean(state.running || queued.length || publishing);
      $("taskBar").classList.toggle("hidden", !hasWork);

      if(state.running && running){
        const elapsed = state.runningElapsed ? `已等 ${state.runningElapsed}s` : "处理中";
        const queueHint = queued.length ? ` · 排队 ${queued.length} 个` : "";
        $("taskBarRunning").innerHTML = `<div><strong>正在运行</strong>：${running.summary}（${elapsed}${queueHint}）</div>`;
        const canJump = Boolean(running.output_dir || (running.dish_name && running.dish_name !== "自动生成"));
        $("taskBarJumpBtn").classList.toggle("hidden", !canJump);
        $("taskBarJumpBtn").dataset.path = running.output_dir || "";
        $("taskBarJumpBtn").dataset.dish = running.dish_name || "";
      }else if(publishing){
        $("taskBarRunning").innerHTML = `<div><strong>正在发布</strong>：${state.publishSnapshot.dish_name || "菜品"} → ${state.publishSnapshot.platform || "…"}</div>`;
        $("taskBarJumpBtn").classList.add("hidden");
      }else if(queued.length){
        $("taskBarRunning").innerHTML = `<div><strong>等待执行</strong>：队列中有 ${queued.length} 个任务</div>`;
        $("taskBarJumpBtn").classList.add("hidden");
      }else{
        $("taskBarRunning").textContent = "后台空闲";
        $("taskBarJumpBtn").classList.add("hidden");
      }

      const toggleBtn = $("taskBarQueueToggle");
      if(queued.length){
        toggleBtn.classList.remove("hidden");
        toggleBtn.textContent = state.taskQueueExpanded ? "收起队列" : `展开队列（${queued.length}）`;
        $("taskBarQueue").classList.toggle("hidden", !state.taskQueueExpanded);
        $("taskBarQueue").innerHTML = queued.map((item, index) => (
          `<div class="task-bar-queue-item"><span class="tag">排队${index + 1}</span><span>${item.summary || item.dish_name || "任务"}</span></div>`
        )).join("");
      }else{
        toggleBtn.classList.add("hidden");
        $("taskBarQueue").classList.add("hidden");
        $("taskBarQueue").innerHTML = "";
        state.taskQueueExpanded = false;
      }

      const pub = $("taskBarPublish");
      if(publishing){
        pub.classList.remove("hidden");
        pub.textContent = `发布平台：${state.publishSnapshot.platform || "…"}（${state.publishSnapshot.output_dir || ""}）`;
      }else{
        pub.classList.add("hidden");
        pub.textContent = "";
      }
      updateViewContextBar();
      applyHistoryTaskBadges();
    }

    function applyHistoryTaskBadges(){
      const snap = state.taskQueueSnapshot || {running: null, queued: []};
      const runningPath = normalizeHistoryPath(snap.running?.output_dir || state.runningTaskPath || "");
      const queuedPaths = (snap.queued || []).map((item, index) => ({
        path: normalizeHistoryPath(item.output_dir || ""),
        index: index + 1,
      })).filter((item) => item.path);
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
        const path = normalizeHistoryPath(el.dataset.path || "");
        el.classList.remove("task-running", "task-queued");
        const oldBadge = el.querySelector(".history-task-badge");
        if(oldBadge){ oldBadge.remove(); }
        if(runningPath && path === runningPath){
          el.classList.add("task-running");
          const badge = document.createElement("span");
          badge.className = "history-task-badge running";
          badge.textContent = "运行中";
          el.appendChild(badge);
          return;
        }
        const queued = queuedPaths.find((item) => item.path === path);
        if(queued){
          el.classList.add("task-queued");
          const badge = document.createElement("span");
          badge.className = "history-task-badge queued";
          badge.textContent = `排队${queued.index}`;
          el.appendChild(badge);
        }
      });
    }

    function findHistoryCardByTask(pathText, dishName){
      const target = normalizeHistoryPath(pathText || "");
      const cards = Array.from($("history").querySelectorAll(".history-item"));
      if(target){
        const byPath = cards.find((el) => normalizeHistoryPath(el.dataset.path) === target);
        if(byPath){ return byPath; }
      }
      const name = String(dishName || "").trim();
      if(!name || name === "自动生成"){ return null; }
      return cards.find((el) => {
        const cardName = el.querySelector(".history-name")?.textContent?.trim() || "";
        return cardName === name || cardName.includes(name) || name.includes(cardName);
      }) || null;
    }

    async function jumpToRunningDish(rawPath){
      const running = state.taskQueueSnapshot?.running;
      const path = String(
        rawPath
        || $("taskBarJumpBtn")?.dataset?.path
        || state.runningTaskPath
        || running?.output_dir
        || ""
      ).trim();
      const dishName = String(
        $("taskBarJumpBtn")?.dataset?.dish
        || running?.dish_name
        || ""
      ).trim();
      let card = findHistoryCardByTask(path, dishName);
      if(!card){
        await loadHistory(true);
        card = findHistoryCardByTask(path, dishName);
      }
      if(!card){
        setStatus(
          dishName
            ? `运行中的「${dishName}」尚未出现在菜品池，请稍后再点或刷新菜品池。`
            : "当前运行任务还没有对应菜品目录。",
          "warn"
        );
        return;
      }
      const item = {
        path: card.dataset.path,
        dish_name: card.querySelector(".history-name")?.textContent?.trim() || dishName,
      };
      await selectHistoryItem(item, card);
      card.scrollIntoView({block: "nearest", behavior: "smooth"});
      setStatus(`已切到运行中菜品：${item.dish_name}`, "ok");
    }

    const PANEL_COL_MIN = {left: 240, plan: 160, gallery: 280, mid: 320, publish: 96, custom: 180};
    const PANEL_COL_DEFAULT = {left: 720, plan: 200, gallery: 720, mid: 400, publish: 118, custom: 228};

    function clampPanelCol(key, value){
      const min = PANEL_COL_MIN[key] || 120;
      return Math.max(min, Math.round(Number(value) || min));
    }

    function 应用面板栏宽(next={}){
      state.colLeft = clampPanelCol("left", next.left ?? state.colLeft);
      state.colPlan = clampPanelCol("plan", next.plan ?? state.colPlan);
      state.colGallery = clampPanelCol("gallery", next.gallery ?? state.colGallery);
      state.colMid = clampPanelCol("mid", next.mid ?? state.colMid);
      state.colPublish = clampPanelCol("publish", next.publish ?? state.colPublish);
      state.colCustom = clampPanelCol("custom", next.custom ?? state.colCustom);
      document.documentElement.style.setProperty("--col-left", `${state.colLeft}px`);
      document.documentElement.style.setProperty("--col-plan", `${state.colPlan}px`);
      document.documentElement.style.setProperty("--col-gallery", `${state.colGallery}px`);
      document.documentElement.style.setProperty("--col-mid", `${state.colMid}px`);
      document.documentElement.style.setProperty("--col-publish", `${state.colPublish}px`);
      document.documentElement.style.setProperty("--col-custom", `${state.colCustom}px`);
    }

    function 保存面板栏宽(){
      try{
        localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({
          left: state.colLeft,
          plan: state.colPlan,
          gallery: state.colGallery,
          mid: state.colMid,
          publish: state.colPublish,
          custom: state.colCustom,
        }));
      }catch{}
    }

    function 加载面板栏宽(){
      try{
        const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
        if(raw){
          const data = JSON.parse(raw);
          应用面板栏宽({
            left: data.left,
            plan: data.plan,
            gallery: data.gallery,
            mid: data.mid,
            publish: data.publish,
            custom: data.custom,
          });
          return;
        }
        const legacyV2 = localStorage.getItem(LAYOUT_STORAGE_KEY_V2);
        if(legacyV2){
          const data = JSON.parse(legacyV2);
          应用面板栏宽({
            left: data.left,
            plan: 200,
            gallery: data.gallery,
            mid: data.mid,
            publish: data.publish,
            custom: data.custom,
          });
          return;
        }
        const legacy = localStorage.getItem(LAYOUT_STORAGE_KEY_V1);
        if(legacy){
          const data = JSON.parse(legacy);
          应用面板栏宽({
            left: data.left,
            gallery: data.left,
            mid: data.mid,
          });
          return;
        }
      }catch{}
      应用面板栏宽(PANEL_COL_DEFAULT);
    }

    function 加载菜品池列数(){
      try{
        const saved = localStorage.getItem(HISTORY_COLS_STORAGE_KEY);
        if(saved && ["1","2","3","auto"].includes(saved)){
          state.historyPoolCols = saved;
        }
      }catch{}
      if($("historyColsSelect")){ $("historyColsSelect").value = state.historyPoolCols || "3"; }
      应用菜品池列数(state.historyPoolCols || "3");
    }

    function applyHistorySearchFilter(){
      const q = (state.historySearchQuery || "").trim().toLowerCase();
      const mealFilter = state.historyMealFilter || "";
      const inPlan = collectPlanAssignedPaths();
      let visible = 0;
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
        const path = normalizeHistoryPath(el.dataset.path || "");
        const name = el.querySelector(".history-name")?.textContent?.trim().toLowerCase() || "";
        let tags = [];
        try{ tags = JSON.parse(el.dataset.mealTags || "[]"); }catch{}
        let show = !q || name.includes(q);
        if(mealFilter && !tags.includes(mealFilter)){ show = false; }
        if(inPlan.has(path)){ show = false; }
        el.classList.toggle("in-publish-plan", inPlan.has(path));
        el.style.display = show ? "" : "none";
        if(show){ visible++; }
      });
      const placeholder = $("history").querySelector(":scope > .history-empty");
      if(placeholder && q && visible === 0){
        placeholder.textContent = "没有匹配的菜名";
        placeholder.style.display = "";
      }else if(placeholder && !q){
        placeholder.textContent = "暂无菜品";
      }
    }

    function getSelectedPublishPlatforms(){
      return Array.from($("publishPlatforms").querySelectorAll('input[type="checkbox"]:checked'))
        .map((el) => el.value)
        .filter(Boolean);
    }

    function setPublishStatus(text, level=""){
      const box = $("publishStatus");
      box.textContent = text;
      box.className = "publish-status" + (level ? (" " + level) : "");
    }

    function isFetchNetworkError(err){
      const msg = String(err?.message || err || "");
      return err instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(msg);
    }

    async function pingPanel(timeoutMs=4000){
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      try{
        const res = await fetch("/api/ping", {cache:"no-store", signal: ctrl.signal});
        return res.ok;
      }catch{
        return false;
      }finally{
        clearTimeout(timer);
      }
    }

    function notePanelReachable(){
      state.panelPingFailStreak = 0;
      if(!state.panelOnline){ setPanelOnlineUi(true); }
    }

    async function refreshPanelOnlineUi(){
      const ok = await pingPanel();
      if(ok){
        notePanelReachable();
        return true;
      }
      state.panelPingFailStreak += 1;
      if(state.panelPingFailStreak >= 3){ setPanelOnlineUi(false); }
      return false;
    }

    function setPanelOnlineUi(online){
      state.panelOnline = Boolean(online);
      if(online){ state.panelPingFailStreak = 0; }
      const tag = $("panelOfflineTag");
      if(tag){ tag.classList.toggle("hidden", online); }
      const publishRunning = Boolean(state.publishSnapshot?.running);
      if($("publishBtn")){
        $("publishBtn").disabled = !online || publishRunning;
      }
      if($("runBtn")){
        $("runBtn").disabled = !online;
      }
    }

    async function ensurePanelOnline(actionLabel="操作"){
      if(await refreshPanelOnlineUi()){
        return true;
      }
      if(state.panelOnline){ return true; }
      const msg = `面板未连接（8765 无响应），无法${actionLabel}。请点击顶栏「重启程序」。`;
      setStatus(msg, "warn");
      setPublishStatus(msg, "warn");
      return false;
    }

    function showLoginModal(message){
      $("loginModalMsg").textContent = message || "请在浏览器中完成平台登录后，点击下方按钮继续。";
      $("loginModal").classList.remove("hidden");
    }

    function hideLoginModal(){
      $("loginModal").classList.add("hidden");
    }

    function startPublishStatusPolling(){
      stopPublishStatusPolling();
      state.publishPollTimer = setInterval(() => fetchPublishStatus({silent: true}), 500);
    }

    function stopPublishStatusPolling(){
      if(state.publishPollTimer){
        clearInterval(state.publishPollTimer);
        state.publishPollTimer = null;
      }
    }

    async function confirmPublishLogin(){
      try{
        const res = await fetch("/api/publish_login_confirm", {method:"POST"});
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "确认失败"); }
        hideLoginModal();
        setPublishStatus("已确认登录，发布脚本继续执行…");
      }catch(err){
        setPublishStatus("登录确认失败：" + err.message, "warn");
      }
    }

    async function fetchPublishStatus(options = {}){
      const silent = Boolean(options?.silent);
      try{
        const res = await fetch(`/api/publish_status?from=${state.publishLogIndex}`);
        const data = await res.json();
        notePanelReachable();
        if(data.logs?.length){
          const tail = data.logs.slice(-6).join("\\n");
          setPublishStatus(tail || "发布中…");
          state.publishLogIndex = data.next_log_index || state.publishLogIndex;
          data.logs.forEach((line) => appendLogs(["[发布] " + line]));
        }
        if(data.login_required){
          showLoginModal(data.login_message || "请在浏览器中完成登录。");
        }else{
          hideLoginModal();
        }
        const publishDir = data.output_dir || state.selectedHistoryPath || "";
        const publishDishName = state.selectedHistoryItem?.dish_name
          || (publishDir ? publishDir.split(/[\\\\/]/).pop() : "");
        state.publishSnapshot = {
          running: Boolean(data.running),
          platform: data.current_platform || "",
          output_dir: publishDir,
          dish_name: publishDishName,
        };
        renderTaskBar();
        $("publishBtn").disabled = Boolean(data.running);
        $("publishBtn").textContent = data.running
          ? `发布中：${data.current_platform || "…"}`
          : "发布";
        if(!data.running && state.publishPolling){
          state.publishPolling = false;
          stopPublishStatusPolling();
          if(data.error){
            if(!silent){ setPublishStatus("发布失败：\\n" + data.error, "warn"); }
          }else if(state.publishLogIndex > 0){
            if(!silent){ setPublishStatus("发布流程已执行完成，请在各平台浏览器核对。", "ok"); }
          }
        }
        if(data.running){ state.publishPolling = true; }
      }catch(err){
        if(isFetchNetworkError(err)){
          setPanelOnlineUi(false);
          if(!silent){
            setPublishStatus("面板未连接，请点击顶栏「重启程序」。", "warn");
          }
          return;
        }
        if(!silent){ setPublishStatus("读取发布状态失败：" + err.message, "warn"); }
      }
    }

    async function shutdownPanelProgram(){
      if(!confirm("确定终止造菜控制台？\\n进行中的造菜/发布任务会被中断，8765 端口服务将停止。")){ return; }
      setStatus("正在终止程序…", "warn");
      try{
        await fetch("/api/panel_shutdown", {method:"POST"});
      }catch{}
      stopPublishStatusPolling();
      stopPolling();
      setPanelOnlineUi(false);
      setTimeout(() => {
        setStatus("程序已终止。请点击顶栏「重启程序」恢复服务。", "warn");
        setPublishStatus("面板已停止，发布/造菜不可用。", "warn");
      }, 800);
    }

    async function restartPanelProgram(){
      if(!confirm("确定重启造菜控制台？\\n页面将在服务恢复后自动刷新。")){ return; }
      setStatus("正在重启程序，请稍候…");
      try{
        await fetch("/api/panel_restart", {method:"POST"});
      }catch{}
      stopPublishStatusPolling();
      stopPolling();
      let tries = 0;
      const timer = setInterval(async () => {
        tries += 1;
        try{
          const res = await fetch("/api/state", {cache:"no-store"});
          if(res.ok){
            clearInterval(timer);
            setStatus("程序已重启，正在刷新页面…", "ok");
            location.reload();
          }
        }catch{}
        if(tries >= 45){
          clearInterval(timer);
          setStatus("重启超时，请手动双击 restart_v2_web_panel.bat。", "warn");
        }
      }, 1000);
    }

    async function startPublish(){
      if(!await ensurePanelOnline("发布")){ return; }
      const outputPath = (state.selectedHistoryPath || state.currentOutputPath || "").trim();
      if(!outputPath || outputPath === "-"){
        setPublishStatus("请先在菜品池或发布计划选择一个菜品。", "warn");
        return;
      }
      const platforms = getSelectedPublishPlatforms();
      if(!platforms.length){
        setPublishStatus("请至少勾选一个发布平台。", "warn");
        return;
      }
      state.publishLogIndex = 0;
      setPublishStatus("正在启动发布任务…");
      try{
        const res = await fetch("/api/publish_start", {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({output_dir: outputPath, platforms})
        });
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "发布启动失败"); }
        state.publishPolling = true;
        startPublishStatusPolling();
        appendLogs([`[${new Date().toLocaleTimeString()}] 发布任务已启动：${platforms.join("、")}`]);
        $("publishBtn").disabled = true;
        await fetchPublishStatus();
      }catch(err){
        if(isFetchNetworkError(err)){
          setPanelOnlineUi(false);
          setPublishStatus("面板未连接，请点击顶栏「重启程序」后再发布。", "warn");
          return;
        }
        setPublishStatus("发布失败：" + err.message, "warn");
      }
    }

    function 初始化拖拽分栏(){
      const splitters = [
        {el: $("splitterLeft"), key: "left"},
        {el: $("splitterPlan"), key: "plan"},
        {el: $("splitterGallery"), key: "gallery"},
        {el: $("splitterMid"), key: "mid"},
        {el: $("splitterPublish"), key: "publish"},
      ].filter((item) => item.el);

      const colStateKey = {left: "colLeft", plan: "colPlan", gallery: "colGallery", mid: "colMid", publish: "colPublish"};
      const bindDrag = (el, key) => {
        el.onmousedown = (e) => {
          e.preventDefault();
          const startX = e.clientX;
          const startWidth = state[colStateKey[key]];
          const moveHandler = (event) => {
            const dx = event.clientX - startX;
            应用面板栏宽({[key]: startWidth + dx});
          };
          const upHandler = () => {
            window.removeEventListener("mousemove", moveHandler);
            window.removeEventListener("mouseup", upHandler);
            保存面板栏宽();
          };
          window.addEventListener("mousemove", moveHandler);
          window.addEventListener("mouseup", upHandler);
        };
      };

      splitters.forEach(({el, key}) => bindDrag(el, key));
    }

    function 同步参数滑块(){
      const t = Math.max(0, Math.min(15, Math.round((Number($("temperature").value || "0") * 10))));
      $("temperatureSlider").value = String(t);
    }

    function 绑定参数滑块(){
      $("temperatureSlider").oninput = () => { $("temperature").value = (Number($("temperatureSlider").value) / 10).toFixed(1); };
      $("temperature").oninput = 同步参数滑块;
    }

    function filterLogLines(lines){
      if(!state.logOnlyErrors){ return lines; }
      return (lines || []).filter((line) => {
        const txt = String(line || "").toLowerCase();
        return txt.includes("失败") || txt.includes("error") || txt.includes("traceback") || txt.includes("异常");
      });
    }

    function renderLogPanel(lines, options = {}){
      const panel = $("logPanel");
      if(!panel){ return; }
      const filtered = filterLogLines(lines || []);
      const keepScroll = Boolean(options.keepScroll);
      const atBottom = keepScroll && panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 24;
      panel.textContent = filtered.length ? filtered.join("\\n") + "\\n" : "等待任务启动...\\n";
      if(!keepScroll || atBottom){ panel.scrollTop = panel.scrollHeight; }
    }

    function appendLogs(lines){
      if(!lines?.length){ return; }
      const panel = $("logPanel");
      const atBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 24;
      const filtered = filterLogLines(lines);
      if(!filtered.length){ return; }
      for(const line of filtered){
        panel.textContent += line + "\\n";
      }
      if(panel.textContent.length > 500000){
        panel.textContent = panel.textContent.slice(panel.textContent.length - 400000);
      }
      if(atBottom){ panel.scrollTop = panel.scrollHeight; }
    }

    async function reloadAllLogs(options = {}){
      const silent = Boolean(options?.silent);
      try{
        const res = await fetch("/api/run_status?from=0");
        const data = await res.json();
        state.logNextIndex = data.next_index || 0;
        renderLogPanel(data.logs || [], {keepScroll: false});
        if(!silent){ setStatus(`已加载 ${state.logNextIndex} 行日志。`, "ok"); }
      }catch(err){
        if(!silent){ setStatus("加载日志失败：" + err.message, "warn"); }
      }
    }

    async function copyLogPanel(){
      const text = ($("logPanel")?.textContent || "").trim();
      if(!text || text === "等待任务启动..."){ setStatus("当前没有可复制日志。", "warn"); return; }
      await copyText(text, "日志已复制到剪贴板。");
    }

    function 切换日志抽屉(打开){
      $("logDrawer").classList.toggle("open", 打开);
      $("logToggleBtn").textContent = 打开 ? "收起日志" : "日志";
      if(打开){ reloadAllLogs({silent: true}); }
    }

    function updateGalleryIndexLabel(){
      const total = state.galleryImages.length;
      const current = total ? state.galleryIndex + 1 : 0;
      $("galleryIndex").textContent = `${current} / ${total}`;
      $("prevImgBtn").disabled = total < 2;
      $("nextImgBtn").disabled = total < 2;
      updateAnchorPosterButtonState();
    }

    function inferPublishKindFromFileName(fileName){
      const name = String(fileName || "").toLowerCase();
      if(name.includes("封面") || name.includes("cover") || name.includes("fengmian")){ return "封面图"; }
      if(name.includes("细节") || name.includes("detail") || name.includes("xijietu")){ return "细节图"; }
      if(name.includes("菜谱") || name.includes("recipe") || name.includes("caipu")){ return "菜谱图"; }
      if(name.includes("海报") || name.includes("poster")){ return "海报"; }
      return "未知类型";
    }

    function refreshThumbSelectionClasses(){
      const selected = new Set(state.galleryReplaceSelected || []);
      Array.from($("thumbs").querySelectorAll(".thumb")).forEach((el, idx) => {
        const path = state.galleryImages[idx];
        el.classList.toggle("selected", selected.has(path));
      });
    }

    function exitGalleryReplaceMode(){
      state.galleryReplaceMode = false;
      state.galleryReplaceSelected = [];
      refreshThumbSelectionClasses();
      updateGalleryActionHint();
    }

    function toggleGalleryReplaceSelection(imgPath){
      const selected = new Set(state.galleryReplaceSelected || []);
      if(selected.has(imgPath)){ selected.delete(imgPath); }
      else { selected.add(imgPath); }
      state.galleryReplaceSelected = Array.from(selected);
      refreshThumbSelectionClasses();
      updateGalleryActionHint();
    }

    function togglePublishReplaceMode(){
      if(!state.selectedHistoryPath){
        setStatus("请先在左侧菜品池选中一道菜。", "danger");
        return;
      }
      if(state.galleryFilter === "publish"){
        setStatus("请切换到「非发布图」再使用替换。", "warn");
        return;
      }
      state.galleryReplaceMode = !state.galleryReplaceMode;
      if(!state.galleryReplaceMode){
        state.galleryReplaceSelected = [];
        refreshThumbSelectionClasses();
      }
      updateGalleryActionHint();
    }

    function updateGalleryActionHint(){
      const replaceBtn = $("publishReplaceBtn");
      const execBtn = $("publishReplaceExecBtn");
      const anchorBtn = $("anchorPosterRegenBtn");
      const hint = $("anchorPosterHint");
      const hasDish = Boolean(state.selectedHistoryPath);
      const inAllView = state.galleryFilter !== "publish";
      const selectedCount = (state.galleryReplaceSelected || []).length;

      if(replaceBtn){
        replaceBtn.classList.toggle("replace-mode-active", state.galleryReplaceMode);
        replaceBtn.disabled = !hasDish || !inAllView || state.submittingTask;
      }
      if(execBtn){
        execBtn.classList.toggle("hidden", !state.galleryReplaceMode);
        execBtn.disabled = !state.galleryReplaceMode || selectedCount < 1 || !hasDish || state.submittingTask;
      }
      if(anchorBtn){
        const hasImage = Boolean(state.currentImagePath);
        const anchorEnabled = hasDish && hasImage && inAllView && !state.submittingTask && !state.galleryReplaceMode;
        anchorBtn.disabled = !anchorEnabled;
      }
      if(!hint){ return; }
      if(!hasDish){
        hint.textContent = "请先在左侧菜品池选中一道菜。";
        return;
      }
      if(!inAllView){
        hint.textContent = "请切换到「非发布图」再使用替换或锚点补生。";
        return;
      }
      if(state.galleryReplaceMode){
        hint.textContent = selectedCount
          ? `已选 ${selectedCount} 张：点击缩略图可增删选择，再点「执行替换」。`
          : "替换模式：点击缩略图多选要换上的图（海报/细节/菜谱/封面），再点「执行替换」。";
        return;
      }
      const fileName = state.currentImagePath ? 文件名(state.currentImagePath) : "";
      hint.textContent = fileName
        ? `当前：${fileName}。可点「替换」多选换图，或选中海报后「设为海报并补生」。`
        : "非发布图中可「替换」已选发布图，或选中海报后「设为海报并补生」。";
    }

    function updateAnchorPosterButtonState(){
      updateGalleryActionHint();
    }

    async function submitPublishImageReplace(){
      if(!state.selectedHistoryPath){
        setStatus("请先在左侧菜品池选中一道菜。", "danger");
        return;
      }
      if(state.galleryFilter === "publish"){
        setStatus("请切换到「非发布图」再执行替换。", "warn");
        return;
      }
      const paths = (state.galleryReplaceSelected || []).filter(Boolean);
      if(!paths.length){
        setStatus("请至少选择一张要替换的图片。", "danger");
        return;
      }
      const lines = paths.map((path) => {
        const kind = inferPublishKindFromFileName(文件名(path));
        return `- ${kind}：${文件名(path)}`;
      });
      const message = [
        "将把以下非发布图复制到 publish/，并 PS 合成后更新 publish/final/ 对应槽位：",
        "",
        ...lines,
        "",
        "原 publish 同类型文件与 final 对应图会被替换。确定执行？"
      ].join("\\n");
      if(!confirm(message)){ return; }
      await submitTask({
        action: "publish_image_replace",
        target_output_dir: state.selectedHistoryPath,
        replacement_paths: paths,
      }, "发布图替换任务已提交。");
      exitGalleryReplaceMode();
    }

    async function submitAnchorPosterRegenerate(){
      if(!state.selectedHistoryPath){
        setStatus("请先在左侧菜品池选中一道菜。", "danger");
        return;
      }
      if(!state.currentImagePath){
        setStatus("请先在非发布图中选中一张候选海报。", "danger");
        return;
      }
      if(state.galleryFilter === "publish"){
        setStatus("请切换到「非发布图」再选择候选海报。", "warn");
        return;
      }
      const fileName = 文件名(state.currentImagePath);
      const detailCount = $("detailCount").value.trim() || "1";
      const recipeCount = $("recipeCount").value.trim() || "1";
      const coverCount = $("coverMode2Count").value.trim() || "1";
      const message = [
        `将使用当前图片作为海报锚点：${fileName}`,
        "",
        "执行前会把现有 publish/ 与 publish/final/ 移入本菜品 history/ 存档（可手动恢复）。",
        `随后补生：细节图 ×${detailCount}、菜谱图 ×${recipeCount}、封面图 ×${coverCount}，并完成 PS 合成。`,
        "",
        "确定开始？"
      ].join("\\n");
      if(!confirm(message)){ return; }
      await submitTask({
        action: "anchor_poster_regenerate",
        target_output_dir: state.selectedHistoryPath,
        anchor_poster_path: state.currentImagePath,
        detail_quality: $("detailQuality").value.trim(),
        detail_count: detailCount,
        recipe_quality: $("recipeQuality").value.trim(),
        recipe_count: recipeCount,
        cover_mode2_quality: $("coverMode2Quality").value.trim(),
        cover_mode2_count: coverCount,
        ...collectImageGenControls()
      }, "锚点海报补生任务已提交。");
    }

    function openImageInNewTab(imagePath){
      if(!imagePath){
        setStatus("当前没有可打开的图片。", "warn");
        return;
      }
      window.open(fileUrl(imagePath), "_blank", "noopener,noreferrer");
    }

    function openGalleryLightbox(index){
      if(!state.galleryImages.length){ return; }
      const total = state.galleryImages.length;
      const safe = ((Number(index) % total) + total) % total;
      state.galleryLightboxOpen = true;
      state.galleryLightboxIndex = safe;
      updateGalleryLightboxImage();
      $("galleryLightbox").classList.remove("hidden");
    }

    function closeGalleryLightbox(){
      state.galleryLightboxOpen = false;
      state.lightboxSource = "gallery";
      $("galleryLightbox").classList.add("hidden");
      $("galleryLightboxImg").removeAttribute("src");
    }

    function updateGalleryLightboxImage(){
      const total = state.galleryImages.length;
      if(!total){ return; }
      const path = state.galleryImages[state.galleryLightboxIndex];
      $("galleryLightboxImg").src = fileUrl(path);
      $("galleryLightboxCounter").textContent = `${state.galleryLightboxIndex + 1} / ${total}`;
      const showNav = total > 1;
      $("galleryLightboxPrev").style.display = showNav ? "" : "none";
      $("galleryLightboxNext").style.display = showNav ? "" : "none";
    }

    function stepGalleryLightbox(delta){
      if(!state.galleryImages.length){ return; }
      const total = state.galleryImages.length;
      state.galleryLightboxIndex = (state.galleryLightboxIndex + delta + total) % total;
      updateGalleryLightboxImage();
      if(state.lightboxSource !== "plan"){
        showGalleryImage(state.galleryLightboxIndex);
      }
    }

    function 显示无图占位(提示="暂未生成图片"){
      $("resultImg").removeAttribute("src");
      $("resultImg").classList.add("hidden");
      $("emptyImageState").style.display = "flex";
      $("emptyImageState").querySelector(".empty-image-title").textContent = 提示;
      state.currentImagePath = "";
      updateGalleryIndexLabel();
    }

    function 文件名(path){
      if(!path){ return ""; }
      const normalized = String(path).replaceAll("\\\\", "/");
      const parts = normalized.split("/");
      return parts[parts.length - 1] || path;
    }

    function setRightTab(tab){
      state.activeRightTab = tab === "text" ? "text" : "image";
      $("imageTabBtn").classList.toggle("active", state.activeRightTab === "image");
      $("textTabBtn").classList.toggle("active", state.activeRightTab === "text");
      $("imagePane").classList.toggle("hidden", state.activeRightTab !== "image");
      $("textPane").classList.toggle("hidden", state.activeRightTab !== "text");
    }

    function captureTextEditorDraft(){
      if(!state.textEditorEditing){ return; }
      const editor = $("textEditor");
      if(!editor || !state.activeTextFilePath){ return; }
      state.textEditorDrafts[state.activeTextFilePath] = editor.value;
    }

    function tryLeaveTextEditorEdit(){
      if(!state.textEditorEditing){ return true; }
      if(state.textEditorDirty){
        if(!confirm("当前文案有未保存修改，确定放弃并切换？")){ return false; }
      }
      const activeFile = getActiveTextFile();
      if(activeFile){
        delete state.textEditorDrafts[activeFile.path];
      }
      state.textEditorEditing = false;
      state.textEditorDirty = false;
      return true;
    }

    function enterTextEditorEdit(){
      state.textEditorEditing = true;
      renderTextAssets();
      const editor = $("textEditor");
      if(editor){
        editor.focus();
        editor.setSelectionRange(editor.value.length, editor.value.length);
      }
    }

    function cancelTextEditorEdit(){
      const activeFile = getActiveTextFile();
      if(activeFile){
        delete state.textEditorDrafts[activeFile.path];
      }
      state.textEditorEditing = false;
      state.textEditorDirty = false;
      state.textSaveStatus = "";
      renderTextAssets();
    }

    function getActiveTextFile(){
      const groups = state.textAssets?.groups || [];
      for(const group of groups){
        const file = group.files.find((item) => item.path === state.activeTextFilePath);
        if(file){ return file; }
      }
      return null;
    }

    function resolveTextEditorContent(activeFile){
      if(!activeFile){ return ""; }
      if(Object.prototype.hasOwnProperty.call(state.textEditorDrafts, activeFile.path)){
        return state.textEditorDrafts[activeFile.path];
      }
      return activeFile.content || "";
    }

    function updateTextEditorDirty(activeFile, currentValue){
      if(!activeFile){
        state.textEditorDirty = false;
        return;
      }
      state.textEditorDirty = String(currentValue) !== String(activeFile.content || "");
    }

    function renderTextAssets(){
      captureTextEditorDraft();
      const panel = $("textPanel");
      const payload = state.textAssets;
      const groups = payload?.groups || [];
      if(!groups.length){
        panel.innerHTML = `<div class="text-empty">当前菜品目录里还没有可显示的 txt 文案。</div>`;
        return;
      }
      if(!state.activeTextCategory || !groups.some((group) => group.key === state.activeTextCategory)){
        state.activeTextCategory = groups[0].key;
      }
      const activeGroup = groups.find((group) => group.key === state.activeTextCategory) || groups[0];
      if(!state.activeTextFilePath || !activeGroup.files.some((file) => file.path === state.activeTextFilePath)){
        state.activeTextFilePath = activeGroup.files[0]?.path || "";
      }
      const activeFile = activeGroup.files.find((file) => file.path === state.activeTextFilePath) || activeGroup.files[0];
      const editorValue = resolveTextEditorContent(activeFile);
      updateTextEditorDirty(activeFile, editorValue);

      panel.innerHTML = "";
      const tabs = document.createElement("div");
      tabs.className = "text-tabs";
      groups.forEach((group) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "text-tab" + (group.key === state.activeTextCategory ? " active" : "");
        btn.textContent = `${group.label}（${group.files.length}）`;
        btn.onclick = () => {
          if(!tryLeaveTextEditorEdit()){ return; }
          state.activeTextCategory = group.key;
          state.activeTextFilePath = "";
          renderTextAssets();
        };
        tabs.appendChild(btn);
      });
      panel.appendChild(tabs);

      const fileList = document.createElement("div");
      fileList.className = "text-file-list";
      activeGroup.files.forEach((file) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "text-file-btn" + (file.path === state.activeTextFilePath ? " active" : "");
        btn.textContent = file.name;
        btn.title = file.relative_path;
        btn.onclick = () => {
          if(!tryLeaveTextEditorEdit()){ return; }
          state.activeTextFilePath = file.path;
          renderTextAssets();
        };
        fileList.appendChild(btn);
      });
      panel.appendChild(fileList);

      const meta = document.createElement("div");
      meta.className = "text-file-meta";
      meta.textContent = activeFile ? activeFile.relative_path : "";
      panel.appendChild(meta);

      const bar = document.createElement("div");
      bar.className = "text-editor-bar";
      const hint = document.createElement("div");
      hint.id = "textEditorHint";
      hint.className = "text-editor-hint" + (state.textEditorDirty ? " dirty" : "");
      if(state.textEditorEditing){
        hint.textContent = state.textEditorDirty
          ? "编辑中，有未保存修改。"
          : "编辑中，Ctrl+S 或点击保存写入磁盘。";
      }else{
        hint.textContent = "只读预览，点击「编辑」后可修改并保存。";
      }
      const actions = document.createElement("div");
      actions.className = "text-editor-actions";
      if(state.textEditorEditing){
        const copyBtn = document.createElement("button");
        copyBtn.id = "textCopyBtn";
        copyBtn.type = "button";
        copyBtn.textContent = "复制";
        copyBtn.disabled = !activeFile;
        copyBtn.onclick = () => {
          const el = $("textEditor");
          if(el){ copyText(el.value, "文案已复制到剪贴板。"); }
        };
        const saveBtn = document.createElement("button");
        saveBtn.id = "textSaveBtn";
        saveBtn.type = "button";
        saveBtn.className = "primary";
        saveBtn.textContent = state.textEditorDirty ? "保存*" : "保存";
        saveBtn.disabled = !activeFile;
        saveBtn.onclick = () => { saveTextAsset(); };
        const cancelBtn = document.createElement("button");
        cancelBtn.id = "textCancelBtn";
        cancelBtn.type = "button";
        cancelBtn.textContent = "取消";
        cancelBtn.onclick = () => { cancelTextEditorEdit(); };
        actions.appendChild(copyBtn);
        actions.appendChild(saveBtn);
        actions.appendChild(cancelBtn);
      }else{
        const copyBtn = document.createElement("button");
        copyBtn.id = "textCopyBtn";
        copyBtn.type = "button";
        copyBtn.textContent = "复制";
        copyBtn.disabled = !activeFile;
        copyBtn.onclick = () => {
          const el = $("textEditor");
          if(el){ copyText(el.value, "文案已复制到剪贴板。"); }
        };
        const editBtn = document.createElement("button");
        editBtn.id = "textEditBtn";
        editBtn.type = "button";
        editBtn.className = "primary";
        editBtn.textContent = "编辑";
        editBtn.disabled = !activeFile;
        editBtn.onclick = () => { enterTextEditorEdit(); };
        actions.appendChild(copyBtn);
        actions.appendChild(editBtn);
      }
      bar.appendChild(hint);
      bar.appendChild(actions);
      panel.appendChild(bar);

      const editor = document.createElement("textarea");
      editor.id = "textEditor";
      editor.className = "text-editor" + (state.textEditorEditing ? "" : " readonly");
      editor.spellcheck = false;
      editor.readOnly = !state.textEditorEditing;
      editor.value = editorValue;
      editor.disabled = !activeFile;
      editor.addEventListener("input", () => {
        if(!activeFile || !state.textEditorEditing){ return; }
        state.textEditorDrafts[activeFile.path] = editor.value;
        updateTextEditorDirty(activeFile, editor.value);
        const hintEl = $("textEditorHint");
        const saveEl = $("textSaveBtn");
        if(hintEl){
          hintEl.classList.toggle("dirty", state.textEditorDirty);
          hintEl.textContent = state.textEditorDirty
            ? "编辑中，有未保存修改。"
            : "编辑中，Ctrl+S 或点击保存写入磁盘。";
        }
        if(saveEl){
          saveEl.textContent = state.textEditorDirty ? "保存*" : "保存";
        }
      });
      panel.appendChild(editor);

      if(state.textSaveStatus){
        const status = document.createElement("div");
        status.className = "status";
        status.textContent = state.textSaveStatus;
        panel.appendChild(status);
      }
    }

    async function saveTextAsset(){
      if(!state.textEditorEditing){ return; }
      const activeFile = getActiveTextFile();
      const editor = $("textEditor");
      if(!activeFile || !editor){ return; }
      const content = editor.value;
      const saveBtn = $("textSaveBtn");
      if(saveBtn){ saveBtn.disabled = true; saveBtn.textContent = "保存中…"; }
      try{
        const res = await fetch("/api/text_assets_save", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ path: activeFile.path, content }),
        });
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "保存失败"); }
        activeFile.content = content;
        delete state.textEditorDrafts[activeFile.path];
        state.textEditorDirty = false;
        state.textEditorEditing = false;
        state.textSaveStatus = "已保存：" + activeFile.name;
        renderTextAssets();
      }catch(err){
        state.textSaveStatus = "保存失败：" + String(err.message || err);
        renderTextAssets();
      }
    }

    async function loadTextAssets(path){
      captureTextEditorDraft();
      state.textAssets = null;
      state.activeTextCategory = "";
      state.activeTextFilePath = "";
      state.textEditorDrafts = {};
      state.textEditorDirty = false;
      state.textEditorEditing = false;
      state.textSaveStatus = "";
      $("textPanel").innerHTML = `<div class="text-empty">正在读取文案...</div>`;
      if(!path){
        $("textPanel").innerHTML = `<div class="text-empty">点击左侧某个菜品后，这里会显示它的造菜信息、平台文案、提示词和其他 txt 文案。</div>`;
        return;
      }
      try{
        const res = await fetch("/api/text_assets?path=" + encodeURIComponent(path));
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "读取文案失败"); }
        state.textAssets = data;
        renderTextAssets();
      }catch(err){
        const msg = document.createElement("div");
        msg.className = "text-empty";
        msg.textContent = "读取文案失败：" + String(err.message || err);
        $("textPanel").innerHTML = "";
        $("textPanel").appendChild(msg);
      }
    }

    function 选图方式文案(mainMode, coverMode){
      const toText = (mode) => {
        if(mode === "direct"){ return "数量=1直入"; }
        if(mode === "scored"){ return "豆包评分"; }
        return "未执行";
      };
      return `主图：${toText(mainMode)} / 封面：${toText(coverMode)}`;
    }

    function 选图方式详情(detail){
      const label = (mode) => {
        const text = String(mode || "").trim();
        if(!text || text === "未执行"){ return "未执行"; }
        if(text === "direct" || text === "fallback_direct"){ return "数量=1直入"; }
        if(text === "scored"){ return "豆包评分"; }
        return text;
      };
      return [
        `海报：${label(detail?.poster_selection_mode)}`,
        `细节：${label(detail?.detail_selection_mode)}`,
        `菜谱：${label(detail?.recipe_selection_mode)}`,
        `封面：${label(detail?.cover_selection_mode)}`,
      ].join(" / ");
    }

    function 展示字段(value, fallback="暂无"){
      const text = String(value || "").trim();
      if(!text || text === "未记录"){ return fallback; }
      return text;
    }

    function applyDishDetail(detail){
      if(!detail){ return; }
      更新结果信息(
        detail.dish_name,
        展示字段(detail.reference_dish, "未记录"),
        展示字段(detail.region_label, "未记录"),
        detail.output_dir,
        detail.poster_selected_image || "",
        detail.cover_selected_image || "",
        detail.poster_selection_mode || "",
        detail.cover_selection_mode || "",
        detail.photoshop_processed_files || [],
        detail.photoshop_error || ""
      );
      $("rPickMode").textContent = 选图方式详情(detail);
      $("rPsStatus").textContent = PS状态文案(detail.photoshop_processed_files, detail.photoshop_error);
      $("rWorkflow").textContent = detail.workflow_status || "暂无";
      const allImages = detail.images || [];
      const publishImages = detail.publish_images || [];
      setGallerySource(allImages, publishImages.length ? publishImages : allImages);
      if(detail.workflow_status){
        $("resultMsg").textContent = detail.workflow_status;
        $("resultMsg").className = "status ok";
      }
    }

    async function loadDishDetail(path){
      if(!path){
        return null;
      }
      const res = await fetch("/api/dish_detail?path=" + encodeURIComponent(path));
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "读取菜品详情失败"); }
      applyDishDetail(data);
      return data;
    }

    function PS状态文案(psFiles, psError){
      if(psError){ return `失败：${psError}`; }
      const count = Array.isArray(psFiles) ? psFiles.length : 0;
      if(count > 0){ return `已覆盖 ${count} 张`; }
      return "未执行";
    }

    function 更新结果信息(菜名, 参考菜, 菜系, 输出目录, 主图首选="", 封面首选="", 主图方式="", 封面方式="", psFiles=[], psError=""){
      $("rDish").textContent = 菜名 || "暂无";
      $("rRef").textContent = 参考菜 || "暂无";
      $("rRegion").textContent = 菜系 || "暂无";
      $("rOut").textContent = 输出目录 || "-";
      $("rBestMain").textContent = 文件名(主图首选) || "暂无";
      $("rBestCover").textContent = 文件名(封面首选) || "暂无";
      $("rPickMode").textContent = 选图方式文案(主图方式, 封面方式);
      $("rPsStatus").textContent = PS状态文案(psFiles, psError);
      state.currentOutputPath = 输出目录 || "";
    }

    function showGalleryImage(index){
      const total = state.galleryImages.length;
      if(!total){
        $("thumbs").innerHTML = "";
        显示无图占位("暂未生成图片");
        return;
      }
      const safe = (index + total) % total;
      state.galleryIndex = safe;
      state.currentImagePath = state.galleryImages[safe];
      $("resultImg").src = fileUrl(state.currentImagePath);
      $("resultImg").classList.remove("hidden");
      $("emptyImageState").style.display = "none";
      updateGalleryIndexLabel();
      const thumbs = Array.from($("thumbs").querySelectorAll(".thumb"));
      thumbs.forEach((el, idx) => el.classList.toggle("active", idx === safe));
    }

    function isUnderPublishDir(imagePath){
      const normalized = String(imagePath || "").replaceAll("\\\\", "/").toLowerCase();
      return normalized.includes("/publish/");
    }

    function rebuildGalleryNonPublishImages(){
      state.galleryNonPublishImages = (state.galleryAllImages || []).filter((path) => !isUnderPublishDir(path));
    }

    function setGallerySource(allImages, publishImages){
      state.galleryAllImages = (allImages || []).filter(Boolean);
      state.galleryPublishImages = (publishImages || []).filter(Boolean);
      rebuildGalleryNonPublishImages();
      applyGalleryFilter(false);
    }

    function applyGalleryFilter(resetIndex=true){
      if(state.galleryFilter === "publish"){
        exitGalleryReplaceMode();
      }
      const images = state.galleryFilter === "publish"
        ? state.galleryPublishImages
        : state.galleryNonPublishImages;
      $("filterPublishBtn").classList.toggle("active", state.galleryFilter === "publish");
      $("filterAllBtn").classList.toggle("active", state.galleryFilter === "all");
      renderGallery(images, resetIndex);
      updateGalleryActionHint();
    }

    function renderGallery(images, resetIndex=true){
      state.galleryImages = (images || []).filter(Boolean);
      if(resetIndex){ state.galleryIndex = 0; }
      else if(state.galleryIndex >= state.galleryImages.length){ state.galleryIndex = 0; }
      const box = $("thumbs");
      box.innerHTML = "";
      state.galleryImages.forEach((imgPath, idx) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "thumb" + (idx === 0 ? " active" : "");
        btn.innerHTML = `<img src="${fileUrl(imgPath)}" alt="缩略图${idx+1}" />`;
        btn.onclick = () => {
          if(state.galleryReplaceMode){
            toggleGalleryReplaceSelection(imgPath);
            return;
          }
          showGalleryImage(idx);
        };
        btn.ondblclick = () => openGalleryLightbox(idx);
        box.appendChild(btn);
      });
      showGalleryImage(0);
      refreshThumbSelectionClasses();
    }

    function renderResult(result){
      state.currentResult = result || null;
      if(result?.run_kind === "idea_batch"){
        setGallerySource([], []);
        更新结果信息(
          result?.dish_name,
          "",
          "",
          result?.batch_dir || result?.output_dir,
          "",
          "",
          "",
          "",
          [],
          ""
        );
        $("rPickMode").textContent = "仅造菜信息";
        $("rPsStatus").textContent = "-";
        $("resultMsg").textContent = `已生成 ${result?.count || 0} 条造菜信息，目录：${result?.batch_dir || "-"}`;
        $("resultMsg").className = "status ok";
        return;
      }
      const gallery = []
        .concat(result?.poster_saved_images || [])
        .concat(result?.detail_saved_images || [])
        .concat(result?.recipe_saved_images || [])
        .concat(result?.cover_saved_images || []);
      const allImages = gallery.length ? gallery : (result?.saved_images || []);
      const publishImages = (result?.photoshop_processed_files || result?.publish_final_images || []).filter(Boolean);
      const fallbackPublish = allImages.filter((p) => /[\\\\/]publish[\\\\/]final[\\\\/]/i.test(String(p)));
      setGallerySource(allImages, publishImages.length ? publishImages : fallbackPublish);
      const psFiles = result?.photoshop_processed_files || [];
      const detailFromResult = {
        dish_name: result?.dish_name,
        reference_dish: result?.reference_dish,
        region_label: result?.region_label,
        output_dir: result?.output_dir,
        poster_selected_image: result?.poster_selected_image || "",
        cover_selected_image: result?.cover_selected_image || (result?.cover_saved_images || [])[0] || "",
        poster_selection_mode: result?.poster_selection_mode || "",
        detail_selection_mode: result?.detail_selection_mode || "",
        recipe_selection_mode: result?.recipe_selection_mode || "",
        cover_selection_mode: result?.cover_selection_mode || "",
        photoshop_processed_files: psFiles,
        photoshop_error: result?.photoshop_error || "",
        workflow_status: psFiles.length ? `已合成发布图（${psFiles.length} 张）` : "四组图流程已完成",
        images: allImages,
        publish_images: publishImages.length ? publishImages : fallbackPublish,
      };
      applyDishDetail(detailFromResult);
      const hasError = Boolean(result?.image_error);
      const errText = result?.image_error || "";
      $("resultMsg").textContent = hasError ? ("流程异常：\\n" + errText) : "四组图流程已完成。";
      $("resultMsg").className = "status " + (hasError ? "warn" : "ok");
    }

    function normalizeHistoryPath(path){
      return String(path || "").replace(/[\\\\/]+$/, "").replace(/\\\\/g, "/").toLowerCase();
    }

    function bumpSuppressHistoryAutoReload(){
      state.suppressHistoryAutoReload = Math.max(state.suppressHistoryAutoReload, 4);
    }

    function removeHistoryCardFromDom(path){
      const target = normalizeHistoryPath(path);
      const card = Array.from($("history").querySelectorAll(".history-item")).find(
        (el) => normalizeHistoryPath(el.dataset.path) === target
      );
      if(card){
        card.remove();
        state.historyOffset = Math.max(0, state.historyOffset - 1);
      }
      if(!$("history").querySelector(".history-item")){
        $("history").innerHTML = '<div class="history-empty">暂无菜品</div>';
        $("loadMoreBtn").style.display = "none";
      }
    }

    async function 删除历史(path){
      const res = await fetch("/api/history_delete", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path})
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "移出失败"); }
      setStatus("已移入备用目录：" + (data.archived_to || "dish_archive"), "ok");
      bumpSuppressHistoryAutoReload();
      removeHistoryCardFromDom(path);
      if(state.selectedHistoryPath === path){
        state.selectedHistoryPath = "";
        state.selectedHistoryItem = null;
        state.selectionSource = "pool";
        markSelectedDishUi();
      }
    }

    async function 批量删除历史(paths){
      const list = (paths || []).filter(Boolean);
      if(!list.length){
        setStatus("请先勾选要移出的菜品。", "warn");
        return;
      }
      const res = await fetch("/api/history_batch_delete", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({paths: list})
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "批量移出失败"); }
      setStatus(`已移入备用目录 ${data.deleted_count || list.length} 个菜品。`, "ok");
      if(list.includes(state.selectedHistoryPath)){
        state.selectedHistoryPath = "";
        state.selectedHistoryItem = null;
        state.selectionSource = "pool";
        markSelectedDishUi();
      }
      setBatchEditMode(false);
      bumpSuppressHistoryAutoReload();
      list.forEach((path) => removeHistoryCardFromDom(path));
      renderPublishPlan();
      applyHistorySearchFilter();
    }

    function markSelectedDishUi(){
      const path = normalizeHistoryPath(state.selectedHistoryPath || "");
      const source = state.selectionSource || "pool";
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
        el.classList.toggle("active", source === "pool" && normalizeHistoryPath(el.dataset.path) === path);
      });
      Array.from(document.querySelectorAll(".plan-slot-chip")).forEach((el) => {
        el.classList.toggle("selected", source === "plan" && normalizeHistoryPath(el.dataset.path) === path);
      });
    }

    function reconcileSelectionSource(){
      const path = normalizeHistoryPath(state.selectedHistoryPath || "");
      if(!path){ markSelectedDishUi(); return; }
      const inPlan = collectPlanAssignedPaths().has(path);
      const poolVisible = Array.from($("history").querySelectorAll(".history-item")).some((el) => (
        normalizeHistoryPath(el.dataset.path) === path && el.style.display !== "none"
      ));
      if(inPlan && !poolVisible){
        state.selectionSource = "plan";
      }else if(poolVisible){
        state.selectionSource = "pool";
      }
      markSelectedDishUi();
    }

    async function selectDishByPath(path, source="pool"){
      if(!path){ return; }
      let item = state.historyItemCache[path];
      if(!item?.dish_name || !item?.images?.length){
        try{
          const res = await fetch(`/api/dish_detail?path=${encodeURIComponent(path)}`);
          const detail = await res.json();
          if(res.ok){
            item = {...(item || {}), ...detail, path};
            state.historyItemCache[path] = item;
          }
        }catch{}
      }
      if(!item){ item = {path, dish_name: getDishLabel(path)}; }

      state.selectedHistoryPath = path;
      state.selectedHistoryItem = item;
      state.selectionSource = source;
      markSelectedDishUi();

      try{
        await loadDishDetail(path);
      }catch(err){
        applyDishDetail(item);
        if(isFetchNetworkError(err)){
          setPanelOnlineUi(false);
          $("resultMsg").textContent = "面板未连接，已显示缓存信息。请点击顶栏「重启程序」。";
        }else{
          $("resultMsg").textContent = "读取菜品详情失败，已显示缓存信息：" + String(err.message || err);
        }
        $("resultMsg").className = "status warn";
      }
      if(state.mode === "target"){
        applyTargetDishFromItem(state.selectedHistoryItem || item);
      }
      await loadTextAssets(path);
      if($("resultMsg").className === "status"){
        $("resultMsg").textContent = "已同步菜品目录状态。";
      }
      updateViewContextBar();
      setRunState(state.running, state.runningPercent);
    }

    async function selectHistoryItem(item, card){
      await selectDishByPath(item.path, "pool");
    }

    async function toggleHistoryFavorite(path, button){
      if(!path){ return; }
      try{
        const res = await fetch("/api/history_favorite", {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({path}),
        });
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "收藏操作失败"); }
        if(button){
          button.classList.toggle("active", Boolean(data.favorited));
          button.title = data.favorited ? "取消收藏" : "收藏";
        }
      }catch(err){
        setStatus("收藏失败：" + String(err.message || err), "warn");
      }
    }

    function createHistoryCard(item){
      if(item?.path){ state.historyItemCache[item.path] = item; }
      const mealTags = item.meal_tags || [];
      const div = document.createElement("div");
      div.className = "history-item draggable-pool";
      div.dataset.path = item.path || "";
      div.dataset.mealTags = JSON.stringify(mealTags);
      div.draggable = !state.batchEditMode;
      const cover = item.preview_image
        ? `<img src="${fileUrl(item.preview_image)}" alt="${item.dish_name || item.name}" draggable="false" />`
        : `<div class="history-empty">暂无图片</div>`;
      const tagChecks = MEAL_TAG_KEYS.map((tag) => {
        const checked = mealTags.includes(tag) ? " checked" : "";
        return `<label><input type="checkbox" data-tag="${tag}"${checked} />${mealTagLabel(tag)}</label>`;
      }).join("");
      const timeText = item.created_at || item.name.split("_").slice(0,2).join(" ");
      const favorited = Boolean(item.favorited);
      div.innerHTML = `
        <label class="history-check" title="勾选批量操作">
          <input type="checkbox" data-path="${item.path || ""}" />
        </label>
        <div class="history-cover">
          <div class="history-cover-media">${cover}</div>
          <button type="button" class="history-tag-add" title="餐次标签">+</button>
          <div class="tag-popover">${tagChecks}</div>
        </div>
        <div class="history-meta">
          <div class="history-meta-top">
            <div class="history-name">${item.dish_name || item.name}</div>
            <button type="button" class="history-fav${favorited ? " active" : ""}" data-op="favorite" title="${favorited ? "取消收藏" : "收藏"}">♥</button>
          </div>
          <div class="history-tag-badges">${renderMealTagBadges(mealTags)}</div>
          <div class="history-time">${timeText}</div>
        </div>
        <div class="history-ops">
          <button data-op="open">打开</button>
          <button data-op="copy">复制</button>
          <button data-op="delete" class="del">移出</button>
        </div>
      `;
      div.addEventListener("dragstart", (event) => {
        if(state.batchEditMode){ event.preventDefault(); return; }
        const dishPath = item.path || "";
        event.dataTransfer.setData(DRAG_DISH_MIME, dishPath);
        event.dataTransfer.setData("text/plain", dishPath);
        event.dataTransfer.effectAllowed = "move";
      });
      const checkbox = div.querySelector(".history-check input");
      checkbox.onchange = (e) => {
        e.stopPropagation();
        const checked = Boolean(checkbox.checked);
        div.classList.toggle("batch-selected", checked);
        toggleBatchEditPath(item.path, checked);
      };
      checkbox.onclick = (e) => e.stopPropagation();
      div.onclick = async () => {
        if(state.batchEditMode){
          checkbox.checked = !checkbox.checked;
          div.classList.toggle("batch-selected", checkbox.checked);
          toggleBatchEditPath(item.path, checkbox.checked);
          return;
        }
        await selectHistoryItem(item, div);
      };
      const tagAddBtn = div.querySelector(".history-tag-add");
      const tagPopover = div.querySelector(".tag-popover");
      if(tagAddBtn && tagPopover){
        tagAddBtn.onclick = (event) => {
          event.stopPropagation();
          const willOpen = !tagPopover.classList.contains("open");
          closeAllTagPopovers();
          if(willOpen){
            tagPopover.classList.add("open");
            state.tagPopoverPath = item.path || "";
          }
        };
        tagPopover.onclick = (event) => event.stopPropagation();
        tagPopover.querySelectorAll("input[data-tag]").forEach((input) => {
          input.onchange = async () => {
            const tags = MEAL_TAG_KEYS.filter((tag) => {
              const el = tagPopover.querySelector(`input[data-tag="${tag}"]`);
              return el && el.checked;
            });
            try{
              await saveMealTags(item.path, tags);
            }catch(err){
              setStatus("保存标签失败：" + err.message, "warn");
            }
          };
        });
      }
      const ops = div.querySelector(".history-ops");
      ops.onclick = async (e) => {
        e.stopPropagation();
        const target = e.target;
        if(!(target instanceof HTMLElement)){ return; }
        const op = target.dataset?.op;
        if(op === "favorite"){
          await toggleHistoryFavorite(item.path, target);
          return;
        }
        if(op === "open"){ await openOutputPath(item.path); }
        if(op === "copy"){ await copyText(item.path, "历史目录路径已复制。"); }
        if(op === "delete"){
          try{ await 删除历史(item.path); }catch(err){ setStatus("移出失败：" + err.message, "warn"); }
        }
      };
      const favBtn = div.querySelector(".history-fav");
      if(favBtn){
        favBtn.onclick = async (e) => {
          e.stopPropagation();
          await toggleHistoryFavorite(item.path, favBtn);
        };
      }
      return div;
    }

    function buildHistorySnapshot(items){
      return (items || []).map((item) => String(item?.name || "")).join("|");
    }

    function markSelectedHistoryCard(){
      markSelectedDishUi();
    }

    async function loadHistory(reset=false){
      if(state.historyLoading){ return; }
      state.historyLoading = true;
      try{
        if(reset){
          state.historyOffset = 0;
          state.historyHasMore = true;
          $("history").innerHTML = "";
        }
        if(!state.historyHasMore){ return; }
        const sort = encodeURIComponent(state.historySort || "favorite");
        const url = `/api/history?offset=${state.historyOffset}&limit=${state.historyLimit}&sort=${sort}`;
        const res = await fetch(url);
        const data = await res.json();
        const items = data.items || [];
        if(reset){
          state.historySnapshot = buildHistorySnapshot(items);
        }
        if(items.length < state.historyLimit){ state.historyHasMore = false; }
        items.forEach((item) => {
          const card = createHistoryCard(item);
          card.dataset.path = item.path;
          $("history").appendChild(card);
        });
        state.historyOffset += items.length;
        markSelectedHistoryCard();
        if(state.batchEditMode){
          $("history").classList.add("batch-mode");
          Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
            const path = el.dataset.path || "";
            const checked = state.batchEditSelected.has(path);
            const checkbox = el.querySelector(".history-check input");
            if(checkbox){ checkbox.checked = checked; }
            el.classList.toggle("batch-selected", checked);
            el.draggable = false;
          });
          updateBatchEditUi();
        }
        $("loadMoreBtn").style.display = state.historyHasMore ? "inline-block" : "none";
        if(!$("history").children.length){
          $("history").innerHTML = '<div class="history-empty">暂无菜品</div>';
          $("loadMoreBtn").style.display = "none";
        }
        applyHistoryTaskBadges();
        applyHistorySearchFilter();
        if(collectPlanAssignedPaths().size){
          await hydratePublishPlanCache();
          renderPublishPlan();
          reconcileSelectionSource();
        }
      }finally{
        state.historyLoading = false;
      }
    }

    function startPolling(){
      if(state.pollTimer){ return; }
      state.pollTimer = setInterval(fetchRunStatus, 1000);
    }

    function stopPolling(){
      if(!state.pollTimer){ return; }
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }

    function setCustomStatus(text, level=""){
      const box = $("customStatus");
      if(!box){ return; }
      box.textContent = text;
      box.style.color = level === "warn" ? "#fbbf24" : (level === "ok" ? "#86efac" : "");
    }

    function renderCustomRefs(){
      const box = $("customRefs");
      if(!box){ return; }
      box.innerHTML = "";
      state.customRefs.forEach((item) => {
        const chip = document.createElement("div");
        chip.className = "custom-ref-chip";
        chip.title = item.name || "参考图";
        const img = document.createElement("img");
        img.src = item.previewUrl;
        img.alt = item.name || "参考图";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "×";
        btn.onclick = (event) => {
          event.stopPropagation();
          URL.revokeObjectURL(item.previewUrl);
          state.customRefs = state.customRefs.filter((ref) => ref.id !== item.id);
          renderCustomRefs();
        };
        chip.appendChild(img);
        chip.appendChild(btn);
        box.appendChild(chip);
      });
    }

    function addCustomRefFiles(fileList){
      const files = Array.from(fileList || []).filter((file) => file && file.type.startsWith("image/"));
      if(!files.length){ return; }
      files.forEach((file) => {
        state.customRefs.push({
          id: `${Date.now()}_${Math.random().toString(16).slice(2)}`,
          file,
          name: file.name,
          previewUrl: URL.createObjectURL(file),
        });
      });
      renderCustomRefs();
    }

    function formatCustomJobTime(raw){
      const text = String(raw || "").trim();
      if(!text){ return ""; }
      const match = text.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
      if(match){
        return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
      }
      return text.replace("T", " ").slice(0, 16);
    }

    function groupCustomHistoryTiles(tiles){
      const groups = [];
      const seen = new Map();
      (tiles || []).forEach((tile) => {
        const jobId = tile.job_id || tile.path || `${tile.created_at || "job"}_${tile.slot_index || 0}`;
        if(!seen.has(jobId)){
          const group = {
            job_id: jobId,
            prompt: tile.prompt || "",
            created_at: tile.created_at || "",
            job_status: tile.job_status || "",
            job_error: tile.job_error || "",
            tiles: [],
          };
          seen.set(jobId, group);
          groups.push(group);
        }
        const group = seen.get(jobId);
        if(tile.job_status){ group.job_status = tile.job_status; }
        if(tile.job_error){ group.job_error = tile.job_error; }
        group.tiles.push(tile);
      });
      return groups;
    }

    function renderCustomHistory(tiles){
      const box = $("customHistory");
      if(!box){ return; }
      box.innerHTML = "";
      if(!tiles.length){
        const empty = document.createElement("div");
        empty.className = "custom-history-empty";
        empty.textContent = "暂无历史图片，输入提示词后点击生成。";
        box.appendChild(empty);
        return;
      }
      groupCustomHistoryTiles(tiles).forEach((group) => {
        const doneTiles = group.tiles.filter((tile) => tile.status === "done" && tile.path);
        const pendingCount = group.tiles.filter((tile) => tile.status !== "done").length;
        const jobFailed = group.job_status === "failed";
        const jobRunning = group.job_status === "running";
        const card = document.createElement("div");
        card.className = "custom-job-card";

        const thumbsWrap = document.createElement("div");
        thumbsWrap.className = "custom-job-thumbs" + (doneTiles.length > 1 ? " multi" : "");

        if(doneTiles.length){
          doneTiles.slice(0, 4).forEach((tile, index) => {
            const thumb = document.createElement("div");
            thumb.className = "custom-job-thumb";
            const img = document.createElement("img");
            img.src = fileUrl(tile.path);
            img.alt = group.prompt || "自定义生图";
            img.loading = "lazy";
            thumb.appendChild(img);
            thumb.title = "双击预览";
            thumb.addEventListener("dblclick", (event) => {
              event.stopPropagation();
              openCustomPreview(tile.path);
            });
            if(index === 0){
              thumb.addEventListener("click", () => openCustomPreview(tile.path));
            }
            thumbsWrap.appendChild(thumb);
          });
        }else{
          const pending = document.createElement("div");
          pending.className = "custom-job-thumb pending" + (jobFailed ? " failed" : "");
          pending.textContent = jobFailed ? "失败" : (jobRunning || pendingCount ? "生成中" : "等待中");
          thumbsWrap.appendChild(pending);
        }

        const body = document.createElement("div");
        body.className = "custom-job-body";

        const promptEl = document.createElement("div");
        promptEl.className = "custom-job-prompt";
        promptEl.textContent = group.prompt || "（无提示词）";
        promptEl.title = group.prompt || "";

        const metaEl = document.createElement("div");
        metaEl.className = "custom-job-meta";
        const timeText = formatCustomJobTime(group.created_at);
        let countText = doneTiles.length ? `${doneTiles.length} 张` : (jobRunning || pendingCount ? "生成中" : "0 张");
        if(jobFailed){ countText = "失败"; }
        metaEl.textContent = [timeText, countText].filter(Boolean).join(" · ");

        const actions = document.createElement("div");
        actions.className = "custom-job-actions";
        const previewBtn = document.createElement("button");
        previewBtn.type = "button";
        previewBtn.className = "custom-job-open";
        previewBtn.textContent = "预览";
        previewBtn.disabled = !doneTiles.length;
        previewBtn.onclick = (event) => {
          event.stopPropagation();
          if(doneTiles[0]?.path){ openCustomPreview(doneTiles[0].path); }
        };
        const openBtn = document.createElement("button");
        openBtn.type = "button";
        openBtn.className = "custom-job-open";
        openBtn.textContent = "打开目录";
        openBtn.disabled = !doneTiles.length;
        openBtn.onclick = async (event) => {
          event.stopPropagation();
          const path = doneTiles[0]?.path;
          if(!path){ return; }
          try{
            const dir = dirnamePath(path);
            if(!dir){ setCustomStatus("未找到图片目录。", "warn"); return; }
            await openOutputPath(dir);
          }catch(err){
            setCustomStatus("打开目录失败：" + err.message, "warn");
          }
        };
        actions.appendChild(previewBtn);
        actions.appendChild(openBtn);

        body.appendChild(promptEl);
        body.appendChild(metaEl);
        if(jobFailed && group.job_error){
          const errEl = document.createElement("div");
          errEl.className = "custom-job-error";
          errEl.textContent = group.job_error;
          errEl.title = group.job_error;
          body.appendChild(errEl);
        }
        body.appendChild(actions);
        card.appendChild(thumbsWrap);
        card.appendChild(body);
        box.appendChild(card);
      });
    }

    function openCustomPreview(path){
      if(!path){ return; }
      $("customPreviewImg").src = fileUrl(path);
      $("customPreviewModal").classList.remove("hidden");
    }

    function closeCustomPreview(){
      $("customPreviewModal").classList.add("hidden");
      $("customPreviewImg").src = "";
    }

    async function fetchCustomImageStatus(options = {}){
      const silent = Boolean(options?.silent);
      try{
        const res = await fetch(`/api/custom_image/status?from=${state.customLogNextIndex}`);
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "读取自定义生图状态失败"); }
        if(data.images_dir){ state.customImagesDir = data.images_dir; }
        state.customRunning = Boolean(data.running);
        if(data.logs?.length){
          appendLogs(data.logs.map((line) => "[自定义生图] " + line));
          state.customLogNextIndex = data.log_next_index || state.customLogNextIndex;
        }
        const jobsSnapshot = JSON.stringify(data.jobs || []);
        const tilesSnapshot = JSON.stringify(data.tiles || []);
        if(jobsSnapshot !== state.customJobsSnapshot || tilesSnapshot !== state.customTilesSnapshot){
          state.customJobsSnapshot = jobsSnapshot;
          state.customTilesSnapshot = tilesSnapshot;
          renderCustomHistory(data.tiles || []);
        }
        const btn = $("customGenerateBtn");
        if(btn){
          btn.disabled = Boolean(data.running);
          btn.textContent = data.running ? "生成中…" : "生成";
        }
        if(data.running){
          state.customPolling = true;
          if(!silent){ setCustomStatus("自定义生图进行中…（可在底部日志查看进度）"); }
        }else{
          const wasPolling = state.customPolling;
          state.customPolling = false;
          if(data.error){
            setCustomStatus("生图失败：" + data.error, "warn");
          }else if(wasPolling){
            setCustomStatus("生图完成。图片保存在 V2/custom_image_gen/images/", "ok");
          }
        }
        updateViewContextBar();
      }catch(err){
        state.customRunning = false;
        updateViewContextBar();
        if(!silent){ setCustomStatus("读取状态失败：" + err.message, "warn"); }
      }
    }

    async function startCustomGenerate(){
      const prompt = ($("customPrompt")?.value || "").trim();
      if(!prompt){
        setCustomStatus("请先输入生图提示词。", "warn");
        return;
      }
      const count = Number($("customCount")?.value || "1");
      const fd = new FormData();
      fd.append("prompt", prompt);
      fd.append("count", String(count));
      const imgControls = collectImageGenControls();
      Object.entries(imgControls).forEach(([key, value]) => fd.append(key, value));
      state.customRefs.forEach((ref, index) => {
        fd.append(`ref_${index}`, ref.file, ref.name || `ref_${index}.png`);
      });
      const btn = $("customGenerateBtn");
      if(btn){ btn.disabled = true; btn.textContent = "生成中…"; }
      setCustomStatus("正在提交自定义生图任务…");
      切换日志抽屉(true);
      try{
        const res = await fetch("/api/custom_image/generate", {method:"POST", body: fd});
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "启动失败"); }
        state.customPolling = true;
        appendLogs([`[${new Date().toLocaleTimeString()}] 自定义生图已启动：${count} 张`]);
        await fetchCustomImageStatus();
      }catch(err){
        if(btn){ btn.disabled = false; btn.textContent = "生成"; }
        setCustomStatus("启动失败：" + err.message, "warn");
      }
    }

    function startAutoRefresh(){
      if(state.autoRefreshTimer){ return; }
      state.autoRefreshTimer = setInterval(async () => {
        if(document.hidden){ return; }
        await refreshPanelOnlineUi();
        if(!state.panelOnline){ return; }
        fetchRunStatus({silent: true});
        fetchPublishStatus({silent: true});
        fetchCustomImageStatus({silent: true});
      }, 2000);
    }

    async function fetchRunStatus(options = {}){
      const silent = Boolean(options?.silent);
      try{
        const res = await fetch(`/api/run_status?from=${state.logNextIndex}`);
        const data = await res.json();
        notePanelReachable();
        if(data.logs?.length){
          appendLogs(data.logs);
          state.logNextIndex = data.next_index || state.logNextIndex;
        }
        state.taskQueueSnapshot = data.task_queue || {running: data.current_task || null, queued: []};
        if(data.running && state.taskQueueSnapshot.running){
          state.runningTaskPath = state.taskQueueSnapshot.running.output_dir || "";
        }else if(!data.running){
          state.runningTaskPath = "";
        }
        state.runningElapsed = Number(data.elapsed_seconds || 0);
        renderTaskBar();

        if(data.running){
          const elapsed = state.runningElapsed;
          const percent = Math.max(1, Math.min(95, Math.floor((elapsed / 120) * 100)));
          setRunState(true, percent);
          const queueText = data.queue_length ? `，队列 ${data.queue_length} 个` : "";
          const runSummary = state.taskQueueSnapshot.running?.summary || "造菜任务";
          if(!silent){
            setStatus(`运行中：${runSummary}（${percent}% · ${elapsed}s${queueText}）`);
          }
          startPolling();
          return;
        }
        const wasRunning = state.running;
        setRunState(false, 0);
        stopPolling();
        if(data.result && data.result.output_dir){
          const doneDir = String(data.result.output_dir || "").replace(/[\\\\/]+$/, "").toLowerCase();
          const selectedDir = String(state.selectedHistoryPath || "").replace(/[\\\\/]+$/, "").toLowerCase();
          const sameSelected = Boolean(doneDir && selectedDir && doneDir === selectedDir);
          if(wasRunning || !state.selectedHistoryPath){
            renderResult(data.result);
          }
          if(sameSelected && (wasRunning || !state.selectedHistoryPath)){
            try{ await loadDishDetail(state.selectedHistoryPath); }catch{}
          }
        }
        if(data.history_revision && data.history_revision !== state.historyRevision){
          const previousRevision = state.historyRevision;
          state.historyRevision = data.history_revision;
          if(state.suppressHistoryAutoReload > 0){
            state.suppressHistoryAutoReload--;
          }else if(wasRunning){
            await loadHistory(true);
            if(state.selectedHistoryPath){
              try{ await loadDishDetail(state.selectedHistoryPath); }catch{}
            }
          }else if(!previousRevision){
            await loadHistory(true);
          }
        }
        if(data.error){
          if(!silent){
            setStatus("失败：" + data.error, "warn");
          }
          return;
        }
        if(data.result && data.result.output_dir){
          if(!silent || wasRunning){
            setStatus("运行完成。", "ok");
          }
          if(wasRunning){
            try{
              if("Notification" in window){
                if(Notification.permission === "granted"){
                  new Notification("V2 生图完成", { body: data.result.dish_name || "任务已完成" });
                }else if(Notification.permission === "default"){
                  Notification.requestPermission();
                }
              }
            }catch{}
          }
        }
      }catch(err){
        if(!silent){
          stopPolling();
          setRunState(false, 0);
          setStatus("读取运行状态失败：" + err.message, "warn");
        }
      }
    }

    async function loadState(showMsg=false){
      const res = await fetch("/api/state");
      const data = await res.json();
      if(data.image_gen_options){
        state.imageSizeTable = data.image_gen_options.size_table || {};
        applyImageGenControlsToUi(data.image_gen_options.current || loadImageGenPrefsFromStorage() || {});
      }else{
        applyImageGenControlsToUi(loadImageGenPrefsFromStorage() || {});
      }
      $("cuisineMode").value = data.config.AUTO_DISH_CUISINE_MODE || "1";
      $("posterCount").value = data.config.MODE2_POSTER_IMAGE_COUNT || data.config.OPENAI_IMAGE_COUNT;
      $("posterQuality").value = data.config.MODE2_POSTER_IMAGE_QUALITY || data.config.OPENAI_IMAGE_QUALITY;
      $("detailCount").value = data.config.MODE2_DETAIL_IMAGE_COUNT || "1";
      $("detailQuality").value = data.config.MODE2_DETAIL_IMAGE_QUALITY || data.config.OPENAI_IMAGE_QUALITY;
      $("recipeCount").value = data.config.MODE2_RECIPE_IMAGE_COUNT || "1";
      $("recipeQuality").value = data.config.MODE2_RECIPE_IMAGE_QUALITY || data.config.OPENAI_IMAGE_QUALITY;
      $("coverMode2Count").value = data.config.MODE2_COVER_IMAGE_COUNT || data.config.COVER_IMAGE_COUNT || "1";
      $("coverMode2Quality").value = data.config.MODE2_COVER_IMAGE_QUALITY || data.config.OPENAI_IMAGE_QUALITY;
      $("temperature").value = data.config.MODEL_TEMPERATURE;
      $("dishName").value = data.idea.dish_name || "";
      $("dishNotes").value = data.idea.notes || "";
      setMode(data.config.AUTO_GENERATE_DISH_IDEA === "1" ? "auto" : "file");
      $("posterCount").value = $("detailCount").value = $("recipeCount").value = $("coverMode2Count").value = "2";
      $("posterQuality").value = $("detailQuality").value = $("recipeQuality").value = $("coverMode2Quality").value = "high";
      同步参数滑块();
      if(data.last_result && !state.selectedHistoryPath){ renderResult(data.last_result); }
      state.logNextIndex = 0;
      $("logPanel").textContent = "";
      state.historyRevision = data.history_revision || "";
      if(data.meal_tag_labels){ state.mealTagLabels = data.meal_tag_labels; }
      if(data.publish_plan){ state.publishPlan = data.publish_plan; }
      if(data.custom_images_dir){ state.customImagesDir = data.custom_images_dir; }
      await loadHistory(true);
      initPublishPlanUi();
      await fetchRunStatus();
      await fetchPublishStatus({silent: true});
      if(showMsg){ setStatus("页面状态已刷新。", "ok"); }
    }

    async function submitTask(payload, okText){
      if(state.submittingTask){
        setStatus("上一个任务仍在提交中，请稍候。", "warn");
        return;
      }
      state.submittingTask = true;
      state.logNextIndex = 0;
      if(!state.pollTimer){ $("logPanel").textContent = ""; }
      setStatus(okText || "任务已提交，正在加入队列...");
      try{
        const res = await fetch("/api/run_start", {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "运行失败"); }
        if(data.started_now){
          appendLogs([`[${new Date().toLocaleTimeString()}] 任务 #${data.task_id} 已开始执行。`]);
          setStatus("任务已开始执行。");
        }else{
          appendLogs([`[${new Date().toLocaleTimeString()}] 任务 #${data.task_id} 已加入队列，前方 ${data.waiting_ahead} 个任务。`]);
          setStatus(`已加入队列，前方还有 ${data.waiting_ahead} 个任务。`, "ok");
        }
        startPolling();
        await fetchRunStatus();
      }catch(err){
        setStatus("失败：" + err.message, "warn");
      }finally{
        state.submittingTask = false;
      }
    }

    async function runNow(){
      if(state.mode === "idea"){
        const dishName = $("ideaDishName").value.trim();
        const mode = dishName ? "file" : "auto";
        const count = Math.max(1, Math.min(30, Number($("ideaCount").value || "1")));
        $("ideaCount").value = String(count);
        await submitTask({
          action: "idea_only",
          mode,
          cuisine_mode: $("cuisineMode").value.trim(),
          model_temperature: $("temperature").value.trim(),
          dish_name: dishName,
          notes: "",
          idea_count: String(count),
          ...collectImageGenControls()
        }, `生菜任务已提交（共 ${count} 条）。`);
        return;
      }
      if(state.mode === "supplement"){
        if(!state.selectedHistoryPath){
          setStatus("补生请先在左侧菜品池选中一个菜品。", "danger");
          return;
        }
        const targets = collectSupplementTargets();
        if(!targets.length){
          setStatus("请至少勾选一项补生内容。", "danger");
          return;
        }
        await submitTask({
          action: "supplement",
          mode: "supplement",
          target_output_dir: state.selectedHistoryPath,
          supplement_targets: targets,
          model_temperature: $("temperature").value.trim(),
          poster_quality: $("posterQuality").value.trim(),
          poster_count: $("posterCount").value.trim(),
          detail_quality: $("detailQuality").value.trim(),
          detail_count: $("detailCount").value.trim(),
          recipe_quality: $("recipeQuality").value.trim(),
          recipe_count: $("recipeCount").value.trim(),
          cover_mode2_quality: $("coverMode2Quality").value.trim(),
          cover_mode2_count: $("coverMode2Count").value.trim(),
          ...collectImageGenControls()
        }, `补生任务已提交（${targets.length} 项）。`);
        return;
      }
      if(state.mode === "file" && !$("dishName").value.trim()){
        setStatus("手动模式下请先填写菜名。", "danger");
        $("dishName").focus();
        return;
      }
      if(state.mode === "target" && !state.selectedHistoryPath){
        setStatus("指定请先在左侧菜品池选中一个菜品。", "danger");
        return;
      }
      await submitTask({
        action: "run",
        mode: state.mode,
        target_output_dir: state.mode === "target" ? state.selectedHistoryPath : "",
        cuisine_mode: $("cuisineMode").value.trim(),
        dish_name: $("dishName").value.trim(),
        notes: $("dishNotes").value.trim(),
        model_temperature: $("temperature").value.trim(),
        poster_quality: $("posterQuality").value.trim(),
        poster_count: $("posterCount").value.trim(),
        detail_quality: $("detailQuality").value.trim(),
        detail_count: $("detailCount").value.trim(),
        recipe_quality: $("recipeQuality").value.trim(),
        recipe_count: $("recipeCount").value.trim(),
        cover_mode2_quality: $("coverMode2Quality").value.trim(),
        cover_mode2_count: $("coverMode2Count").value.trim(),
        ...collectImageGenControls()
      });
    }

    async function stopBackgroundWork(){
      if(!confirm("确定停止所有后台任务？\\n\\n将清空队列并中断进行中的生图、写文案、发布等脚本；面板本身保持运行。")){ return; }
      try{
        const res = await fetch("/api/work_stop", {method:"POST"});
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "停止失败"); }
        setStatus(data.message || "已请求停止后台任务。", "ok");
        await fetchRunStatus({silent: true});
        await fetchPublishStatus({silent: true});
        await fetchCustomImageStatus({silent: true});
        await reloadAllLogs({silent: true});
      }catch(err){
        setStatus("停止失败：" + err.message, "danger");
      }
    }

    async function copyText(text, okMessage){
      if(!text){ setStatus("当前没有可复制内容。", "warn"); return; }
      try{
        await navigator.clipboard.writeText(text);
        setStatus(okMessage, "ok");
      }catch{
        setStatus("复制失败，请手动复制。", "warn");
      }
    }

    async function openOutputPath(pathText=null){
      const path = (pathText ?? state.currentOutputPath ?? "").trim();
      if(!path || path === "-"){
        setStatus("当前没有可打开的输出目录。", "warn");
        return;
      }
      const res = await fetch("/api/open_output", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path})
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "打开目录失败"); }
      setStatus("已在本机打开输出目录。", "ok");
    }

    function bindEvents(){
      bindImageGenControls();
      $("modeAutoBtn").onclick = () => setMode("auto");
      $("modeFileBtn").onclick = () => setMode("file");
      $("modeTargetBtn").onclick = () => setMode("target");
      $("modeSupplementBtn").onclick = () => setMode("supplement");
      $("modeIdeaBtn").onclick = () => setMode("idea");
      $("ideaDishName").oninput = syncIdeaCountUi;
      $("batchEditBtn").onclick = () => setBatchEditMode(!state.batchEditMode);
      $("batchRemoveBtn")?.addEventListener("click", async () => {
        try{
          await 批量删除历史(Array.from(state.batchEditSelected));
        }catch(err){
          setStatus("批量移出失败：" + err.message, "warn");
        }
      });
      $("batchEditBar")?.addEventListener("click", async (event) => {
        const target = event.target;
        if(!(target instanceof HTMLElement)){ return; }
        const addTag = target.dataset?.batchAdd;
        const removeTag = target.dataset?.batchRemove;
        if(!addTag && !removeTag){ return; }
        try{
          await batchUpdateMealTags(
            Array.from(state.batchEditSelected),
            addTag ? {add: [addTag]} : {remove: [removeTag]}
          );
        }catch(err){
          setStatus("批量标签操作失败：" + err.message, "warn");
        }
      });
      $("mealFilterRow")?.addEventListener("click", (event) => {
        const target = event.target;
        if(!(target instanceof HTMLElement)){ return; }
        const btn = target.closest(".meal-filter-btn");
        if(!btn){ return; }
        state.historyMealFilter = btn.dataset.mealFilter || "";
        Array.from($("mealFilterRow").querySelectorAll(".meal-filter-btn")).forEach((el) => {
          el.classList.toggle("active", el === btn);
        });
        applyHistorySearchFilter();
      });
      $("planStartDate")?.addEventListener("change", async () => {
        const plan = {...state.publishPlan, start: $("planStartDate").value || ""};
        try{ await savePublishPlan(plan); }catch(err){ setStatus("更新计划日期失败：" + err.message, "warn"); }
      });
      $("planEndDate")?.addEventListener("change", async () => {
        const plan = {...state.publishPlan, end: $("planEndDate").value || ""};
        try{ await savePublishPlan(plan); }catch(err){ setStatus("更新计划日期失败：" + err.message, "warn"); }
      });
      document.addEventListener("click", () => closeAllTagPopovers());
      $("historyColsSelect")?.addEventListener("change", () => {
        应用菜品池列数($("historyColsSelect").value || "3");
      });
      $("historySearch")?.addEventListener("input", () => {
        state.historySearchQuery = $("historySearch").value || "";
        applyHistorySearchFilter();
      });
      $("historySortSelect").value = state.historySort || "favorite";
      $("historySortSelect").onchange = async () => {
        state.historySort = $("historySortSelect").value || "favorite";
        localStorage.setItem(HISTORY_SORT_STORAGE_KEY, state.historySort);
        await loadHistory(true);
      };
      $("imageTabBtn").onclick = () => setRightTab("image");
      $("textTabBtn").onclick = () => setRightTab("text");
      document.addEventListener("keydown", (event) => {
        if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s"){
          if(state.activeRightTab !== "text" || !state.activeTextFilePath || !state.textEditorEditing){ return; }
          event.preventDefault();
          saveTextAsset();
        }
      });
      $("runBtn").onclick = runNow;
      $("prevImgBtn").onclick = () => showGalleryImage(state.galleryIndex - 1);
      $("nextImgBtn").onclick = () => showGalleryImage(state.galleryIndex + 1);
      $("filterPublishBtn").onclick = () => { state.galleryFilter = "publish"; applyGalleryFilter(true); };
      $("filterAllBtn").onclick = () => { state.galleryFilter = "all"; applyGalleryFilter(true); };
      $("publishReplaceBtn").onclick = togglePublishReplaceMode;
      $("publishReplaceExecBtn").onclick = submitPublishImageReplace;
      $("anchorPosterRegenBtn").onclick = () => { submitAnchorPosterRegenerate(); };
      $("publishBtn").onclick = startPublish;
      $("loginModalConfirm").onclick = confirmPublishLogin;
      $("resultImg").ondblclick = () => openGalleryLightbox(state.galleryIndex);
      $("galleryLightboxClose")?.addEventListener("click", (event) => {
        event.stopPropagation();
        closeGalleryLightbox();
      });
      $("galleryLightboxPrev")?.addEventListener("click", (event) => {
        event.stopPropagation();
        stepGalleryLightbox(-1);
      });
      $("galleryLightboxNext")?.addEventListener("click", (event) => {
        event.stopPropagation();
        stepGalleryLightbox(1);
      });
      $("galleryLightbox")?.addEventListener("click", (event) => {
        if(event.target === $("galleryLightbox") || event.target === $("galleryLightboxImg")){
          closeGalleryLightbox();
        }
      });
      $("galleryLightbox")?.addEventListener("wheel", (event) => {
        if(!$("galleryLightbox") || $("galleryLightbox").classList.contains("hidden")){ return; }
        event.preventDefault();
        stepGalleryLightbox(event.deltaY > 0 ? 1 : -1);
      }, {passive: false});
      $("resultImg").onerror = () => 显示无图占位("图片加载失败，请切换其它图片或重新运行");
      $("copyOutputBtn").onclick = () => copyText(state.currentOutputPath, "输出目录已复制到剪贴板。");
      $("copyOutputTopBtn").onclick = () => copyText(state.currentOutputPath, "输出目录已复制到剪贴板。");
      $("openOutputBtn").onclick = async () => { try{ await openOutputPath(); }catch(err){ setStatus("打开目录失败：" + err.message, "warn"); } };
      $("openPublishBtn").onclick = async () => {
        try{
          const publishPath = state.currentOutputPath ? (state.currentOutputPath.replace(/[\\\\/]+$/, "") + "/publish") : "";
          await openOutputPath(publishPath);
        }catch(err){
          setStatus("打开 publish 失败：" + err.message, "warn");
        }
      };
      $("openOutputTopBtn").onclick = async () => { try{ await openOutputPath(); }catch(err){ setStatus("打开目录失败：" + err.message, "warn"); } };
      $("refreshTopBtn").onclick = () => loadState(true);
      $("restartPanelBtn").onclick = () => restartPanelProgram();
      $("shutdownPanelBtn").onclick = () => shutdownPanelProgram();
      $("stopWorkBtn").onclick = () => stopBackgroundWork();
      $("taskBar").addEventListener("click", (event) => {
        const target = event.target;
        if(!(target instanceof HTMLElement)){ return; }
        if(target.closest("#taskBarJumpBtn")){
          event.preventDefault();
          jumpToRunningDish();
          return;
        }
        if(target.closest("#taskBarQueueToggle")){
          event.preventDefault();
          state.taskQueueExpanded = !state.taskQueueExpanded;
          renderTaskBar();
        }
      });
      $("loadMoreBtn").onclick = () => loadHistory(false);
      $("logToggleBtn").onclick = () => 切换日志抽屉(!$("logDrawer").classList.contains("open"));
      $("logCloseBtn").onclick = () => 切换日志抽屉(false);
      $("logCopyBtn").onclick = () => copyLogPanel();
      $("logReloadBtn").onclick = () => reloadAllLogs();
      $("logClearBtn").onclick = () => { $("logPanel").textContent = "等待任务启动...\\n"; };
      $("logFilterBtn").onclick = async () => {
        state.logOnlyErrors = !state.logOnlyErrors;
        $("logFilterBtn").textContent = `只看错误：${state.logOnlyErrors ? "开" : "关"}`;
        await reloadAllLogs({silent: true});
      };
      $("customAddBtn")?.addEventListener("click", () => $("customFileInput")?.click());
      $("customFileInput")?.addEventListener("change", (event) => {
        const input = event.target;
        if(input?.files?.length){ addCustomRefFiles(input.files); }
        input.value = "";
      });
      $("customGenerateBtn")?.addEventListener("click", () => startCustomGenerate());
      $("customPreviewClose")?.addEventListener("click", () => closeCustomPreview());
      $("customPreviewModal")?.addEventListener("click", (event) => {
        if(event.target === $("customPreviewModal") || event.target === $("customPreviewImg")){
          closeCustomPreview();
        }
      });
    }

    bindEvents();
    绑定参数滑块();
    syncIdeaCountUi();
    state.historySort = localStorage.getItem(HISTORY_SORT_STORAGE_KEY) || "favorite";
    if($("historySortSelect")){ $("historySortSelect").value = state.historySort; }
    加载面板栏宽();
    加载菜品池列数();
    初始化拖拽分栏();
    bindHistoryPoolDropZone();
    refreshPanelOnlineUi();
    loadState();
    fetchCustomImageStatus({silent: true});
    startAutoRefresh();
    document.addEventListener("visibilitychange", () => {
      if(!document.hidden){
        fetchRunStatus({silent: true});
      }
    });
  </script>
</body>
</html>
""".replace("__PANEL_VERSION__", PANEL_VERSION)


def read_idea_file() -> dict[str, str]:
    if not IDEA_FILE.exists():
        return {"dish_name": "", "notes": ""}
    lines = [line.strip() for line in IDEA_FILE.read_text(encoding="utf-8").splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        return {"dish_name": "", "notes": ""}
    return {"dish_name": non_empty[0], "notes": "\n".join(non_empty[1:]).strip()}


def infer_dish_name_from_folder(folder_name: str) -> str:
    import re

    matched = re.match(r"^\d{4}_(.+)$", folder_name)
    if matched:
        return matched.group(1).strip()
    parts = folder_name.split("_", 2)
    if len(parts) >= 3 and parts[2].strip():
        return parts[2].strip()
    if len(parts) >= 2 and parts[-1].strip():
        return parts[-1].strip()
    return folder_name.strip()


IMAGE_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def task_action_label(task: dict[str, Any]) -> str:
    action = str(task.get("action", "run")).strip().lower() or "run"
    mode = str(task.get("mode", "")).strip().lower()
    if action == "idea_only":
        return "生菜"
    if action == "supplement":
        targets = [str(item).strip() for item in (task.get("supplement_targets") or []) if str(item).strip()]
        if not targets:
            return "补生"
        short = "/".join(targets[:2])
        if len(targets) > 2:
            short += "…"
        return f"补生·{short}"
    if action == "anchor_poster_regenerate":
        return "锚点海报补生"
    if action == "publish_image_replace":
        return "发布图替换"
    labels = {"auto": "自动", "file": "手动", "target": "指定", "idea": "生菜"}
    return labels.get(mode, "造菜")


def build_task_panel_item(task: dict[str, Any], *, status: str) -> dict[str, Any]:
    dish_name = str(task.get("dish_name", "")).strip()
    target_dir = str(task.get("live_output_dir") or task.get("target_output_dir", "")).strip()
    if not dish_name and target_dir:
        dish_name = infer_dish_name_from_folder(Path(target_dir).name)
    action_label = task_action_label(task)
    display_name = dish_name or "自动生成"
    return {
        "task_id": task.get("task_id"),
        "status": status,
        "dish_name": display_name,
        "output_dir": target_dir,
        "action_label": action_label,
        "summary": f"{display_name} · {action_label}",
    }


def build_task_queue_snapshot() -> dict[str, Any]:
    with RUN_LOCK:
        running_item: dict[str, Any] | None = None
        if RUNNING and CURRENT_TASK:
            running_item = build_task_panel_item(CURRENT_TASK, status="running")
        queued = [build_task_panel_item(task, status="queued") for task in TASK_QUEUE]
    return {
        "running": running_item,
        "queued": queued,
        "queue_length": len(queued),
    }


def build_task_run_params(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "action": str(task.get("action", "")).strip(),
        "mode": str(task.get("mode", "")).strip(),
        "cuisine_mode": str(task.get("cuisine_mode", "")).strip(),
        "model_temperature": str(task.get("model_temperature", "")).strip(),
        "poster_quality": str(task.get("poster_quality", "")).strip(),
        "poster_count": str(task.get("poster_count", "")).strip(),
        "detail_quality": str(task.get("detail_quality", "")).strip(),
        "detail_count": str(task.get("detail_count", "")).strip(),
        "recipe_quality": str(task.get("recipe_quality", "")).strip(),
        "recipe_count": str(task.get("recipe_count", "")).strip(),
        "cover_mode2_quality": str(task.get("cover_mode2_quality", "")).strip(),
        "cover_mode2_count": str(task.get("cover_mode2_count", "")).strip(),
        "supplement_targets": list(task.get("supplement_targets") or []),
        "target_output_dir": str(task.get("target_output_dir", "")).strip(),
        "anchor_poster_path": str(task.get("anchor_poster_path", "")).strip(),
        "replacement_paths": list(task.get("replacement_paths") or []),
    }


def resolve_run_log_dir(result: dict[str, Any] | None, task: dict[str, Any]) -> Path | None:
    if isinstance(result, dict):
        output_dir_text = str(result.get("output_dir", "")).strip()
        if output_dir_text:
            output_dir = Path(output_dir_text)
            if output_dir.exists() and output_dir.is_dir():
                return output_dir
        batch_dir_text = str(result.get("batch_dir", "")).strip()
        if batch_dir_text:
            batch_dir = Path(batch_dir_text)
            if batch_dir.exists() and batch_dir.is_dir():
                return batch_dir
    target_output_dir = str(task.get("target_output_dir", "")).strip()
    if target_output_dir:
        target_dir = Path(target_output_dir)
        if target_dir.exists() and target_dir.is_dir():
            return target_dir
    return None


def write_run_log_file(
    *,
    result: dict[str, Any] | None,
    task: dict[str, Any],
    log_lines: list[str],
    success: bool,
    error_text: str = "",
) -> str:
    log_dir = resolve_run_log_dir(result, task)
    if log_dir is None:
        return ""
    header = [
        f"任务 #{task.get('task_id', '-')}",
        f"开始时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(task.get('queued_at', time.time()))))}",
        f"结束时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"类型：{task.get('action', 'run')} / {task.get('mode', '')}",
        f"状态：{'成功' if success else '失败'}",
    ]
    if error_text.strip():
        header.append(f"错误：{error_text.strip()}")
    header.append(f"运行参数：{json.dumps(build_task_run_params(task), ensure_ascii=False)}")
    if isinstance(result, dict) and result.get("image_generation_settings"):
        header.append(
            "生图配置："
            + json.dumps(result.get("image_generation_settings"), ensure_ascii=False)
        )
    header.append("")
    content = "\n".join(header + log_lines).rstrip() + "\n"
    log_file = log_dir / RUN_LOG_FILE_NAME
    log_file.write_text(content, encoding="utf-8")
    return str(log_file)


def write_publish_log_file(output_dir_text: str, log_lines: list[str]) -> str:
    if not output_dir_text.strip():
        return ""
    output_dir = Path(output_dir_text.strip())
    if not output_dir.exists() or not output_dir.is_dir():
        return ""
    header = [
        f"发布时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    content = "\n".join(header + log_lines).rstrip() + "\n"
    log_file = output_dir / PUBLISH_LOG_FILE_NAME
    log_file.write_text(content, encoding="utf-8")
    return str(log_file)


def build_run_meta(result: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    ps_files = result.get("photoshop_processed_files") or []
    poster_selected = str(result.get("poster_selected_image", "")).strip()
    workflow_status = ""
    if ps_files:
        workflow_status = f"已合成发布图（{len(ps_files)} 张）"
    elif poster_selected:
        workflow_status = "已选图"
    meta: dict[str, Any] = {
        "dish_name": str(result.get("dish_name", "")).strip(),
        "region_label": str(result.get("region_label", "")).strip(),
        "reference_dish": str(result.get("reference_dish", "")).strip(),
        "output_dir": str(result.get("output_dir", "")).strip(),
        "run_kind": str(result.get("run_kind", "run")).strip() or "run",
        "poster_selected_image": poster_selected,
        "cover_selected_image": str(result.get("cover_selected_image", "")).strip(),
        "poster_selection_mode": str(result.get("poster_selection_mode", "")).strip(),
        "detail_selection_mode": str(result.get("detail_selection_mode", "")).strip(),
        "recipe_selection_mode": str(result.get("recipe_selection_mode", "")).strip(),
        "cover_selection_mode": str(result.get("cover_selection_mode", "")).strip(),
        "photoshop_count": len(ps_files),
        "workflow_status": workflow_status,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "image_generation_settings": result.get("image_generation_settings") or {},
        "run_log_file": str(result.get("run_log_file", "")).strip(),
    }
    if task is not None:
        meta["run_params"] = build_task_run_params(task)
        meta["task_id"] = task.get("task_id")
    return meta


def write_run_meta(result: dict[str, Any], task: dict[str, Any] | None = None) -> None:
    output_dir_text = str(result.get("output_dir", "")).strip()
    if not output_dir_text:
        return
    output_dir = Path(output_dir_text)
    if not output_dir.exists() or not output_dir.is_dir():
        return
    meta_file = output_dir / RUN_META_FILE_NAME
    meta_file.write_text(
        json.dumps(build_run_meta(result, task=task), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_run_meta(folder: Path) -> dict[str, Any]:
    meta_file = folder / RUN_META_FILE_NAME
    if not meta_file.exists() or not meta_file.is_file():
        return {}
    try:
        payload = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "dish_name": str(payload.get("dish_name", "")).strip(),
        "region_label": str(payload.get("region_label", "")).strip(),
        "reference_dish": str(payload.get("reference_dish", "")).strip(),
        "poster_selected_image": str(payload.get("poster_selected_image", "")).strip(),
        "cover_selected_image": str(payload.get("cover_selected_image", "")).strip(),
        "poster_selection_mode": str(payload.get("poster_selection_mode", "")).strip(),
        "detail_selection_mode": str(payload.get("detail_selection_mode", "")).strip(),
        "recipe_selection_mode": str(payload.get("recipe_selection_mode", "")).strip(),
        "cover_selection_mode": str(payload.get("cover_selection_mode", "")).strip(),
        "workflow_status": str(payload.get("workflow_status", "")).strip(),
        "photoshop_count": int(payload.get("photoshop_count", 0) or 0),
        "finished_at": str(payload.get("finished_at", "")).strip(),
        "image_generation_settings": payload.get("image_generation_settings") or {},
        "run_params": payload.get("run_params") or {},
        "run_log_file": str(payload.get("run_log_file", "")).strip(),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _selection_mode_label(payload: dict[str, Any]) -> str:
    if not payload:
        return "未执行"
    if payload.get("auto_selected"):
        return "数量=1直入"
    if payload.get("winner_image_name") or payload.get("winner_index"):
        return "豆包评分"
    return "未执行"


def _existing_file_path(path_text: str) -> str:
    text = str(path_text or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if candidate.is_file():
        return str(candidate.resolve())
    return ""


def _filter_existing_files(paths: list[str]) -> list[str]:
    existing: list[str] = []
    for path_text in paths:
        resolved = _existing_file_path(path_text)
        if resolved:
            existing.append(resolved)
    return existing


def _find_image_by_name(folder: Path, file_name: str) -> str:
    text = str(file_name or "").strip()
    if not text:
        return ""
    stem = Path(text).stem
    search_dirs = [folder / "publish" / "final", folder / "publish", folder]
    for base in search_dirs:
        if not base.is_dir():
            continue
        exact = base / text
        if exact.is_file():
            return str(exact.resolve())
        for candidate in base.iterdir():
            if not candidate.is_file():
                continue
            if candidate.stem == stem and candidate.suffix.lower() in IMAGE_FILE_SUFFIXES:
                return str(candidate.resolve())
    return ""


def _collect_folder_images(folder_path: Path, *, recursive: bool = False) -> list[str]:
    image_paths: list[Path] = []
    patterns = ("*.png", "*.jpg", "*.jpeg", "*.webp")
    if recursive:
        for pattern in patterns:
            image_paths.extend(folder_path.rglob(pattern))
    else:
        for pattern in patterns:
            image_paths.extend(folder_path.glob(pattern))
    unique = sorted({path.resolve() for path in image_paths}, key=lambda item: str(item).lower())
    return [str(path) for path in unique]


def read_dish_output_summary(folder: Path) -> dict[str, Any]:
    publish_dir = folder / "publish"
    final_dir = publish_dir / "final"
    meta = read_run_meta(folder)

    dish_name = meta.get("dish_name", "") or infer_dish_name_from_folder(folder.name)
    region_label = str(meta.get("region_label", "")).strip()
    reference_dish = str(meta.get("reference_dish", "")).strip()
    idea_notes = ""
    try:
        idea_payload = load_dish_idea_record_from_dir(folder)
        dish_name = str(idea_payload.get("dish_name", "")).strip() or dish_name
        idea_notes = str(idea_payload.get("notes", "")).strip()
        region_label = region_label or str(idea_payload.get("region_label", "")).strip()
        reference_dish = reference_dish or str(idea_payload.get("reference_dish", "")).strip()
    except Exception:
        pass

    poster_result = _read_json_object(publish_dir / "海报筛选结果.json")
    detail_result = _read_json_object(publish_dir / "细节图筛选结果.json")
    recipe_result = _read_json_object(publish_dir / "菜谱图筛选结果.json")
    cover_result = _read_json_object(publish_dir / "封面图筛选结果.json")

    poster_selected = _find_image_by_name(folder, str(poster_result.get("winner_image_name", "")))
    if not poster_selected:
        poster_selected = _existing_file_path(str(meta.get("poster_selected_image", "")).strip())
    detail_selected = _find_image_by_name(folder, str(detail_result.get("winner_image_name", "")))
    recipe_selected = _find_image_by_name(folder, str(recipe_result.get("winner_image_name", "")))
    cover_selected = _find_image_by_name(folder, str(cover_result.get("winner_image_name", "")))
    if not cover_selected:
        cover_selected = _existing_file_path(str(meta.get("cover_selected_image", "")).strip())

    photoshop_files = _collect_folder_images(final_dir) if final_dir.is_dir() else []
    if not cover_selected and photoshop_files:
        for image_path in photoshop_files:
            if "封面" in Path(image_path).name:
                cover_selected = image_path
                break

    ps_error = ""
    ps_fail = folder / "Photoshop合成失败原因.txt"
    if ps_fail.is_file():
        ps_error = ps_fail.read_text(encoding="utf-8", errors="replace").strip()[:300]

    images = _filter_existing_files(_collect_folder_images(folder, recursive=True))
    publish_images = _filter_existing_files(photoshop_files or _collect_folder_images(publish_dir, recursive=False))

    workflow_status = str(meta.get("workflow_status", "")).strip()
    if not workflow_status:
        if photoshop_files:
            workflow_status = f"已合成发布图（{len(photoshop_files)} 张）"
        elif poster_result:
            workflow_status = "已选图待合成" if not ps_error else "选图完成（PS 失败）"
        elif images:
            workflow_status = "已生图"
        else:
            workflow_status = "仅造菜信息"

    preview_image = poster_selected or (publish_images[0] if publish_images else (images[0] if images else ""))

    return {
        "dish_name": dish_name,
        "region_label": region_label,
        "reference_dish": reference_dish,
        "idea_notes": idea_notes,
        "output_dir": str(folder.resolve()),
        "poster_selected_image": poster_selected,
        "detail_selected_image": detail_selected,
        "recipe_selected_image": recipe_selected,
        "cover_selected_image": cover_selected,
        "poster_selection_mode": _selection_mode_label(poster_result) if poster_result else str(meta.get("poster_selection_mode", "")).strip() or "未执行",
        "detail_selection_mode": _selection_mode_label(detail_result) if detail_result else str(meta.get("detail_selection_mode", "")).strip() or "未执行",
        "recipe_selection_mode": _selection_mode_label(recipe_result) if recipe_result else str(meta.get("recipe_selection_mode", "")).strip() or "未执行",
        "cover_selection_mode": _selection_mode_label(cover_result) if cover_result else str(meta.get("cover_selection_mode", "")).strip() or "未执行",
        "photoshop_processed_files": photoshop_files,
        "photoshop_error": ps_error,
        "workflow_status": workflow_status,
        "images": images,
        "publish_images": publish_images,
        "preview_image": preview_image,
    }


def classify_text_file(path: Path) -> tuple[str, str]:
    name = path.name
    if "造菜信息" in name:
        return "idea", "造菜信息"
    if name in {RUN_LOG_FILE_NAME, PUBLISH_LOG_FILE_NAME}:
        return "other", "运行日志"
    if "平台文案" in name or "标题" in name or "话题" in name or "描述" in name:
        return "publish", "平台文案"
    if "prompt" in name.lower() or "提示词" in name:
        return "prompt", "提示词"
    return "other", "其他"


def read_text_assets(raw_path: str) -> dict[str, Any]:
    folder = resolve_output_path(raw_path)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"目录不存在：{folder}")

    grouped: dict[str, dict[str, Any]] = {}
    order = ["idea", "publish", "prompt", "other"]
    for text_file in sorted(folder.rglob("*.txt"), key=lambda item: str(item.relative_to(folder)).lower()):
        if not text_file.is_file():
            continue
        category_key, category_label = classify_text_file(text_file)
        group = grouped.setdefault(category_key, {"key": category_key, "label": category_label, "files": []})
        try:
            content = text_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = text_file.read_text(encoding="utf-8", errors="replace")
        if len(content) > 200_000:
            content = content[:200_000] + "\n\n……内容过长，已截断显示。"
        group["files"].append(
            {
                "name": text_file.name,
                "relative_path": str(text_file.relative_to(folder)),
                "path": str(text_file),
                "content": content,
            }
        )

    groups = [grouped[key] for key in order if key in grouped]
    return {"path": str(folder), "groups": groups}


def save_text_asset(raw_path: str, content: str) -> dict[str, Any]:
    file_path = resolve_output_file(raw_path)
    file_path.write_text(content, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "path": str(file_path),
        "name": file_path.name,
    }


def format_folder_time(folder_name: str) -> str:
    parts = folder_name.split("_", 2)
    if len(parts) < 2:
        return ""
    date_text = parts[0]
    time_text = parts[1]
    if len(date_text) != 8 or len(time_text) != 6:
        return ""
    return f"{date_text[0:4]}-{date_text[4:6]}-{date_text[6:8]} {time_text[0:2]}:{time_text[2:4]}:{time_text[4:6]}"


def folder_has_generated_image(folder: Path) -> bool:
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            return True
    publish_dir = folder / "publish"
    if not publish_dir.is_dir():
        return False
    for path in publish_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            return True
    return False


def collect_history_dirs() -> list[Path]:
    dirs: list[Path] = []
    if OUTPUT_DIR.exists():
        dirs.extend(path for path in OUTPUT_DIR.iterdir() if path.is_dir())
    if DISH_POOL_DIR.exists():
        for batch_dir in DISH_POOL_DIR.iterdir():
            if not batch_dir.is_dir() or not batch_dir.name.endswith("_batch"):
                continue
            dirs.extend(path for path in batch_dir.iterdir() if path.is_dir())
    return dirs


def sort_history_dirs(dirs: list[Path], sort: str, favorites: dict[str, str]) -> list[Path]:
    normalized_sort = sort if sort in VALID_HISTORY_SORTS else "favorite"
    if normalized_sort == "name":
        return sorted(dirs, key=lambda path: infer_dish_name_from_folder(path.name).lower())
    if normalized_sort == "created_asc":
        return sorted(dirs, key=lambda path: path.stat().st_mtime)
    if normalized_sort == "created_desc":
        return sorted(dirs, key=lambda path: path.stat().st_mtime, reverse=True)
    if normalized_sort == "image_first":
        with_image = [path for path in dirs if folder_has_generated_image(path)]
        with_image.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return with_image

    favorite_dirs = [path for path in dirs if str(path.resolve()) in favorites]
    other_dirs = [path for path in dirs if str(path.resolve()) not in favorites]
    favorite_dirs.sort(key=lambda path: favorites[str(path.resolve())], reverse=True)
    other_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return favorite_dirs + other_dirs


def list_history(limit: int = 30, offset: int = 0, sort: str = "favorite") -> list[dict[str, Any]]:
    if offset < 0:
        offset = 0
    if limit < 1:
        limit = 30
    favorites = load_dish_favorites()
    meal_tags_map = load_dish_meal_tags()
    dirs = sort_history_dirs(collect_history_dirs(), sort, favorites)
    rows: list[dict[str, Any]] = []
    sliced = dirs[offset : offset + limit]

    for folder in sliced:
        folder_key = str(folder.resolve())
        summary = read_dish_output_summary(folder)
        if (not summary.get("region_label") or not summary.get("reference_dish")) and isinstance(LAST_RESULT, dict):
            current_output_dir = str(LAST_RESULT.get("output_dir", "")).strip()
            if current_output_dir and Path(current_output_dir).resolve() == folder.resolve():
                summary["region_label"] = summary.get("region_label") or str(LAST_RESULT.get("region_label", "")).strip()
                summary["reference_dish"] = summary.get("reference_dish") or str(LAST_RESULT.get("reference_dish", "")).strip()
        rows.append(
            {
                "name": folder.name,
                "dish_name": summary.get("dish_name", ""),
                "region_label": summary.get("region_label", ""),
                "reference_dish": summary.get("reference_dish", ""),
                "created_at": format_folder_time(folder.name),
                "favorited": folder_key in favorites,
                "favorited_at": favorites.get(folder_key, ""),
                "meal_tags": meal_tags_map.get(folder_key, []),
                "path": str(folder),
                "preview_image": summary.get("preview_image", ""),
                "images": summary.get("images", []),
                "publish_images": summary.get("publish_images", []),
                "idea_notes": summary.get("idea_notes", ""),
                "workflow_status": summary.get("workflow_status", ""),
                "poster_selected_image": summary.get("poster_selected_image", ""),
                "cover_selected_image": summary.get("cover_selected_image", ""),
                "poster_selection_mode": summary.get("poster_selection_mode", ""),
                "detail_selection_mode": summary.get("detail_selection_mode", ""),
                "recipe_selection_mode": summary.get("recipe_selection_mode", ""),
                "cover_selection_mode": summary.get("cover_selection_mode", ""),
                "photoshop_processed_files": summary.get("photoshop_processed_files", []),
                "photoshop_error": summary.get("photoshop_error", ""),
            }
        )
    return rows


def archive_history_folder(raw_path: str) -> Path:
    folder = resolve_output_path(raw_path)
    return archive_dish_folder(folder)


def delete_history_folders(raw_paths: list[str]) -> list[str]:
    archived: list[str] = []
    for raw_path in raw_paths:
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        archived.append(str(archive_history_folder(path_text)))
        remove_dish_favorite(path_text)
        remove_dish_meal_tags(path_text)
        remove_path_from_publish_plan(path_text)
    if not archived:
        raise ValueError("没有可移出的菜品目录。")
    return archived


def delete_history_folder(raw_path: str) -> Path:
    return archive_history_folder(raw_path)


def write_idea_file(dish_name: str, notes: str) -> None:
    dish = dish_name.strip()
    if not dish:
        raise ValueError("手动模式下，菜名不能为空。")
    payload = dish + ("\n" + notes.strip() if notes.strip() else "")
    IDEA_FILE.write_text(payload + "\n", encoding="utf-8")


def load_prompt_from_output_dir(source_output_dir: Path) -> tuple[str, Path]:
    prompt_files = sorted(source_output_dir.glob("*_豆包提示词.txt"))
    if not prompt_files:
        raise FileNotFoundError("当前目录未找到可用于重生图的提示词文件（*_豆包提示词.txt）。")
    prompt_file = prompt_files[0]
    prompt_text = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise ValueError(f"提示词文件为空：{prompt_file}")
    return prompt_text, prompt_file


def regenerate_images_from_output_dir(source_output_dir: Path) -> dict[str, Any]:
    dish_name = infer_dish_name_from_folder(source_output_dir.name)
    prompt_text, source_prompt_file = load_prompt_from_output_dir(source_output_dir)

    timestamp = get_timestamp()
    run_output_dir = build_run_output_dir(timestamp, dish_name)
    prompt_file = run_output_dir / f"{dish_name}_豆包提示词.txt"
    save_text_output(prompt_text, prompt_file)
    print(f"重新生成沿用提示词：{source_prompt_file}")
    print(f"重新生成输出目录：{run_output_dir}")

    image_client = build_openai_image_client()
    image_settings = get_image_settings()
    print(
        "重新生图参数："
        f"quality={image_settings['quality']}，n={image_settings['image_count']}，model={image_settings['model']}"
    )

    image_items: list[dict[str, str]] = []
    image_error = ""
    try:
        image_items = generate_images_by_prompt(
            client=image_client,
            prompt_text=prompt_text,
            settings=image_settings,
        )
    except Exception as image_exc:
        image_error = f"生图失败：{image_exc}"
        error_file = run_output_dir / "生图失败原因.txt"
        save_text_output(
            "本轮为重新生图模式，已沿用原提示词执行到生图调用点。\n"
            f"失败原因：{image_exc}\n"
            "建议：稍后重试，或先检查 OPENAI_API_KEY、网络代理与账号可用区。",
            error_file,
        )
        print(f"重新生图失败，已写入说明：{error_file}")
    finally:
        close_image = getattr(image_client, "close", None)
        if callable(close_image):
            close_image()

    saved_images: list[str] = []
    if image_items:
        saved_images = save_generated_images(
            image_items=image_items,
            output_dir=run_output_dir,
            timestamp=timestamp,
            dish_name=dish_name,
        )
        for image_file in saved_images:
            print(f"已保存图片：{image_file}")

    return {
        "dish_name": dish_name,
        "notes": "",
        "region_label": "",
        "reference_dish": "",
        "memory_file": "",
        "prompt_file": str(prompt_file),
        "output_dir": str(run_output_dir),
        "saved_images": saved_images,
        "image_error": image_error,
        "run_kind": "regenerate_image",
        "source_output_dir": str(source_output_dir),
        "source_prompt_file": str(source_prompt_file),
    }


def current_config_snapshot() -> dict[str, str]:
    ensure_runtime_config_loaded()
    controls = sync_panel_image_gen_prefs()
    return {
        "AUTO_GENERATE_DISH_IDEA": os.getenv("AUTO_GENERATE_DISH_IDEA", "0").strip() or "0",
        "AUTO_DISH_CUISINE_MODE": os.getenv("AUTO_DISH_CUISINE_MODE", "1").strip() or "1",
        "MODEL_TEMPERATURE": os.getenv("MODEL_TEMPERATURE", "0.3").strip() or "0.3",
        "IMAGE_API_PROVIDER": controls["provider"],
        "IMAGE_ASPECT_RATIO": controls["aspect_ratio"],
        "IMAGE_RESOLUTION_TIER": controls["resolution_tier"],
        "OPENAI_IMAGE_SIZE": controls["size"],
        "PHOTOSHOP_TEMPLATE_FILE": controls["template_file"],
        "OPENAI_IMAGE_MODEL": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
        "OPENAI_IMAGE_QUALITY": controls["quality"],
        "OPENAI_IMAGE_COUNT": os.getenv("OPENAI_IMAGE_COUNT", "1").strip() or "1",
        "COVER_IMAGE_COUNT": str(get_cover_image_count()),
        "MODE2_POSTER_IMAGE_QUALITY": os.getenv("MODE2_POSTER_IMAGE_QUALITY", "").strip(),
        "MODE2_POSTER_IMAGE_COUNT": os.getenv("MODE2_POSTER_IMAGE_COUNT", "").strip(),
        "MODE2_DETAIL_IMAGE_QUALITY": os.getenv("MODE2_DETAIL_IMAGE_QUALITY", "").strip(),
        "MODE2_DETAIL_IMAGE_COUNT": os.getenv("MODE2_DETAIL_IMAGE_COUNT", "").strip(),
        "MODE2_RECIPE_IMAGE_QUALITY": os.getenv("MODE2_RECIPE_IMAGE_QUALITY", "").strip(),
        "MODE2_RECIPE_IMAGE_COUNT": os.getenv("MODE2_RECIPE_IMAGE_COUNT", "").strip(),
        "MODE2_COVER_IMAGE_QUALITY": os.getenv("MODE2_COVER_IMAGE_QUALITY", "").strip(),
        "MODE2_COVER_IMAGE_COUNT": os.getenv("MODE2_COVER_IMAGE_COUNT", "").strip(),
    }


def sync_panel_image_gen_prefs(payload: dict[str, Any] | None = None) -> dict[str, str]:
    global PANEL_IMAGE_GEN_PREFS
    merged = {**PANEL_IMAGE_GEN_PREFS, **(payload or {})}
    controls = apply_image_gen_controls(merged)
    PANEL_IMAGE_GEN_PREFS = {
        "image_provider": controls["provider"],
        "image_aspect_ratio": controls["aspect_ratio"],
        "image_resolution_tier": controls["resolution_tier"],
        "image_quality": controls["quality"],
    }
    return controls


def apply_runtime_overrides(payload: dict[str, Any]) -> None:
    image_keys = (
        "image_provider",
        "image_aspect_ratio",
        "image_resolution_tier",
        "image_quality",
    )
    if any(str(payload.get(key, "")).strip() for key in image_keys):
        sync_panel_image_gen_prefs(
            {
                "image_provider": str(payload.get("image_provider", "")).strip(),
                "image_aspect_ratio": str(payload.get("image_aspect_ratio", "")).strip(),
                "image_resolution_tier": str(payload.get("image_resolution_tier", "")).strip(),
                "image_quality": str(payload.get("image_quality", "")).strip(),
            }
        )
    else:
        sync_panel_image_gen_prefs()

    mode = str(payload.get("mode", "")).strip().lower()
    if mode in {"auto", "file", "target"}:
        os.environ["AUTO_GENERATE_DISH_IDEA"] = "1" if mode == "auto" else "0"
    cuisine_mode = str(payload.get("cuisine_mode", "")).strip()
    if cuisine_mode in {"0", "1", "2", "3", "4", "5", "6", "7"}:
        os.environ["AUTO_DISH_CUISINE_MODE"] = cuisine_mode
    if str(payload.get("model_temperature", "")).strip():
        os.environ["MODEL_TEMPERATURE"] = str(payload["model_temperature"]).strip()

    mode2_pairs = (
        ("poster", "poster_quality", "poster_count"),
        ("detail", "detail_quality", "detail_count"),
        ("recipe", "recipe_quality", "recipe_count"),
        ("cover", "cover_mode2_quality", "cover_mode2_count"),
    )
    for group, quality_key, count_key in mode2_pairs:
        quality_value = str(payload.get(quality_key, "")).strip()
        count_value = str(payload.get(count_key, "")).strip()
        prefix = group.upper()
        if quality_value:
            os.environ[f"MODE2_{prefix}_IMAGE_QUALITY"] = quality_value
        if count_value:
            os.environ[f"MODE2_{prefix}_IMAGE_COUNT"] = count_value


def _allowed_output_roots() -> list[Path]:
    from custom_image_service import CUSTOM_IMAGE_DIR

    return [OUTPUT_DIR.resolve(), DISH_POOL_DIR.resolve(), CUSTOM_IMAGE_DIR.resolve()]


def _is_under_allowed_roots(target: Path) -> bool:
    return any(target == root or root in target.parents for root in _allowed_output_roots())


def resolve_output_path(raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("输出目录不能为空。")
    target = Path(raw_path).resolve()
    if target.is_file():
        target = target.parent
    if not _is_under_allowed_roots(target):
        raise ValueError("只允许打开 V2/output 或 V2/dish_pool 下的目录。")
    if not target.exists():
        raise FileNotFoundError(f"目录不存在：{target}")
    return target


def resolve_output_file(raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("文件路径不能为空。")
    target = Path(raw_path).resolve()
    if not _is_under_allowed_roots(target) and not is_custom_image_path(target):
        raise ValueError("只允许访问 V2/output、V2/dish_pool 或 V2/custom_image_gen 下的文件。")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")
    return target


def open_output_path(raw_path: str) -> Path:
    target = resolve_output_path(raw_path)
    if hasattr(os, "startfile"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["explorer", str(target)])
    return target


def start_next_task_locked() -> bool:
    global RUNNING, LAST_ERROR, LAST_STARTED_AT, LAST_FINISHED_AT, CURRENT_TASK
    if RUNNING or not TASK_QUEUE:
        return False
    task = TASK_QUEUE.pop(0)
    RUNNING = True
    LAST_ERROR = ""
    LAST_STARTED_AT = time.time()
    LAST_FINISHED_AT = 0.0
    CURRENT_TASK = task
    worker = threading.Thread(target=run_task_worker, args=(task,), daemon=True)
    worker.start()
    return True


def run_task_worker(task: dict[str, Any]) -> None:
    global RUNNING, LAST_RESULT, LAST_ERROR, LAST_FINISHED_AT, CURRENT_TASK
    stream = LiveLogWriter()
    action = str(task.get("action", "run")).strip().lower() or "run"
    mode = str(task.get("mode", "")).strip().lower()
    dish_name = str(task.get("dish_name", "")).strip()
    if action == "idea_only":
        action_label = "生菜"
    elif action == "supplement":
        action_label = "补生"
    elif action == "anchor_poster_regenerate":
        action_label = "锚点海报补生"
    elif action == "publish_image_replace":
        action_label = "发布图替换"
    elif mode == "target":
        action_label = "指定"
    else:
        action_label = "四组图"
    mode_label = {
        "auto": "自动",
        "file": "手动",
        "target": "指定",
        "supplement": "补生",
        "idea": "生菜",
    }.get(mode, mode or "按配置")
    append_run_log(
        f"[{time.strftime('%H:%M:%S')}] 开始任务 #{task.get('task_id', '-')}"
        f"（类型：{action_label}，造菜：{mode_label}，菜名：{dish_name or '自动生成'}）"
    )
    log_start_index = len(RUN_LOG_LINES)
    result: dict[str, Any] | None = None
    clear_work_cancel()
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            apply_runtime_overrides(task)
            raise_if_work_cancelled("任务")
            if action == "idea_only":
                idea_count = int(str(task.get("idea_count", "1")).strip() or "1")
                result = run_idea_batch(
                    count=idea_count,
                    mode=mode if mode in {"auto", "file"} else None,
                    dish_name=dish_name,
                    notes=str(task.get("notes", "")).strip(),
                )
            elif action == "supplement":
                target_output_dir = str(task.get("target_output_dir", "")).strip()
                if not target_output_dir:
                    raise ValueError("补生需要选中菜品目录。")
                supplement_targets = [
                    str(item).strip()
                    for item in (task.get("supplement_targets") or [])
                    if str(item).strip()
                ]
                result = run_supplement_for_output_dir(
                    target_output_dir,
                    targets=supplement_targets,
                )
            elif action == "anchor_poster_regenerate":
                target_output_dir = str(task.get("target_output_dir", "")).strip()
                anchor_poster_path = str(task.get("anchor_poster_path", "")).strip()
                if not target_output_dir:
                    raise ValueError("锚点海报补生需要选中菜品目录。")
                if not anchor_poster_path:
                    raise ValueError("锚点海报路径不能为空。")
                resolve_output_file(anchor_poster_path)
                dish_dir = resolve_output_path(target_output_dir)
                result = run_anchor_poster_regenerate(
                    dish_dir,
                    anchor_poster_path=anchor_poster_path,
                )
            elif action == "publish_image_replace":
                target_output_dir = str(task.get("target_output_dir", "")).strip()
                replacement_paths = [
                    str(item).strip()
                    for item in (task.get("replacement_paths") or [])
                    if str(item).strip()
                ]
                if not target_output_dir:
                    raise ValueError("发布图替换需要选中菜品目录。")
                if not replacement_paths:
                    raise ValueError("请至少选择一张要替换的图片。")
                dish_dir = resolve_output_path(target_output_dir)
                for path_text in replacement_paths:
                    resolve_output_file(path_text)
                result = run_publish_image_replace(
                    dish_dir,
                    replacement_paths=replacement_paths,
                )
            else:
                if mode == "file":
                    write_idea_file(str(task.get("dish_name", "")).strip(), str(task.get("notes", "")).strip())
                if mode == "target":
                    target_output_dir = str(task.get("target_output_dir", "")).strip()
                    if not target_output_dir:
                        raise ValueError("指定造菜需要选中菜品目录。")
                    result = run_v2_mode2(mode="target", target_output_dir=target_output_dir)
                else:
                    result = run_v2_mode2(mode=mode if mode in {"auto", "file"} else None)
        task_log_lines = RUN_LOG_LINES[log_start_index:]
        run_log_file = write_run_log_file(
            result=result,
            task=task,
            log_lines=task_log_lines,
            success=True,
        )
        if run_log_file:
            result["run_log_file"] = run_log_file
            append_run_log(f"[{time.strftime('%H:%M:%S')}] 运行日志已保存：{run_log_file}")
        with RUN_LOCK:
            LAST_RESULT = result
            LAST_ERROR = ""
            try:
                write_run_meta(result, task=task)
            except Exception as meta_exc:  # noqa: BLE001
                append_run_log(f"[{time.strftime('%H:%M:%S')}] 写入运行元数据失败：{meta_exc}")
        append_run_log(f"[{time.strftime('%H:%M:%S')}] 任务 #{task.get('task_id', '-')} 完成。")
    except Exception as exc:  # noqa: BLE001
        task_log_lines = RUN_LOG_LINES[log_start_index:]
        run_log_file = write_run_log_file(
            result=result,
            task=task,
            log_lines=task_log_lines,
            success=False,
            error_text=str(exc),
        )
        if run_log_file:
            append_run_log(f"[{time.strftime('%H:%M:%S')}] 运行日志已保存：{run_log_file}")
        with RUN_LOCK:
            LAST_ERROR = str(exc)
        append_run_log(f"[{time.strftime('%H:%M:%S')}] 任务 #{task.get('task_id', '-')} 失败：{exc}")
        append_run_log(traceback.format_exc())
    finally:
        with RUN_LOCK:
            RUNNING = False
            LAST_FINISHED_AT = time.time()
            CURRENT_TASK = None
            start_next_task_locked()


class V2PanelHandler(BaseHTTPRequestHandler):
    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        safe_message = message if message and message.isascii() else HTTPStatus(code).phrase
        super().send_error(code, safe_message, explain)

    def log_message(self, format: str, *args: Any) -> None:
        if len(args) >= 2:
            request_line = str(args[0])
            status_code = str(args[1])
            if status_code.isdigit() and int(status_code) < 400:
                parts = request_line.split()
                if len(parts) >= 2:
                    method = parts[0]
                    path_only = parts[1].split("?", 1)[0]
                    if path_only in SILENT_HTTP_LOG_PATHS:
                        return
                    # 轮询类 GET（state/history/dish_detail 等）不写终端，避免 Cursor 集成终端刷屏卡顿
                    if method == "GET" and (
                        path_only.startswith("/api/")
                        or path_only in {"/", "/favicon.ico", "/favicon.png"}
                    ):
                        return
        super().log_message(format, *args)

    def _write_body(self, raw: bytes) -> None:
        try:
            self.wfile.write(raw)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            # 轮询端断开连接时忽略，避免长任务期间刷屏报错。
            return

    def _send_json(self, payload: dict[str, Any], code: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self._write_body(raw)

    def _send_text(self, text: str, code: int = 200) -> None:
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self._write_body(raw)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._write_body(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/favicon.ico", "/favicon.png"}:
            self._send_file(ROOT_DIR / "favicon.png")
            return
        if parsed.path == "/":
            self._send_text(HTML_PAGE, 200)
            return
        if parsed.path == "/api/ping":
            self._send_json({"ok": True})
            return
        if parsed.path == "/api/state":
            sync_panel_image_gen_prefs()
            self._send_json(
                {
                    "config": current_config_snapshot(),
                    "image_gen_options": image_gen_options_for_frontend(PANEL_IMAGE_GEN_PREFS),
                    "idea": read_idea_file(),
                    "history": list_history(limit=12, offset=0),
                    "history_revision": history_revision(),
                    "last_result": LAST_RESULT or {},
                    "publish_platforms": {key: item["label"] for key, item in PUBLISH_PLATFORMS.items()},
                    "meal_tag_labels": MEAL_TAG_LABELS,
                    "publish_plan": load_publish_plan(),
                    "custom_images_dir": str((ROOT_DIR / "custom_image_gen" / "images").resolve()),
                }
            )
            return
        if parsed.path == "/api/publish_plan":
            self._send_json(load_publish_plan())
            return
        if parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            raw_limit = (query.get("limit", ["30"])[0] or "30").strip()
            raw_offset = (query.get("offset", ["0"])[0] or "0").strip()
            try:
                limit = max(1, min(60, int(raw_limit)))
            except ValueError:
                limit = 30
            try:
                offset = max(0, int(raw_offset))
            except ValueError:
                offset = 0
            raw_sort = (query.get("sort", ["favorite"])[0] or "favorite").strip()
            sort = raw_sort if raw_sort in VALID_HISTORY_SORTS else "favorite"
            self._send_json(
                {
                    "items": list_history(limit=limit, offset=offset, sort=sort),
                    "offset": offset,
                    "limit": limit,
                    "sort": sort,
                }
            )
            return
        if parsed.path == "/api/run_status":
            query = parse_qs(parsed.query)
            raw_from = (query.get("from", ["0"])[0] or "0").strip()
            try:
                from_index = max(0, int(raw_from))
            except ValueError:
                from_index = 0
            with RUN_LOCK:
                total_logs = len(RUN_LOG_LINES)
                logs = RUN_LOG_LINES[from_index:] if from_index < total_logs else []
                now = time.time()
                elapsed = int(max(0.0, (now - LAST_STARTED_AT))) if RUNNING and LAST_STARTED_AT > 0 else 0
                task_queue = build_task_queue_snapshot()
                payload = {
                    "running": RUNNING,
                    "elapsed_seconds": elapsed,
                    "logs": logs,
                    "next_index": total_logs,
                    "result": LAST_RESULT or {},
                    "error": LAST_ERROR,
                    "queue_length": task_queue["queue_length"],
                    "current_task": task_queue.get("running") or {},
                    "task_queue": task_queue,
                    "history": list_history(limit=12, offset=0),
                    "history_revision": history_revision(),
                }
            self._send_json(payload)
            return
        if parsed.path == "/api/publish_status":
            query = parse_qs(parsed.query)
            raw_from = (query.get("from", ["0"])[0] or "0").strip()
            try:
                log_from = max(0, int(raw_from))
            except ValueError:
                log_from = 0
            self._send_json(publish_status_snapshot(log_from=log_from))
            return
        if parsed.path == "/api/custom_image/status":
            query = parse_qs(parsed.query)
            raw_from = (query.get("from", ["0"])[0] or "0").strip()
            try:
                log_from = max(0, int(raw_from))
            except ValueError:
                log_from = 0
            self._send_json(custom_image_status_snapshot(log_from=log_from))
            return
        if parsed.path == "/api/dish_detail":
            query = parse_qs(parsed.query)
            raw_path = (query.get("path", [""])[0] or "").strip()
            if not raw_path:
                self._send_json({"error": "path 不能为空。"}, code=400)
                return
            try:
                folder = resolve_output_path(raw_path)
                self._send_json(read_dish_output_summary(folder))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/text_assets":
            query = parse_qs(parsed.query)
            raw_path = (query.get("path", [""])[0] or "").strip()
            if not raw_path:
                self._send_json({"error": "path 不能为空。"}, code=400)
                return
            try:
                self._send_json(read_text_assets(raw_path))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/file":
            query = parse_qs(parsed.query)
            raw_path = (query.get("path", [""])[0] or "").strip()
            if not raw_path:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_file(resolve_output_file(raw_path))
            except (ValueError, FileNotFoundError):
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        global RUNNING, LAST_RESULT, LAST_ERROR, LAST_STARTED_AT, LAST_FINISHED_AT, TASK_SEQ
        parsed = urlparse(self.path)
        if parsed.path == "/api/custom_image/generate":
            length = int(self.headers.get("Content-Length", "0") or "0")
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(length) if length > 0 else b""
            try:
                self._send_json(handle_custom_image_generate(body, content_type))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return

        if parsed.path not in {
            "/api/run_start",
            "/api/open_output",
            "/api/history_delete",
            "/api/publish_start",
            "/api/publish_login_confirm",
            "/api/history_batch_delete",
            "/api/history_favorite",
            "/api/dish_tags",
            "/api/dish_tags/batch",
            "/api/publish_plan",
            "/api/text_assets_save",
            "/api/image_gen_prefs",
            "/api/work_stop",
            "/api/panel_shutdown",
            "/api/panel_restart",
        }:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            self._send_json({"error": "请求体 JSON 格式错误。"}, code=400)
            return

        if parsed.path == "/api/open_output":
            try:
                opened = open_output_path(str(payload.get("path", "")).strip())
                self._send_json({"ok": True, "path": str(opened)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/history_delete":
            try:
                archived_to = archive_history_folder(str(payload.get("path", "")).strip())
                self._send_json({"ok": True, "archived_to": str(archived_to), "deleted": str(archived_to)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/history_batch_delete":
            try:
                raw_paths = payload.get("paths") or []
                if not isinstance(raw_paths, list):
                    raise ValueError("paths 必须是数组。")
                archived = delete_history_folders([str(item) for item in raw_paths])
                self._send_json({"ok": True, "archived": archived, "deleted": archived, "deleted_count": len(archived)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/history_favorite":
            try:
                self._send_json(toggle_dish_favorite(str(payload.get("path", "")).strip()))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/dish_tags":
            try:
                self._send_json(
                    set_dish_meal_tags(
                        str(payload.get("path", "")).strip(),
                        payload.get("tags") or [],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/dish_tags/batch":
            try:
                raw_paths = payload.get("paths") or []
                if not isinstance(raw_paths, list):
                    raise ValueError("paths 必须是数组。")
                self._send_json(
                    batch_update_dish_meal_tags(
                        [str(item) for item in raw_paths],
                        add_tags=payload.get("add") or payload.get("add_tags") or [],
                        remove_tags=payload.get("remove") or payload.get("remove_tags") or [],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/publish_plan":
            try:
                self._send_json(save_publish_plan(payload))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/publish_login_confirm":
            try:
                confirm_publish_login()
                self._send_json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/work_stop":
            self._send_json(stop_background_work())
            return
        if parsed.path == "/api/panel_shutdown":
            schedule_panel_shutdown(restart=False)
            self._send_json({"ok": True, "message": "面板正在终止。"})
            return
        if parsed.path == "/api/panel_restart":
            schedule_panel_shutdown(restart=True)
            self._send_json({"ok": True, "message": "面板正在重启。"})
            return
        if parsed.path == "/api/publish_start":
            try:
                output_dir_text = str(payload.get("output_dir", "")).strip()
                platform_keys = [str(item).strip() for item in (payload.get("platforms") or []) if str(item).strip()]
                start_publish_task(output_dir_text, platform_keys)
                self._send_json({"ok": True, "platforms": platform_keys})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/text_assets_save":
            try:
                raw_path = str(payload.get("path", "")).strip()
                if not raw_path:
                    raise ValueError("path 不能为空。")
                if "content" not in payload:
                    raise ValueError("content 不能为空。")
                content = str(payload.get("content", ""))
                self._send_json(save_text_asset(raw_path, content))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/image_gen_prefs":
            try:
                controls = sync_panel_image_gen_prefs(payload)
                self._send_json({"ok": True, "current": controls})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return

        action = str(payload.get("action", "run")).strip().lower() or "run"
        if action not in {"run", "idea_only", "supplement", "anchor_poster_regenerate", "publish_image_replace"}:
            action = "run"
        mode = str(payload.get("mode", "")).strip().lower()
        dish_name = str(payload.get("dish_name", "")).strip()
        idea_count_raw = str(payload.get("idea_count", "1")).strip() or "1"

        if action == "run" and mode == "file" and not dish_name:
            self._send_json({"error": "手动模式下，菜名不能为空。"}, code=400)
            return
        if action == "run" and mode == "target":
            target_output_dir = str(payload.get("target_output_dir", "")).strip()
            if not target_output_dir:
                self._send_json({"error": "指定请先在菜品池选中一个菜品。"}, code=400)
                return
        if action == "supplement":
            target_output_dir = str(payload.get("target_output_dir", "")).strip()
            if not target_output_dir:
                self._send_json({"error": "补生请先在菜品池选中一个菜品。"}, code=400)
                return
            supplement_targets = [
                str(item).strip()
                for item in (payload.get("supplement_targets") or [])
                if str(item).strip()
            ]
            if not supplement_targets:
                self._send_json({"error": "请至少勾选一项补生内容。"}, code=400)
                return
        if action == "anchor_poster_regenerate":
            target_output_dir = str(payload.get("target_output_dir", "")).strip()
            anchor_poster_path = str(payload.get("anchor_poster_path", "")).strip()
            if not target_output_dir:
                self._send_json({"error": "请先在菜品池选中一道菜。"}, code=400)
                return
            if not anchor_poster_path:
                self._send_json({"error": "请先选中一张候选海报。"}, code=400)
                return
            try:
                anchor_file = resolve_output_file(anchor_poster_path)
                dish_dir = resolve_output_path(target_output_dir)
                publish_dir = (dish_dir / "publish").resolve()
                history_dir = (dish_dir / "history").resolve()
                if publish_dir in anchor_file.parents or anchor_file.parent == publish_dir:
                    raise ValueError("请选择 publish 文件夹外的候选海报。")
                if history_dir in anchor_file.parents or anchor_file.parent == history_dir:
                    raise ValueError("请勿选择 history 存档内的图片。")
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
                return
        if action == "publish_image_replace":
            target_output_dir = str(payload.get("target_output_dir", "")).strip()
            replacement_paths = [
                str(item).strip()
                for item in (payload.get("replacement_paths") or [])
                if str(item).strip()
            ]
            if not target_output_dir:
                self._send_json({"error": "请先在菜品池选中一道菜。"}, code=400)
                return
            if not replacement_paths:
                self._send_json({"error": "请至少选择一张要替换的图片。"}, code=400)
                return
            try:
                dish_dir = resolve_output_path(target_output_dir)
                publish_dir = (dish_dir / "publish").resolve()
                history_dir = (dish_dir / "history").resolve()
                for path_text in replacement_paths:
                    candidate = resolve_output_file(path_text)
                    if publish_dir in candidate.parents or candidate.parent == publish_dir:
                        raise ValueError("请从「非发布图」中选择菜品目录或 history 外的候选图。")
                    if history_dir in candidate.parents or candidate.parent == history_dir:
                        raise ValueError("请勿选择 history 存档内的图片。")
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
                return
        if action == "idea_only":
            try:
                idea_count = int(idea_count_raw)
            except ValueError:
                self._send_json({"error": "生成数量必须是整数。"}, code=400)
                return
            if idea_count < 1 or idea_count > 30:
                self._send_json({"error": "生成数量须在 1～30 之间。"}, code=400)
                return
            if mode == "file" and idea_count > 1:
                self._send_json({"error": "手动生菜每次只能生成 1 条。"}, code=400)
                return

        with RUN_LOCK:
            waiting_ahead = len(TASK_QUEUE) + (1 if RUNNING else 0)
            TASK_SEQ += 1
            target_output_dir = str(payload.get("target_output_dir", "")).strip()
            task_item = {
                "task_id": TASK_SEQ,
                "action": action,
                "mode": mode if mode in {"auto", "file", "target", "supplement", "idea"} else "",
                "target_output_dir": target_output_dir if action in {"run", "supplement", "anchor_poster_regenerate", "publish_image_replace"} and (mode in {"target", "supplement"} or action in {"anchor_poster_regenerate", "publish_image_replace"}) else "",
                "anchor_poster_path": str(payload.get("anchor_poster_path", "")).strip() if action == "anchor_poster_regenerate" else "",
                "replacement_paths": [
                    str(item).strip()
                    for item in (payload.get("replacement_paths") or [])
                    if str(item).strip()
                ] if action == "publish_image_replace" else [],
                "supplement_targets": [
                    str(item).strip()
                    for item in (payload.get("supplement_targets") or [])
                    if str(item).strip()
                ] if action == "supplement" else [],
                "cuisine_mode": str(payload.get("cuisine_mode", "")).strip(),
                "dish_name": dish_name,
                "notes": str(payload.get("notes", "")).strip(),
                "model_temperature": str(payload.get("model_temperature", "")).strip(),
                "idea_count": idea_count_raw if action == "idea_only" else "",
                "poster_quality": str(payload.get("poster_quality", "")).strip(),
                "poster_count": str(payload.get("poster_count", "")).strip(),
                "detail_quality": str(payload.get("detail_quality", "")).strip(),
                "detail_count": str(payload.get("detail_count", "")).strip(),
                "recipe_quality": str(payload.get("recipe_quality", "")).strip(),
                "recipe_count": str(payload.get("recipe_count", "")).strip(),
                "cover_mode2_quality": str(payload.get("cover_mode2_quality", "")).strip(),
                "cover_mode2_count": str(payload.get("cover_mode2_count", "")).strip(),
                "queued_at": time.time(),
            }
            if waiting_ahead == 0:
                RUN_LOG_LINES.clear()
                LAST_RESULT = None
                LAST_ERROR = ""
            TASK_QUEUE.append(task_item)
            started_now = start_next_task_locked()
            queue_waiting = len(TASK_QUEUE)

        self._send_json(
            {
                "ok": True,
                "task_id": task_item["task_id"],
                "started_now": started_now,
                "waiting_ahead": waiting_ahead,
                "queue_waiting": queue_waiting,
            }
        )


def main() -> None:
    global PANEL_SERVER
    reexec_in_project_venv_if_needed()
    ensure_runtime_config_loaded()
    sync_panel_image_gen_prefs()
    init_custom_image_service()
    server = ThreadingHTTPServer((HOST, PORT), V2PanelHandler)
    PANEL_SERVER = server
    print(f"V2 前端面板已启动：http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务。")
    server.serve_forever()


if __name__ == "__main__":
    main()

