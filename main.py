"""示波器音乐播放器 · 启动器

两种播放模式:
  1) 单窗口模式 —— 一个窗口里渲染磷光辉光示波器 (流畅、低占用)
  2) 粒子多窗口模式 —— 把示波器拆成一堆会实时游动的小窗口铺在桌面 (视觉效果强但吃性能)

安全停止: 全程注册全局热键 Ctrl+Alt+Q/ESC, 任意时刻一键停止退出。
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from audio_engine import AudioEngine
from hotkey import HOTKEY_NAME, start_hotkey
from i18n import t

AUDIO_EXTS = (".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif")
SKIP_SCAN_DIRS = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode"}


def launch(cfg: dict) -> bool:
    """启动播放。返回 True 表示用户触发了全局退出热键 (Ctrl+Alt+Q), 应退出程序;
    返回 False 表示 ESC / 关闭窗口, 应回到 GUI。"""
    stop_event = threading.Event()
    quit_event = threading.Event()   # 仅 Ctrl+Alt+Q 触发
    start_hotkey(quit_event)         # 热键 -> quit_event

    def _watch_quit():
        quit_event.wait()
        stop_event.set()             # 同时通知渲染器停下来
    threading.Thread(target=_watch_quit, daemon=True).start()

    engine = AudioEngine(cfg["file"], scope_size=2048,
                         blocksize=512 if cfg["mode"] == "single" else 1024)
    engine.loop = cfg["loop"]

    layout = _layout_windows(cfg)

    # ---- 进度窗口 (所有播放模式都有) ----
    from progress_window import ProgressWindow
    progress_window = ProgressWindow(engine, cfg["file"], geometry=layout["progress_geometry"])

    # ---- 调试面板 (可选) — 仅单窗口模式有效 ----
    debug_panel = None
    params = None
    if cfg["mode"] == "single":
        # 不管开不开调试面板, 都先准备 params 给渲染线程读
        from debug_panel import make_params
        params = make_params({
            "show_fps":  cfg["show_fps"],
            "show_spec": cfg["show_spec"],
        })
        if cfg.get("debug"):
            from debug_panel import DebugPanel
            debug_panel = DebugPanel(params, geometry=layout["debug_geometry"])

    try:
        if cfg["mode"] == "single":
            from single_window import run_single_window
            run_single_window(engine, stop_event,
                              width=cfg["res"], height=cfg["res"],
                              density=cfg["density"], color=cfg["color"],
                              show_fps=cfg["show_fps"], show_spec=cfg["show_spec"],
                              backend=cfg.get("backend", "auto"),
                              params=params,
                              window_pos=layout["render_pos"])
        else:
            from particle_windows import run_particles
            run_particles(engine, stop_event,
                          count=cfg["count"], cell=cfg["cell"],
                          color=cfg["color"])
    finally:
        stop_event.set()
        engine.stop()
        if debug_panel is not None:
            debug_panel.close()
            debug_panel.wait_closed(timeout=3.0)
        progress_window.close()
        progress_window.wait_closed(timeout=3.0)

    return quit_event.is_set()


def _screen_w() -> int:
    """取主屏宽度。失败就给个保守值。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetSystemMetrics(0))
    except Exception:
        return 1920


