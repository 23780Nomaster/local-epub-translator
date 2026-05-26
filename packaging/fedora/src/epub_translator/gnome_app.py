#!/usr/bin/env python3
"""EPUB Translator — GNOME desktop application with GTK4 + Libadwaita."""
import os
import sys
import threading
import time

# Ensure sibling modules are importable (flat imports used throughout)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Gdk  # noqa: E402

import psutil

from epub_handler import EpubBook
from llm_backend import TranslationServer, Translator
from ocr_backend import OCRBackend
from checkpoint import CheckpointManager

DEFAULT_MODEL = os.path.expanduser("~/models/Hy-MT2-1.8B-Q8_0.gguf")

# ── globals ──────────────────────────────────────────────────────
_server: TranslationServer | None = None
_checkpoint: CheckpointManager | None = None
_worker_thread: threading.Thread | None = None
_hw_thread: threading.Thread | None = None
_pause_event = threading.Event()
_pause_event.set()
_lock = threading.Lock()

_state = {
    "status": "idle",          # idle | running | paused | done | error
    "mode": None,              # text | scanned
    "input_path": None,
    "output_path": None,
    "model_path": DEFAULT_MODEL,
    "target_lang": "Simplified Chinese",
    "progress": 0,
    "total": 0,
    "message": "就绪",
    "error": None,
    "scanned": False,
    "cpu_percent": 0.0,
    "ram_percent": 0.0,
    "ram_used_gb": 0.0,
    "ram_total_gb": 0.0,
    "model_name": "",
    "completed_on_resume": 0,
    "start_time": 0.0,
    "eta_seconds": 0,
    "mode_label": "",
}


def _update(**kwargs):
    with _lock:
        _state.update(kwargs)


def _get(key):
    with _lock:
        return _state[key]


def _snapshot(*keys):
    with _lock:
        return tuple(_state[k] for k in keys)


# ── translation workers ──────────────────────────────────────────

def _translation_worker():
    global _server, _checkpoint
    try:
        input_path, scanned = _snapshot("input_path", "scanned")
        book = EpubBook(input_path)
        if scanned:
            _scanned_worker(book)
        else:
            _text_worker(book)
        output_path = _get("output_path")
        book.save(output_path)
        if _checkpoint:
            _checkpoint.delete()
        _update(status="done", message="翻译完成")
        total = _get("total")
        _update(progress=total)
    except Exception as e:
        _update(status="error", message=str(e), error=str(e))
    finally:
        if _server:
            _server.stop()
            _server = None


def _text_worker(book):
    global _server, _checkpoint
    target_lang, model_path = _snapshot("target_lang", "model_path")
    translator = Translator(_server, target_lang=target_lang)
    blocks = list(book.iter_text_blocks())

    _update(total=len(blocks))
    completed_count = 0
    if _checkpoint:
        completed_count = sum(
            1 for it, _, _, idx in blocks
            if _checkpoint.is_completed(it.file_name, idx)
        )
    _update(progress=completed_count, completed_on_resume=completed_count)

    for item, tag, text, idx in blocks:
        _pause_event.wait()
        status = _get("status")
        if status == "idle":
            return

        file_name = item.file_name
        if _checkpoint and _checkpoint.is_completed(file_name, idx):
            translated = _checkpoint.get_translated(file_name, idx)
            book.apply_translation(tag, translated)
            _update(progress=_get("progress") + 1)
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
                    _update(message="重建服务器连接...")
                    _server.stop()
                    mp = _get("model_path")
                    _server = TranslationServer(mp)
                    _server.start()
                    translator.server = _server
                elif attempt == 2:
                    pass
                else:
                    raise

        p = _get("progress") + 1
        total = _get("total")
        _update(progress=p, message=f"正在翻译第 {p}/{total} 块...")


