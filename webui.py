# encoding: utf-8
"""
Goldbug Web UI — 小红书爆款图片抓取工具可视化界面
"""

import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

# 工作目录切换到 Goldbug 所在目录
os.chdir(Path(__file__).parent)

import config
import scraper

app = Flask(__name__, static_folder=None)

# ── 全局状态 ──────────────────────────────────────────────
log_queue: queue.Queue = queue.Queue()
status = {"running": False, "done": False, "downloaded": 0, "start_time": None, "save_dir": ""}
downloaded_images: list[str] = []

# 半自动确认（Web UI 弹窗用）
_confirm_event = threading.Event()
_confirm_action = "skip"


def _webui_semi_auto_confirm(note: dict, index: int, total: int) -> str:
    global _confirm_action
    _confirm_event.clear()
    payload = json.dumps({
        "index": index, "total": total,
        "likes": note.get("likes", 0), "title": note.get("title", ""),
    }, ensure_ascii=False)
    log_queue.put(json.dumps({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": "__CONFIRM__",
        "msg": payload,
    }))
    if not _confirm_event.wait(timeout=300):
        scraper.logger.warning("半自动确认超时，跳过该笔记")
        return "skip"
    return _confirm_action


# ── SSE 日志桥接 ─────────────────────────────────────────
class SSEHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put(json.dumps({"time": datetime.now().strftime("%H:%M:%S"),
                                       "level": record.levelname, "msg": msg}))
        except Exception:
            pass


sse_handler = SSEHandler()
sse_handler.setFormatter(logging.Formatter("%(message)s"))
sse_handler.setLevel(logging.DEBUG)
scraper.logger.addHandler(sse_handler)
# 也挂到 root logger 以捕获 urllib 等底层日志
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(sse_handler)


# ── 抓取线程 ─────────────────────────────────────────────
def _run_scrape():
    global status, downloaded_images
    status["running"] = True
    status["done"] = False
    status["downloaded"] = 0
    status["start_time"] = time.time()
    status["save_dir"] = ""
    downloaded_images = []

    try:
        scraper.reset_scrape_abort()
        if config.SEMI_AUTO_MODE:
            scraper.set_semi_auto_confirm(_webui_semi_auto_confirm)
        else:
            scraper.set_semi_auto_confirm(None)
        results = scraper.run()
        downloaded_images = results
        status["downloaded"] = len(results)
        status["save_dir"] = scraper.setup_image_dir()
    except Exception as e:
        scraper.logger.error("抓取异常: %s", e)
    finally:
        status["running"] = False
        status["done"] = True