def _screen_h() -> int:
    """取主屏高度。失败就给个保守值。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetSystemMetrics(1))
    except Exception:
        return 1080


def _layout_windows(cfg: dict) -> dict:
    """根据播放模式/调试面板计算 pygame 窗口和进度窗口位置。"""
    sw, sh = _screen_w(), _screen_h()
    progress_h = 140
    margin = 8
    if cfg["mode"] == "single":
        size = int(cfg["res"])
        if cfg.get("debug"):
            dbg_w, dbg_h = 320, 540
            dbg_x, dbg_y = max(0, sw - 360), 80
            render_x = max(20, (dbg_x - size) // 2)
            render_y = max(40, min(80, sh - size - progress_h - 60))
            prog_y = min(dbg_y + dbg_h + margin, max(0, sh - progress_h - 48))
            return {
                "render_pos": (render_x, render_y),
                "debug_geometry": f"{dbg_w}x{dbg_h}+{dbg_x}+{dbg_y}",
                "progress_geometry": f"{dbg_w}x{progress_h}+{dbg_x}+{prog_y}",
            }
        render_x = max(20, (sw - size) // 2)
        render_y = max(40, (sh - size - progress_h - 80) // 2)
        prog_y = min(render_y + size + margin, max(0, sh - progress_h - 48))
        return {
            "render_pos": (render_x, render_y),
            "debug_geometry": None,
            "progress_geometry": f"{size}x{progress_h}+{render_x}+{prog_y}",
        }
    prog_w = min(720, max(360, sw - 160))
    prog_x = max(0, (sw - prog_w) // 2)
    prog_y = max(0, sh - progress_h - 72)
    return {
        "render_pos": None,
        "debug_geometry": None,
        "progress_geometry": f"{prog_w}x{progress_h}+{prog_x}+{prog_y}",
    }


# ----------------------- 启动器界面 -----------------------
class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = None
        root.title(t("app_title"))
        root.configure(bg="#0b0f0b")
        root.resizable(False, False)

        BG      = "#0d1410"
        BG2     = "#141f19"
        BG3     = "#1c2e24"
        FG      = "#8EFFA8"
        FG_DIM  = "#3d7a52"     # 禁用态文字
        ACCENT  = "#2aff7c"
        SEL_BG  = "#1a3d28"     # 选中/激活背景
        ENTRY   = "#0f1c16"
        BORDER  = "#1e3828"

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # 全局基础
        style.configure(".",
                        background=BG, foreground=FG,
                        fieldbackground=ENTRY, selectbackground=SEL_BG,
                        selectforeground=ACCENT, troughcolor=BG2,
                        bordercolor=BORDER, darkcolor=BG2, lightcolor=BG3,
                        insertcolor=ACCENT, font=("Segoe UI", 10))

        # Frame / Label
        style.configure("TFrame",  background=BG)
        style.configure("TLabel",  background=BG, foreground=FG)
        style.map("TLabel",
                  background=[("disabled", BG)],
                  foreground=[("disabled", FG_DIM)])

        # Button
        style.configure("TButton", background=BG3, foreground=ACCENT,
                        bordercolor=BORDER, focuscolor=SEL_BG, padding=4)
        style.map("TButton",
                  background=[("active", SEL_BG), ("pressed", BG2)],
                  foreground=[("active", "#ffffff"), ("disabled", FG_DIM)],
                  bordercolor=[("active", ACCENT)])

        # Radiobutton / Checkbutton
        for w in ("TRadiobutton", "TCheckbutton"):
            style.configure(w, background=BG, foreground=FG,
                            indicatorbackground=ENTRY, indicatorforeground=ACCENT,
                            focuscolor=BG)
            style.map(w,
                      background=[("active", BG), ("disabled", BG)],
                      foreground=[("active", ACCENT), ("disabled", FG_DIM)],
                      indicatorbackground=[("selected", SEL_BG),
                                           ("active",   BG3),
                                           ("disabled", BG2)],
                      indicatorforeground=[("selected", ACCENT),
                                           ("disabled", FG_DIM)])

        # Entry — clam 的 fieldbackground 在 disabled 下被系统覆盖,
        # 只能用固定色并在 map 里覆盖 lightcolor/darkcolor 骗过主题引擎
        style.configure("TEntry", fieldbackground=ENTRY, foreground=FG,
                        insertcolor=ACCENT, bordercolor=BORDER,
                        lightcolor=ENTRY, darkcolor=ENTRY)
        style.map("TEntry",
                  foreground=[("disabled", FG_DIM)],
                  fieldbackground=[("disabled", ENTRY),
                                   ("!disabled", ENTRY)],
                  lightcolor=[("disabled", ENTRY), ("!disabled", ENTRY)],
                  darkcolor=[("disabled", ENTRY), ("!disabled", ENTRY)],
                  bordercolor=[("focus", ACCENT)])

        # Combobox — 音乐文件可搜索下拉框
        style.configure("TCombobox", fieldbackground=ENTRY, foreground=FG,
                        arrowcolor=FG, bordercolor=BORDER, insertcolor=ACCENT,
                        lightcolor=ENTRY, darkcolor=ENTRY)
        style.map("TCombobox",
                  foreground=[("disabled", FG_DIM)],
                  fieldbackground=[("disabled", ENTRY),
                                   ("!disabled", ENTRY)],
                  lightcolor=[("disabled", ENTRY), ("!disabled", ENTRY)],
                  darkcolor=[("disabled", ENTRY), ("!disabled", ENTRY)],
                  arrowcolor=[("disabled", FG_DIM), ("active", ACCENT)],
                  bordercolor=[("focus", ACCENT)])

        # Spinbox — 同理
        style.configure("TSpinbox", fieldbackground=ENTRY, foreground=FG,
                        arrowcolor=FG, bordercolor=BORDER, insertcolor=ACCENT,
                        lightcolor=ENTRY, darkcolor=ENTRY)
        style.map("TSpinbox",
                  foreground=[("disabled", FG_DIM)],
                  fieldbackground=[("disabled", ENTRY),
                                   ("!disabled", ENTRY)],
                  lightcolor=[("disabled", ENTRY), ("!disabled", ENTRY)],
                  darkcolor=[("disabled", ENTRY), ("!disabled", ENTRY)],
                  arrowcolor=[("disabled", FG_DIM)],
                  bordercolor=[("focus", ACCENT)])

        root.configure(bg=BG)

        pad = dict(padx=12, pady=6, sticky="w")
        frm = ttk.Frame(root, padding=16)
        frm.grid()

        ttk.Label(frm, text=t("launcher_title"), font=("Segoe UI", 16, "bold")
                  ).grid(row=0, column=0, columnspan=3, pady=(0, 10))

        # 文件: 可输入、可下拉、可模糊搜索
        ttk.Label(frm, text=t("music_file")).grid(row=1, column=0, **pad)
        self.file_var = tk.StringVar()
        self.audio_paths: list[str] = []
        self.audio_display_to_path: dict[str, str] = {}
        self.selected_file_path = ""
        self._scan_generation = 0
        self.file_combo = ttk.Combobox(frm, textvariable=self.file_var, width=34)
        self.file_combo.grid(row=1, column=1, **pad)
        self.file_combo.bind("<KeyRelease>", self._on_file_search)
        self.file_combo.bind("<<ComboboxSelected>>", self._on_file_selected)
        ttk.Button(frm, text=t("browse"), command=self._pick).grid(row=1, column=2, **pad)

        # 拖拽支持 (tkinterdnd2 可选; 没有则静默跳过)
        try:
            frm.drop_target_register("DND_Files")
            frm.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        # 模式
        ttk.Label(frm, text=t("mode")).grid(row=2, column=0, **pad)
        self.mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(frm, text=t("mode_single"), value="single",
                        variable=self.mode_var, command=self._sync
                        ).grid(row=2, column=1, **pad)
        ttk.Radiobutton(frm, text=t("mode_particles"), value="particles",
                        variable=self.mode_var, command=self._sync
                        ).grid(row=2, column=2, **pad)

        # 单窗口: 分辨率
        self.res_var = tk.IntVar(value=900)
        self.res_row = ttk.Frame(frm)
        ttk.Label(self.res_row, text=t("resolution")).pack(side="left", padx=(0, 8))
        for r in (600, 800, 900, 1200):
            ttk.Radiobutton(self.res_row, text=f"{r}px", value=r,
                            variable=self.res_var).pack(side="left", padx=4)
        self.res_row.grid(row=3, column=0, columnspan=3, **pad)

        # 粒子: 数量 + 大小
        self.part_row = ttk.Frame(frm)
        ttk.Label(self.part_row, text=t("particle_count")).pack(side="left")
        self.count_var = tk.IntVar(value=120)
        ttk.Spinbox(self.part_row, from_=16, to=600, increment=8, width=6,
                    textvariable=self.count_var).pack(side="left", padx=8)
        ttk.Label(self.part_row, text=t("particle_size")).pack(side="left", padx=(12, 0))
        self.cell_var = tk.IntVar(value=8)
        ttk.Spinbox(self.part_row, from_=3, to=40, increment=1, width=5,
                    textvariable=self.cell_var).pack(side="left", padx=8)
        self.part_row.grid(row=4, column=0, columnspan=3, **pad)

        # 单窗口抽稀
        self.dens_row = ttk.Frame(frm)
        ttk.Label(self.dens_row, text=t("density")).pack(side="left")
        self.dens_var = tk.IntVar(value=1)
        ttk.Spinbox(self.dens_row, from_=1, to=8, width=5,
                    textvariable=self.dens_var).pack(side="left", padx=8)
        self.dens_row.grid(row=5, column=0, columnspan=3, **pad)

        # 渲染后端 (单窗口才生效)
        self.backend_row = ttk.Frame(frm)
        ttk.Label(self.backend_row, text=t("backend")).pack(side="left", padx=(0, 8))
        self.backend_var = tk.StringVar(value="auto")
        ttk.Radiobutton(self.backend_row, text=t("backend_auto"), value="auto",
                        variable=self.backend_var).pack(side="left", padx=4)
        ttk.Radiobutton(self.backend_row, text=t("backend_gpu"), value="gpu",
                        variable=self.backend_var).pack(side="left", padx=4)
        ttk.Radiobutton(self.backend_row, text=t("backend_cpu"), value="cpu",
                        variable=self.backend_var).pack(side="left", padx=4)
        self.backend_row.grid(row=6, column=0, columnspan=3, **pad)

        # 颜色选择
        self.color_var = tk.StringVar(value="#3CFF96")
        color_row = ttk.Frame(frm)
        ttk.Label(color_row, text=t("phosphor_color")).pack(side="left", padx=(0, 8))
        self._color_btn = tk.Button(
            color_row, width=4, relief="flat", cursor="hand2",
            bg=self.color_var.get(), command=self._pick_color)
        self._color_btn.pack(side="left")
        self._color_hex_lbl = ttk.Label(color_row,
                                        text=self.color_var.get(),
                                        foreground=ACCENT)
        self._color_hex_lbl.pack(side="left", padx=8)
        color_row.grid(row=7, column=0, columnspan=3, **pad)

        # 循环 / 显示 / 调试开关
        self.loop_var      = tk.BooleanVar(value=True)
        self.show_fps_var  = tk.BooleanVar(value=True)
        self.show_spec_var = tk.BooleanVar(value=True)
        self.debug_var     = tk.BooleanVar(value=False)
        chk_row = ttk.Frame(frm)
        ttk.Checkbutton(chk_row, text=t("loop"), variable=self.loop_var
                        ).pack(side="left", padx=(0,16))
        ttk.Checkbutton(chk_row, text=t("show_fps"), variable=self.show_fps_var
                        ).pack(side="left", padx=(0,16))
        ttk.Checkbutton(chk_row, text=t("show_spec"), variable=self.show_spec_var
                        ).pack(side="left", padx=(0,16))
        self.debug_chk = ttk.Checkbutton(chk_row, text=t("debug_mode"),
                                         variable=self.debug_var)
        self.debug_chk.pack(side="left")
        chk_row.grid(row=8, column=0, columnspan=3, **pad)

        ttk.Label(frm, text=t("hotkey_hint", hotkey=HOTKEY_NAME),
                  foreground="#FFD479").grid(row=9, column=0, columnspan=3, pady=(4, 8))

        ttk.Button(frm, text=t("start_play"), command=self._start
                   ).grid(row=10, column=0, columnspan=3, pady=8, ipadx=20, ipady=4)

        self._scan_audio_dir(os.getcwd())
        self._sync()

    def _scan_audio_dir(self, root_dir: str):
        """后台递归扫描目录下的音频文件并刷新下拉列表，避免阻塞 Tk 主线程。"""
        if not root_dir or not os.path.isdir(root_dir):
            return
        self._scan_generation += 1
        generation = self._scan_generation
        root_dir = os.path.abspath(root_dir)

        def _worker():
            paths: list[str] = []
            max_files = 2000
            try:
                for cur, dirs, files in os.walk(root_dir):
                    dirs[:] = [d for d in dirs if d not in SKIP_SCAN_DIRS]
                    for name in files:
                        if name.lower().endswith(AUDIO_EXTS):
                            paths.append(os.path.abspath(os.path.join(cur, name)))
                            if len(paths) >= max_files:
                                raise StopIteration
            except StopIteration:
                pass
            except Exception:
                return
            paths = sorted(dict.fromkeys(paths), key=lambda p: (os.path.basename(p).lower(), p.lower()))

            def _apply():
                # 如果用户又触发了新扫描，旧结果丢弃
                if generation != self._scan_generation:
                    return
                self.audio_paths = paths
                self._update_file_values(self.file_var.get())

            try:
                self.root.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _make_audio_display_items(self, paths: list[str]) -> list[str]:
        counts: dict[str, int] = {}
        for p in paths:
            name = os.path.basename(p)
            counts[name] = counts.get(name, 0) + 1

        current_value = self.file_var.get().strip()
        current_path = self.selected_file_path
        self.audio_display_to_path.clear()
        if current_value and current_path:
            self.audio_display_to_path[current_value] = current_path
        items: list[str] = []
        for p in paths:
            name = os.path.basename(p)
            if counts[name] > 1:
                parent = os.path.basename(os.path.dirname(p))
                label = f"{name}  ({parent})"
            else:
                label = name
            # 极少数同名且父目录也同名的情况，追加序号保证 key 唯一
            base_label = label
            n = 2
            while label in self.audio_display_to_path:
                label = f"{base_label} #{n}"
                n += 1
            self.audio_display_to_path[label] = p
            items.append(label)
        return items

    def _update_file_values(self, query: str = ""):
        q = (query or "").strip().lower()
        if q:
            paths = [p for p in self.audio_paths
                     if q in p.lower() or q in os.path.basename(p).lower()]
        else:
            paths = self.audio_paths
        self.file_combo.configure(values=self._make_audio_display_items(paths[:300]))

    def _on_file_search(self, event=None):
        # 导航键/确认键不触发过滤，避免干扰 Combobox 自身选择行为
        if event is not None and event.keysym in {"Up", "Down", "Return", "Escape", "Tab"}:
            return
        self._update_file_values(self.file_var.get())

    def _on_file_selected(self, event=None):
        value = self.file_var.get()
        path = self.audio_display_to_path.get(value, value)
        if path:
            self.selected_file_path = path
            self.file_var.set(value if value in self.audio_display_to_path else os.path.basename(path))
            self._scan_audio_dir(os.path.dirname(path))

    def _pick(self):
        path = filedialog.askopenfilename(
            title=t("select_music_title"),
            filetypes=[(t("filetype_audio"), "*.wav *.flac *.ogg *.mp3 *.aiff *.aif"),
                       (t("filetype_all"), "*.*")])
        if path:
            self.selected_file_path = path
            self.file_var.set(os.path.basename(path))
            self._scan_audio_dir(os.path.dirname(path))

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")   # tkinterdnd2 在空格路径外套花括号
        self.selected_file_path = path
        self.file_var.set(os.path.basename(path))
        self._scan_audio_dir(os.path.dirname(path))

    def _pick_color(self):
        result = colorchooser.askcolor(color=self.color_var.get(),
                                       title=t("select_color_title"))
        if result and result[1]:
            c = result[1]
            self.color_var.set(c)
            self._color_btn.configure(bg=c)
            self._color_hex_lbl.configure(text=c)

    def _sync(self):
        single = self.mode_var.get() == "single"
        for w in self.res_row.winfo_children():
            if not isinstance(w, ttk.Label):
                w.configure(state="normal" if single else "disabled")
        for w in self.dens_row.winfo_children():
            if not isinstance(w, ttk.Label):
                w.configure(state="normal" if single else "disabled")
        for w in self.backend_row.winfo_children():
            if not isinstance(w, ttk.Label):
                w.configure(state="normal" if single else "disabled")
        # 调试面板只对单窗口模式有意义 (粒子模式没参数可调)
        self.debug_chk.configure(state="normal" if single else "disabled")
        for w in self.part_row.winfo_children():
            if not isinstance(w, ttk.Label):
                w.configure(state="disabled" if single else "normal")

    def _resolve_file_path(self) -> str:
        value = self.file_var.get().strip()
        if value in self.audio_display_to_path:
            return self.audio_display_to_path[value]
        if self.selected_file_path:
            name = os.path.basename(self.selected_file_path)
            # 下拉框可能显示 "文件名  (父目录)" 或 "文件名 #2"，但真实路径保存在 selected_file_path
            if value == name or value.startswith(f"{name}  (") or value.startswith(f"{name} #"):
                return self.selected_file_path
        return value

    def _start(self):
        file_path = self._resolve_file_path()
        if not file_path:
            messagebox.showwarning(t("warning_title"), t("warning_no_file"))
            return
        self.cfg = {
            "file": file_path,
            "mode": "single" if self.mode_var.get() == "single" else "particles",
            "res": self.res_var.get(),
            "density": self.dens_var.get(),
            "count": self.count_var.get(),
            "cell": self.cell_var.get(),
            "loop": self.loop_var.get(),
            "show_fps": self.show_fps_var.get(),
            "show_spec": self.show_spec_var.get(),
            "color": self.color_var.get(),
            "backend": self.backend_var.get(),
            "debug": self.debug_var.get(),
        }
        self.root.destroy()


def main():
    while True:
        root = tk.Tk()
        app = Launcher(root)
        root.mainloop()
        if app.cfg is None:   # 用户直接关闭了启动器
            return
        try:
            quit_program = launch(app.cfg)
        except Exception as exc:
            quit_program = False
            try:
                r = tk.Tk(); r.withdraw()
                messagebox.showerror(t("error_title"), str(exc))
                r.destroy()
            except Exception:
                print("Error:", exc, file=sys.stderr)
        if quit_program:
            return   # Ctrl+Alt+Q 彻底退出, 不再弹 GUI


if __name__ == "__main__":
    main()