def _scanned_worker(book):
    global _server, _checkpoint
    target_lang = _get("target_lang")
    translator = Translator(_server, target_lang=target_lang)
    ocr = OCRBackend()
    all_images = list(book.iter_all_images(min_height=200))

    _update(total=len(all_images))
    completed_count = 0
    if _checkpoint:
        completed_count = sum(
            1 for it, _, _, _, idx in all_images
            if _checkpoint.is_completed(it.file_name, idx)
        )
    _update(progress=completed_count, completed_on_resume=completed_count)

    for item, img_tag, image_data, mime_type, idx in all_images:
        _pause_event.wait()
        status = _get("status")
        if status == "idle":
            return

        file_name = item.file_name
        if _checkpoint and _checkpoint.is_completed(file_name, idx):
            translated = _checkpoint.get_translated(file_name, idx)
            book.apply_image_translation(img_tag, translated)
            _update(progress=_get("progress") + 1)
            continue

        try:
            ocr_text = ocr.ocr(image_data, mime_type)
            if ocr_text.strip():
                translated = translator.translate(ocr_text)
                book.apply_image_translation(img_tag, translated)
                if _checkpoint:
                    _checkpoint.mark_completed(file_name, idx, translated)
        except Exception:
            pass

        p = _get("progress") + 1
        total = _get("total")
        _update(progress=p, message=f"OCR+翻译第 {p}/{total} 页...")


def _hardware_monitor():
    while True:
        status = _get("status")
        if status not in ("running", "paused"):
            break
        cpu = psutil.cpu_percent(interval=1.0)
        mem = psutil.virtual_memory()
        _update(
            cpu_percent=round(cpu, 1),
            ram_percent=round(mem.percent, 1),
            ram_used_gb=round(mem.used / (1024**3), 1),
            ram_total_gb=round(mem.total / (1024**3), 1),
        )
        time.sleep(0.5)


def _compute_eta():
    total, progress, start_time = _snapshot("total", "progress", "start_time")
    if total <= 0 or progress <= 0 or start_time <= 0:
        return
    elapsed = time.time() - start_time
    if elapsed < 2:
        return
    rate = progress / max(elapsed, 0.001)
    remaining = total - progress
    if rate > 0:
        _update(eta_seconds=int(remaining / rate))


# ── GTK UI ───────────────────────────────────────────────────────

CSS = """
.hw-cpu .fill-block { background-color: @red_3; }
.hw-cpu .empty-block { background-color: alpha(@red_3, 0.2); }
.hw-ram .fill-block { background-color: @orange_3; }
.hw-ram .empty-block { background-color: alpha(@orange_3, 0.2); }
"""


class EpubTranslatorWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("EPUB 翻译器")
        self.set_default_size(560, 720)

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        clamp.set_margin_top(18)
        clamp.set_margin_bottom(24)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page = Adw.PreferencesPage()

        # ── file group ───────────────────────────────────────────
        file_group = Adw.PreferencesGroup()
        file_group.set_title("文件")

        self.file_entry = Gtk.Entry()
        self.file_entry.set_hexpand(True)
        self.file_entry.set_placeholder_text("选择或输入 EPUB 文件路径...")
        self.file_entry.connect("changed", self._on_file_entry_changed)

        browse_btn = Gtk.Button(label="浏览")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.connect("clicked", self._on_browse)

        file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        file_box.append(self.file_entry)
        file_box.append(browse_btn)

        file_row = Adw.ActionRow()
        file_row.set_title("EPUB 文件")
        file_row.add_suffix(file_box)
        file_group.add(file_row)

        self.file_info_label = Gtk.Label(label="未选择文件")
        self.file_info_label.set_wrap(True)
        self.file_info_label.set_xalign(0)
        self.file_info_label.add_css_class("dim-label")
        self.file_info_label.set_margin_start(6)
        file_group.add(self.file_info_label)

        page.add(file_group)

        # ── settings group ───────────────────────────────────────
        settings_group = Adw.PreferencesGroup()
        settings_group.set_title("翻译设置")

        lang_model = Gtk.StringList()
        lang_model.append("简体中文 (Simplified Chinese)")
        lang_model.append("English")
        lang_model.append("日本語 (Japanese)")
        lang_model.append("繁體中文 (Traditional Chinese)")
        self.lang_combo = Gtk.DropDown.new(lang_model)
        self.lang_combo.set_selected(0)
        self.lang_combo.connect("notify::selected", self._on_lang_changed)
        lang_row = Adw.ActionRow()
        lang_row.set_title("目标语言")
        lang_row.add_suffix(self.lang_combo)
        settings_group.add(lang_row)

        self.model_label = Gtk.Label(
            label=os.path.basename(DEFAULT_MODEL)
        )
        self.model_label.set_ellipsize(2)
        self.model_label.set_xalign(0)
        self.model_label.add_css_class("dim-label")
        self.model_label.set_tooltip_text(DEFAULT_MODEL)
        model_row = Adw.ActionRow()
        model_row.set_title("翻译模型")
        model_row.add_suffix(self.model_label)
        settings_group.add(model_row)

        page.add(settings_group)

        # ── progress group ───────────────────────────────────────
        progress_group = Adw.PreferencesGroup()
        progress_group.set_title("进度")

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("0%")
        self.progress_bar.set_valign(Gtk.Align.CENTER)
        progress_group.add(self.progress_bar)

        self.status_label = Gtk.Label(label="就绪")
        self.status_label.set_xalign(0)
        self.status_label.set_margin_start(6)
        self.status_label.add_css_class("dim-label")
        progress_group.add(self.status_label)

        self.eta_label = Gtk.Label(label="")
        self.eta_label.set_xalign(0)
        self.eta_label.set_margin_start(6)
        self.eta_label.add_css_class("dim-label")
        progress_group.add(self.eta_label)

        page.add(progress_group)

        # ── hardware group ───────────────────────────────────────
        hw_group = Adw.PreferencesGroup()
        hw_group.set_title("硬件状态")

        self.cpu_bar = Gtk.LevelBar()
        self.cpu_bar.set_min_value(0)
        self.cpu_bar.set_max_value(100)
        self.cpu_bar.set_valign(Gtk.Align.CENTER)
        self.cpu_bar.add_css_class("hw-cpu")
        cpu_row = Adw.ActionRow()
        cpu_row.set_title("CPU")
        cpu_row.add_suffix(self.cpu_bar)
        hw_group.add(cpu_row)

        self.cpu_value = Gtk.Label(label="--%")
        self.cpu_value.set_xalign(0)
        self.cpu_value.set_margin_start(6)
        self.cpu_value.add_css_class("dim-label")
        hw_group.add(self.cpu_value)

        self.ram_bar = Gtk.LevelBar()
        self.ram_bar.set_min_value(0)
        self.ram_bar.set_max_value(100)
        self.ram_bar.set_valign(Gtk.Align.CENTER)
        self.ram_bar.add_css_class("hw-ram")
        ram_row = Adw.ActionRow()
        ram_row.set_title("内存")
        ram_row.add_suffix(self.ram_bar)
        hw_group.add(ram_row)

        self.ram_value = Gtk.Label(label="--")
        self.ram_value.set_xalign(0)
        self.ram_value.set_margin_start(6)
        self.ram_value.add_css_class("dim-label")
        hw_group.add(self.ram_value)

        self.mode_label2 = Gtk.Label(label="--")
        self.mode_label2.add_css_class("dim-label")
        mode_row = Adw.ActionRow()
        mode_row.set_title("处理模式")
        mode_row.add_suffix(self.mode_label2)
        hw_group.add(mode_row)

        page.add(hw_group)
        content.append(page)

        # ── buttons ──────────────────────────────────────────────
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)

        self.start_btn = Gtk.Button(label="开始翻译")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.add_css_class("pill")
        self.start_btn.connect("clicked", self._on_start)
        btn_box.append(self.start_btn)

        self.pause_btn = Gtk.Button(label="暂停")
        self.pause_btn.add_css_class("pill")
        self.pause_btn.connect("clicked", self._on_pause)
        self.pause_btn.set_visible(False)
        btn_box.append(self.pause_btn)

        self.stop_btn = Gtk.Button(label="停止")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.add_css_class("pill")
        self.stop_btn.connect("clicked", self._on_stop)
        self.stop_btn.set_visible(False)
        btn_box.append(self.stop_btn)

        content.append(btn_box)

        clamp.set_child(content)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)
        self.set_content(toolbar)

        self._poll_source = GLib.timeout_add(500, self._refresh_ui)

    # ── callbacks ────────────────────────────────────────────────

    def _on_file_entry_changed(self, entry):
        path = entry.get_text().strip()
        if path and os.path.exists(path) and path.lower().endswith(".epub"):
            self._load_file_info(path)
        else:
            self.file_info_label.set_label("未选择文件")

    def _on_browse(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("选择 EPUB 文件")
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB 文件 (*.epub)")
        epub_filter.add_pattern("*.epub")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(epub_filter)
        dialog.open(self, None, self._on_file_selected)

    def _on_file_selected(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile:
                path = gfile.get_path()
                self.file_entry.set_text(path)
                self._load_file_info(path)
        except GLib.Error:
            pass

    def _load_file_info(self, path):
        try:
            book = EpubBook(path)
            scanned = book.is_scanned()
            if scanned:
                imgs = len(list(book.iter_all_images(min_height=200)))
                text = f"扫描文档 — {imgs} 页图片 (将使用 OCR)"
            else:
                blocks = len(list(book.iter_text_blocks()))
                text = f"文本文档 — {blocks} 个文本块"
            self.file_info_label.set_label(text)
        except Exception as e:
            self.file_info_label.set_label(f"解析失败: {e}")

    def _on_lang_changed(self, combo, pspec):
        lang_map = {
            0: "Simplified Chinese",
            1: "English",
            2: "Japanese",
            3: "Traditional Chinese",
        }
        _update(target_lang=lang_map[combo.get_selected()])

    def _on_start(self, btn):
        global _server, _worker_thread, _checkpoint, _hw_thread
        input_path = os.path.expanduser(self.file_entry.get_text().strip())
        if not input_path or not os.path.exists(input_path):
            self._show_error("文件不存在")
            return

        model_path = os.path.expanduser(DEFAULT_MODEL)
        if not os.path.exists(model_path):
            self._show_error(f"模型不存在: {model_path}")
            return

        base = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(
            os.path.dirname(input_path) or ".", f"{base}.translated.epub"
        )

        book = EpubBook(input_path)
        scanned = book.is_scanned()
        mode = "scanned" if scanned else "text"
        target_lang = _get("target_lang")

        _checkpoint = CheckpointManager.load(input_path, output_path, mode, target_lang)
        if not _checkpoint:
            _checkpoint = CheckpointManager(input_path, output_path, mode, target_lang)

        model_name = os.path.basename(model_path)
        mode_label = "扫描 (OCR+翻译)" if scanned else "文本 (直接翻译)"
        _update(
            status="running", mode=mode, input_path=input_path,
            output_path=output_path, model_path=model_path,
            target_lang=target_lang, scanned=scanned, progress=0, total=0,
            message="启动翻译服务...", error=None, model_name=model_name,
            completed_on_resume=len(_checkpoint.completed),
            start_time=time.time(), eta_seconds=0,
            mode_label=mode_label,
        )

        _pause_event.set()

        try:
            _server = TranslationServer(model_path)
            _server.start()
        except Exception as e:
            _update(status="error", message=f"启动模型失败: {e}", error=str(e))
            self._show_error(f"启动模型失败: {e}")
            return

        _worker_thread = threading.Thread(target=_translation_worker, daemon=True)
        _worker_thread.start()
        _hw_thread = threading.Thread(target=_hardware_monitor, daemon=True)
        _hw_thread.start()

    def _on_pause(self, btn):
        status = _get("status")
        if status == "paused":
            self._do_resume()
        else:
            self._do_pause()

    def _do_pause(self):
        global _server
        _pause_event.clear()
        _update(status="paused", message="已暂停")
        if _checkpoint:
            _checkpoint.save()
        if _server:
            _server.stop()
            _server = None

    def _do_resume(self):
        global _server, _worker_thread, _hw_thread
        _pause_event.set()
        _update(status="running", message="恢复翻译...")
        try:
            mp = _get("model_path")
            _server = TranslationServer(mp)
            _server.start()
        except Exception as e:
            _update(status="error", message=f"重启模型失败: {e}", error=str(e))
            self._show_error(f"重启模型失败: {e}")
            return
        _worker_thread = threading.Thread(target=_translation_worker, daemon=True)
        _worker_thread.start()
        _hw_thread = threading.Thread(target=_hardware_monitor, daemon=True)
        _hw_thread.start()

    def _on_stop(self, btn):
        global _server
        _pause_event.set()
        _update(status="idle", message="已停止")
        if _checkpoint:
            _checkpoint.save()
        if _server:
            _server.stop()
            _server = None

    def _show_error(self, msg):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="错误",
            body=msg,
        )
        dialog.add_response("ok", "确定")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present()

    def _update_buttons(self):
        status = _get("status")
        active = status in ("running", "paused")
        idle_state = status in ("idle", "done", "error")

        self.start_btn.set_visible(idle_state)
        self.pause_btn.set_visible(active)
        self.stop_btn.set_visible(active)

        if status == "paused":
            self.pause_btn.set_label("继续")
        else:
            self.pause_btn.set_label("暂停")

    def _refresh_ui(self):
        status, total, progress, message = _snapshot(
            "status", "total", "progress", "message"
        )

        if total > 0:
            fraction = progress / total
            pct = int(fraction * 100)
            self.progress_bar.set_fraction(fraction)
            self.progress_bar.set_text(f"{pct}%  ({progress}/{total})")
        else:
            self.progress_bar.set_fraction(0.0)
            self.progress_bar.set_text("0%")

        self.status_label.set_label(message or status)

        _compute_eta()
        eta = _get("eta_seconds")
        if status == "running" and eta > 0:
            if eta < 60:
                self.eta_label.set_label(f"预计剩余: 约 {eta} 秒")
            elif eta < 3600:
                self.eta_label.set_label(f"预计剩余: 约 {eta // 60} 分 {eta % 60} 秒")
            else:
                h = eta // 3600
                m = (eta % 3600) // 60
                self.eta_label.set_label(f"预计剩余: 约 {h} 小时 {m} 分")
        elif status == "done":
            self.eta_label.set_label("已完成")
        elif status == "error":
            self.eta_label.set_label("发生错误")
        else:
            self.eta_label.set_label("")

        self._update_buttons()

        cpu, ram_pct, ram_used, ram_total = _snapshot(
            "cpu_percent", "ram_percent", "ram_used_gb", "ram_total_gb"
        )
        if status in ("running", "paused"):
            self.cpu_bar.set_value(cpu)
            self.cpu_value.set_label(f"{cpu}%")
            self.ram_bar.set_value(ram_pct)
            self.ram_value.set_label(f"{ram_used}G / {ram_total}G")
        else:
            self.cpu_bar.set_value(0)
            self.cpu_value.set_label("--%")
            self.ram_bar.set_value(0)
            self.ram_value.set_label("--")

        mode_label = _get("mode_label")
        if mode_label:
            self.mode_label2.set_label(mode_label)
        model_name = _get("model_name")
        if model_name:
            self.model_label.set_label(model_name)

        return True


class EpubTranslatorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.voyager.epubtranslator",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = EpubTranslatorWindow(application=app)
        win.present()


def main():
    app = EpubTranslatorApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
