"""EPUB Translator — tkinter desktop window application."""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psutil
from epub_handler import EpubBook
from llm_backend import TranslationServer, Translator, _EXE_DIR
from ocr_backend import OCRBackend
from checkpoint import CheckpointManager

IS_WINDOWS = sys.platform == "win32"

# ── bundled model detection ────────────────────────────
BUNDLED_MODEL = os.path.join(_EXE_DIR, "models", "Hy-MT2-1.8B-Q8_0.gguf")
USER_MODEL = os.path.join(os.path.expanduser("~"), "models", "Hy-MT2-1.8B-Q8_0.gguf")

LANG_OPTIONS = ["Simplified Chinese", "English", "Japanese", "Traditional Chinese"]
LANG_LABELS = {
    "Simplified Chinese": "简体中文",
    "English": "English",
    "Japanese": "日本語",
    "Traditional Chinese": "繁體中文",
}


class AppState:
    def __init__(self):
        self._lock = threading.Lock()
        self.status = "idle"
        self.mode = None
        self.input_path = None
        self.output_path = None
        self.model_path = None
        self.target_lang = "Simplified Chinese"
        self.progress = 0
        self.total = 0
        self.message = "就绪"
        self.error = None
        self.scanned = False
        self.cpu_percent = 0.0
        self.ram_percent = 0.0
        self.ram_used_gb = 0.0
        self.ram_total_gb = 0.0
        self.model_name = ""
        self.completed_on_resume = 0
        self.start_time = 0.0
        self.eta_seconds = -1

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return {k: getattr(self, k) for k in [
                "status", "mode", "input_path", "output_path", "model_path",
                "target_lang", "progress", "total", "message", "error",
                "scanned", "cpu_percent", "ram_percent", "ram_used_gb",
                "ram_total_gb", "model_name", "completed_on_resume",
                "start_time", "eta_seconds",
            ]}


