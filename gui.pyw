# encoding: utf-8
"""
Goldbug GUI — 小红书爆款图片抓取工具（原生桌面窗口）
双击 启动抓取.bat 或直接 python gui.py
"""

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import Tk, Frame, Label, Button, Entry, Checkbutton, Spinbox, BooleanVar, StringVar, IntVar
from tkinter import Text, Scrollbar, messagebox, ttk
from tkinter.font import Font

# High DPI support for 4K displays
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

os.chdir(Path(__file__).parent)

import config
import scraper

# ── 全局状态 ──────────────────────────────────────────────
log_queue: queue.Queue = queue.Queue()
status = {"running": False, "done": False, "downloaded": 0, "start_time": None}
scrape_thread: threading.Thread | None = None


# ── 日志桥接 ─────────────────────────────────────────────
class GUIHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] [{record.levelname}] {msg}")
        except Exception:
            pass


handler = GUIHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
handler.setLevel(logging.DEBUG)
scraper.logger.addHandler(handler)
scraper.logger.propagate = False  # 阻止日志重复传播到 root logger


# ── 抓取线程 ─────────────────────────────────────────────
def _run_scrape():
    global status
    status["running"] = True
    status["done"] = False
    status["downloaded"] = 0
    status["start_time"] = time.time()
    try:
        results = scraper.run()
        status["downloaded"] = len(results)
    except Exception as e:
        scraper.logger.error("抓取异常: %s", e)
    finally:
        status["running"] = False
        status["done"] = True


