from __future__ import annotations

import json
import mimetypes
import os
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

from mode2_flow import run_v2_mode2
from idea_batch import run_idea_batch, DISH_POOL_DIR
from v2_core import (
    IDEA_FILE,
    OUTPUT_DIR,
    build_openai_image_client,
    build_run_output_dir,
    ensure_runtime_config_loaded,
    generate_images_by_prompt,
    get_cover_image_count,
    get_image_settings,
    get_timestamp,
    load_dish_idea_record_from_dir,
    save_generated_images,
    save_text_output,
)


HOST = "127.0.0.1"
PORT = 8765
PANEL_VERSION = "v0.66"
REPO_ROOT = ROOT_DIR.parent

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

RUN_LOCK = threading.Lock()
RUNNING = False
LAST_RESULT: dict[str, Any] | None = None
LAST_ERROR = ""
LAST_STARTED_AT = 0.0
LAST_FINISHED_AT = 0.0
RUN_LOG_LINES: list[str] = []
MAX_LOG_LINES = 2000
TASK_QUEUE: list[dict[str, Any]] = []
CURRENT_TASK: dict[str, Any] | None = None
TASK_SEQ = 0
RUN_META_FILE_NAME = "_run_meta.json"

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


def append_run_log(text: str) -> None:
    line = text.rstrip("\r\n")
    if not line:
        return
    with RUN_LOCK:
        RUN_LOG_LINES.append(line)
        if len(RUN_LOG_LINES) > MAX_LOG_LINES:
            del RUN_LOG_LINES[: len(RUN_LOG_LINES) - MAX_LOG_LINES]


def append_publish_log(text: str) -> None:
    line = text.rstrip("\r\n")
    if not line:
        return
    with PUBLISH_LOCK:
        PUBLISH_LOG_LINES.append(line)
        if len(PUBLISH_LOG_LINES) > MAX_PUBLISH_LOG_LINES:
            del PUBLISH_LOG_LINES[: len(PUBLISH_LOG_LINES) - MAX_PUBLISH_LOG_LINES]