class EpubTranslatorWindow:
    def __init__(self):
        self.state = AppState()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._worker_thread = None
        self._server = None
        self._checkpoint = None
        self._poll_id = None

        self.root = tk.Tk()
        self.root.title("EPUB Translator")
        self.root.geometry("620x680")
        self.root.resizable(True, True)
        self.root.minsize(520, 600)
        if IS_WINDOWS:
            try:
                self.root.iconbitmap(default="")
            except Exception:
                pass

        self._build_ui()
        self._poll_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # ── UI construction ────────────────────────────────

    def _build_ui(self):
        bg = "#1a1a2e"
        fg = "#e0e0e0"
        accent = "#e94560"
        secondary_bg = "#16213e"
        entry_bg = "#0f3460"
        self.root.configure(bg=bg)

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=accent, font=("", 10, "bold"))
        style.configure("TLabel", background=bg, foreground=fg, font=("", 9))
        style.configure("Accent.TLabel", background=bg, foreground=accent, font=("", 9, "bold"))
        style.configure("Title.TLabel", background=bg, foreground=accent, font=("", 16, "bold"))
        style.configure("TButton", background=accent, foreground="white", borderwidth=0, font=("", 9))
        style.map("TButton", background=[("active", "#d63851")])
        style.configure("Secondary.TButton", background=entry_bg, foreground=fg, font=("", 9))
        style.map("Secondary.TButton", background=[("active", "#1a4a8a")])
        style.configure("TCombobox", fieldbackground=entry_bg, background=entry_bg, foreground=fg)
        style.configure("TProgressbar", background=accent, troughcolor=entry_bg)
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)

        main = ttk.Frame(self.root, padding=20)
        main.pack(fill="both", expand=True)

        # Title
        ttk.Label(main, text="EPUB 翻译器", style="Title.TLabel").pack(pady=(0, 16))

        # ── File section ──
        file_frame = ttk.Labelframe(main, text=" 文件 ", padding=12)
        file_frame.pack(fill="x", pady=(0, 10))

        row1 = ttk.Frame(file_frame)
        row1.pack(fill="x")
        ttk.Label(row1, text="文件路径:").pack(side="left")
        self.file_path_var = tk.StringVar()
        self.file_entry = ttk.Entry(row1, textvariable=self.file_path_var, font=("", 9))
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(row1, text="浏览", style="Secondary.TButton", command=self._browse_file).pack(side="right")

        self.file_info_var = tk.StringVar(value="未选择文件")
        ttk.Label(file_frame, textvariable=self.file_info_var, foreground="#a0a0b0", font=("", 8)).pack(anchor="w", pady=(8, 0))

        # ── Settings section ──
        settings_frame = ttk.Labelframe(main, text=" 设置 ", padding=12)
        settings_frame.pack(fill="x", pady=(0, 10))

        row2 = ttk.Frame(settings_frame)
        row2.pack(fill="x")
        ttk.Label(row2, text="目标语言:").pack(side="left")
        self.lang_var = tk.StringVar(value="Simplified Chinese")
        self.lang_combo = ttk.Combobox(row2, textvariable=self.lang_var,
                                       values=LANG_OPTIONS, state="readonly", width=18)
        self.lang_combo.pack(side="left", padx=(8, 20))
        ttk.Label(row2, text="翻译模型:").pack(side="left")
        self.model_var = tk.StringVar(value=self._detect_model())
        self.model_combo = ttk.Combobox(row2, textvariable=self.model_var,
                                        values=self._model_choices(), width=28)
        self.model_combo.pack(side="left", padx=(8, 0), fill="x", expand=True)

        self.output_var = tk.StringVar(value="--")
        ttk.Label(settings_frame, textvariable=self.output_var, foreground="#a0a0b0", font=("", 8)).pack(anchor="w", pady=(10, 0))

        # ── Progress section ──
        progress_frame = ttk.Labelframe(main, text=" 进度 ", padding=12)
        progress_frame.pack(fill="x", pady=(0, 10))

        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", length=560)
        self.progress_bar.pack(fill="x")

        self.progress_label_var = tk.StringVar(value="0% (0/0)")
        ttk.Label(progress_frame, textvariable=self.progress_label_var, font=("", 9, "bold")).pack(anchor="center", pady=(6, 2))

        self.eta_var = tk.StringVar(value="")
        ttk.Label(progress_frame, textvariable=self.eta_var, foreground="#a0a0b0", font=("", 8)).pack(anchor="center")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(progress_frame, textvariable=self.status_var, foreground="#a0a0b0", font=("", 8)).pack(anchor="center", pady=(2, 0))

        # ── Buttons ──
        btn_frame = ttk.Frame(main)
        btn_frame.pack(pady=(0, 12))
        self.btn_start = ttk.Button(btn_frame, text="开始", command=self._start)
        self.btn_start.pack(side="left", padx=4, ipadx=12)
        self.btn_pause = ttk.Button(btn_frame, text="暂停", style="Secondary.TButton", command=self._pause)
        self.btn_pause.pack(side="left", padx=4, ipadx=12)
        self.btn_stop = ttk.Button(btn_frame, text="停止", style="Secondary.TButton", command=self._stop)
        self.btn_stop.pack(side="left", padx=4, ipadx=12)
        self._update_button_state("idle")

        # ── Hardware section ──
        hw_frame = ttk.Labelframe(main, text=" 硬件状态 ", padding=12)
        hw_frame.pack(fill="x")

        hw_grid = ttk.Frame(hw_frame)
        hw_grid.pack(fill="x")

        # CPU
        cpu_col = ttk.Frame(hw_grid)
        cpu_col.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.cpu_label_var = tk.StringVar(value="CPU: --%")
        ttk.Label(cpu_col, textvariable=self.cpu_label_var, font=("", 9, "bold")).pack(anchor="w")
        self.cpu_bar = ttk.Progressbar(cpu_col, mode="determinate", length=200)
        self.cpu_bar.pack(fill="x", pady=(4, 0))

        # RAM
        ram_col = ttk.Frame(hw_grid)
        ram_col.pack(side="right", fill="x", expand=True, padx=(10, 0))
        self.ram_label_var = tk.StringVar(value="内存: --")
        ttk.Label(ram_col, textvariable=self.ram_label_var, font=("", 9, "bold")).pack(anchor="w")
        self.ram_bar = ttk.Progressbar(ram_col, mode="determinate", length=200)
        self.ram_bar.pack(fill="x", pady=(4, 0))

        info_row = ttk.Frame(hw_frame)
        info_row.pack(fill="x", pady=(10, 0))
        self.model_display_var = tk.StringVar(value="模型: --")
        ttk.Label(info_row, textvariable=self.model_display_var, font=("", 8)).pack(anchor="w")
        self.mode_display_var = tk.StringVar(value="模式: --")
        ttk.Label(info_row, textvariable=self.mode_display_var, foreground="#a0a0b0", font=("", 8)).pack(anchor="w")

    # ── model detection ────────────────────────────────

    def _detect_model(self) -> str:
        if os.path.isfile(BUNDLED_MODEL):
            return BUNDLED_MODEL
        if os.path.isfile(USER_MODEL):
            return USER_MODEL
        return USER_MODEL  # default, user will adjust

    def _model_choices(self) -> list:
        choices = []
        if os.path.isfile(BUNDLED_MODEL):
            choices.append(BUNDLED_MODEL)
        if os.path.isfile(USER_MODEL):
            choices.append(USER_MODEL)
        if not choices:
            choices.append(USER_MODEL)
        return choices

    # ── UI helpers ─────────────────────────────────────

    def _update_button_state(self, st):
        if st in ("idle", "done", "error"):
            self.btn_start.pack(side="left", padx=4, ipadx=12)
            self.btn_start.configure(state="normal")
            self.btn_pause.pack_forget()
            self.btn_stop.pack_forget()
        elif st in ("running", "paused"):
            self.btn_start.pack_forget()
            self.btn_pause.pack(side="left", padx=4, ipadx=12)
            self.btn_pause.configure(text="继续" if st == "paused" else "暂停",
                                     state="normal")
            self.btn_stop.pack(side="left", padx=4, ipadx=12)
            self.btn_stop.configure(state="normal")

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="选择 EPUB 文件",
            filetypes=[("EPUB 文件", "*.epub"), ("所有文件", "*.*")],
        )
        if path:
            self.file_path_var.set(path)
            self._update_file_info(path)

    def _update_file_info(self, path):
        try:
            book = EpubBook(path)
            scanned = book.is_scanned()
            if scanned:
                imgs = len(list(book.iter_all_images(min_height=200)))
                self.file_info_var.set(f"扫描文档 — {imgs} 页图片 (将使用 OCR)")
            else:
                blocks = len(list(book.iter_text_blocks()))
                self.file_info_var.set(f"文本文档 — {blocks} 个文本块")
        except Exception as e:
            self.file_info_var.set(f"无法读取: {e}")

    # ── poll state → update UI ─────────────────────────

    def _poll_state(self):
        s = self.state.snapshot()
        total = s["total"]
        progress = s["progress"]
        pct = int(progress / total * 100) if total > 0 else 0

        self.progress_bar["value"] = pct
        self.progress_label_var.set(f"{pct}% ({progress}/{total})")

        if s["eta_seconds"] > 0:
            m = s["eta_seconds"] // 60
            sec = s["eta_seconds"] % 60
            self.eta_var.set(f"预计剩余: {m}分{sec}秒" if m > 0 else f"预计剩余: {sec}秒")
        else:
            self.eta_var.set("")

        self.status_var.set(s["message"] or s["status"])

        self.cpu_label_var.set(f"CPU: {s['cpu_percent']}%")
        self.cpu_bar["value"] = s["cpu_percent"]
        self.ram_label_var.set(f"内存: {s['ram_used_gb']}G / {s['ram_total_gb']}G" if s["ram_used_gb"] else "内存: --")
        self.ram_bar["value"] = s["ram_percent"]

        self.model_display_var.set(f"模型: {s['model_name'] or '--'}")
        mode_text = "扫描文档 (OCR + 翻译)" if s["scanned"] else ("文本文档 (直接翻译)" if s["mode"] else "--")
        self.mode_display_var.set(f"模式: {mode_text}")

        self.output_var.set(f"输出: {s['output_path'] or '--'}")

        # Button state transitions
        st = s["status"]
        self._update_button_state(st)

        if st not in ("running", "paused"):
            self.cpu_label_var.set("CPU: --%")
            self.cpu_bar["value"] = 0
            self.ram_label_var.set("内存: --")
            self.ram_bar["value"] = 0

        self._poll_id = self.root.after(800, self._poll_state)

    # ── actions ─────────────────────────────────────────

    def _start(self):
        path = self.file_path_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("错误", "请先选择有效的 EPUB 文件")
            return

        model = self.model_var.get().strip()
        if not os.path.isfile(model):
            messagebox.showerror("错误", f"模型文件不存在:\n{model}\n\n请下载 Hy-MT2-1.8B-Q8_0.gguf 放到 models/ 目录")
            return

        lang = self.lang_var.get()

        # Detect output path
        base = os.path.splitext(os.path.basename(path))[0]
        output = os.path.join(os.path.dirname(path) or ".", f"{base}.translated.epub")

        book = EpubBook(path)
        scanned = book.is_scanned()
        mode = "scanned" if scanned else "text"

        self._checkpoint = CheckpointManager.load(path, output, mode, lang)
        if not self._checkpoint:
            self._checkpoint = CheckpointManager(path, output, mode, lang)

        self.state.update(
            status="running", mode=mode,
            input_path=path, output_path=output, model_path=model,
            target_lang=lang, scanned=scanned,
            progress=0, total=0,
            message="启动翻译服务...", error=None,
            model_name=os.path.basename(model),
            completed_on_resume=len(self._checkpoint.completed),
            start_time=time.time(), eta_seconds=-1,
        )

        self._pause_event.set()

        try:
            self._server = TranslationServer(model)
            self._server.start()
        except Exception as e:
            self.state.update(status="error", error=str(e), message=f"启动失败: {e}")
            messagebox.showerror("启动失败", str(e))
            return

        self._worker_thread = threading.Thread(target=self._translation_worker, daemon=True)
        self._worker_thread.start()
        threading.Thread(target=self._hardware_monitor, daemon=True).start()

    def _pause(self):
        s = self.state.snapshot()
        if s["status"] == "paused":
            # Resume
            self._pause_event.set()
            self.state.update(status="running", message="恢复翻译...", start_time=time.time())

            try:
                self._server = TranslationServer(s["model_path"])
                self._server.start()
            except Exception as e:
                self.state.update(status="error", error=str(e), message=f"重启失败: {e}")
                messagebox.showerror("重启失败", str(e))
                return

            self._worker_thread = threading.Thread(target=self._translation_worker, daemon=True)
            self._worker_thread.start()
            threading.Thread(target=self._hardware_monitor, daemon=True).start()
        else:
            # Pause
            self._pause_event.clear()
            self.state.update(status="paused", message="已暂停")
            if self._checkpoint:
                self._checkpoint.save()
            if self._server:
                self._server.stop()

    def _stop(self):
        self._pause_event.set()
        self.state.update(status="idle", message="已停止")
        if self._checkpoint:
            self._checkpoint.save()
        if self._server:
            self._server.stop()
            self._server = None

    def _on_close(self):
        if self._server:
            self._server.stop()
        if self._checkpoint:
            self._checkpoint.save()
        if self._poll_id:
            self.root.after_cancel(self._poll_id)
        self.root.destroy()

    # ── background workers ─────────────────────────────

    def _hardware_monitor(self):
        while self.state.snapshot()["status"] in ("running", "paused"):
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            self.state.update(
                cpu_percent=round(cpu, 1),
                ram_percent=round(mem.percent, 1),
                ram_used_gb=round(mem.used / (1024**3), 1),
                ram_total_gb=round(mem.total / (1024**3), 1),
            )
            # ETA
            s = self.state.snapshot()
            if s["start_time"] > 0 and s["progress"] > s["completed_on_resume"]:
                elapsed = time.time() - s["start_time"]
                done = s["progress"] - s["completed_on_resume"]
                remaining = s["total"] - s["progress"]
                if done > 0 and remaining > 0:
                    self.state.update(eta_seconds=int(remaining * elapsed / done))
                else:
                    self.state.update(eta_seconds=-1)
            else:
                self.state.update(eta_seconds=-1)
            time.sleep(1.0)

    def _translation_worker(self):
        try:
            book = EpubBook(self.state.snapshot()["input_path"])
            if self.state.snapshot()["scanned"]:
                self._scanned_worker(book)
            else:
                self._text_worker(book)
            book.save(self.state.snapshot()["output_path"])
            if self._checkpoint:
                self._checkpoint.delete()
            self.state.update(status="done", message="翻译完成",
                              progress=self.state.snapshot()["total"])
        except Exception as e:
            self.state.update(status="error", error=str(e), message=str(e))
        finally:
            if self._server:
                self._server.stop()
                self._server = None

    def _text_worker(self, book):
        s = self.state.snapshot()
        translator = Translator(self._server, target_lang=s["target_lang"])
        blocks = list(book.iter_text_blocks())

        self.state.update(total=len(blocks))
        completed_count = 0
        if self._checkpoint:
            completed_count = sum(1 for it, _, _, idx in blocks
                                  if self._checkpoint.is_completed(it.file_name, idx))
        self.state.update(progress=completed_count, completed_on_resume=completed_count)

        for item, tag, text, idx in blocks:
            self._pause_event.wait()
            if self.state.snapshot()["status"] == "idle":
                return

            file_name = item.file_name
            if self._checkpoint and self._checkpoint.is_completed(file_name, idx):
                translated = self._checkpoint.get_translated(file_name, idx)
                book.apply_translation(tag, translated)
                self.state.update(progress=self.state.snapshot()["progress"] + 1)
                continue

            for attempt in range(3):
                try:
                    translated = translator.translate(text)
                    book.apply_translation(tag, translated)
                    if self._checkpoint:
                        self._checkpoint.mark_completed(file_name, idx, translated)
                    break
                except Exception as e:
                    if attempt < 2 and ("Connection" in str(e) or "Remote" in str(e)):
                        self.state.update(message="重建服务器连接...")
                        self._server.stop()
                        self._server = TranslationServer(self.state.snapshot()["model_path"])
                        self._server.start()
                        translator.server = self._server
                    elif attempt == 2:
                        pass
                    else:
                        raise

            p = self.state.snapshot()["progress"] + 1
            t = self.state.snapshot()["total"]
            self.state.update(progress=p, message=f"正在翻译第 {p}/{t} 块...")

    def _scanned_worker(self, book):
        s = self.state.snapshot()
        translator = Translator(self._server, target_lang=s["target_lang"])
        ocr = OCRBackend()
        all_images = list(book.iter_all_images(min_height=200))

        self.state.update(total=len(all_images))
        completed_count = 0
        if self._checkpoint:
            completed_count = sum(1 for it, _, _, _, idx in all_images
                                  if self._checkpoint.is_completed(it.file_name, idx))
        self.state.update(progress=completed_count, completed_on_resume=completed_count)

        for item, img_tag, image_data, mime_type, idx in all_images:
            self._pause_event.wait()
            if self.state.snapshot()["status"] == "idle":
                return

            file_name = item.file_name
            if self._checkpoint and self._checkpoint.is_completed(file_name, idx):
                translated = self._checkpoint.get_translated(file_name, idx)
                book.apply_image_translation(img_tag, translated)
                self.state.update(progress=self.state.snapshot()["progress"] + 1)
                continue

            try:
                ocr_text = ocr.ocr(image_data, mime_type)
                if ocr_text.strip():
                    translated = translator.translate(ocr_text)
                    book.apply_image_translation(img_tag, translated)
                    if self._checkpoint:
                        self._checkpoint.mark_completed(file_name, idx, translated)
            except Exception:
                pass

            p = self.state.snapshot()["progress"] + 1
            t = self.state.snapshot()["total"]
            self.state.update(progress=p, message=f"OCR+翻译第 {p}/{t} 页...")


def main():
    EpubTranslatorWindow()


if __name__ == "__main__":
    main()