# ── GUI 应用 ─────────────────────────────────────────────
class GoldbugGUI:
    def __init__(self):
        self.root = Tk()
        self.root.title("Goldbug - 小红书爆款抓取")
        self.root.geometry("920x680")
        self.root.minsize(780, 520)
        self.root.configure(bg="#f0f0f0")

        # 字体
        self.font_mono = Font(family="Consolas", size=10)
        self.font_ui = Font(family="Microsoft YaHei", size=10)
        self.font_title = Font(family="Microsoft YaHei", size=11, weight="bold")

        self._build_config()
        self._build_log()
        self._build_statusbar()

        # 定时刷新
        self._poll_logs()
        self._poll_status()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_placeholder(self, entry, placeholder):
        """给 Entry 添加占位提示文字 + 自动格式化日期"""
        def on_focus_in(e):
            if entry.get() == placeholder:
                entry.delete(0, "end")
                entry.configure(fg="black")
        def on_focus_out(e):
            val = entry.get().strip()
            if not val:
                entry.insert(0, placeholder)
                entry.configure(fg="gray")
            else:
                # 自动格式化：纯数字 → YYYY-MM-DD
                formatted = self._auto_format_date(val)
                if formatted:
                    entry.delete(0, "end")
                    entry.insert(0, formatted)
        if not entry.get():
            entry.insert(0, placeholder)
            entry.configure(fg="gray")
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    def _auto_format_date(self, text):
        """纯数字 → YYYY-MM-DD，失败返回 None"""
        import re
        digits = re.sub(r"\D", "", text)
        if len(digits) == 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if len(digits) == 4:
            from datetime import datetime
            y = str(datetime.now().year)
            return f"{y}-{digits[:2]}-{digits[2:4]}"
        return None

    def _validate_date(self, text):
        """校验日期格式 YYYY-MM-DD，空值返回 True"""
        if not text or not text.strip():
            return True
        text = text.strip()
        if text in ("起始日期", "结束日期"):
            return True
        import re
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            try:
                from datetime import datetime
                datetime.strptime(text, "%Y-%m-%d")
                return True
            except ValueError:
                pass
        return False

    # ── 配置区 ─────────────────────────────────────────────
    def _build_config(self):
        frame = Frame(self.root, bg="#ffffff", padx=16, pady=12)
        frame.pack(fill="x", padx=8, pady=(8, 0))

        Label(frame, text="Goldbug 抓取配置", font=self.font_title, bg="#fff", fg="#ee5a24").grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(0, 8))

        # 关键词
        Label(frame, text="关键词:", font=self.font_ui, bg="#fff").grid(row=1, column=0, sticky="e", padx=(0, 6))
        self.kw_var = StringVar(value=", ".join(config.KEYWORDS))
        self.kw_entry = Entry(frame, textvariable=self.kw_var, font=self.font_ui, width=52)
        self.kw_entry.grid(row=1, column=1, columnspan=5, sticky="we", padx=(0, 8))
        Label(frame, text="逗号分隔", font=("Microsoft YaHei", 9), bg="#fff", fg="#999").grid(
            row=1, column=6, sticky="w")

        # 参数行
        row2 = Frame(frame, bg="#fff")
        row2.grid(row=2, column=0, columnspan=8, sticky="we", pady=(10, 4))

        Label(row2, text="Top N:", font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 4))
        self.topn_var = StringVar(value=str(config.TOP_N))
        self.topn_spin = Spinbox(row2, textvariable=self.topn_var, font=self.font_ui, width=5, from_=1, to=50)
        self.topn_spin.pack(side="left", padx=(0, 12))

        Label(row2, text="滚动:", font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 4))
        self.scroll_var = StringVar(value=str(config.SCROLL_TIMES))
        self.scroll_spin = Spinbox(row2, textvariable=self.scroll_var, font=self.font_ui, width=5, from_=1, to=10)
        self.scroll_spin.pack(side="left", padx=(0, 12))

        Label(row2, text="下载模式:", font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 4))
        self.mode_var = StringVar(value=config.DOWNLOAD_MODE)
        ttk.Combobox(row2, textvariable=self.mode_var, values=["all", "cover"], state="readonly",
                     font=self.font_ui, width=6).pack(side="left", padx=(0, 12))

        Label(row2, text="日期:", font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 4))
        self.date_start_var = StringVar(value=config.DATE_FILTER_START or "")
        self.date_start_entry = Entry(row2, textvariable=self.date_start_var, font=self.font_ui, width=10,
                                      fg="gray")
        self.date_start_entry.pack(side="left")
        self._setup_placeholder(self.date_start_entry, "起始日期")
        Label(row2, text="~", font=self.font_ui, bg="#fff").pack(side="left", padx=(2, 2))
        self.date_end_var = StringVar(value=config.DATE_FILTER_END or "")
        self.date_end_entry = Entry(row2, textvariable=self.date_end_var, font=self.font_ui, width=10,
                                    fg="gray")
        self.date_end_entry.pack(side="left", padx=(0, 12))
        self._setup_placeholder(self.date_end_entry, "结束日期")

        self.headless_var = BooleanVar(value=config.HEADLESS)
        Checkbutton(row2, text="无头模式", variable=self.headless_var,
                    font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 12))

        row3 = Frame(frame, bg="#fff")
        row3.grid(row=3, column=0, columnspan=8, sticky="w", pady=(4, 0))
        self.low_freq_var = BooleanVar(value=config.LOW_FREQ_MODE)
        Checkbutton(row3, text="低频模式", variable=self.low_freq_var,
                    font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 12))
        self.semi_auto_var = BooleanVar(value=config.SEMI_AUTO_MODE)
        Checkbutton(row3, text="半自动", variable=self.semi_auto_var,
                    font=self.font_ui, bg="#fff").pack(side="left", padx=(0, 12))
        Label(row3, text="低频=慢速限量仅封面 · 半自动=每篇需确认",
              font=("Microsoft YaHei", 9), bg="#fff", fg="#999").pack(side="left")

        # 按钮
        self.btn_start = Button(row2, text="▶  开始抓取", font=self.font_ui, bg="#ee5a24", fg="#fff",
                                relief="flat", padx=18, pady=2, cursor="hand2",
                                command=self._start)
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_stop = Button(row2, text="■ 停止", font=self.font_ui, bg="#e74c3c", fg="#fff",
                               relief="flat", padx=14, pady=2, cursor="hand2",
                               command=self._stop, state="disabled")

        self.btn_open = Button(row2, text="📁 打开目录", font=self.font_ui, bg="#3498db", fg="#fff",
                               relief="flat", padx=12, pady=2, cursor="hand2",
                               command=self._open_dir)
        self.btn_open.pack(side="right", padx=(0, 0))
        self.btn_stop.pack(side="right", padx=(0, 8))

        # 让 Entry 自适应宽度
        frame.columnconfigure(1, weight=1)

    # ── 日志区 ─────────────────────────────────────────────
    def _build_log(self):
        frame = Frame(self.root, bg="#1e1e1e")
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        self.log_text = Text(frame, bg="#1e1e1e", fg="#d4d4d4", insertbackground="#fff",
                             font=self.font_mono, wrap="word", relief="flat", padx=10, pady=8,
                             state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = Scrollbar(frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # 颜色标签
        self.log_text.tag_configure("INFO", foreground="#61afef")
        self.log_text.tag_configure("WARNING", foreground="#e5c07b")
        self.log_text.tag_configure("ERROR", foreground="#e06c75")
        self.log_text.tag_configure("DIM", foreground="#666")

        self._append_log("等待启动...", "DIM")

    # ── 状态栏 ─────────────────────────────────────────────
    def _build_statusbar(self):
        bar = Frame(self.root, bg="#e0e0e0", height=28)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.status_label = Label(bar, text="就绪", font=("Microsoft YaHei", 9),
                                  bg="#e0e0e0", fg="#333")
        self.status_label.pack(side="left", padx=12)

        self.count_label = Label(bar, text="", font=("Microsoft YaHei", 9),
                                 bg="#e0e0e0", fg="#666")
        self.count_label.pack(side="right", padx=12)

    # ── 日志输出 ───────────────────────────────────────────
    def _append_log(self, text, tag=None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_logs(self):
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                # 检测日志级别着色
                if "[ERROR]" in msg:
                    tag = "ERROR"
                elif "[WARNING]" in msg:
                    tag = "WARNING"
                elif "[INFO]" in msg:
                    tag = "INFO"
                else:
                    tag = None
                self._append_log(msg, tag)
            except queue.Empty:
                break
        self.root.after(200, self._poll_logs)

    def _poll_status(self):
        global status
        if status["running"]:
            elapsed = int(time.time() - status["start_time"]) if status["start_time"] else 0
            self.status_label.configure(text=f"抓取中...  {elapsed // 60}:{elapsed % 60:02d}")
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        elif status["done"]:
            self.status_label.configure(text=f"完成 · 共 {status['downloaded']} 张图片")
            self.count_label.configure(text=f"已下载 {status['downloaded']} 张")
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            status["done"] = False  # 只显示一次
        else:
            pass  # 保持现有状态

        self.root.after(1000, self._poll_status)

    # ── 操作 ───────────────────────────────────────────────
    def _semi_auto_confirm(self, note, index, total):
        result = ["skip"]
        event = threading.Event()
        title = note.get("title", "")[:40]
        prompt = f"[{index}/{total}]  {note.get('likes', 0)}赞\n{title}"

        def ask():
            ans = messagebox.askyesnocancel("半自动确认", f"{prompt}\n\n是=下载  否=跳过  取消=停止")
            if ans is True:
                result[0] = "download"
            elif ans is False:
                result[0] = "skip"
            else:
                result[0] = "stop"
            event.set()

        self.root.after(0, ask)
        event.wait(timeout=300)
        return result[0]

    def _start(self):
        global status, scrape_thread
        if status["running"]:
            messagebox.showinfo("提示", "抓取已在运行中")
            return

        # 更新 config
        keywords = [k.strip() for k in self.kw_var.get().split(",") if k.strip()]
        if not keywords:
            messagebox.showwarning("提示", "请至少输入一个关键词")
            return
        config.KEYWORDS = keywords
        config.TOP_N = int(self.topn_spin.get())
        config.SCROLL_TIMES = int(self.scroll_spin.get())
        config.DOWNLOAD_MODE = self.mode_var.get()
        config.HEADLESS = self.headless_var.get()
        date_start = self.date_start_var.get().strip()
        date_end = self.date_end_var.get().strip()
        if not self._validate_date(date_start) or not self._validate_date(date_end):
            messagebox.showwarning("提示", "日期格式错误\n输入 8 位数字即可，如: 20260620 或 0620")
            return
        config.DATE_FILTER_START = date_start if date_start not in ("起始日期", "") else None
        config.DATE_FILTER_END = date_end if date_end not in ("结束日期", "") else None
        config.LOW_FREQ_MODE = self.low_freq_var.get()
        config.SEMI_AUTO_MODE = self.semi_auto_var.get()
        scraper.reset_scrape_abort()
        if config.SEMI_AUTO_MODE:
            scraper.set_semi_auto_confirm(self._semi_auto_confirm)
        else:
            scraper.set_semi_auto_confirm(None)

        # 清空日志
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._append_log(f"开始抓取: {', '.join(keywords)}", "INFO")
        date_info = f"{config.DATE_FILTER_START or '...'} ~ {config.DATE_FILTER_END or '...'}"
        self._append_log(f"Top {config.TOP_N} · 模式: {config.DOWNLOAD_MODE} · 日期: {date_info} · 无头: {config.HEADLESS}", "DIM")
        modes = []
        if config.LOW_FREQ_MODE:
            modes.append("低频")
        if config.SEMI_AUTO_MODE:
            modes.append("半自动")
        if modes:
            self._append_log(f"安全模式: {', '.join(modes)}", "DIM")

        scrape_thread = threading.Thread(target=_run_scrape, daemon=True)
        scrape_thread.start()

    def _stop(self):
        global status
        status["running"] = False
        scraper.request_scrape_abort()
        scraper.logger.warning("用户请求停止")
        self.status_label.configure(text="停止中...")
        self.btn_stop.configure(state="disabled")

    def _open_dir(self):
        save_dir = scraper.setup_image_dir()
        if os.path.exists(save_dir):
            os.startfile(save_dir)
        else:
            os.startfile(config.IMAGE_DIR)

    def _on_close(self):
        global status
        if status["running"]:
            if not messagebox.askyesno("确认退出", "抓取正在进行中，确定退出吗？"):
                return
            status["running"] = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── 入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        GoldbugGUI().run()
    except Exception:
        import traceback
        err = traceback.format_exc()
        # 写入日志文件以便排查
        with open("gui_error.log", "w", encoding="utf-8") as f:
            f.write(err)
        # 弹窗显示错误
        try:
            from tkinter import messagebox
            messagebox.showerror("Goldbug 启动失败", err)
        except Exception:
            pass
        raise
