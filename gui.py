#!/usr/bin/env python3
"""EPUB Translator GUI — Flask web interface with progress, pause/resume, hardware monitoring."""
import os
import sys
import json
import signal
import threading
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, send_from_directory
import psutil

from epub_handler import EpubBook
from llm_backend import TranslationServer, Translator
from ocr_backend import OCRBackend
from checkpoint import CheckpointManager

app = Flask(__name__)

# ── global state ──────────────────────────────────────────────
state = {
    "status": "idle",          # idle | running | paused | done | error
    "mode": None,              # text | scanned
    "input_path": None,
    "output_path": None,
    "model_path": None,
    "target_lang": "Simplified Chinese",
    "progress": 0,             # current block
    "total": 0,                # total blocks
    "message": "就绪",
    "error": None,
    "scanned": False,
    "cpu_percent": 0.0,
    "ram_percent": 0.0,
    "ram_used_gb": 0.0,
    "ram_total_gb": 0.0,
    "model_name": "",
    "completed_on_resume": 0,
}
_lock = threading.Lock()
_pause_event = threading.Event()
_pause_event.set()            # initially not paused
_worker_thread = None
_server = None
_checkpoint = None


def _update(**kwargs):
    with _lock:
        state.update(kwargs)


def _set_status(status, message=None):
    with _lock:
        state["status"] = status
        if message:
            state["message"] = message


def _hardware_monitor():
    """Background thread: update CPU/RAM readings every 1s."""
    while state["status"] in ("running", "paused"):
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        with _lock:
            state["cpu_percent"] = round(cpu, 1)
            state["ram_percent"] = round(mem.percent, 1)
            state["ram_used_gb"] = round(mem.used / (1024**3), 1)
            state["ram_total_gb"] = round(mem.total / (1024**3), 1)
        time.sleep(1.0)


def _translation_worker():
    """Background thread: run the translation loop."""
    global _server, _checkpoint
    try:
        book = EpubBook(state["input_path"])

        if state["scanned"]:
            _scanned_worker(book)
        else:
            _text_worker(book)

        book.save(state["output_path"])
        if _checkpoint:
            _checkpoint.delete()
        _set_status("done", "翻译完成")
        _update(progress=state["total"])
    except Exception as e:
        _set_status("error", str(e))
        _update(error=str(e))
    finally:
        if _server:
            _server.stop()
            _server = None


def _text_worker(book):
    global _server, _checkpoint
    translator = Translator(_server, target_lang=state["target_lang"])
    blocks = list(book.iter_text_blocks())

    _update(total=len(blocks))
    completed_count = 0
    if _checkpoint:
        completed_count = sum(1 for it, _, _, idx in blocks
                              if _checkpoint.is_completed(it.file_name, idx))
    _update(progress=completed_count, completed_on_resume=completed_count)

    for item, tag, text, idx in blocks:
        _pause_event.wait()
        if state["status"] == "idle":
            return

        file_name = item.file_name
        if _checkpoint and _checkpoint.is_completed(file_name, idx):
            translated = _checkpoint.get_translated(file_name, idx)
            book.apply_translation(tag, translated)
            _update(progress=state["progress"] + 1)
            continue

        for attempt in range(3):
            try:
                translated = translator.translate(text)
                book.apply_translation(tag, translated)
                if _checkpoint:
                    _checkpoint.mark_completed(file_name, idx, translated)
                break
            except Exception as e:
                if attempt < 2 and ("Connection" in str(e) or "Remote" in str(e)):
                    _set_status("running", "重建服务器连接...")
                    _server.stop()
                    _server = TranslationServer(state["model_path"])
                    _server.start()
                    translator.server = _server
                elif attempt == 2:
                    pass
                else:
                    raise

        _update(progress=state["progress"] + 1)
        _update(message=f"正在翻译第 {state['progress']}/{state['total']} 块...")


