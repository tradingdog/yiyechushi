from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import run_v2_first_feature
from v2_core import IDEA_FILE, OUTPUT_DIR, ensure_runtime_config_loaded


HOST = "127.0.0.1"
PORT = 8765

RUN_LOCK = threading.Lock()
RUNNING = False
LAST_RESULT: dict[str, Any] | None = None


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>V2 自动造菜控制台</title>
  <style>
    :root{
      --bg:#f3f6fb; --card:#ffffff; --line:#e6ebf3; --text:#1f2937; --sub:#6b7280;
      --pri:#2563eb; --pri-soft:#eff6ff; --ok:#059669; --warn:#d97706; --danger:#dc2626;
      --shadow:0 6px 18px rgba(18,38,63,.06);
    }
    *{box-sizing:border-box}
    body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--text)}
    .wrap{max-width:1320px;margin:0 auto;padding:20px}
    .top{
      display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;
      background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow);
      position:sticky;top:10px;z-index:5;
    }
    .title{font-size:22px;font-weight:700}
    .sub{font-size:13px;color:var(--sub)}
    .chips{display:flex;gap:8px;flex-wrap:wrap}
    .chip{font-size:12px;background:#f8fafc;border:1px solid var(--line);padding:6px 9px;border-radius:999px;color:#334155}
    .grid{display:grid;grid-template-columns:420px 1fr;gap:16px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:var(--shadow)}
    .sec-title{font-size:16px;font-weight:700;margin:0 0 10px}
    .sec-desc{font-size:12px;color:var(--sub);margin:-2px 0 10px}
    label{display:block;font-size:13px;color:var(--sub);margin-bottom:6px}
    input,textarea,select,button{
      width:100%;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px;
      font-family:inherit;background:#fff;outline:none;
    }
    input:focus,textarea:focus,select:focus{border-color:#93c5fd;box-shadow:0 0 0 3px #dbeafe}
    textarea{min-height:92px;resize:vertical;line-height:1.5}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    .mode{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}
    .mode button{padding:10px 8px}
    .mode .active{background:var(--pri);color:#fff;border-color:var(--pri)}
    .input-disabled{opacity:.65;background:#f8fafc}
    .btn-primary{
      background:var(--pri);color:#fff;border-color:var(--pri);font-weight:700;cursor:pointer;
      display:flex;align-items:center;justify-content:center;gap:8px;
    }
    .btn-primary:disabled{opacity:.6;cursor:not-allowed}
    .btn-line{
      border:1px solid var(--line);background:#fff;color:#334155;cursor:pointer;font-size:13px;
    }
    .status{
      margin-top:10px;font-size:13px;color:var(--sub);white-space:pre-wrap;line-height:1.45;
      border:1px dashed var(--line);border-radius:10px;padding:9px;background:#fcfdff;
    }
    .ok{color:var(--ok)} .warn{color:var(--warn)} .danger{color:var(--danger)}
    .result-meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}
    .kv{border:1px solid var(--line);border-radius:10px;padding:10px;background:#fcfdff}
    .k{font-size:12px;color:var(--sub)}
    .v{font-size:14px;font-weight:600;word-break:break-all}
    .img-wrap{border:1px solid var(--line);border-radius:12px;padding:8px;background:#fcfdff}
    .img-wrap img{width:100%;border-radius:8px;display:block;min-height:260px;object-fit:cover;background:#f3f6fb}
    .mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
    .history-actions{display:flex;gap:8px;margin-bottom:8px}
    .history-list{max-height:300px;overflow:auto;padding-right:2px}
    .history-item{border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:8px;cursor:pointer;background:#fff}
    .history-item:hover{border-color:#b9c7e6;background:#f8fbff}
    .history-item.active{border-color:#60a5fa;background:var(--pri-soft)}
    .history-name{font-weight:700;margin-bottom:4px}
    .history-path{font-size:12px;color:var(--sub);word-break:break-all}
    .history-time{font-size:12px;color:#475569}
    .split{height:1px;background:var(--line);margin:12px 0}
    .hint{font-size:12px;color:#64748b;line-height:1.5}
    @media(max-width:1080px){.grid{grid-template-columns:1fr}}
    @media(max-width:640px){.row,.result-meta{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <div class="title">V2 自动造菜控制台（UX 优化版）</div>
        <div class="sub">自动/手动菜名 -> 豆包提示词 -> gpt-image-2 生图，支持参数调节与历史预览</div>
      </div>
      <div class="chips">
        <span id="envModel" class="chip">模型加载中...</span>
        <span id="envQuality" class="chip">质量 -</span>
        <span id="envCount" class="chip">数量 -</span>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3 class="sec-title">运行控制</h3>
        <div class="sec-desc">先选模式，再调整参数，最后点击「开始运行」。</div>
        <div class="mode">
          <button id="modeAutoBtn" class="active" type="button">自动造菜</button>
          <button id="modeFileBtn" type="button">手动菜名</button>
        </div>

        <label>手动菜名（仅“手动菜名”模式生效）</label>
        <input id="dishName" placeholder="例如：蒜香煎嫩鸡胸肉" />

        <label style="margin-top:10px">补充说明（可选）</label>
        <textarea id="dishNotes" placeholder="可写关键做法、口味倾向、你想强调的卖点"></textarea>

        <div class="row" style="margin-top:10px">
          <div>
            <label>温度 MODEL_TEMPERATURE</label>
            <input id="temperature" type="number" step="0.1" min="0" max="1.5" />
          </div>
          <div>
            <label>图片数量 OPENAI_IMAGE_COUNT</label>
            <input id="imageCount" type="number" step="1" min="1" max="4" />
          </div>
        </div>

        <label style="margin-top:10px">图片质量 OPENAI_IMAGE_QUALITY</label>
        <select id="imageQuality">
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="auto">auto</option>
        </select>

        <button id="runBtn" class="btn-primary" style="margin-top:12px">开始运行</button>
        <div id="status" class="status">就绪。你可以先点右侧历史记录预览最近结果。</div>
        <div class="hint" style="margin-top:8px">提示：运行期间按钮会锁定，避免重复触发。</div>
      </div>

      <div class="card">
        <h3 class="sec-title">本轮结果</h3>
        <div class="result-meta">
          <div class="kv"><div class="k">菜名</div><div id="rDish" class="v">-</div></div>
          <div class="kv"><div class="k">参考传统菜</div><div id="rRef" class="v">-</div></div>
          <div class="kv"><div class="k">菜系</div><div id="rRegion" class="v">-</div></div>
          <div class="kv"><div class="k">输出目录</div><div id="rOut" class="v mono">-</div></div>
        </div>
        <div class="img-wrap">
          <img id="resultImg" alt="暂无图片" />
        </div>
        <div id="resultMsg" class="status"></div>
        <div class="history-actions">
          <button id="copyOutputBtn" class="btn-line" type="button">复制输出目录</button>
          <button id="refreshBtn" class="btn-line" type="button">刷新历史</button>
        </div>

        <h3 class="sec-title" style="margin-top:14px">最近输出</h3>
        <div id="history" class="history-list"></div>
      </div>
    </div>
  </div>

  <script>
    const state = { mode: "auto" };
    const $ = (id) => document.getElementById(id);

    function setMode(mode){
      state.mode = mode;
      $("modeAutoBtn").classList.toggle("active", mode === "auto");
      $("modeFileBtn").classList.toggle("active", mode === "file");
      const manual = mode === "file";
      $("dishName").disabled = !manual;
      $("dishNotes").disabled = !manual;
      $("dishName").classList.toggle("input-disabled", !manual);
      $("dishNotes").classList.toggle("input-disabled", !manual);
    }

    function fileUrl(path){
      return "/api/file?path=" + encodeURIComponent(path);
    }

    function renderResult(result){
      $("rDish").textContent = result?.dish_name || "-";
      $("rRef").textContent = result?.reference_dish || "-";
      $("rRegion").textContent = result?.region_label || "-";
      $("rOut").textContent = result?.output_dir || "-";

      if (result?.saved_images?.length){
        $("resultImg").src = fileUrl(result.saved_images[0]);
      } else {
        $("resultImg").removeAttribute("src");
      }
      const msg = result?.image_error ? ("生图异常：\\n" + result.image_error) : "生图成功。";
      $("resultMsg").textContent = msg;
      $("resultMsg").className = "status " + (result?.image_error ? "warn" : "ok");
    }

    function renderHistory(items){
      const box = $("history");
      box.innerHTML = "";
      if(!items?.length){
        box.innerHTML = '<div class="sub">暂无历史输出</div>';
        return;
      }
      const currentOut = $("rOut").textContent || "";
      items.forEach((item) => {
        const div = document.createElement("div");
        div.className = "history-item" + (currentOut === item.path ? " active" : "");
        const timeText = item.name.split("_").slice(0,2).join(" ");
        div.innerHTML = `
          <div class="history-name">${item.name}</div>
          <div class="history-time">${timeText}</div>
          <div class="history-path">${item.path}</div>
        `;
        div.onclick = () => {
          if (item.preview_image) {
            $("resultImg").src = fileUrl(item.preview_image);
          }
          $("rOut").textContent = item.path;
          $("resultMsg").textContent = "已切换为历史预览。";
          $("resultMsg").className = "status";
        };
        box.appendChild(div);
      });
    }

    async function loadState(showMsg=false){
      const res = await fetch("/api/state");
      const data = await res.json();
      $("envModel").textContent = `模型 ${data.config.OPENAI_IMAGE_MODEL}`;
      $("envQuality").textContent = `质量 ${data.config.OPENAI_IMAGE_QUALITY}`;
      $("envCount").textContent = `数量 ${data.config.OPENAI_IMAGE_COUNT}`;
      $("temperature").value = data.config.MODEL_TEMPERATURE;
      $("imageCount").value = data.config.OPENAI_IMAGE_COUNT;
      $("imageQuality").value = data.config.OPENAI_IMAGE_QUALITY;

      $("dishName").value = data.idea.dish_name || "";
      $("dishNotes").value = data.idea.notes || "";
      setMode(data.config.AUTO_GENERATE_DISH_IDEA === "1" ? "auto" : "file");
      renderHistory(data.history);
      if (data.last_result){ renderResult(data.last_result); }
      if(showMsg){
        $("status").textContent = "历史已刷新。";
        $("status").className = "status ok";
      }
    }

    async function runNow(){
      if(state.mode === "file" && !$("dishName").value.trim()){
        $("status").textContent = "手动模式下请先填写菜名。";
        $("status").className = "status danger";
        $("dishName").focus();
        return;
      }
      $("runBtn").disabled = true;
      const startedAt = Date.now();
      $("status").textContent = "运行中，请稍候...";
      $("status").className = "status";
      const timer = setInterval(() => {
        const sec = Math.max(1, Math.floor((Date.now() - startedAt) / 1000));
        $("status").textContent = `运行中，请稍候...（已等待 ${sec}s）`;
      }, 1000);
      try{
        const payload = {
          mode: state.mode,
          dish_name: $("dishName").value.trim(),
          notes: $("dishNotes").value.trim(),
          model_temperature: $("temperature").value.trim(),
          image_quality: $("imageQuality").value.trim(),
          image_count: $("imageCount").value.trim()
        };
        const res = await fetch("/api/run", {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if(!res.ok){ throw new Error(data.error || "运行失败"); }
        renderResult(data.result);
        renderHistory(data.history);
        $("status").textContent = "运行完成。";
        $("status").className = "status ok";
      }catch(err){
        $("status").textContent = "失败：" + err.message;
        $("status").className = "status warn";
      }finally{
        clearInterval(timer);
        $("runBtn").disabled = false;
      }
    }

    async function copyOutputPath(){
      const path = $("rOut").textContent.trim();
      if(!path || path === "-"){
        $("status").textContent = "当前没有可复制的输出目录。";
        $("status").className = "status warn";
        return;
      }
      try{
        await navigator.clipboard.writeText(path);
        $("status").textContent = "输出目录已复制到剪贴板。";
        $("status").className = "status ok";
      }catch{
        $("status").textContent = "复制失败，请手动选中路径复制。";
        $("status").className = "status warn";
      }
    }

    $("modeAutoBtn").onclick = () => setMode("auto");
    $("modeFileBtn").onclick = () => setMode("file");
    $("runBtn").onclick = runNow;
    $("copyOutputBtn").onclick = copyOutputPath;
    $("refreshBtn").onclick = () => loadState(true);

    loadState();
  </script>
</body>
</html>
"""


def read_idea_file() -> dict[str, str]:
    if not IDEA_FILE.exists():
        return {"dish_name": "", "notes": ""}
    lines = [line.strip() for line in IDEA_FILE.read_text(encoding="utf-8").splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        return {"dish_name": "", "notes": ""}
    return {"dish_name": non_empty[0], "notes": "\n".join(non_empty[1:]).strip()}


def list_history(limit: int = 12) -> list[dict[str, str]]:
    if not OUTPUT_DIR.exists():
        return []
    dirs = [p for p in OUTPUT_DIR.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rows: list[dict[str, str]] = []
    for folder in dirs[:limit]:
        preview = ""
        for image in sorted(folder.glob("*.png")):
            preview = str(image)
            break
        rows.append({"name": folder.name, "path": str(folder), "preview_image": preview})
    return rows


def write_idea_file(dish_name: str, notes: str) -> None:
    dish = dish_name.strip()
    if not dish:
        raise ValueError("手动模式下，菜名不能为空。")
    payload = dish + ("\n" + notes.strip() if notes.strip() else "")
    IDEA_FILE.write_text(payload + "\n", encoding="utf-8")


def current_config_snapshot() -> dict[str, str]:
    ensure_runtime_config_loaded()
    return {
        "AUTO_GENERATE_DISH_IDEA": os.getenv("AUTO_GENERATE_DISH_IDEA", "0").strip() or "0",
        "MODEL_TEMPERATURE": os.getenv("MODEL_TEMPERATURE", "0.3").strip() or "0.3",
        "OPENAI_IMAGE_MODEL": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2",
        "OPENAI_IMAGE_QUALITY": os.getenv("OPENAI_IMAGE_QUALITY", "low").strip() or "low",
        "OPENAI_IMAGE_COUNT": os.getenv("OPENAI_IMAGE_COUNT", "1").strip() or "1",
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
        if parsed.path == "/":
            self._send_text(HTML_PAGE, 200)
            return
        if parsed.path == "/api/state":
            self._send_json(
                {
                    "config": current_config_snapshot(),
                    "idea": read_idea_file(),
                    "history": list_history(),
                    "last_result": LAST_RESULT or {},
                }
            )
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
        global RUNNING, LAST_RESULT
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            self._send_json({"error": "请求体 JSON 格式错误。"}, code=400)
            return

        with RUN_LOCK:
            if RUNNING:
                self._send_json({"error": "已有任务在运行，请稍后再试。"}, code=409)
                return
            RUNNING = True

        try:
            apply_runtime_overrides(payload)
            mode = str(payload.get("mode", "")).strip().lower()
            if mode == "file":
                write_idea_file(str(payload.get("dish_name", "")).strip(), str(payload.get("notes", "")).strip())
            result = run_v2_first_feature(mode=mode if mode in {"auto", "file"} else None)
            LAST_RESULT = result
            self._send_json({"ok": True, "result": result, "history": list_history()})
        except Exception as exc:
            self._send_json({"error": str(exc)}, code=500)
        finally:
            with RUN_LOCK:
                RUNNING = False


def main() -> None:
    ensure_runtime_config_loaded()
    server = ThreadingHTTPServer((HOST, PORT), V2PanelHandler)
    print(f"V2 前端面板已启动：http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务。")
    server.serve_forever()


if __name__ == "__main__":
    main()

