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

from main import run_v2_first_feature
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
    save_generated_images,
    save_text_output,
)


HOST = "127.0.0.1"
PORT = 8765
PANEL_VERSION = "v0.36"

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


def append_run_log(text: str) -> None:
    line = text.rstrip("\r\n")
    if not line:
        return
    with RUN_LOCK:
        RUN_LOG_LINES.append(line)
        if len(RUN_LOG_LINES) > MAX_LOG_LINES:
            del RUN_LOG_LINES[: len(RUN_LOG_LINES) - MAX_LOG_LINES]


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
    .top-actions button{
      width:auto;padding:7px 10px;border:1px solid #334155;border-radius:8px;background:#0b1220;color:#dbe7ff;cursor:pointer;font-size:12px;
    }
    .top-actions .danger-btn{border-color:#7f1d1d;color:#fecaca;background:#2b1313}
    .three-col{
      display:grid;
      grid-template-columns:var(--col-left) var(--splitter) var(--col-mid) var(--splitter) minmax(520px, 1fr);
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
    .panel-right{margin-left:6px}
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
    .mode{display:grid;grid-template-columns:1fr 1fr;gap:8px}
    .mode button{
      width:100%;padding:10px;border:1px solid #3a475f;border-radius:9px;background:#111b2f;color:#dbe7ff;cursor:pointer;font-weight:700;
    }
    .mode .active{background:linear-gradient(180deg,#1f2937,#111827);border-color:#6b7280}
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
    .history-grid{display:flex;flex-direction:column;gap:8px}
    .history-empty{color:var(--sub);font-size:12px}
    .history-item{
      position:relative;border:1px solid #2f3b55;border-radius:10px;background:#0f172a;overflow:hidden;cursor:pointer;
      transition:transform .15s ease,border-color .15s ease;
      display:grid;grid-template-columns:64px 1fr;grid-template-areas:"cover meta" "ops ops";gap:8px;align-items:center;padding:8px;
    }
    .history-item:hover{transform:translateY(-1px);border-color:#60a5fa}
    .history-cover{grid-area:cover;width:64px;height:64px;background:#111827;border-radius:7px;display:flex;align-items:center;justify-content:center;overflow:hidden}
    .history-cover img{width:100%;height:100%;object-fit:cover}
    .history-meta{grid-area:meta;padding:0;min-width:0}
    .history-name{font-size:13px;font-weight:700;line-height:1.3;word-break:break-all}
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
    @media(max-width:1400px){.three-col{grid-template-columns:300px var(--splitter) 400px var(--splitter) minmax(460px,1fr)}}
    @media(max-width:1200px){
      .three-col{grid-template-columns:1fr}
      .splitter{display:none}
      .panel{height:auto}
      .panel-left,.panel-mid,.panel-right{margin:0 0 10px}
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
        <div class="sub">三栏布局：左侧历史目录，中间造菜控制，右侧看图栏；宽度可拖拽并自动记忆。</div>
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
        <button id="refreshTopBtn" type="button">刷新历史</button>
      </div>
    </div>

    <div id="threeColLayout" class="three-col">
      <section class="panel panel-left">
        <h3 class="panel-title">历史输出</h3>
        <div id="history" class="history-grid"></div>
        <div id="historyLoadMore" class="load-more">
          <button id="loadMoreBtn" type="button">加载更多</button>
        </div>
      </section>

      <div id="splitterLeft" class="splitter" title="拖拽调整宽度"></div>

      <aside class="panel panel-mid">
        <div class="section-card">
          <h3 class="sec-title">模式选择</h3>
          <div class="mode">
            <button id="modeAutoBtn" class="active" type="button">自动造菜</button>
            <button id="modeFileBtn" type="button">手动点名</button>
          </div>
          <label style="margin-top:8px">手动菜名（仅手动点名生效）</label>
          <input id="dishName" placeholder="例如：蒜香煎嫩鸡胸肉" />
        </div>

        <div class="section-card">
          <h3 class="sec-title">参数调节</h3>
          <div class="param-grid">
            <div class="param-item">
              <label>创意强度</label>
              <input id="temperature" type="number" step="0.1" min="0" max="1.5" />
              <input id="temperatureSlider" class="slider" type="range" min="0" max="15" step="1" />
            </div>
            <div class="param-item">
              <label>出图数量</label>
              <input id="imageCount" type="number" step="1" min="1" max="4" />
              <input id="imageCountSlider" class="slider" type="range" min="1" max="4" step="1" />
            </div>
            <div class="param-item">
              <label>画质档位</label>
              <select id="imageQuality">
                <option value="low">标准清晰（省成本）</option>
                <option value="medium">中等清晰</option>
                <option value="high">高清细节（高质量）</option>
                <option value="auto">自动选择</option>
              </select>
              <input id="imageQualitySlider" class="slider" type="range" min="0" max="3" step="1" />
            </div>
          </div>
          <div class="preset-row">
            <button id="presetBudgetBtn" type="button">省成本模板</button>
            <button id="presetQualityBtn" type="button">高质量模板</button>
          </div>
        </div>

        <div class="section-card">
          <h3 class="sec-title">封面参数</h3>
          <label>封面生成数量</label>
          <input id="coverCount" type="number" step="1" min="1" max="4" />
          <input id="coverCountSlider" class="slider" type="range" min="1" max="4" step="1" />
          <div class="sec-desc">封面会基于主图首选（publish）生成，再进行封面评分并选入 publish。</div>
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
          <h3 class="panel-title">看图栏</h3>
          <div class="gallery-strip">
            <div class="gallery-tools">
              <div class="gallery-actions">
                <button id="prevImgBtn" type="button">上一张</button>
                <button id="nextImgBtn" type="button">下一张</button>
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
              <div class="empty-image-icon">🖼</div>
              <div class="empty-image-title">暂未生成图片</div>
              <div class="empty-image-sub">运行任务后在这里查看结果，双击可新标签打开原图。</div>
            </div>
          </div>
          <div class="result-overlay">
            <div class="overlay-line"><div class="overlay-k">菜名</div><div id="rDish" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">参考菜</div><div id="rRef" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">菜系</div><div id="rRegion" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">目录</div><div id="rOut" class="overlay-v mono">-</div></div>
            <div class="overlay-line"><div class="overlay-k">主图首选</div><div id="rBestMain" class="overlay-v mono">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">封面首选</div><div id="rBestCover" class="overlay-v mono">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">选图方式</div><div id="rPickMode" class="overlay-v">暂无</div></div>
            <div class="overlay-line"><div class="overlay-k">PS合成</div><div id="rPsStatus" class="overlay-v">暂无</div></div>
          </div>
          <div class="result-actions">
            <button id="openOutputBtn" type="button">打开输出目录</button>
            <button id="openPublishBtn" type="button">打开 publish</button>
            <button id="copyOutputBtn" type="button">复制路径</button>
            <button id="regenBtn" type="button">重新生成</button>
          </div>
          <div id="resultMsg" class="status"></div>
        </div>
      </section>
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
      running: false,
      colLeft: 320,
      colMid: 430
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

    function setMode(mode){
      state.mode = mode;
      $("modeAutoBtn").classList.toggle("active", mode === "auto");
      $("modeFileBtn").classList.toggle("active", mode === "file");
      const manual = mode === "file";
      $("dishName").disabled = !manual;
      $("dishName").classList.toggle("input-disabled", !manual);
      $("dishNotes").disabled = !manual;
      $("dishNotes").classList.toggle("input-disabled", !manual);
      $("notesCard").style.display = manual ? "block" : "none";
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
      const layout = $("threeColLayout");
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

    function 初始化拖拽分栏(){
      const layout = $("threeColLayout");
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
      const c = Math.max(1, Math.min(4, Number($("imageCount").value || "1")));
      $("imageCountSlider").value = String(c);
      const coverCount = Math.max(1, Math.min(4, Number($("coverCount").value || "1")));
      $("coverCountSlider").value = String(coverCount);
      $("imageQualitySlider").value = String(QUALITY_INDEX[$("imageQuality").value] ?? 0);
    }

    function 绑定参数滑块(){
      $("temperatureSlider").oninput = () => { $("temperature").value = (Number($("temperatureSlider").value) / 10).toFixed(1); };
      $("imageCountSlider").oninput = () => { $("imageCount").value = String(Number($("imageCountSlider").value)); };
      $("coverCountSlider").oninput = () => { $("coverCount").value = String(Number($("coverCountSlider").value)); };
      $("imageQualitySlider").oninput = () => { $("imageQuality").value = INDEX_QUALITY[Number($("imageQualitySlider").value)] || "low"; };
      $("temperature").oninput = 同步参数滑块;
      $("imageCount").oninput = 同步参数滑块;
      $("coverCount").oninput = 同步参数滑块;
      $("imageQuality").onchange = 同步参数滑块;
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

    function 选图方式文案(mainMode, coverMode){
      const toText = (mode) => {
        if(mode === "direct"){ return "数量=1直入"; }
        if(mode === "scored"){ return "豆包评分"; }
        return "未执行";
      };
      return `主图：${toText(mainMode)} / 封面：${toText(coverMode)}`;
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

    function renderGallery(images){
      state.galleryImages = (images || []).filter(Boolean);
      state.galleryIndex = 0;
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
      更新结果信息(
        result?.dish_name,
        result?.reference_dish,
        result?.region_label,
        result?.output_dir,
        result?.primary_selected_image,
        result?.cover_selected_image,
        result?.primary_selection_mode,
        result?.cover_selection_mode,
        result?.photoshop_processed_files || [],
        result?.photoshop_error || ""
      );
      const coverImages = result?.cover_saved_images || [];
      renderGallery((result?.saved_images || []).concat(coverImages));
      const isRegen = result?.run_kind === "regenerate_image";
      const hasError = Boolean(result?.image_error || result?.cover_image_error);
      const errText = [result?.image_error, result?.cover_image_error].filter(Boolean).join("\\n");
      const msg = hasError
        ? ((isRegen ? "重新生图异常：\\n" : "生图异常：\\n") + errText)
        : (isRegen ? "已按原提示词重新生图完成。" : "主图/封面流程已完成。");
      $("resultMsg").textContent = msg;
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
      await loadHistory(true);
    }

    function createHistoryCard(item){
      const div = document.createElement("div");
      div.className = "history-item";
      const cover = item.preview_image
        ? `<img src="${fileUrl(item.preview_image)}" alt="${item.dish_name || item.name}" />`
        : `<div class="history-empty">暂无图片</div>`;
      const timeText = item.created_at || item.name.split("_").slice(0,2).join(" ");
      div.innerHTML = `
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
      div.onclick = () => {
        const 历史参考菜 = item.reference_dish || "未记录参考菜";
        const 历史菜系 = item.region_label || "未记录菜系";
        更新结果信息(item.dish_name, 历史参考菜, 历史菜系, item.path, "", "", "", "", [], "");
        renderGallery(item.images || (item.preview_image ? [item.preview_image] : []));
        $("resultMsg").textContent = "已切换为历史预览。";
        $("resultMsg").className = "status";
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
        items.forEach((item) => $("history").appendChild(createHistoryCard(item)));
        state.historyOffset += items.length;
        $("loadMoreBtn").style.display = state.historyHasMore ? "inline-block" : "none";
        if(!$("history").children.length){
          $("history").innerHTML = '<div class="history-empty">暂无历史输出</div>';
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
        if(state.running){ return; }
        fetchRunStatus({silent: true});
      }, 5000);
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
          renderResult(data.result);
        }
        if(data.history){
          const nextSnapshot = buildHistorySnapshot(data.history);
          if(nextSnapshot !== state.historySnapshot){
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
      $("envQuality").textContent = `画质：${画质文案(data.config.OPENAI_IMAGE_QUALITY)}`;
      $("envCount").textContent = `出图数：${data.config.OPENAI_IMAGE_COUNT}`;
      $("envCoverCount").textContent = `封面数：${data.config.COVER_IMAGE_COUNT || "-"}`;
      $("envMode").textContent = `模式：${data.config.AUTO_GENERATE_DISH_IDEA === "1" ? "自动造菜" : "手动点名"}`;
      $("temperature").value = data.config.MODEL_TEMPERATURE;
      $("imageCount").value = data.config.OPENAI_IMAGE_COUNT;
      $("coverCount").value = data.config.COVER_IMAGE_COUNT || "1";
      $("imageQuality").value = data.config.OPENAI_IMAGE_QUALITY;
      $("dishName").value = data.idea.dish_name || "";
      $("dishNotes").value = data.idea.notes || "";
      setMode(data.config.AUTO_GENERATE_DISH_IDEA === "1" ? "auto" : "file");
      同步参数滑块();
      if(data.last_result){ renderResult(data.last_result); }
      state.logNextIndex = 0;
      $("logPanel").textContent = "";
      await loadHistory(true);
      await fetchRunStatus();
      if(showMsg){ setStatus("页面状态已刷新。", "ok"); }
    }

    async function runNow(options = {}){
      const regenerateOnly = Boolean(options?.regenerateOnly);
      if(!regenerateOnly && state.mode === "file" && !$("dishName").value.trim()){
        setStatus("手动点名模式下请先填写菜名。", "danger");
        $("dishName").focus();
        return;
      }
      if(regenerateOnly && !state.currentOutputPath){
        setStatus("请先在右侧选中一条已有结果，再执行重新生成。", "warn");
        return;
      }
      state.logNextIndex = 0;
      if(!state.pollTimer){ $("logPanel").textContent = ""; }
      if(regenerateOnly){
        setStatus("重新生成任务已提交：沿用原提示词，仅按当前画质/数量重新出图。");
      }else{
        setStatus("任务已提交，正在加入队列...");
      }
      try{
        const payload = regenerateOnly
          ? {
              action: "regenerate_image",
              source_output_dir: state.currentOutputPath,
              image_quality: $("imageQuality").value.trim(),
              image_count: $("imageCount").value.trim(),
              cover_count: $("coverCount").value.trim()
            }
          : {
              action: "run",
              mode: state.mode,
              dish_name: $("dishName").value.trim(),
              notes: $("dishNotes").value.trim(),
              model_temperature: $("temperature").value.trim(),
              image_quality: $("imageQuality").value.trim(),
              image_count: $("imageCount").value.trim(),
              cover_count: $("coverCount").value.trim()
            };
        const res = await fetch("/api/run_start", {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "运行失败"); }
        if(data.started_now){
          appendLogs([`[${new Date().toLocaleTimeString()}] 任务 #${data.task_id} 已开始执行。`]);
          setStatus(regenerateOnly ? "重新生成已开始执行。" : "任务已开始执行。");
        }else{
          appendLogs([`[${new Date().toLocaleTimeString()}] 任务 #${data.task_id} 已加入队列，前方 ${data.waiting_ahead} 个任务。`]);
          setStatus(
            regenerateOnly
              ? `重新生成已加入队列，前方还有 ${data.waiting_ahead} 个任务。`
              : `已加入队列，前方还有 ${data.waiting_ahead} 个任务。`,
            "ok"
          );
        }
        startPolling();
        await fetchRunStatus();
      }catch(err){
        setStatus("失败：" + err.message, "warn");
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

    function applyPreset(kind){
      if(kind === "budget"){
        $("temperature").value = "0.2";
        $("imageCount").value = "1";
        $("coverCount").value = "1";
        $("imageQuality").value = "low";
        同步参数滑块();
        setStatus("已套用省成本模板。", "ok");
        return;
      }
      $("temperature").value = "0.6";
      $("imageCount").value = "2";
      $("coverCount").value = "2";
      $("imageQuality").value = "high";
      同步参数滑块();
      setStatus("已套用高质量模板。", "ok");
    }

    function bindEvents(){
      $("modeAutoBtn").onclick = () => setMode("auto");
      $("modeFileBtn").onclick = () => setMode("file");
      $("runBtn").onclick = runNow;
      $("regenBtn").onclick = () => runNow({regenerateOnly: true});
      $("presetBudgetBtn").onclick = () => applyPreset("budget");
      $("presetQualityBtn").onclick = () => applyPreset("quality");
      $("prevImgBtn").onclick = () => showGalleryImage(state.galleryIndex - 1);
      $("nextImgBtn").onclick = () => showGalleryImage(state.galleryIndex + 1);
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
    parts = folder_name.split("_", 2)
    if len(parts) >= 3 and parts[2].strip():
        return parts[2].strip()
    if len(parts) >= 2 and parts[-1].strip():
        return parts[-1].strip()
    return folder_name.strip()


def build_run_meta(result: dict[str, Any]) -> dict[str, str]:
    return {
        "dish_name": str(result.get("dish_name", "")).strip(),
        "region_label": str(result.get("region_label", "")).strip(),
        "reference_dish": str(result.get("reference_dish", "")).strip(),
        "output_dir": str(result.get("output_dir", "")).strip(),
        "run_kind": str(result.get("run_kind", "run")).strip() or "run",
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


def read_run_meta(folder: Path) -> dict[str, str]:
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


def list_history(limit: int = 12, offset: int = 0) -> list[dict[str, Any]]:
    if not OUTPUT_DIR.exists():
        return []
    if offset < 0:
        offset = 0
    if limit < 1:
        limit = 12
    dirs = [p for p in OUTPUT_DIR.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rows: list[dict[str, Any]] = []
    sliced = dirs[offset : offset + limit]

    def collect_images(folder_path: Path) -> list[str]:
        image_paths: list[Path] = []
        for pattern in ("*.png", "*.jpg", "*.jpeg"):
            image_paths.extend(folder_path.glob(pattern))
        return [str(path) for path in sorted(image_paths, key=lambda item: item.name.lower())]

    for folder in sliced:
        images = collect_images(folder)
        publish_dir = folder / "publish"
        if publish_dir.exists() and publish_dir.is_dir():
            images.extend(collect_images(publish_dir))
        meta = read_run_meta(folder)
        dish_name = meta.get("dish_name", "") or infer_dish_name_from_folder(folder.name)
        region_label = meta.get("region_label", "")
        reference_dish = meta.get("reference_dish", "")
        # 兜底：当前会话里最新结果尚未落盘时，优先用内存结果补上关键字段。
        if (not region_label or not reference_dish) and isinstance(LAST_RESULT, dict):
            current_output_dir = str(LAST_RESULT.get("output_dir", "")).strip()
            if current_output_dir and Path(current_output_dir).resolve() == folder.resolve():
                region_label = region_label or str(LAST_RESULT.get("region_label", "")).strip()
                reference_dish = reference_dish or str(LAST_RESULT.get("reference_dish", "")).strip()
        preview = ""
        if images:
            preview = images[0]
        rows.append(
            {
                "name": folder.name,
                "dish_name": dish_name,
                "region_label": region_label,
                "reference_dish": reference_dish,
                "created_at": format_folder_time(folder.name),
                "path": str(folder),
                "preview_image": preview,
                "images": images,
            }
        )
    return rows


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
        "MODEL_TEMPERATURE": os.getenv("MODEL_TEMPERATURE", "0.3").strip() or "0.3",
        "OPENAI_IMAGE_MODEL": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
        "OPENAI_IMAGE_QUALITY": os.getenv("OPENAI_IMAGE_QUALITY", "low").strip() or "low",
        "OPENAI_IMAGE_COUNT": os.getenv("OPENAI_IMAGE_COUNT", "1").strip() or "1",
        "COVER_IMAGE_COUNT": str(get_cover_image_count()),
    }


def apply_runtime_overrides(payload: dict[str, Any]) -> None:
    mode = str(payload.get("mode", "")).strip().lower()
    if mode in {"auto", "file"}:
        os.environ["AUTO_GENERATE_DISH_IDEA"] = "1" if mode == "auto" else "0"
    if str(payload.get("model_temperature", "")).strip():
        os.environ["MODEL_TEMPERATURE"] = str(payload["model_temperature"]).strip()
    if str(payload.get("image_quality", "")).strip():
        os.environ["OPENAI_IMAGE_QUALITY"] = str(payload["image_quality"]).strip()
    if str(payload.get("image_count", "")).strip():
        os.environ["OPENAI_IMAGE_COUNT"] = str(payload["image_count"]).strip()
    if str(payload.get("cover_count", "")).strip():
        os.environ["COVER_IMAGE_COUNT"] = str(payload["cover_count"]).strip()


def resolve_output_path(raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("输出目录不能为空。")
    target = Path(raw_path).resolve()
    if target.is_file():
        target = target.parent
    output_root = OUTPUT_DIR.resolve()
    if target != output_root and output_root not in target.parents:
        raise ValueError("只允许打开 V2/output 下的目录。")
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
    source_output_dir = str(task.get("source_output_dir", "")).strip()
    append_run_log(
        f"[{time.strftime('%H:%M:%S')}] 开始任务 #{task.get('task_id', '-')}"
        f"（类型：{'重新生图' if action == 'regenerate_image' else '正常生成'}，"
        f"模式：{mode or '按配置'}，菜名：{dish_name or '自动生成'}）"
    )
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            apply_runtime_overrides(task)
            if action == "regenerate_image":
                source_dir = resolve_output_path(source_output_dir)
                result = regenerate_images_from_output_dir(source_dir)
            else:
                if mode == "file":
                    write_idea_file(str(task.get("dish_name", "")).strip(), str(task.get("notes", "")).strip())
                result = run_v2_first_feature(mode=mode if mode in {"auto", "file"} else None)
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
                    "last_result": LAST_RESULT or {},
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
                }
            self._send_json(payload)
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
        if parsed.path not in {"/api/run_start", "/api/open_output", "/api/history_delete"}:
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

        action = str(payload.get("action", "run")).strip().lower() or "run"
        mode = str(payload.get("mode", "")).strip().lower()
        dish_name = str(payload.get("dish_name", "")).strip()
        source_output_dir = str(payload.get("source_output_dir", "")).strip()

        if action == "regenerate_image":
            if not source_output_dir:
                self._send_json({"error": "重新生成需要指定来源输出目录。"}, code=400)
                return
            try:
                source_dir = resolve_output_path(source_output_dir)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"来源目录无效：{exc}"}, code=400)
                return
            dish_name = infer_dish_name_from_folder(source_dir.name)
        else:
            if mode == "file" and not dish_name:
                self._send_json({"error": "手动模式下，菜名不能为空。"}, code=400)
                return

        with RUN_LOCK:
            waiting_ahead = len(TASK_QUEUE) + (1 if RUNNING else 0)
            TASK_SEQ += 1
            task_item = {
                "task_id": TASK_SEQ,
                "action": action if action in {"run", "regenerate_image"} else "run",
                "mode": mode if mode in {"auto", "file"} else "",
                "dish_name": dish_name,
                "notes": str(payload.get("notes", "")).strip(),
                "model_temperature": str(payload.get("model_temperature", "")).strip(),
                "image_quality": str(payload.get("image_quality", "")).strip(),
                "image_count": str(payload.get("image_count", "")).strip(),
                "cover_count": str(payload.get("cover_count", "")).strip(),
                "source_output_dir": str(source_dir) if action == "regenerate_image" else "",
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

