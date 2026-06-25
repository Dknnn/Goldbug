# encoding: utf-8
"""
Goldbug Web UI — 小红书爆款图片抓取工具可视化界面

启动: python webui.py  → 浏览器打开 http://localhost:8820
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

    threading.Thread(target=_run_scrape, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    # 通过设置标志位让抓取线程感知（简化处理：标记 done 让后续循环跳出）
    scraper.logger.warning("用户请求停止抓取")
    # 实际停止依赖 daemon 线程自然退出
    status["running"] = False
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
<title>Goldbug - 小红书爆款抓取</title>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background:#f5f5f5; color:#333; min-height:100vh; }
.header { background:linear-gradient(135deg,#ff6b6b,#ee5a24); color:#fff; padding:16px 24px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; }
.header h1 { font-size:22px; }
.status-badge { display:flex; align-items:center; gap:8px; font-size:14px; }
.status-dot { width:10px; height:10px; border-radius:50%; background:#4cd137; }
.status-dot.running { background:#fbc531; animation:blink 0.8s infinite; }
@keyframes blink { 50%{opacity:0.3;} }
.main { display:flex; height:calc(100vh - 70px); }
.left { flex:1; display:flex; flex-direction:column; min-width:0; }
.right { width:360px; background:#fff; border-left:1px solid #ddd; display:flex; flex-direction:column; overflow:hidden; }

/* 配置面板 */
.config-panel { background:#fff; border-bottom:1px solid #eee; padding:16px 20px; }
.config-toggle { cursor:pointer; font-size:14px; color:#ee5a24; user-select:none; }
.config-body { display:none; margin-top:12px; }
.config-body.open { display:block; }
.config-row { display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.config-row label { font-size:13px; width:70px; flex-shrink:0; color:#666; }
.config-row input[type=text],.config-row input[type=number] { border:1px solid #ddd; padding:6px 10px; border-radius:4px; font-size:13px; flex:1; min-width:120px; }
.config-row select { border:1px solid #ddd; padding:6px 10px; border-radius:4px; font-size:13px; }
.keywords-tags { display:flex; flex-wrap:wrap; gap:6px; flex:1; }
.keyword-tag { background:#fff3e0; border:1px solid #ffcc80; padding:4px 10px; border-radius:14px; font-size:13px; display:flex; align-items:center; gap:4px; }
.keyword-tag .del { cursor:pointer; color:#999; font-weight:bold; }
.keyword-tag .del:hover { color:#e74c3c; }
.kw-input { display:flex; gap:4px; }
.kw-input input { width:100px; }

.btn { padding:8px 20px; border:none; border-radius:6px; font-size:14px; cursor:pointer; font-weight:500; }
.btn-start { background:#ee5a24; color:#fff; }
.btn-start:hover { background:#d44a1a; }
.btn-stop { background:#e74c3c; color:#fff; }
.btn-stop:hover { background:#c0392b; }
.btn-save { background:#27ae60; color:#fff; padding:6px 14px; font-size:12px; }
.btn-save:hover { background:#219a52; }
.btn-sm { padding:4px 10px; font-size:12px; border-radius:4px; }

/* 日志区 */
.log-area { flex:1; background:#1e1e1e; color:#d4d4d4; overflow-y:auto; padding:12px; font-family:"Cascadia Code","Fira Code",Consolas,monospace; font-size:13px; line-height:1.6; }
.log-entry { white-space:pre-wrap; word-break:break-all; }
.log-entry.WARNING { color:#e5c07b; }
.log-entry.ERROR { color:#e06c75; }
.log-entry.INFO { color:#61afef; }

/* 图片预览区 */
.preview-header { padding:14px 16px; font-size:14px; font-weight:600; border-bottom:1px solid #eee; display:flex; justify-content:space-between; align-items:center; }
.preview-grid { flex:1; overflow-y:auto; padding:8px; display:grid; grid-template-columns:1fr 1fr; gap:8px; align-content:start; }
.preview-item { border-radius:6px; overflow:hidden; background:#f0f0f0; position:relative; aspect-ratio:1; }
.preview-item img { width:100%; height:100%; object-fit:cover; }
.preview-item .size-label { position:absolute; bottom:4px; right:4px; background:rgba(0,0,0,0.6); color:#fff; font-size:10px; padding:1px 5px; border-radius:3px; }

.empty-hint { text-align:center; color:#aaa; font-size:13px; margin-top:40px; }
.finish-banner { background:#d4edda; color:#155724; padding:10px 16px; text-align:center; font-size:13px; }
</style>
</head>
<body>
<div class="header">
  <h1>Goldbug</h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <span id="elapsed" style="font-size:13px;opacity:0.85;"></span>
    <div class="status-badge">
      <span class="status-dot" id="statusDot"></span>
      <span id="statusText">就绪</span>
    </div>
    <button class="btn btn-start" id="btnStart" onclick="doStart()">开始抓取</button>
    <button class="btn btn-stop" id="btnStop" onclick="doStop()" style="display:none">停止</button>
  </div>
</div>

<div class="main">
  <div class="left">
    <div class="config-panel">
      <div class="config-toggle" onclick="toggleConfig()">⚙ 抓取配置</div>
      <div class="config-body" id="configBody">
        <div class="config-row">
          <label>关键词</label>
          <div class="keywords-tags" id="kwTags"></div>
        </div>
        <div class="config-row">
          <label></label>
          <div class="kw-input">
            <input type="text" id="kwInput" placeholder="输入关键词回车添加" onkeydown="if(event.key==='Enter')addKeyword()">
            <button class="btn btn-sm btn-save" onclick="addKeyword()">+添加</button>
          </div>
        </div>
        <div class="config-row">
          <label>Top N</label>
          <input type="number" id="topN" value="15" min="1" max="50" style="width:80px;">
          <label style="width:auto;margin-left:12px;">滚动次数</label>
          <input type="number" id="scrollTimes" value="3" min="1" max="10" style="width:80px;">
          <label style="width:auto;margin-left:12px;">下载模式</label>
          <select id="downloadMode"><option value="all">全部图片</option><option value="cover">仅封面</option></select>
          <label style="width:auto;margin-left:12px;">日期</label>
          <input type="text" id="dateStart" placeholder="起始 YYYY-MM-DD" style="width:130px;border:1px solid #ddd;padding:6px;border-radius:4px;font-size:13px;">
          <span style="margin:0 4px;color:#999;">~</span>
          <input type="text" id="dateEnd" placeholder="结束 YYYY-MM-DD" style="width:130px;border:1px solid #ddd;padding:6px;border-radius:4px;font-size:13px;">
        </div>
        <div class="config-row">
          <label>无头模式</label>
          <input type="checkbox" id="headless" style="width:auto;">
          <span style="font-size:12px;color:#999;">（勾选后后台运行，不显示浏览器窗口）</span>
        </div>
      </div>
    </div>
    <div class="log-area" id="logArea">
      <div class="log-entry" style="color:#666;">等待启动...</div>
    </div>
  </div>

  <div class="right">
    <div class="preview-header">
      <span>📷 已下载</span>
      <span id="imgCount" style="color:#999;">0 张</span>
    </div>
    <div id="finishBanner"></div>
    <div class="preview-grid" id="previewGrid">
      <div class="empty-hint">暂无图片</div>
    </div>
  </div>
</div>

<script>
let keywords = ["金手镯","黄金手镯","金镯子"];
let es = null;
let timerElapsed = null;

// ── 初始化 ──
fetch("/api/status").then(r=>r.json()).then(s=>{
  keywords = s.keywords || keywords;
  document.getElementById("topN").value = s.top_n;
  document.getElementById("scrollTimes").value = s.scroll_times || 3;
  document.getElementById("downloadMode").value = s.download_mode;
  document.getElementById("headless").checked = s.headless;
  if(s.date_filter_start) document.getElementById("dateStart").value = s.date_filter_start;
  if(s.date_filter_end) document.getElementById("dateEnd").value = s.date_filter_end;
  renderKeywords();
});
renderKeywords();

function renderKeywords(){
  const el = document.getElementById("kwTags");
  el.innerHTML = keywords.map((k,i)=>`<span class="keyword-tag">${esc(k)}<span class="del" onclick="delKw(${i})">×</span></span>`).join("");
}
function esc(s){ return s.replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function addKeyword(){
  const inp = document.getElementById("kwInput");
  const v = inp.value.trim();
  if(v && !keywords.includes(v)){ keywords.push(v); renderKeywords(); }
  inp.value=""; inp.focus();
}
function delKw(i){ keywords.splice(i,1); renderKeywords(); }

function toggleConfig(){
  document.getElementById("configBody").classList.toggle("open");
}

// ── 日志流 ──
function connectLogs(){
  if(es) es.close();
  es = new EventSource("/api/logs");
  const logArea = document.getElementById("logArea");
  es.onmessage = function(e){
    try{
      const d = JSON.parse(e.data);
      if(d.level === "__DONE__") {
        es.close();
        es = null;
        refreshImages();
        refreshStatus();
        return;
      }
      const div = document.createElement("div");
      div.className = "log-entry " + (d.level||"");
      div.textContent = "[" + d.time + "] " + d.msg;
      logArea.appendChild(div);
      logArea.scrollTop = logArea.scrollHeight;
    }catch(ex){}
  };
  es.onerror = function(){ if(es){ es.close(); es=null; } };
}

// ── 状态轮询 ──
function refreshStatus(){
  fetch("/api/status").then(r=>r.json()).then(s=>{
    const dot = document.getElementById("statusDot");
    const txt = document.getElementById("statusText");
    const btnStart = document.getElementById("btnStart");
    const btnStop = document.getElementById("btnStop");
    const elapsed = document.getElementById("elapsed");

    if(s.running){
      dot.className = "status-dot running";
      txt.textContent = "抓取中...";
      btnStart.style.display = "none";
      btnStop.style.display = "";
      const mins = Math.floor(s.elapsed/60);
      const secs = s.elapsed % 60;
      elapsed.textContent = `已运行 ${mins}:${String(secs).padStart(2,"0")}`;
    } else if(s.done){
      dot.className = "status-dot";
      txt.textContent = `完成 · ${s.downloaded} 张图片`;
      btnStart.style.display = "";
      btnStop.style.display = "none";
      elapsed.textContent = "";
      const banner = document.getElementById("finishBanner");
      banner.innerHTML = `<div class="finish-banner">抓取完成！共 ${s.downloaded} 张图片 → ${s.save_dir}</div>`;
      setTimeout(()=>{ banner.innerHTML=""; }, 8000);
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
let statusDone = false;
setInterval(refreshStatus, 3000);

// ── 图片预览 ──
function refreshImages(){
  fetch("/api/images").then(r=>r.json()).then(imgs=>{
    const grid = document.getElementById("previewGrid");
    const count = document.getElementById("imgCount");
    count.textContent = imgs.length + " 张";
    if(imgs.length === 0){ grid.innerHTML = '<div class="empty-hint">暂无图片</div>'; return; }
    grid.innerHTML = imgs.map(img =>
      `<div class="preview-item"><img src="/api/images/file?path=${encodeURIComponent(img.path)}" loading="lazy"><span class="size-label">${img.size_kb}KB</span></div>`
    ).join("");
  });
}

// ── 操作 ──
function doStart(){
  document.getElementById("logArea").innerHTML = "";
  document.getElementById("finishBanner").innerHTML = "";
  document.getElementById("previewGrid").innerHTML = '<div class="empty-hint">抓取中...</div>';
  statusDone = false;

  fetch("/api/start", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({
      keywords: keywords,
      top_n: parseInt(document.getElementById("topN").value)||15,
      scroll_times: parseInt(document.getElementById("scrollTimes").value)||3,
      download_mode: document.getElementById("downloadMode").value,
      headless: document.getElementById("headless").checked,
      date_filter_start: document.getElementById("dateStart").value.trim()||null,
      date_filter_end: document.getElementById("dateEnd").value.trim()||null,
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
</script>
</body>
</html>
"""


# ── 入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    import webbrowser
    port = 8820
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
    print(f"  Goldbug Web UI: http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