# ── API 路由 ─────────────────────────────────────────────
@app.route("/api/start", methods=["POST"])
def api_start():
    if status["running"]:
        return jsonify({"ok": False, "msg": "抓取已在运行中"}), 409
    data = request.get_json(silent=True) or {}

    # 更新 config 模块变量
    if data.get("keywords"):
        config.KEYWORDS = data["keywords"]
    if data.get("top_n"):
        config.TOP_N = int(data["top_n"])
    if data.get("headless") is not None:
        config.HEADLESS = bool(data["headless"])
    if data.get("download_mode"):
        config.DOWNLOAD_MODE = data["download_mode"]
    if data.get("scroll_times"):
        config.SCROLL_TIMES = int(data["scroll_times"])
    if data.get("date_filter_start") is not None:
        config.DATE_FILTER_START = data["date_filter_start"] or None
    if data.get("date_filter_end") is not None:
        config.DATE_FILTER_END = data["date_filter_end"] or None
    if data.get("low_freq_mode") is not None:
        config.LOW_FREQ_MODE = bool(data["low_freq_mode"])
    if data.get("semi_auto_mode") is not None:
        config.SEMI_AUTO_MODE = bool(data["semi_auto_mode"])

    threading.Thread(target=_run_scrape, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    scraper.logger.warning("用户请求停止抓取")
    scraper.request_scrape_abort()
    status["running"] = False
    return jsonify({"ok": True})


@app.route("/api/confirm", methods=["POST"])
def api_confirm():
    global _confirm_action
    data = request.get_json(silent=True) or {}
    action = data.get("action", "skip")
    if action not in ("download", "skip", "stop"):
        action = "skip"
    _confirm_action = action
    _confirm_event.set()
    return jsonify({"ok": True})


@app.route("/api/logs")
def api_logs():
    def stream():
        while True:
            try:
                msg = log_queue.get(timeout=10)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
                if not status["running"]:
                    # 再等最后一波日志
                    drained = False
                    while not log_queue.empty():
                        try:
                            msg = log_queue.get_nowait()
                            yield f"data: {msg}\n\n"
                        except queue.Empty:
                            drained = True
                            break
                    if drained or log_queue.empty():
                        yield "data: {\"time\":\"\",\"level\":\"__DONE__\",\"msg\":\"\"}\n\n"
                        return
    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/status")
def api_status():
    elapsed = 0
    if status["start_time"]:
        elapsed = time.time() - status["start_time"]
    return jsonify({
        "running": status["running"],
        "done": status["done"],
        "downloaded": status["downloaded"],
        "elapsed": int(elapsed),
        "save_dir": status["save_dir"],
        "keywords": config.KEYWORDS,
        "top_n": config.TOP_N,
        "download_mode": config.DOWNLOAD_MODE,
        "headless": config.HEADLESS,
        "date_filter_start": config.DATE_FILTER_START,
        "date_filter_end": config.DATE_FILTER_END,
        "low_freq_mode": config.LOW_FREQ_MODE,
        "semi_auto_mode": config.SEMI_AUTO_MODE,
    })


@app.route("/api/images")
def api_images():
    """返回已下载图片列表（用于缩略图预览）"""
    result = []
    for path in downloaded_images:
        if os.path.exists(path):
            result.append({
                "path": path.replace("\\", "/"),
                "size_kb": round(os.path.getsize(path) / 1024, 1),
            })
    return jsonify(result)


@app.route("/api/images/file")
def api_image_file():
    """返回单张图片文件"""
    path = request.args.get("path", "")
    if path and os.path.isfile(path):
        return send_file(path)
    return ("", 404)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存当前 config 变量到 config.py 文件"""
    data = request.get_json(silent=True) or {}
    if data.get("keywords"):
        config.KEYWORDS = data["keywords"]
    if data.get("top_n"):
        config.TOP_N = int(data["top_n"])
    if data.get("headless") is not None:
        config.HEADLESS = bool(data["headless"])
    if data.get("download_mode"):
        config.DOWNLOAD_MODE = data["download_mode"]
    if data.get("scroll_times"):
        config.SCROLL_TIMES = int(data["scroll_times"])
    if data.get("date_filter_start") is not None:
        config.DATE_FILTER_START = data["date_filter_start"] or None
    if data.get("date_filter_end") is not None:
        config.DATE_FILTER_END = data["date_filter_end"] or None
    return jsonify({"ok": True})


# ── 前端页面 ─────────────────────────────────────────────
@app.route("/")
def index():
    return HTML_PAGE


# ── 内嵌 HTML ────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Goldbug</title>
<style>
:root {
  --bg: #f5f5f7;
  --card: #ffffff;
  --border: #e5e5e7;
  --text: #1d1d1f;
  --text-secondary: #86868b;
  --text-tertiary: #aeaeb2;
  --accent: #0071e3;
  --accent-hover: #0077ed;
  --danger: #ff3b30;
  --success: #34c759;
  --warning: #ffcc00;
  --radius: 10px;
  --radius-sm: 7px;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.06);
  --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
  --transition: 0.2s cubic-bezier(0.25, 0.1, 0.25, 1);
}

* { box-sizing:border-box; margin:0; padding:0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
}

/* ═══ Layout ═══ */
.app { display:flex; height:100vh; }
.sidebar { width:300px; min-width:300px; background:var(--card); border-right:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; }
.main { flex:1; display:flex; flex-direction:column; min-width:0; }
.preview { width:320px; min-width:320px; background:var(--card); border-left:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; }

/* ═══ Header ═══ */
.header {
  padding:14px 22px;
  display:flex; align-items:center; justify-content:space-between;
  border-bottom:1px solid var(--border);
  background:rgba(255,255,255,0.72);
  backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px);
  z-index:10;
}
.header h1 { font-size:17px; font-weight:700; letter-spacing:-0.3px; color:var(--text); }
.header-right { display:flex; align-items:center; gap:14px; }

/* ═══ Status ═══ */
.status { display:flex; align-items:center; gap:7px; font-size:12px; color:var(--text-secondary); }
.status-dot { width:7px; height:7px; border-radius:50%; background:var(--success); flex-shrink:0; }
.status-dot.running { background:var(--warning); animation:pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(255,204,0,0.5);} 50%{opacity:0.4;box-shadow:0 0 0 8px rgba(255,204,0,0);} }

/* ═══ Buttons ═══ */
.btn {
  font-family:inherit; font-size:13px; font-weight:600; padding:7px 20px;
  border-radius:20px; border:none; cursor:pointer; transition:var(--transition);
  white-space:nowrap;
}
.btn-primary { background:var(--accent); color:#fff; }
.btn-primary:hover { background:var(--accent-hover); box-shadow:0 4px 14px rgba(0,113,227,0.3); }
.btn-primary:active { transform:scale(0.97); }
.btn-danger { background:var(--danger); color:#fff; }
.btn-danger:hover { opacity:0.85; }
.btn-ghost { background:#f5f5f7; color:var(--text); }
.btn-ghost:hover { background:#ebebed; }
.btn:disabled { opacity:0.35; pointer-events:none; }

/* ═══ Config Panel ═══ */
.config-header {
  padding:14px 18px; font-size:12px; font-weight:700; color:var(--text);
  display:flex; align-items:center; justify-content:space-between;
  cursor:pointer; user-select:none; border-bottom:1px solid var(--border);
  letter-spacing:-0.2px;
}
.config-header:hover { color:var(--accent); }
.config-body { padding:16px 18px; overflow-y:auto; flex:1; }
.config-body.hidden { display:none; }
.config-group { margin-bottom:18px; }
.config-label { font-size:11px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px; }
.config-input {
  width:100%; padding:8px 12px; font-size:13px; font-family:inherit;
  background:#f5f5f7; border:1px solid var(--border); border-radius:var(--radius-sm);
  color:var(--text); outline:none; transition:var(--transition);
}
.config-input:focus { border-color:var(--accent); background:#fff; box-shadow:0 0 0 3px rgba(0,113,227,0.12); }
.config-input::placeholder { color:var(--text-tertiary); }
.config-row { display:flex; gap:8px; align-items:center; }
select.config-input { appearance:none; background-image:url("data:image/svg+xml,%3Csvg width='10' height='6' fill='%2386868b' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 0h10L5 6z'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 10px center; padding-right:28px; cursor:pointer; }

/* Keywords */
.kw-tags { display:flex; flex-wrap:wrap; gap:5px; }
.kw-tag {
  display:flex; align-items:center; gap:4px;
  padding:4px 10px; font-size:12px; font-weight:500;
  background:#e8f2fd; border:1px solid #c2daf5;
  border-radius:14px; color:var(--accent); transition:var(--transition);
}
.kw-tag .kw-del { cursor:pointer; opacity:0.4; font-size:15px; line-height:1; }
.kw-tag .kw-del:hover { opacity:1; color:var(--danger); }

/* Toggle */
.toggle-row { display:flex; align-items:center; justify-content:space-between; padding:6px 0; }
.toggle-label { font-size:13px; font-weight:500; color:var(--text); }
.toggle {
  width:40px; height:24px; border-radius:12px; background:#d1d1d6;
  cursor:pointer; transition:var(--transition); position:relative;
}
.toggle.on { background:var(--accent); }
.toggle::after {
  content:''; position:absolute; top:2px; left:2px;
  width:20px; height:20px; border-radius:50%; background:#fff;
  transition:var(--transition); box-shadow:0 1px 3px rgba(0,0,0,0.15);
}
.toggle.on::after { transform:translateX(16px); }

/* ═══ Console ═══ */
.console {
  flex:1; overflow-y:auto; padding:16px 22px;
  font-family:'SF Mono','Cascadia Code','Fira Code',Consolas,monospace;
  font-size:11.5px; line-height:1.7;
  background:#fafafa;
  scroll-behavior:smooth;
}
.console-entry { white-space:pre-wrap; word-break:break-all; padding:0.5px 0; }
.console-entry .time { color:var(--text-tertiary); margin-right:8px; }
.console-entry .tag-info { color:#0071e3; }
.console-entry .tag-warn { color:#e6a800; }
.console-entry .tag-err { color:var(--danger); }
.console-entry .tag-dim { color:var(--text-tertiary); }
.console-empty { color:var(--text-tertiary); font-style:italic; padding:24px 0; text-align:center; font-family:inherit; font-size:13px; }

/* ═══ Preview ═══ */
.preview-header {
  padding:14px 18px; font-size:12px; font-weight:700; color:var(--text);
  border-bottom:1px solid var(--border);
  display:flex; justify-content:space-between; align-items:center;
  letter-spacing:-0.2px;
}
.preview-grid {
  flex:1; overflow-y:auto; padding:10px;
  display:grid; grid-template-columns:1fr 1fr; gap:8px; align-content:start;
}
.preview-card {
  border-radius:var(--radius-sm); overflow:hidden; background:#f0f0f0;
  transition:var(--transition); cursor:pointer; position:relative; aspect-ratio:1;
  border:1px solid var(--border);
}
.preview-card:hover { transform:scale(1.03); box-shadow:var(--shadow-md); }
.preview-card img { width:100%; height:100%; object-fit:cover; }
.preview-card .badge {
  position:absolute; bottom:5px; right:5px;
  background:rgba(0,0,0,0.65); backdrop-filter:blur(6px);
  color:#fff; font-size:10px; padding:1px 5px; border-radius:3px;
}
.preview-empty { text-align:center; color:var(--text-tertiary); padding:40px 16px; font-size:13px; }

/* ═══ Toast ═══ */
.toast {
  position:fixed; top:18px; left:50%; transform:translateX(-50%);
  padding:10px 22px; border-radius:20px; font-size:13px; font-weight:600;
  background:#1d1d1f; color:#fff; z-index:100;
  box-shadow:0 8px 24px rgba(0,0,0,0.2);
  opacity:0; pointer-events:none; transition:opacity 0.35s,transform 0.35s;
}
.toast.show { opacity:1; transform:translateX(-50%) translateY(0); }

/* ═══ Scrollbar ═══ */
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:#d5d5d7; border-radius:10px; }
::-webkit-scrollbar-thumb:hover { background:#c0c0c3; }

@media (max-width:1100px) { .preview { display:none; } }
@media (max-width:700px) { .sidebar { width:260px; min-width:260px; } }
</style>
</style>
</head>
<body>
<div class="app">

<!-- ═══ Sidebar ═══ -->
<div class="sidebar">
  <div class="config-header" onclick="toggleConfig()">
    <span>抓取配置</span>
    <span id="configArrow" style="font-size:10px;">▼</span>
  </div>
  <div class="config-body" id="configBody">
    <div class="config-group">
      <div class="config-label">关键词</div>
      <div class="kw-tags" id="kwTags"></div>
      <div style="display:flex;gap:6px;margin-top:8px;">
        <input class="config-input" id="kwInput" placeholder="添加关键词" style="flex:1;"
               onkeydown="if(event.key==='Enter')addKeyword()">
        <button class="btn btn-ghost" onclick="addKeyword()" style="padding:7px 12px;font-size:16px;">+</button>
      </div>
    </div>

    <div class="config-group">
      <div class="config-label">每个关键词抓取数量</div>
      <input class="config-input" type="number" id="topN" value="15" min="1" max="50" style="width:100%">
    </div>

    <div class="config-group">
      <div class="config-label">搜索滚动次数</div>
      <input class="config-input" type="number" id="scrollTimes" value="3" min="1" max="10" style="width:100%">
    </div>

    <div class="config-group">
      <div class="config-label">下载模式</div>
      <select class="config-input" id="downloadMode">
        <option value="all">全部图片</option>
        <option value="cover">仅封面</option>
      </select>
    </div>

    <div class="config-group">
      <div class="config-label">日期范围</div>
      <div class="config-row">
        <input class="config-input" type="text" id="dateStart" placeholder="起始日期" style="flex:1">
        <span style="color:var(--text-secondary);font-size:12px;">至</span>
        <input class="config-input" type="text" id="dateEnd" placeholder="结束日期" style="flex:1">
      </div>
      <span style="font-size:10px;color:var(--text-secondary);margin-top:4px;display:block;">输入8位数字如 20260620，留空不限</span>
    </div>

    <div class="toggle-row">
      <span class="toggle-label">无头模式</span>
      <div class="toggle" id="headlessToggle" onclick="toggleHeadless()"></div>
    </div>
    <span style="font-size:10px;color:var(--text-secondary);">后台运行，不显示浏览器窗口</span>

    <div class="toggle-row" style="margin-top:10px;">
      <span class="toggle-label">低频模式</span>
      <div class="toggle on" id="lowFreqToggle" onclick="toggleLowFreq()"></div>
    </div>
    <span style="font-size:10px;color:var(--text-secondary);">慢速随机延迟、限量、仅封面</span>

    <div class="toggle-row" style="margin-top:10px;">
      <span class="toggle-label">半自动</span>
      <div class="toggle" id="semiAutoToggle" onclick="toggleSemiAuto()"></div>
    </div>
    <span style="font-size:10px;color:var(--text-secondary);">每篇笔记弹窗确认后再下载</span>
  </div>
</div>

<!-- ═══ Main ═══ -->
<div class="main">
  <div class="header">
    <h1>Goldbug</h1>
    <div class="header-right">
      <div class="status">
        <span class="status-dot" id="statusDot"></span>
        <span id="statusText">就绪</span>
        <span id="elapsed" style="margin-left:4px;"></span>
      </div>
      <button class="btn btn-primary" id="btnStart" onclick="doStart()">开始抓取</button>
      <button class="btn btn-danger" id="btnStop" onclick="doStop()" style="display:none;">停止</button>
    </div>
  </div>
  <div class="console" id="console">
    <div class="console-empty">等待启动...</div>
  </div>
</div>

<!-- ═══ Preview ═══ -->
<div class="preview">
  <div class="preview-header">
    <span>已下载</span>
    <span id="imgCount">0 张</span>
  </div>
  <div class="preview-grid" id="previewGrid">
    <div class="preview-empty">暂无图片</div>
  </div>
</div>

</div>

<!-- ═══ Toast ═══ -->
<div class="toast" id="toast"></div>

<!-- ═══ 半自动确认 ═══ -->
<div id="confirmModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:999;align-items:center;justify-content:center;">
  <div style="background:#fff;border-radius:12px;padding:20px 24px;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.2);">
    <div style="font-size:13px;font-weight:600;margin-bottom:8px;">半自动确认</div>
    <div id="confirmText" style="font-size:12px;color:#333;line-height:1.6;margin-bottom:16px;white-space:pre-wrap;"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button class="btn btn-ghost" onclick="answerConfirm('skip')">跳过</button>
      <button class="btn btn-danger" onclick="answerConfirm('stop')">停止</button>
      <button class="btn btn-primary" onclick="answerConfirm('download')">下载</button>
    </div>
  </div>
</div>

<script>
let keywords = ["金手镯","黄金手镯","金镯子"];
let es = null;
let headlessOn = false;
let lowFreqOn = true;
let semiAutoOn = false;
let statusDone = false;

// ── Init ──
fetch("/api/status").then(r=>r.json()).then(s=>{
  keywords = s.keywords || keywords;
  document.getElementById("topN").value = s.top_n;
  document.getElementById("scrollTimes").value = s.scroll_times || 3;
  document.getElementById("downloadMode").value = s.download_mode;
  if(s.date_filter_start) document.getElementById("dateStart").value = s.date_filter_start;
  if(s.date_filter_end) document.getElementById("dateEnd").value = s.date_filter_end;
  if(s.headless) { headlessOn=true; document.getElementById("headlessToggle").classList.add("on"); }
  if(s.low_freq_mode !== false) { lowFreqOn=true; document.getElementById("lowFreqToggle").classList.add("on"); }
  else { lowFreqOn=false; document.getElementById("lowFreqToggle").classList.remove("on"); }
  if(s.semi_auto_mode) { semiAutoOn=true; document.getElementById("semiAutoToggle").classList.add("on"); }
  renderKeywords();
});
renderKeywords();

// ── Keywords ──
function renderKeywords(){
  document.getElementById("kwTags").innerHTML = keywords.map((k,i)=>
    `<span class="kw-tag">${esc(k)}<span class="kw-del" onclick="delKw(${i})">x</span></span>`
  ).join("");
}
function esc(s){ return s.replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function addKeyword(){
  const inp = document.getElementById("kwInput");
  const v = inp.value.trim();
  if(v && !keywords.includes(v)){ keywords.push(v); renderKeywords(); }
  inp.value=""; inp.focus();
}
function delKw(i){ keywords.splice(i,1); renderKeywords(); }

// ── Config ──
function toggleConfig(){
  const body = document.getElementById("configBody");
  const arrow = document.getElementById("configArrow");
  body.classList.toggle("hidden");
  arrow.textContent = body.classList.contains("hidden") ? "▶" : "▼";
}
function toggleHeadless(){
  headlessOn = !headlessOn;
  document.getElementById("headlessToggle").classList.toggle("on", headlessOn);
}
function toggleLowFreq(){
  lowFreqOn = !lowFreqOn;
  document.getElementById("lowFreqToggle").classList.toggle("on", lowFreqOn);
}
function toggleSemiAuto(){
  semiAutoOn = !semiAutoOn;
  document.getElementById("semiAutoToggle").classList.toggle("on", semiAutoOn);
}

function showConfirmModal(info){
  const m = document.getElementById("confirmModal");
  document.getElementById("confirmText").textContent =
    `[${info.index}/${info.total}]  ${info.likes}赞\n${info.title}`;
  m.style.display = "flex";
}
function answerConfirm(action){
  document.getElementById("confirmModal").style.display = "none";
  fetch("/api/confirm", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({action})
  });
}

// ── Console ──
function addConsole(msg, tag) {
  const c = document.getElementById("console");
  if (c.querySelector(".console-empty")) c.innerHTML = "";
  const div = document.createElement("div");
  div.className = "console-entry";
  const now = new Date().toTimeString().slice(0,8);
  div.innerHTML = `<span class="time">${now}</span>` + msg;
  if (tag) div.classList.add("tag-"+tag);
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

// ── Logs SSE ──
function connectLogs(){
  if(es) es.close();
  es = new EventSource("/api/logs");
  es.onmessage = function(e){
    try{
      const d = JSON.parse(e.data);
      if(d.level === "__DONE__") { es.close(); es=null; refreshImages(); refreshStatus(); return; }
      if(d.level === "__CONFIRM__") {
        try { showConfirmModal(JSON.parse(d.msg)); } catch(ex){}
        return;
      }
      let tag = null;
      if(d.level==="INFO") tag="info";
      else if(d.level==="WARNING") tag="warn";
      else if(d.level==="ERROR") tag="err";
      addConsole("["+d.level+"] "+d.msg, tag);
    }catch(ex){}
  };
  es.onerror = function(){ if(es){ es.close(); es=null; } };
}

// ── Status ──
function refreshStatus(){
  fetch("/api/status").then(r=>r.json()).then(s=>{
    const dot = document.getElementById("statusDot");
    const txt = document.getElementById("statusText");
    const btnStart = document.getElementById("btnStart");
    const btnStop = document.getElementById("btnStop");
    const elapsed = document.getElementById("elapsed");
    if(s.running){
      dot.className = "status-dot running";
      txt.textContent = "抓取中";
      btnStart.style.display = "none";
      btnStop.style.display = "";
      const m = Math.floor(s.elapsed/60), sec = s.elapsed%60;
      elapsed.textContent = m+":"+String(sec).padStart(2,"0");
    } else if(s.done){
      dot.className = "status-dot";
      txt.textContent = "完成 "+s.downloaded+" 张";
      btnStart.style.display = "";
      btnStop.style.display = "none";
      elapsed.textContent = "";
      toast("抓取完成 "+s.downloaded+" 张图片, "+s.save_dir);
      statusDone = true;
    } else {
      dot.className = "status-dot";
      txt.textContent = "就绪";
      btnStart.style.display = "";
      btnStop.style.display = "none";
      elapsed.textContent = "";
    }
  });
}
setInterval(refreshStatus, 3000);

// ── Images ──
function refreshImages(){
  fetch("/api/images").then(r=>r.json()).then(imgs=>{
    const grid = document.getElementById("previewGrid");
    const count = document.getElementById("imgCount");
    count.textContent = imgs.length + " 张";
    if(!imgs.length){ grid.innerHTML = '<div class="preview-empty">暂无图片</div>'; return; }
    grid.innerHTML = imgs.map(img =>
      `<div class="preview-card" onclick="window.open('/api/images/file?path=${encodeURIComponent(img.path)}')"><img src="/api/images/file?path=${encodeURIComponent(img.path)}" loading="lazy"><span class="badge">${img.size_kb}KB</span></div>`
    ).join("");
  });
}

// ── Actions ──
function doStart(){
  document.getElementById("console").innerHTML = "";
  document.getElementById("previewGrid").innerHTML = '<div class="preview-empty">抓取中...</div>';
  statusDone = false;
  let ds = document.getElementById("dateStart").value.trim();
  let de = document.getElementById("dateEnd").value.trim();
  // auto-format dates
  [ds,de].forEach((v,i) => {
    if(!v) return;
    let digits = v.replace(/\D/g,'');
    if(digits.length===8) { let f=digits.slice(0,4)+'-'+digits.slice(4,6)+'-'+digits.slice(6,8); (i?document.getElementById("dateEnd"):document.getElementById("dateStart")).value=f; }
  });
  ds = document.getElementById("dateStart").value.trim();
  de = document.getElementById("dateEnd").value.trim();

  fetch("/api/start", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({
      keywords, headless: headlessOn,
      top_n: parseInt(document.getElementById("topN").value)||15,
      scroll_times: parseInt(document.getElementById("scrollTimes").value)||3,
      download_mode: document.getElementById("downloadMode").value,
      date_filter_start: ds||null,
      date_filter_end: de||null,
      low_freq_mode: lowFreqOn,
      semi_auto_mode: semiAutoOn,
    })
  }).then(r=>r.json()).then(d=>{
    if(d.ok){ connectLogs(); refreshStatus(); }
    else { alert(d.msg||"启动失败"); }
  });
}

function doStop(){
  fetch("/api/stop", {method:"POST"});
  document.getElementById("statusText").textContent = "停止中...";
}

function toast(msg){
  const el = document.getElementById("toast");
  el.textContent = msg; el.classList.add("show");
  setTimeout(()=>el.classList.remove("show"), 3000);
}
</script>
</body>
</html>
"""


# ── 入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    import threading
    port = 8820
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)

    # Flask 在后台线程运行
    t = threading.Thread(target=app.run, daemon=True,
                         kwargs={"host": "127.0.0.1", "port": port, "debug": False})
    t.start()

    # pywebview 原生窗口显示 Web UI
    import webview
    webview.create_window("Goldbug", f"http://localhost:{port}",
                          width=1280, height=820, min_size=(900, 600))
    webview.start()