def history_revision() -> str:
    dirs = collect_history_dirs()
    if not dirs:
        return "0:0"
    max_mtime = max(path.stat().st_mtime for path in dirs)
    return f"{len(dirs)}:{max_mtime:.3f}"


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
    proc = subprocess.Popen(
        [sys.executable, str(script_path), str(output_dir)],
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
    assert proc.stdout is not None
    for line in proc.stdout:
        append_publish_log(line)
        confirm_kind = _wait_for_publish_login(line, platform["label"])
        if confirm_kind and proc.stdin is not None:
            proc.stdin.write("y\n" if confirm_kind == "y" else "\n")
            proc.stdin.flush()
    return_code = proc.wait()
    if return_code != 0:
        raise RuntimeError(f"{platform['label']} 发布脚本退出码 {return_code}，请查看日志。")
    append_publish_log(f"===== 完成发布：{platform['label']} =====")


def publish_worker(output_dir_text: str, platform_keys: list[str]) -> None:
    global PUBLISH_RUNNING, PUBLISH_ERROR, PUBLISH_OUTPUT_DIR, PUBLISH_CURRENT_PLATFORM, PUBLISH_QUEUE
    try:
        output_dir = resolve_output_path(output_dir_text)
        final_dir = output_dir / "publish" / "final"
        if not final_dir.is_dir():
            raise FileNotFoundError(f"未找到发布图目录：{final_dir}")
        with PUBLISH_LOCK:
            PUBLISH_OUTPUT_DIR = str(output_dir)
            PUBLISH_QUEUE = list(platform_keys)
        for platform_key in platform_keys:
            with PUBLISH_LOCK:
                PUBLISH_CURRENT_PLATFORM = platform_key
                if platform_key in PUBLISH_QUEUE:
                    PUBLISH_QUEUE.remove(platform_key)
            run_single_platform_publish(platform_key, output_dir)
        append_publish_log("全部所选平台发布流程已执行完成，请在各平台浏览器中核对。")
    except Exception as exc:  # noqa: BLE001
        with PUBLISH_LOCK:
            PUBLISH_ERROR = str(exc)
        append_publish_log(f"发布失败：{exc}")
        append_publish_log(traceback.format_exc())
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
  <title>V2 自动造菜控制台</title>
  <link rel="icon" type="image/png" href="/favicon.png" />
  <link rel="shortcut icon" type="image/png" href="/favicon.ico" />
  <style>
    :root{
      --bg:#0d1117; --panel:#121a27; --panel-soft:#1a2435; --line:#2d3a52; --text:#e6edf7; --sub:#9aa7bd;
      --pri:#020617; --ok:#22c55e; --warn:#f59e0b; --danger:#ef4444;
      --shadow:0 10px 28px rgba(0,0,0,.35);
      --col-left:320px;
      --col-mid:430px;
      --col-publish:220px;
      --splitter:8px;
    }
    *{box-sizing:border-box}
    body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:radial-gradient(1200px 600px at 10% -10%, #1b2438 0%, transparent 60%),var(--bg);color:var(--text)}
    .wrap{max-width:1600px;margin:0 auto;padding:14px 16px 84px}
    .top{
      position:sticky;top:10px;z-index:30;display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px;
      background:rgba(16,24,39,.92);border:1px solid var(--line);border-radius:12px;padding:10px 12px;box-shadow:var(--shadow);backdrop-filter:blur(4px);
    }
    .title{font-size:22px;font-weight:700}
    .sub{font-size:12px;color:var(--sub)}
    .chips{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .chip{font-size:12px;background:#0b1220;border:1px solid #334155;padding:6px 10px;border-radius:999px;color:#dbe7ff}
    .version-tag{font-size:12px;color:#e2e8f0;background:#1e293b;border:1px solid #475569;border-radius:999px;padding:4px 10px}
    .top-actions{display:flex;gap:8px}
    button{transition:background .15s ease,border-color .15s ease,box-shadow .15s ease,color .15s ease,transform .12s ease}
    button:hover{border-color:#60a5fa!important;box-shadow:0 0 0 2px rgba(96,165,250,.22)}
    button:active{transform:translateY(1px)}
    button.active,.view-tab.active,.text-tab.active,.text-file-btn.active{
      border-color:#22c55e!important;background:linear-gradient(180deg,#064e3b,#022c22)!important;color:#dcfce7!important;
      box-shadow:0 0 0 2px rgba(34,197,94,.22)!important;
    }
    .top-actions button{
      width:auto;padding:7px 10px;border:1px solid #334155;border-radius:8px;background:#0b1220;color:#dbe7ff;cursor:pointer;font-size:12px;
    }
    .top-actions .danger-btn{border-color:#7f1d1d;color:#fecaca;background:#2b1313}
    .four-col{
      display:grid;
      grid-template-columns:var(--col-left) var(--splitter) var(--col-mid) var(--splitter) minmax(420px, 1fr) var(--col-publish);
      gap:0;
      align-items:start;
      min-height:calc(100vh - 120px);
    }
    .panel{
      min-width:0;
      background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;box-shadow:var(--shadow);
      height:calc(100vh - 112px);overflow:auto;
    }
    .panel-left{margin-right:6px}
    .panel-mid{margin:0 6px}
    .panel-right{margin:0 6px}
    .panel-publish{margin-left:6px;display:flex;flex-direction:column;gap:10px}
    .publish-platform-list{display:flex;flex-direction:column;gap:8px;flex:1}
    .publish-platform-item{display:flex;align-items:center;gap:8px;font-size:13px;color:#dbe7ff;border:1px solid #2f3b55;border-radius:8px;padding:8px 10px;background:#0f172a;cursor:pointer}
    .publish-platform-item input{width:auto;margin:0;accent-color:#22c55e}
    .publish-status{min-height:72px;max-height:180px;overflow:auto;font-size:12px;color:var(--sub);white-space:pre-wrap;line-height:1.45;border:1px solid #2a364f;border-radius:8px;padding:8px;background:#0b1220}
    .btn-publish{width:100%;padding:12px;border:1px solid #4b5563;border-radius:10px;background:linear-gradient(180deg,#14532d,#052e16);color:#dcfce7;font-size:15px;font-weight:700;cursor:pointer}
    .btn-publish[disabled]{opacity:.6;cursor:not-allowed}
    .gallery-filter{width:auto;padding:7px 10px;border:1px solid #3a475f;border-radius:8px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:12px}
    .modal{position:fixed;inset:0;background:rgba(2,6,23,.72);display:flex;align-items:center;justify-content:center;z-index:80;padding:16px}
    .modal.hidden{display:none}
    .modal-box{max-width:460px;width:100%;border:1px solid #334155;border-radius:12px;background:#111827;padding:16px;box-shadow:var(--shadow)}
    .modal-box h3{margin:0 0 10px;font-size:18px}
    .modal-box p{margin:0 0 14px;font-size:13px;line-height:1.6;color:#cbd5e1;white-space:pre-wrap}
    .modal-box button{width:100%;padding:11px;border:1px solid #22c55e;border-radius:9px;background:#14532d;color:#dcfce7;font-weight:700;cursor:pointer}
    .splitter{
      margin:0 2px;border-radius:8px;background:linear-gradient(180deg,#334155,#1f2937);cursor:col-resize;height:calc(100vh - 112px);
      user-select:none;position:relative;
    }
    .splitter::after{
      content:"";position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:3px;height:60px;border-radius:3px;background:#94a3b8;opacity:.55;
    }
    .panel-title{font-size:15px;font-weight:700;margin:0 0 8px}
    .section-card{border:1px solid #283449;background:var(--panel-soft);border-radius:10px;padding:10px}
    .sec-title{margin:0 0 8px;font-size:14px;font-weight:700}
    .sec-desc{margin:0 0 8px;font-size:12px;color:var(--sub)}
    .mode{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
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
    .history-head-actions{display:flex;gap:6px;align-items:center}
    .history-head-actions button{
      width:auto;padding:5px 9px;border:1px solid #475569;border-radius:7px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-size:11px;
    }
    .history-head-actions .danger-btn{border-color:#7f1d1d;color:#fecaca;background:#2b1313}
    .history-head-actions .hidden{display:none}
    .history-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:8px;align-content:start}
    .history-empty{color:var(--sub);font-size:12px;grid-column:1/-1}
    .history-item{
      position:relative;border:1px solid #2f3b55;border-radius:10px;background:#0f172a;overflow:hidden;cursor:pointer;
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
    .history-cover{grid-area:cover;width:52px;height:52px;background:#111827;border-radius:7px;display:flex;align-items:center;justify-content:center;overflow:hidden}
    .history-cover img{width:100%;height:100%;object-fit:cover}
    .history-meta{grid-area:meta;padding:0;min-width:0}
    .history-name{font-size:12px;font-weight:700;line-height:1.3;word-break:break-all;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
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
    .text-content{margin:0;min-height:360px;max-height:calc(100vh - 360px);overflow:auto;white-space:pre-wrap;word-break:break-word;border:1px solid #26344b;border-radius:10px;background:#08111f;padding:12px;color:#e5edf8;font-size:13px;line-height:1.65}
    .log-toggle{
      position:fixed;right:16px;bottom:16px;z-index:55;padding:9px 12px;border-radius:999px;border:1px solid #475569;
      background:#0f172a;color:#e2e8f0;box-shadow:var(--shadow);cursor:pointer;font-size:12px;
    }
    .log-drawer{
      position:fixed;left:10px;right:10px;bottom:10px;height:0;opacity:0;pointer-events:none;z-index:54;
      border:1px solid #334155;border-radius:12px;background:#111827;box-shadow:var(--shadow);overflow:hidden;transition:all .2s ease;
    }
    .log-drawer.open{height:260px;opacity:1;pointer-events:auto;resize:vertical;min-height:180px;max-height:70vh}
    .log-head{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid #334155}
    .log-head-title{font-size:13px;font-weight:700}
    .log-head-actions{display:flex;gap:8px}
    .log-head-actions button{width:auto;padding:6px 9px;border:1px solid #475569;border-radius:8px;background:#0b1220;color:#dbe7ff;cursor:pointer;font-size:12px}
    .log-panel{
      height:calc(100% - 44px);margin:0;padding:10px;overflow:auto;font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.45;
      white-space:pre-wrap;word-break:break-word;color:#d1d9e9;
    }
    @media(max-width:1500px){.four-col{grid-template-columns:300px var(--splitter) 400px var(--splitter) minmax(380px,1fr) 200px}}
    @media(max-width:1200px){
      .four-col{grid-template-columns:1fr}
      .splitter{display:none}
      .panel{height:auto}
      .panel-left,.panel-mid,.panel-right,.panel-publish{margin:0 0 10px}
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
      <div>
        <div class="title">V2 自动造菜控制台</div>
        <div class="sub">四栏布局：菜品池、造菜控制、看图/文字、发布平台；左侧宽度可拖拽并自动记忆。</div>
      </div>
      <div class="chips">
        <span id="envQuality" class="chip">画质：-</span>
        <span id="envCount" class="chip">出图数：-</span>
        <span id="envCoverCount" class="chip">封面数：-</span>
        <span id="envMode" class="chip">模式：-</span>
        <span class="version-tag">版本：__PANEL_VERSION__</span>
      </div>
      <div class="top-actions">
        <button id="openOutputTopBtn" type="button">打开目录</button>
        <button id="copyOutputTopBtn" type="button">复制路径</button>
        <button id="refreshTopBtn" type="button">刷新菜品池</button>
      </div>
    </div>

    <div id="fourColLayout" class="four-col">
      <section class="panel panel-left">
        <div class="history-head">
          <h3 class="panel-title">菜品池</h3>
          <div class="history-head-actions">
            <button id="batchDeleteBtn" type="button">批量删除</button>
            <button id="batchDeleteAllBtn" type="button" class="danger-btn hidden">全部删除</button>
          </div>
        </div>
        <div id="history" class="history-grid"></div>
        <div id="historyLoadMore" class="load-more">
          <button id="loadMoreBtn" type="button">加载更多</button>
        </div>
      </section>

      <div id="splitterLeft" class="splitter" title="拖拽调整宽度"></div>

      <aside class="panel panel-mid">
        <div class="section-card">
          <h3 class="sec-title">造菜方式</h3>
          <div class="mode">
            <button id="modeAutoBtn" class="active" type="button">自动造菜</button>
            <button id="modeFileBtn" type="button">手动点名</button>
            <button id="modeTargetBtn" type="button">指定造菜</button>
          </div>
          <div id="targetModeHint" class="sec-desc" style="display:none;margin-top:8px">在左侧菜品池选中一项后，将自动读取菜名与描述，并在原目录内生成图片与文案。</div>
          <div id="cuisineCard" style="margin-top:8px">
            <label>自动造菜菜系</label>
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
            <div class="sec-desc">仅「自动造菜」生效，将写入 AUTO_DISH_CUISINE_MODE。</div>
          </div>
          <label style="margin-top:8px">手动菜名（仅手动点名生效）</label>
          <input id="dishName" placeholder="例如：蒜香煎嫩鸡胸肉" />
        </div>

        <div class="section-card">
          <h3 class="sec-title">只生成造菜信息</h3>
          <div class="sec-desc">仅调用豆包写入 txt，不生成图片。自动造菜可批量；手动点名每次 1 条。</div>
          <label>生成数量</label>
          <input id="ideaCount" type="number" min="1" max="30" step="1" value="3" />
          <button id="ideaOnlyBtn" type="button" style="margin-top:8px;width:100%;padding:10px;border:1px solid #3a475f;border-radius:9px;background:#101a2a;color:#dbe7ff;cursor:pointer;font-weight:700">只生成造菜信息</button>
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
                <label>数量</label><input id="posterCount" type="number" min="1" max="4" step="1" />
                <label style="margin-top:6px">画质</label>
                <select id="posterQuality"><option value="low">标准</option><option value="medium">中等</option><option value="high">高清</option><option value="auto">自动</option></select>
              </div>
              <div class="mode2-item">
                <h4>细节图</h4>
                <label>数量</label><input id="detailCount" type="number" min="1" max="4" step="1" />
                <label style="margin-top:6px">画质</label>
                <select id="detailQuality"><option value="low">标准</option><option value="medium">中等</option><option value="high">高清</option><option value="auto">自动</option></select>
              </div>
              <div class="mode2-item">
                <h4>菜谱图</h4>
                <label>数量</label><input id="recipeCount" type="number" min="1" max="4" step="1" />
                <label style="margin-top:6px">画质</label>
                <select id="recipeQuality"><option value="low">标准</option><option value="medium">中等</option><option value="high">高清</option><option value="auto">自动</option></select>
              </div>
              <div class="mode2-item">
                <h4>封面图</h4>
                <label>数量</label><input id="coverMode2Count" type="number" min="1" max="4" step="1" />
                <label style="margin-top:6px">画质</label>
                <select id="coverMode2Quality"><option value="low">标准</option><option value="medium">中等</option><option value="high">高清</option><option value="auto">自动</option></select>
              </div>
            </div>
          </div>
          <div class="preset-row" style="margin-top:8px">
            <button id="presetBudgetBtn" type="button">省成本模板</button>
            <button id="presetQualityBtn" type="button">高质量模板</button>
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

      <div id="splitterRight" class="splitter" title="拖拽调整宽度"></div>

      <section class="panel panel-right">
        <div class="result-card">
          <div class="right-tabs">
            <button id="imageTabBtn" class="view-tab active" type="button">看图栏</button>
            <button id="textTabBtn" class="view-tab" type="button">文字栏</button>
          </div>
          <div id="imagePane" class="content-pane">
            <div class="gallery-strip">
              <div class="gallery-tools">
                <div class="gallery-actions">
                  <button id="prevImgBtn" type="button">上一张</button>
                  <button id="nextImgBtn" type="button">下一张</button>
                  <button id="filterPublishBtn" class="gallery-filter" type="button">发布图</button>
                  <button id="filterAllBtn" class="gallery-filter active" type="button">非发布图</button>
                </div>
                <div id="galleryIndex" class="gallery-index">0 / 0</div>
              </div>
              <div id="thumbs" class="thumbs"></div>
            </div>
            <div class="result-stage">
              <div id="runSkeleton" class="skeleton" style="display:none"></div>
              <div id="runBadge" class="run-badge">处理中 0%</div>
              <img id="resultImg" class="gallery-main" alt="暂无图片" />
              <div id="emptyImageState" class="empty-image-state">
                <div class="empty-image-icon">图</div>
                <div class="empty-image-title">暂未生成图片</div>
                <div class="empty-image-sub">运行任务后在这里查看结果，双击可新标签打开原图。</div>
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

      <aside class="panel panel-publish">
        <h3 class="panel-title">发布平台</h3>
        <div id="publishPlatforms" class="publish-platform-list">
          <label class="publish-platform-item"><input type="checkbox" value="douyin" checked />抖音</label>
          <label class="publish-platform-item"><input type="checkbox" value="kuaishou" checked />快手</label>
          <label class="publish-platform-item"><input type="checkbox" value="xiaohongshu" checked />小红书</label>
          <label class="publish-platform-item"><input type="checkbox" value="weixin_mp" checked />微信公众号</label>
          <label class="publish-platform-item"><input type="checkbox" value="weixin_channels" checked />微信视频号</label>
        </div>
        <div id="publishStatus" class="publish-status">选中左侧菜品后，勾选平台并点击发布。</div>
        <button id="publishBtn" class="btn-publish" type="button">发布</button>
      </aside>
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
        <button id="logFilterBtn" type="button">只看错误：关</button>
        <button id="logClearBtn" type="button">清空日志</button>
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
      historyLimit: 12,
      historyHasMore: true,
      historyLoading: false,
      historySnapshot: "",
      historyRevision: "",
      running: false,
      colLeft: 320,
      colMid: 430,
      activeRightTab: "image",
      selectedHistoryPath: "",
      galleryFilter: "all",
      galleryAllImages: [],
      galleryPublishImages: [],
      textAssets: null,
      activeTextCategory: "",
      activeTextFilePath: "",
      publishLogIndex: 0,
      publishPolling: false,
      batchDeleteMode: false,
      batchDeleteSelected: new Set(),
      selectedHistoryItem: null
    };
    const $ = (id) => document.getElementById(id);
    const QUALITY_INDEX = { low: 0, medium: 1, high: 2, auto: 3 };
    const INDEX_QUALITY = ["low", "medium", "high", "auto"];
    const LAYOUT_STORAGE_KEY = "v2_panel_layout_v1";

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
      const manual = mode === "file";
      const target = mode === "target";
      $("cuisineCard").style.display = mode === "auto" ? "block" : "none";
      $("targetModeHint").style.display = target ? "block" : "none";
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
      const manual = state.mode === "file";
      const target = state.mode === "target";
      $("ideaCount").disabled = manual || target;
      $("ideaCount").classList.toggle("input-disabled", manual || target);
      if(manual){ $("ideaCount").value = "1"; }
    }

    function setBatchDeleteMode(enabled){
      state.batchDeleteMode = Boolean(enabled);
      state.batchDeleteSelected = new Set();
      $("history").classList.toggle("batch-mode", state.batchDeleteMode);
      $("batchDeleteBtn").textContent = state.batchDeleteMode ? "取消批量" : "批量删除";
      $("batchDeleteAllBtn").classList.toggle("hidden", !state.batchDeleteMode);
      $("batchDeleteAllBtn").disabled = true;
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
        el.classList.remove("batch-selected");
        const checkbox = el.querySelector(".history-check input");
        if(checkbox){ checkbox.checked = false; }
      });
    }

    function updateBatchDeleteUi(){
      const count = state.batchDeleteSelected.size;
      $("batchDeleteAllBtn").disabled = count < 1;
      $("batchDeleteAllBtn").textContent = count > 0 ? `全部删除（${count}）` : "全部删除";
    }

    function toggleBatchDeletePath(path, checked){
      if(!path){ return; }
      if(checked){ state.batchDeleteSelected.add(path); }
      else{ state.batchDeleteSelected.delete(path); }
      updateBatchDeleteUi();
    }

    function setStatus(text, level=""){
      $("status").textContent = text;
      $("status").className = "status" + (level ? (" " + level) : "");
    }

    function setRunState(running, percent=0){
      state.running = running;
      const hasImage = Boolean(state.currentImagePath);
      $("runSkeleton").style.display = (running && !hasImage) ? "block" : "none";
      $("runBadge").style.display = running ? "block" : "none";
      $("runBadge").textContent = `处理中 ${percent}%`;
      if(running){
        $("runBtn").textContent = `运行中 ${percent}%（可继续排队）`;
        $("runBtn").disabled = false;
        return;
      }
      $("runBtn").textContent = "开始运行";
      $("runBtn").disabled = false;
    }

    function 应用三栏宽度(leftWidth, midWidth){
      const layout = $("fourColLayout");
      if(!layout){ return; }
      const total = layout.clientWidth || 1400;
      const splitterSpace = 2 * 8;
      const minLeft = 240;
      const minMid = 320;
      const minRight = 500;
      const maxLeft = Math.max(minLeft, total - splitterSpace - minMid - minRight);
      const safeLeft = Math.max(minLeft, Math.min(maxLeft, Math.round(leftWidth)));
      const maxMid = Math.max(minMid, total - splitterSpace - safeLeft - minRight);
      const safeMid = Math.max(minMid, Math.min(maxMid, Math.round(midWidth)));
      state.colLeft = safeLeft;
      state.colMid = safeMid;
      document.documentElement.style.setProperty("--col-left", `${safeLeft}px`);
      document.documentElement.style.setProperty("--col-mid", `${safeMid}px`);
    }

    function 保存三栏宽度(){
      try{
        localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({left: state.colLeft, mid: state.colMid}));
      }catch{}
    }

    function 加载三栏宽度(){
      try{
        const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
        if(raw){
          const data = JSON.parse(raw);
          应用三栏宽度(Number(data.left || 320), Number(data.mid || 430));
          return;
        }
      }catch{}
      应用三栏宽度(320, 430);
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

    function showLoginModal(message){
      $("loginModalMsg").textContent = message || "请在浏览器中完成平台登录后，点击下方按钮继续。";
      $("loginModal").classList.remove("hidden");
    }

    function hideLoginModal(){
      $("loginModal").classList.add("hidden");
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
        $("publishBtn").disabled = Boolean(data.running);
        $("publishBtn").textContent = data.running
          ? `发布中：${data.current_platform || "…"}`
          : "发布";
        if(!data.running && state.publishPolling){
          state.publishPolling = false;
          if(data.error){
            if(!silent){ setPublishStatus("发布失败：\\n" + data.error, "warn"); }
          }else if(state.publishLogIndex > 0){
            if(!silent){ setPublishStatus("发布流程已执行完成，请在各平台浏览器核对。", "ok"); }
          }
        }
        if(data.running){ state.publishPolling = true; }
      }catch(err){
        if(!silent){ setPublishStatus("读取发布状态失败：" + err.message, "warn"); }
      }
    }

    async function startPublish(){
      const outputPath = (state.selectedHistoryPath || state.currentOutputPath || "").trim();
      if(!outputPath || outputPath === "-"){
        setPublishStatus("请先在左侧菜品池选择一个菜品。", "warn");
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
        appendLogs([`[${new Date().toLocaleTimeString()}] 发布任务已启动：${platforms.join("、")}`]);
        $("publishBtn").disabled = true;
        await fetchPublishStatus();
      }catch(err){
        setPublishStatus("发布失败：" + err.message, "warn");
      }
    }

    function 初始化拖拽分栏(){
      const layout = $("fourColLayout");
      const splitL = $("splitterLeft");
      const splitR = $("splitterRight");
      if(!layout || !splitL || !splitR){ return; }

      const onDrag = (type, startX, startLeft, startMid) => (event) => {
        const dx = event.clientX - startX;
        if(type === "left"){
          应用三栏宽度(startLeft + dx, startMid);
        }else{
          应用三栏宽度(startLeft, startMid + dx);
        }
      };

      const bindDrag = (el, type) => {
        el.onmousedown = (e) => {
          e.preventDefault();
          const startX = e.clientX;
          const startLeft = state.colLeft;
          const startMid = state.colMid;
          const moveHandler = onDrag(type, startX, startLeft, startMid);
          const upHandler = () => {
            window.removeEventListener("mousemove", moveHandler);
            window.removeEventListener("mouseup", upHandler);
            保存三栏宽度();
          };
          window.addEventListener("mousemove", moveHandler);
          window.addEventListener("mouseup", upHandler);
        };
      };

      bindDrag(splitL, "left");
      bindDrag(splitR, "right");

      window.addEventListener("resize", () => 应用三栏宽度(state.colLeft, state.colMid));
    }

    function 同步参数滑块(){
      const t = Math.max(0, Math.min(15, Math.round((Number($("temperature").value || "0") * 10))));
      $("temperatureSlider").value = String(t);
    }

    function 绑定参数滑块(){
      $("temperatureSlider").oninput = () => { $("temperature").value = (Number($("temperatureSlider").value) / 10).toFixed(1); };
      $("temperature").oninput = 同步参数滑块;
    }

    function appendLogs(lines){
      if(!lines?.length){ return; }
      const panel = $("logPanel");
      const atBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 20;
      for(const line of lines){
        if(state.logOnlyErrors){
          const txt = String(line || "").toLowerCase();
          if(!(txt.includes("失败") || txt.includes("error") || txt.includes("traceback") || txt.includes("异常"))){
            continue;
          }
        }
        panel.textContent += line + "\\n";
      }
      if(panel.textContent.length > 200000){
        panel.textContent = panel.textContent.slice(panel.textContent.length - 150000);
      }
      if(atBottom){ panel.scrollTop = panel.scrollHeight; }
    }

    function 切换日志抽屉(打开){
      $("logDrawer").classList.toggle("open", 打开);
      $("logToggleBtn").textContent = 打开 ? "收起日志" : "日志";
    }

    function updateGalleryIndexLabel(){
      const total = state.galleryImages.length;
      const current = total ? state.galleryIndex + 1 : 0;
      $("galleryIndex").textContent = `${current} / ${total}`;
      $("prevImgBtn").disabled = total < 2;
      $("nextImgBtn").disabled = total < 2;
    }

    function openImageInNewTab(imagePath){
      if(!imagePath){
        setStatus("当前没有可打开的图片。", "warn");
        return;
      }
      window.open(fileUrl(imagePath), "_blank", "noopener,noreferrer");
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

    function renderTextAssets(){
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

      panel.innerHTML = "";
      const tabs = document.createElement("div");
      tabs.className = "text-tabs";
      groups.forEach((group) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "text-tab" + (group.key === state.activeTextCategory ? " active" : "");
        btn.textContent = `${group.label}（${group.files.length}）`;
        btn.onclick = () => {
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

      const content = document.createElement("pre");
      content.className = "text-content";
      content.textContent = activeFile?.content || "";
      panel.appendChild(content);
    }

    async function loadTextAssets(path){
      state.textAssets = null;
      state.activeTextCategory = "";
      state.activeTextFilePath = "";
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

    function setGallerySource(allImages, publishImages){
      state.galleryAllImages = (allImages || []).filter(Boolean);
      state.galleryPublishImages = (publishImages || []).filter(Boolean);
      applyGalleryFilter(false);
    }

    function applyGalleryFilter(resetIndex=true){
      const images = state.galleryFilter === "publish" ? state.galleryPublishImages : state.galleryAllImages;
      $("filterPublishBtn").classList.toggle("active", state.galleryFilter === "publish");
      $("filterAllBtn").classList.toggle("active", state.galleryFilter === "all");
      renderGallery(images, resetIndex);
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
        btn.onclick = () => showGalleryImage(idx);
        btn.ondblclick = () => openImageInNewTab(imgPath);
        box.appendChild(btn);
      });
      showGalleryImage(0);
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

    async function 删除历史(path){
      const ok = window.confirm("确定删除该历史记录及其文件夹吗？");
      if(!ok){ return; }
      const res = await fetch("/api/history_delete", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path})
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "删除失败"); }
      setStatus("历史记录已删除。", "ok");
      if(state.selectedHistoryPath === path){
        state.selectedHistoryPath = "";
        state.selectedHistoryItem = null;
      }
      await loadHistory(true);
    }

    async function 批量删除历史(paths){
      const list = (paths || []).filter(Boolean);
      if(!list.length){
        setStatus("请先勾选要删除的菜品。", "warn");
        return;
      }
      const ok = window.confirm(`确定删除选中的 ${list.length} 个菜品及其文件夹吗？`);
      if(!ok){ return; }
      const res = await fetch("/api/history_batch_delete", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({paths: list})
      });
      const data = await res.json();
      if(!res.ok){ throw new Error(data.error || "批量删除失败"); }
      setStatus(`已删除 ${data.deleted_count || list.length} 个菜品。`, "ok");
      if(list.includes(state.selectedHistoryPath)){
        state.selectedHistoryPath = "";
        state.selectedHistoryItem = null;
      }
      setBatchDeleteMode(false);
      await loadHistory(true);
    }

    async function selectHistoryItem(item, card){
      state.selectedHistoryPath = item.path;
      state.selectedHistoryItem = item;
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => el.classList.remove("active"));
      if(card){ card.classList.add("active"); }
      try{
        await loadDishDetail(item.path);
      }catch(err){
        applyDishDetail(item);
        $("resultMsg").textContent = "读取菜品详情失败，已显示缓存信息：" + String(err.message || err);
        $("resultMsg").className = "status warn";
      }
      if(state.mode === "target"){
        applyTargetDishFromItem(state.selectedHistoryItem || item);
      }
      await loadTextAssets(item.path);
      if($("resultMsg").className === "status"){
        $("resultMsg").textContent = "已同步菜品目录状态。";
      }
    }

    function createHistoryCard(item){
      const div = document.createElement("div");
      div.className = "history-item";
      div.dataset.path = item.path || "";
      const cover = item.preview_image
        ? `<img src="${fileUrl(item.preview_image)}" alt="${item.dish_name || item.name}" />`
        : `<div class="history-empty">暂无图片</div>`;
      const timeText = item.created_at || item.name.split("_").slice(0,2).join(" ");
      div.innerHTML = `
        <label class="history-check" title="勾选删除">
          <input type="checkbox" data-path="${item.path || ""}" />
        </label>
        <div class="history-cover">${cover}</div>
        <div class="history-meta">
          <div class="history-name">${item.dish_name || item.name}</div>
          <div class="history-time">${timeText}</div>
        </div>
        <div class="history-ops">
          <button data-op="open">打开</button>
          <button data-op="copy">复制</button>
          <button data-op="delete" class="del">删除</button>
        </div>
      `;
      const checkbox = div.querySelector(".history-check input");
      checkbox.onchange = (e) => {
        e.stopPropagation();
        const checked = Boolean(checkbox.checked);
        div.classList.toggle("batch-selected", checked);
        toggleBatchDeletePath(item.path, checked);
      };
      checkbox.onclick = (e) => e.stopPropagation();
      div.onclick = async () => {
        if(state.batchDeleteMode){
          checkbox.checked = !checkbox.checked;
          div.classList.toggle("batch-selected", checkbox.checked);
          toggleBatchDeletePath(item.path, checkbox.checked);
          return;
        }
        await selectHistoryItem(item, div);
      };
      const ops = div.querySelector(".history-ops");
      ops.onclick = async (e) => {
        e.stopPropagation();
        const op = e.target?.dataset?.op;
        if(op === "open"){ await openOutputPath(item.path); }
        if(op === "copy"){ await copyText(item.path, "历史目录路径已复制。"); }
        if(op === "delete"){
          try{ await 删除历史(item.path); }catch(err){ setStatus("删除失败：" + err.message, "warn"); }
        }
      };
      return div;
    }

    function buildHistorySnapshot(items){
      return (items || []).map((item) => String(item?.name || "")).join("|");
    }

    function markSelectedHistoryCard(){
      const selected = state.selectedHistoryPath;
      if(!selected){ return; }
      Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
        el.classList.toggle("active", el.dataset.path === selected);
      });
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
        const url = `/api/history?offset=${state.historyOffset}&limit=${state.historyLimit}`;
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
        if(state.batchDeleteMode){
          $("history").classList.add("batch-mode");
          Array.from($("history").querySelectorAll(".history-item")).forEach((el) => {
            const path = el.dataset.path || "";
            const checked = state.batchDeleteSelected.has(path);
            const checkbox = el.querySelector(".history-check input");
            if(checkbox){ checkbox.checked = checked; }
            el.classList.toggle("batch-selected", checked);
          });
          updateBatchDeleteUi();
        }
        $("loadMoreBtn").style.display = state.historyHasMore ? "inline-block" : "none";
        if(!$("history").children.length){
          $("history").innerHTML = '<div class="history-empty">暂无菜品</div>';
          $("loadMoreBtn").style.display = "none";
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

    function startAutoRefresh(){
      if(state.autoRefreshTimer){ return; }
      state.autoRefreshTimer = setInterval(() => {
        if(document.hidden){ return; }
        fetchRunStatus({silent: true});
        fetchPublishStatus({silent: true});
      }, 2000);
    }

    async function fetchRunStatus(options = {}){
      const silent = Boolean(options?.silent);
      try{
        const res = await fetch(`/api/run_status?from=${state.logNextIndex}`);
        const data = await res.json();
        if(data.logs?.length){
          appendLogs(data.logs);
          state.logNextIndex = data.next_index || state.logNextIndex;
        }
        if(data.running){
          const elapsed = Number(data.elapsed_seconds || 0);
          const percent = Math.max(1, Math.min(95, Math.floor((elapsed / 120) * 100)));
          setRunState(true, percent);
          const queueText = data.queue_length ? `，队列待执行 ${data.queue_length} 个` : "";
          if(!silent){
            setStatus(`运行中 ${percent}%（已等待 ${elapsed}s${queueText}）`);
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
          state.historyRevision = data.history_revision;
          await loadHistory(true);
          if(state.selectedHistoryPath){
            try{ await loadDishDetail(state.selectedHistoryPath); }catch{}
          }
        }else if(data.history){
          const nextSnapshot = buildHistorySnapshot(data.history);
          if(nextSnapshot !== state.historySnapshot){
            state.historySnapshot = nextSnapshot;
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
      $("envQuality").textContent = `海报画质：${画质文案(data.config.MODE2_POSTER_IMAGE_QUALITY || data.config.OPENAI_IMAGE_QUALITY)}`;
      $("envCount").textContent = `海报数：${data.config.MODE2_POSTER_IMAGE_COUNT || data.config.OPENAI_IMAGE_COUNT}`;
      $("envCoverCount").textContent = `封面数：${data.config.MODE2_COVER_IMAGE_COUNT || "-"}`;
      const cuisineText = data.config.AUTO_GENERATE_DISH_IDEA === "1"
        ? 菜系文案(data.config.AUTO_DISH_CUISINE_MODE || "1")
        : "";
      if(state.mode === "target"){
        $("envMode").textContent = "指定造菜";
      }else{
        $("envMode").textContent = data.config.AUTO_GENERATE_DISH_IDEA === "1"
          ? `自动造菜 / ${cuisineText}`
          : "手动点名";
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
      同步参数滑块();
      if(data.last_result && !state.selectedHistoryPath){ renderResult(data.last_result); }
      state.logNextIndex = 0;
      $("logPanel").textContent = "";
      state.historyRevision = data.history_revision || "";
      await loadHistory(true);
      await fetchRunStatus();
      await fetchPublishStatus({silent: true});
      if(showMsg){ setStatus("页面状态已刷新。", "ok"); }
    }

    async function submitTask(payload, okText){
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
      }
    }

    async function runNow(){
      if(state.mode === "file" && !$("dishName").value.trim()){
        setStatus("手动点名模式下请先填写菜名。", "danger");
        $("dishName").focus();
        return;
      }
      if(state.mode === "target" && !state.selectedHistoryPath){
        setStatus("指定造菜请先在左侧菜品池选中一个菜品。", "danger");
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
        cover_mode2_count: $("coverMode2Count").value.trim()
      });
    }

    async function runIdeaOnly(){
      if(state.mode === "file" && !$("dishName").value.trim()){
        setStatus("手动点名模式下请先填写菜名。", "danger");
        $("dishName").focus();
        return;
      }
      const count = Math.max(1, Math.min(30, Number($("ideaCount").value || "1")));
      $("ideaCount").value = String(count);
      await submitTask({
        action: "idea_only",
        mode: state.mode,
        cuisine_mode: $("cuisineMode").value.trim(),
        dish_name: $("dishName").value.trim(),
        notes: $("dishNotes").value.trim(),
        idea_count: String(count)
      }, `造菜信息任务已提交（共 ${count} 条）。`);
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

    function applyPreset(kind){
      if(kind === "budget"){
        $("temperature").value = "0.2";
        $("posterCount").value = $("detailCount").value = $("recipeCount").value = $("coverMode2Count").value = "1";
        $("posterQuality").value = $("detailQuality").value = $("recipeQuality").value = $("coverMode2Quality").value = "low";
        同步参数滑块();
        setStatus("已套用省成本模板。", "ok");
        return;
      }
      $("temperature").value = "0.6";
      $("posterCount").value = $("detailCount").value = "2";
      $("recipeCount").value = $("coverMode2Count").value = "2";
      $("posterQuality").value = $("detailQuality").value = $("recipeQuality").value = $("coverMode2Quality").value = "high";
      同步参数滑块();
      setStatus("已套用高质量模板。", "ok");
    }

    function bindEvents(){
      $("modeAutoBtn").onclick = () => setMode("auto");
      $("modeFileBtn").onclick = () => setMode("file");
      $("modeTargetBtn").onclick = () => setMode("target");
      $("batchDeleteBtn").onclick = () => setBatchDeleteMode(!state.batchDeleteMode);
      $("batchDeleteAllBtn").onclick = async () => {
        try{
          await 批量删除历史(Array.from(state.batchDeleteSelected));
        }catch(err){
          setStatus("批量删除失败：" + err.message, "warn");
        }
      };
      $("imageTabBtn").onclick = () => setRightTab("image");
      $("textTabBtn").onclick = () => setRightTab("text");
      $("runBtn").onclick = runNow;
      $("ideaOnlyBtn").onclick = runIdeaOnly;
      $("presetBudgetBtn").onclick = () => applyPreset("budget");
      $("presetQualityBtn").onclick = () => applyPreset("quality");
      $("prevImgBtn").onclick = () => showGalleryImage(state.galleryIndex - 1);
      $("nextImgBtn").onclick = () => showGalleryImage(state.galleryIndex + 1);
      $("filterPublishBtn").onclick = () => { state.galleryFilter = "publish"; applyGalleryFilter(true); };
      $("filterAllBtn").onclick = () => { state.galleryFilter = "all"; applyGalleryFilter(true); };
      $("publishBtn").onclick = startPublish;
      $("loginModalConfirm").onclick = confirmPublishLogin;
      $("resultImg").ondblclick = () => openImageInNewTab(state.currentImagePath);
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
      $("loadMoreBtn").onclick = () => loadHistory(false);
      $("history").onscroll = () => {
        const nearBottom = $("history").scrollTop + $("history").clientHeight >= $("history").scrollHeight - 60;
        if(nearBottom){ loadHistory(false); }
      };
      $("logToggleBtn").onclick = () => 切换日志抽屉(!$("logDrawer").classList.contains("open"));
      $("logCloseBtn").onclick = () => 切换日志抽屉(false);
      $("logClearBtn").onclick = () => { $("logPanel").textContent = ""; };
      $("logFilterBtn").onclick = () => {
        state.logOnlyErrors = !state.logOnlyErrors;
        $("logFilterBtn").textContent = `只看错误：${state.logOnlyErrors ? "开" : "关"}`;
      };
    }

    bindEvents();
    绑定参数滑块();
    syncIdeaCountUi();
    加载三栏宽度();
    初始化拖拽分栏();
    loadState();
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


def build_run_meta(result: dict[str, Any]) -> dict[str, Any]:
    ps_files = result.get("photoshop_processed_files") or []
    poster_selected = str(result.get("poster_selected_image", "")).strip()
    workflow_status = ""
    if ps_files:
        workflow_status = f"已合成发布图（{len(ps_files)} 张）"
    elif poster_selected:
        workflow_status = "已选图"
    return {
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
    }


def write_run_meta(result: dict[str, Any]) -> None:
    output_dir_text = str(result.get("output_dir", "")).strip()
    if not output_dir_text:
        return
    output_dir = Path(output_dir_text)
    if not output_dir.exists() or not output_dir.is_dir():
        return
    meta_file = output_dir / RUN_META_FILE_NAME
    meta_file.write_text(
        json.dumps(build_run_meta(result), ensure_ascii=False, indent=2) + "\n",
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
        poster_selected = str(meta.get("poster_selected_image", "")).strip()
    detail_selected = _find_image_by_name(folder, str(detail_result.get("winner_image_name", "")))
    recipe_selected = _find_image_by_name(folder, str(recipe_result.get("winner_image_name", "")))
    cover_selected = _find_image_by_name(folder, str(cover_result.get("winner_image_name", "")))
    if not cover_selected:
        cover_selected = str(meta.get("cover_selected_image", "")).strip()

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

    images = _collect_folder_images(folder, recursive=True)
    publish_images = photoshop_files or _collect_folder_images(publish_dir, recursive=False)

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


def format_folder_time(folder_name: str) -> str:
    parts = folder_name.split("_", 2)
    if len(parts) < 2:
        return ""
    date_text = parts[0]
    time_text = parts[1]
    if len(date_text) != 8 or len(time_text) != 6:
        return ""
    return f"{date_text[0:4]}-{date_text[4:6]}-{date_text[6:8]} {time_text[0:2]}:{time_text[2:4]}:{time_text[4:6]}"


def collect_history_dirs() -> list[Path]:
    dirs: list[Path] = []
    if OUTPUT_DIR.exists():
        dirs.extend(path for path in OUTPUT_DIR.iterdir() if path.is_dir())
    if DISH_POOL_DIR.exists():
        for batch_dir in DISH_POOL_DIR.iterdir():
            if not batch_dir.is_dir() or not batch_dir.name.endswith("_batch"):
                continue
            dirs.extend(path for path in batch_dir.iterdir() if path.is_dir())
    dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return dirs


def list_history(limit: int = 12, offset: int = 0) -> list[dict[str, Any]]:
    if offset < 0:
        offset = 0
    if limit < 1:
        limit = 12
    dirs = collect_history_dirs()
    rows: list[dict[str, Any]] = []
    sliced = dirs[offset : offset + limit]

    for folder in sliced:
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


def delete_history_folders(raw_paths: list[str]) -> list[str]:
    deleted: list[str] = []
    for raw_path in raw_paths:
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        deleted.append(str(delete_history_folder(path_text)))
    if not deleted:
        raise ValueError("没有可删除的菜品目录。")
    return deleted


def delete_history_folder(raw_path: str) -> Path:
    folder = resolve_output_path(raw_path)
    if folder == OUTPUT_DIR.resolve():
        raise ValueError("不能删除输出根目录。")
    shutil.rmtree(folder)
    return folder


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
    return {
        "AUTO_GENERATE_DISH_IDEA": os.getenv("AUTO_GENERATE_DISH_IDEA", "0").strip() or "0",
        "AUTO_DISH_CUISINE_MODE": os.getenv("AUTO_DISH_CUISINE_MODE", "1").strip() or "1",
        "MODEL_TEMPERATURE": os.getenv("MODEL_TEMPERATURE", "0.3").strip() or "0.3",
        "OPENAI_IMAGE_MODEL": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
        "OPENAI_IMAGE_QUALITY": os.getenv("OPENAI_IMAGE_QUALITY", "low").strip() or "low",
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


def apply_runtime_overrides(payload: dict[str, Any]) -> None:
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


def resolve_output_path(raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("输出目录不能为空。")
    target = Path(raw_path).resolve()
    if target.is_file():
        target = target.parent
    allowed_roots = [OUTPUT_DIR.resolve(), DISH_POOL_DIR.resolve()]
    if not any(target == root or root in target.parents for root in allowed_roots):
        raise ValueError("只允许打开 V2/output 或 V2/dish_pool 下的目录。")
    if not target.exists():
        raise FileNotFoundError(f"目录不存在：{target}")
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
        action_label = "造菜信息"
    elif mode == "target":
        action_label = "指定造菜"
    else:
        action_label = "四组图"
    mode_label = {"auto": "自动造菜", "file": "手动点名", "target": "指定造菜"}.get(mode, mode or "按配置")
    append_run_log(
        f"[{time.strftime('%H:%M:%S')}] 开始任务 #{task.get('task_id', '-')}"
        f"（类型：{action_label}，造菜：{mode_label}，菜名：{dish_name or '自动生成'}）"
    )
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            apply_runtime_overrides(task)
            if action == "idea_only":
                idea_count = int(str(task.get("idea_count", "1")).strip() or "1")
                result = run_idea_batch(
                    count=idea_count,
                    mode=mode if mode in {"auto", "file"} else None,
                    dish_name=dish_name,
                    notes=str(task.get("notes", "")).strip(),
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
        with RUN_LOCK:
            LAST_RESULT = result
            LAST_ERROR = ""
            try:
                write_run_meta(result)
            except Exception as meta_exc:  # noqa: BLE001
                append_run_log(f"[{time.strftime('%H:%M:%S')}] 写入运行元数据失败：{meta_exc}")
        append_run_log(f"[{time.strftime('%H:%M:%S')}] 任务 #{task.get('task_id', '-')} 完成。")
    except Exception as exc:  # noqa: BLE001
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
    def _send_json(self, payload: dict[str, Any], code: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str, code: int = 200) -> None:
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "文件不存在")
            return
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/favicon.ico", "/favicon.png"}:
            self._send_file(ROOT_DIR / "favicon.png")
            return
        if parsed.path == "/":
            self._send_text(HTML_PAGE, 200)
            return
        if parsed.path == "/api/state":
            self._send_json(
                {
                    "config": current_config_snapshot(),
                    "idea": read_idea_file(),
                    "history": list_history(limit=12, offset=0),
                    "history_revision": history_revision(),
                    "last_result": LAST_RESULT or {},
                    "publish_platforms": {key: item["label"] for key, item in PUBLISH_PLATFORMS.items()},
                }
            )
            return
        if parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            raw_limit = (query.get("limit", ["12"])[0] or "12").strip()
            raw_offset = (query.get("offset", ["0"])[0] or "0").strip()
            try:
                limit = max(1, min(50, int(raw_limit)))
            except ValueError:
                limit = 12
            try:
                offset = max(0, int(raw_offset))
            except ValueError:
                offset = 0
            self._send_json({"items": list_history(limit=limit, offset=offset), "offset": offset, "limit": limit})
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
                payload = {
                    "running": RUNNING,
                    "elapsed_seconds": elapsed,
                    "logs": logs,
                    "next_index": total_logs,
                    "result": LAST_RESULT or {},
                    "error": LAST_ERROR,
                    "queue_length": len(TASK_QUEUE),
                    "current_task": CURRENT_TASK or {},
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
                self.send_error(HTTPStatus.BAD_REQUEST, "path 不能为空")
                return
            self._send_file(Path(raw_path))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        global RUNNING, LAST_RESULT, LAST_ERROR, LAST_STARTED_AT, LAST_FINISHED_AT, TASK_SEQ
        parsed = urlparse(self.path)
        if parsed.path not in {
            "/api/run_start",
            "/api/open_output",
            "/api/history_delete",
            "/api/publish_start",
            "/api/publish_login_confirm",
            "/api/history_batch_delete",
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
                deleted = delete_history_folder(str(payload.get("path", "")).strip())
                self._send_json({"ok": True, "deleted": str(deleted)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return
        if parsed.path == "/api/history_batch_delete":
            try:
                raw_paths = payload.get("paths") or []
                if not isinstance(raw_paths, list):
                    raise ValueError("paths 必须是数组。")
                deleted = delete_history_folders([str(item) for item in raw_paths])
                self._send_json({"ok": True, "deleted": deleted, "deleted_count": len(deleted)})
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
        if parsed.path == "/api/publish_start":
            try:
                output_dir_text = str(payload.get("output_dir", "")).strip()
                platform_keys = [str(item).strip() for item in (payload.get("platforms") or []) if str(item).strip()]
                start_publish_task(output_dir_text, platform_keys)
                self._send_json({"ok": True, "platforms": platform_keys})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, code=400)
            return

        action = str(payload.get("action", "run")).strip().lower() or "run"
        if action not in {"run", "idea_only"}:
            action = "run"
        mode = str(payload.get("mode", "")).strip().lower()
        dish_name = str(payload.get("dish_name", "")).strip()
        idea_count_raw = str(payload.get("idea_count", "1")).strip() or "1"

        if mode == "file" and not dish_name:
            self._send_json({"error": "手动模式下，菜名不能为空。"}, code=400)
            return
        if action == "run" and mode == "target":
            target_output_dir = str(payload.get("target_output_dir", "")).strip()
            if not target_output_dir:
                self._send_json({"error": "指定造菜请先在菜品池选中一个菜品。"}, code=400)
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
                self._send_json({"error": "手动点名每次只能生成 1 条造菜信息。"}, code=400)
                return

        with RUN_LOCK:
            waiting_ahead = len(TASK_QUEUE) + (1 if RUNNING else 0)
            TASK_SEQ += 1
            task_item = {
                "task_id": TASK_SEQ,
                "action": action,
                "mode": mode if mode in {"auto", "file", "target"} else "",
                "target_output_dir": str(payload.get("target_output_dir", "")).strip() if mode == "target" else "",
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
    ensure_runtime_config_loaded()
    server = ThreadingHTTPServer((HOST, PORT), V2PanelHandler)
    print(f"V2 前端面板已启动：http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务。")
    server.serve_forever()


if __name__ == "__main__":
    main()