def _scanned_worker(book):
    global _server, _checkpoint
    translator = Translator(_server, target_lang=state["target_lang"])
    ocr = OCRBackend()
    all_images = list(book.iter_all_images(min_height=200))

    _update(total=len(all_images))
    completed_count = 0
    if _checkpoint:
        completed_count = sum(1 for it, _, _, _, idx in all_images
                              if _checkpoint.is_completed(it.file_name, idx))
    _update(progress=completed_count, completed_on_resume=completed_count)

    for item, img_tag, image_data, mime_type, idx in all_images:
        _pause_event.wait()
        if state["status"] == "idle":
            return

        file_name = item.file_name
        if _checkpoint and _checkpoint.is_completed(file_name, idx):
            translated = _checkpoint.get_translated(file_name, idx)
            book.apply_image_translation(img_tag, translated)
            _update(progress=state["progress"] + 1)
            continue

        try:
            ocr_text = ocr.ocr(image_data, mime_type)
            if ocr_text.strip():
                translated = translator.translate(ocr_text)
                book.apply_image_translation(img_tag, translated)
                if _checkpoint:
                    _checkpoint.mark_completed(file_name, idx, translated)
        except Exception as e:
            pass

        _update(progress=state["progress"] + 1)
        _update(message=f"OCR+翻译第 {state['progress']}/{state['total']} 页...")


# ── routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return _HTML


@app.route("/api/state")
def api_state():
    with _lock:
        return jsonify({k: state[k] for k in [
            "status", "mode", "input_path", "output_path", "model_path",
            "target_lang", "progress", "total", "message", "error",
            "scanned", "cpu_percent", "ram_percent", "ram_used_gb",
            "ram_total_gb", "model_name", "completed_on_resume",
        ]})


@app.route("/api/start", methods=["POST"])
def api_start():
    global _server, _worker_thread, _checkpoint, _pause_event
    data = request.get_json() or {}
    input_path = os.path.expanduser(data.get("input_path", ""))
    target_lang = data.get("target_lang", "Simplified Chinese")
    model_path = os.path.expanduser(data.get("model_path", "~/models/Hy-MT2-1.8B-Q8_0.gguf"))

    if not input_path or not os.path.exists(input_path):
        return jsonify({"ok": False, "error": "文件不存在"})

    if not os.path.exists(model_path):
        return jsonify({"ok": False, "error": f"模型不存在: {model_path}"})

    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(os.path.dirname(input_path) or ".", f"{base}.translated.epub")

    book = EpubBook(input_path)
    scanned = book.is_scanned()
    mode = "scanned" if scanned else "text"

    # load or create checkpoint
    _checkpoint = CheckpointManager.load(input_path, output_path, mode, target_lang)
    if not _checkpoint:
        _checkpoint = CheckpointManager(input_path, output_path, mode, target_lang)

    model_name = os.path.basename(model_path)
    _update(
        status="running",
        mode=mode,
        input_path=input_path,
        output_path=output_path,
        model_path=model_path,
        target_lang=target_lang,
        scanned=scanned,
        progress=0,
        total=0,
        message="启动翻译服务...",
        error=None,
        model_name=model_name,
        completed_on_resume=len(_checkpoint.completed) if _checkpoint else 0,
    )

    _pause_event.set()

    try:
        _server = TranslationServer(model_path)
        _server.start()
    except Exception as e:
        _set_status("error", f"启动模型失败: {e}")
        return jsonify({"ok": False, "error": str(e)})

    _worker_thread = threading.Thread(target=_translation_worker, daemon=True)
    _worker_thread.start()

    # start HW monitor
    threading.Thread(target=_hardware_monitor, daemon=True).start()

    return jsonify({"ok": True, "mode": mode, "checkpoint_blocks": len(_checkpoint.completed)})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    _pause_event.clear()
    with _lock:
        if state["status"] == "running":
            state["status"] = "paused"
            state["message"] = "已暂停"
    # save checkpoint for resume
    if _checkpoint:
        _checkpoint.save()
    if _server:
        _server.stop()
    return jsonify({"ok": True})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    global _server, _worker_thread
    _pause_event.set()

    with _lock:
        if state["status"] == "paused":
            state["status"] = "running"
            state["message"] = "恢复翻译..."

    # restart server
    try:
        _server = TranslationServer(state["model_path"])
        _server.start()
    except Exception as e:
        _set_status("error", f"重启模型失败: {e}")
        return jsonify({"ok": False, "error": str(e)})

    _worker_thread = threading.Thread(target=_translation_worker, daemon=True)
    _worker_thread.start()

    threading.Thread(target=_hardware_monitor, daemon=True).start()

    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _server
    _pause_event.set()
    with _lock:
        state["status"] = "idle"
        state["message"] = "已停止"
    if _checkpoint:
        _checkpoint.save()
    if _server:
        _server.stop()
        _server = None
    return jsonify({"ok": True})


@app.route("/api/browse", methods=["POST"])
def api_browse():
    """Browse directories for epub files."""
    data = request.get_json() or {}
    dir_path = os.path.expanduser(data.get("path", os.path.expanduser("~")))

    if not os.path.isdir(dir_path):
        dir_path = os.path.expanduser("~")

    try:
        entries = []
        for name in sorted(os.listdir(dir_path)):
            full = os.path.join(dir_path, name)
            if os.path.isdir(full) and not name.startswith("."):
                entries.append({"name": name, "type": "dir", "path": full})
            elif name.lower().endswith(".epub"):
                size_mb = round(os.path.getsize(full) / (1024 * 1024), 1)
                entries.append({"name": name, "type": "epub", "path": full, "size_mb": size_mb})

        parent = os.path.dirname(dir_path) if dir_path != "/" else None
        return jsonify({"ok": True, "current": dir_path, "parent": parent, "entries": entries})
    except PermissionError:
        return jsonify({"ok": False, "error": "无权限访问此目录"})


@app.route("/api/info", methods=["POST"])
def api_info():
    """Get basic info about an EPUB (scanned vs text, block counts)."""
    data = request.get_json() or {}
    path = os.path.expanduser(data.get("path", ""))
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "文件不存在"})

    try:
        book = EpubBook(path)
        scanned = book.is_scanned()
        if scanned:
            imgs = len(list(book.iter_all_images(min_height=200)))
            return jsonify({"ok": True, "scanned": True, "images": imgs, "text_blocks": 0})
        else:
            blocks = len(list(book.iter_text_blocks()))
            return jsonify({"ok": True, "scanned": False, "images": 0, "text_blocks": blocks})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── inline HTML ───────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EPUB 翻译器</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #1a1a2e; color: #e0e0e0;
  display: flex; justify-content: center; align-items: center;
  min-height: 100vh; padding: 20px;
}
.card {
  background: #16213e; border-radius: 16px; padding: 32px;
  max-width: 640px; width: 100%; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
}
h1 { font-size: 1.5em; margin-bottom: 24px; color: #e94560; text-align: center; }
label { font-size: 0.85em; color: #a0a0b0; display: block; margin-bottom: 4px; }
.row { display: flex; gap: 8px; margin-bottom: 16px; }
.row input, .row select { flex: 1; }
input, select, button {
  background: #0f3460; color: #e0e0e0; border: 1px solid #1a4a8a;
  border-radius: 8px; padding: 10px 14px; font-size: 0.95em;
}
input:focus, select:focus { outline: none; border-color: #e94560; }
button {
  cursor: pointer; background: #e94560; border: none;
  font-weight: 600; transition: background 0.2s;
}
button:hover { background: #d63851; }
button.secondary { background: #0f3460; border: 1px solid #1a4a8a; }
button.secondary:hover { background: #1a4a8a; }

.progress-section { margin: 20px 0; }
.progress-bar-wrap {
  background: #0f3460; border-radius: 8px; height: 24px;
  overflow: hidden; position: relative; margin-bottom: 8px;
}
.progress-bar {
  background: linear-gradient(90deg, #e94560, #f39c12);
  height: 100%; width: 0%; border-radius: 8px;
  transition: width 0.3s ease;
}
.progress-text {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  font-size: 0.8em; font-weight: 700; color: #fff; white-space: nowrap;
}
#status-text { font-size: 0.85em; color: #a0a0b0; margin-bottom: 16px; }

.buttons { display: flex; gap: 8px; margin-bottom: 24px; }
.buttons button { flex: 1; }

.hw-section { border-top: 1px solid #1a4a8a; padding-top: 16px; }
.hw-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.hw-item { }
.hw-item .label { font-size: 0.8em; color: #a0a0b0; }
.hw-item .value { font-size: 1.1em; font-weight: 700; }
.hw-item .bar {
  background: #0f3460; height: 8px; border-radius: 4px; margin-top: 4px; overflow: hidden;
}
.hw-item .bar-fill {
  height: 100%; border-radius: 4px; transition: width 0.5s ease;
}
.cpu-fill { background: #e94560; }
.ram-fill { background: #f39c12; }

#browser { display: none; }
#browser .path { font-size: 0.8em; color: #a0a0b0; margin-bottom: 8px; word-break: break-all; }
#browser .entry {
  cursor: pointer; padding: 6px 8px; border-radius: 4px; font-size: 0.9em;
}
#browser .entry:hover { background: #0f3460; }
#browser .entry.dir { color: #f39c12; }
#browser .entry.epub { color: #e94560; }

.mode-badge {
  display: inline-block; padding: 2px 10px; border-radius: 12px;
  font-size: 0.75em; font-weight: 700; margin-left: 8px;
}
.mode-badge.scanned { background: #e94560; }
.mode-badge.text { background: #f39c12; }
</style>
</head>
<body>
<div class="card">
  <h1>EPUB 翻译器</h1>

  <label>文件路径</label>
  <div class="row">
    <input type="text" id="file-path" placeholder="选择或输入 EPUB 文件路径...">
    <button class="secondary" onclick="toggleBrowser()">浏览</button>
  </div>
  <div id="browser">
    <div class="path" id="browser-path"></div>
    <div id="browser-entries"></div>
  </div>

  <div style="margin-bottom:16px;">
    <label>文件信息</label>
    <div id="file-info" style="font-size:0.85em;color:#a0a0b0;">未选择文件</div>
  </div>

  <label>目标语言</label>
  <div class="row">
    <select id="target-lang">
      <option value="Simplified Chinese">简体中文</option>
      <option value="English">English</option>
      <option value="Japanese">日本語</option>
      <option value="Traditional Chinese">繁體中文</option>
    </select>
    <input type="text" id="model-path" value="~/models/Hy-MT2-1.8B-Q8_0.gguf" style="flex:2;">
  </div>

  <div class="progress-section">
    <div class="progress-bar-wrap">
      <div class="progress-bar" id="progress-bar"></div>
      <div class="progress-text" id="progress-label">0%</div>
    </div>
    <div id="status-text">就绪</div>
  </div>

  <div class="buttons">
    <button id="btn-start" onclick="start()">开始</button>
    <button id="btn-pause" onclick="pause()" class="secondary">暂停</button>
    <button id="btn-stop" onclick="stop()" class="secondary">停止</button>
  </div>

  <div class="hw-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="font-weight:700;">硬件状态</span>
      <span id="mode-display"></span>
    </div>
    <div class="hw-grid">
      <div class="hw-item">
        <div class="label">CPU</div>
        <div class="value" id="cpu-val">--%</div>
        <div class="bar"><div class="bar-fill cpu-fill" id="cpu-bar" style="width:0%"></div></div>
      </div>
      <div class="hw-item">
        <div class="label">内存</div>
        <div class="value" id="ram-val">--</div>
        <div class="bar"><div class="bar-fill ram-fill" id="ram-bar" style="width:0%"></div></div>
      </div>
    </div>
    <div style="margin-top:12px;font-size:0.85em;">
      <div>模型: <span id="model-display">--</span></div>
      <div style="color:#a0a0b0;">模式: <span id="mode-display-2">--</span></div>
    </div>
  </div>
</div>

<script>
let polling = null;
let currentDir = '/home/jeffhan';

async function api(url, body) {
  const r = await fetch(url, {
    method: body ? 'POST' : 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

async function poll() {
  const s = await api('/api/state');
  const pct = s.total > 0 ? Math.round(s.progress / s.total * 100) : 0;
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('progress-label').textContent =
    pct + '%  (' + s.progress + '/' + s.total + ')';
  document.getElementById('status-text').textContent = s.message || s.status;

  // buttons
  const running = s.status === 'running';
  const paused = s.status === 'paused';
  document.getElementById('btn-start').style.display =
    (s.status === 'idle' || s.status === 'done' || s.status === 'error') ? '' : 'none';
  document.getElementById('btn-pause').textContent = paused ? '继续' : '暂停';
  document.getElementById('btn-pause').style.display = (running || paused) ? '' : 'none';
  document.getElementById('btn-stop').style.display = (running || paused) ? '' : 'none';

  // HW
  document.getElementById('cpu-val').textContent = s.cpu_percent ? s.cpu_percent + '%' : '--%';
  document.getElementById('cpu-bar').style.width = (s.cpu_percent || 0) + '%';
  document.getElementById('ram-val').textContent =
    s.ram_used_gb ? s.ram_used_gb + 'G / ' + s.ram_total_gb + 'G' : '--';
  document.getElementById('ram-bar').style.width = (s.ram_percent || 0) + '%';
  document.getElementById('model-display').textContent = s.model_name || '--';
  document.getElementById('mode-display-2').textContent =
    s.scanned ? '扫描文档 (OCR + 翻译)' : (s.mode ? '文本文档 (直接翻译)' : '--');
  const badge = s.scanned
    ? '<span class="mode-badge scanned">扫描</span>'
    : (s.mode ? '<span class="mode-badge text">文本</span>' : '');
  document.getElementById('mode-display').innerHTML = badge || '--';

  if (s.status === 'done' || s.status === 'error') {
    document.getElementById('btn-start').style.display = '';
    document.getElementById('btn-pause').style.display = 'none';
    document.getElementById('btn-stop').style.display = 'none';
  }

  if (s.status !== 'running' && s.status !== 'paused') {
    document.getElementById('cpu-val').textContent = '--%';
    document.getElementById('cpu-bar').style.width = '0%';
    document.getElementById('ram-val').textContent = '--';
    document.getElementById('ram-bar').style.width = '0%';
  }
}

function startPolling() {
  if (polling) clearInterval(polling);
  polling = setInterval(poll, 800);
}

async function start() {
  const path = document.getElementById('file-path').value.trim();
  const lang = document.getElementById('target-lang').value;
  const model = document.getElementById('model-path').value.trim();
  if (!path) return alert('请先选择文件');
  const r = await api('/api/start', {input_path: path, target_lang: lang, model_path: model});
  if (!r.ok) return alert(r.error);
  startPolling();
}

async function pause() {
  const s = await api('/api/state');
  if (s.status === 'paused') {
    await api('/api/resume');
    startPolling();
  } else {
    await api('/api/pause');
  }
}

async function stop() {
  await api('/api/stop');
}

async function toggleBrowser() {
  const b = document.getElementById('browser');
  if (b.style.display === 'block') { b.style.display = 'none'; return; }
  b.style.display = 'block';
  await browse(currentDir);
}

async function browse(dir) {
  const r = await api('/api/browse', {path: dir});
  if (!r.ok) return;
  currentDir = r.current;
  document.getElementById('browser-path').textContent = r.current;
  let html = '';
  if (r.parent !== null) {
    html += '<div class="entry dir" onclick="browse(\'' + r.parent + '\')">📁 ..</div>';
  }
  for (const e of r.entries) {
    if (e.type === 'dir') {
      html += '<div class="entry dir" onclick="browse(\'' + e.path + '\')">📁 ' + e.name + '</div>';
    } else {
      html += '<div class="entry epub" onclick="selectFile(\'' + e.path + '\',\'' + e.name + '\')">📄 ' + e.name + ' (' + e.size_mb + 'MB)</div>';
    }
  }
  document.getElementById('browser-entries').innerHTML = html;
}

async function selectFile(path, name) {
  document.getElementById('file-path').value = path;
  document.getElementById('browser').style.display = 'none';
  // get file info
  const r = await api('/api/info', {path: path});
  if (r.ok) {
    const info = r.scanned
      ? '扫描文档 — ' + r.images + ' 页图片 (将使用 OCR)'
      : '文本文档 — ' + r.text_blocks + ' 个文本块';
    document.getElementById('file-info').textContent = '📖 ' + name + ' | ' + info;
  }
}

// Init
document.getElementById('file-path').addEventListener('change', function() {
  const path = this.value.trim();
  if (path) selectFile(path, path.split('/').pop());
});
startPolling();
</script>
</body>
</html>"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5050, help="Web UI port (default: 5050)")
    ap.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = ap.parse_args()

    print(f"EPUB 翻译器 GUI 已启动: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
