"""示波器音乐播放器 · 启动器

两种播放模式:
  1) 单窗口模式 —— 一个窗口里渲染磷光辉光示波器 (流畅、低占用)
  2) 粒子多窗口模式 —— 把示波器拆成一堆会实时游动的小窗口铺在桌面 (视觉效果强但吃性能)

安全停止: 全程注册全局热键 Ctrl+Alt+Q/ESC, 任意时刻一键停止退出。
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from audio_engine import AudioEngine
from hotkey import HOTKEY_NAME, start_hotkey


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
            # 把面板贴在主屏右侧, 留出窗口位置
            geom = "320x540+{x}+80".format(x=max(0, _screen_w() - 360))
            debug_panel = DebugPanel(params, geometry=geom)

    try:
        if cfg["mode"] == "single":
            from single_window import run_single_window
            run_single_window(engine, stop_event,
                              width=cfg["res"], height=cfg["res"],
                              density=cfg["density"], color=cfg["color"],
                              show_fps=cfg["show_fps"], show_spec=cfg["show_spec"],
                              backend=cfg.get("backend", "auto"),
                              params=params)
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

    return quit_event.is_set()


def _screen_w() -> int:
    """取主屏宽度, 用于决定调试面板位置。失败就给个保守值。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetSystemMetrics(0))
    except Exception:
        return 1920


# ----------------------- 启动器界面 -----------------------
class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = None
        root.title("示波器音乐播放器")
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

        ttk.Label(frm, text="示波器音乐播放器", font=("Segoe UI", 16, "bold")
                  ).grid(row=0, column=0, columnspan=3, pady=(0, 10))

        # 文件
        ttk.Label(frm, text="音乐文件:").grid(row=1, column=0, **pad)
        self.file_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.file_var, width=34).grid(row=1, column=1, **pad)
        ttk.Button(frm, text="浏览…", command=self._pick).grid(row=1, column=2, **pad)

        # 拖拽支持 (tkinterdnd2 可选; 没有则静默跳过)
        try:
            frm.drop_target_register("DND_Files")
            frm.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        # 模式
        ttk.Label(frm, text="播放模式:").grid(row=2, column=0, **pad)
        self.mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(frm, text="单窗口 (流畅)", value="single",
                        variable=self.mode_var, command=self._sync
                        ).grid(row=2, column=1, **pad)
        ttk.Radiobutton(frm, text="粒子多窗口 (吃性能)", value="particles",
                        variable=self.mode_var, command=self._sync
                        ).grid(row=2, column=2, **pad)

        # 单窗口: 分辨率
        self.res_var = tk.IntVar(value=900)
        self.res_row = ttk.Frame(frm)
        ttk.Label(self.res_row, text="分辨率:").pack(side="left", padx=(0, 8))
        for r in (600, 800, 900, 1200):
            ttk.Radiobutton(self.res_row, text=f"{r}px", value=r,
                            variable=self.res_var).pack(side="left", padx=4)
        self.res_row.grid(row=3, column=0, columnspan=3, **pad)

        # 粒子: 数量 + 大小
        self.part_row = ttk.Frame(frm)
        ttk.Label(self.part_row, text="窗口数量(分辨率):").pack(side="left")
        self.count_var = tk.IntVar(value=120)
        ttk.Spinbox(self.part_row, from_=16, to=600, increment=8, width=6,
                    textvariable=self.count_var).pack(side="left", padx=8)
        ttk.Label(self.part_row, text="粒子大小:").pack(side="left", padx=(12, 0))
        self.cell_var = tk.IntVar(value=8)
        ttk.Spinbox(self.part_row, from_=3, to=40, increment=1, width=5,
                    textvariable=self.cell_var).pack(side="left", padx=8)
        self.part_row.grid(row=4, column=0, columnspan=3, **pad)

        # 单窗口抽稀
        self.dens_row = ttk.Frame(frm)
        ttk.Label(self.dens_row, text="描线抽稀(性能):").pack(side="left")
        self.dens_var = tk.IntVar(value=1)
        ttk.Spinbox(self.dens_row, from_=1, to=8, width=5,
                    textvariable=self.dens_var).pack(side="left", padx=8)
        self.dens_row.grid(row=5, column=0, columnspan=3, **pad)

        # 渲染后端 (单窗口才生效)
        self.backend_row = ttk.Frame(frm)
        ttk.Label(self.backend_row, text="渲染后端:").pack(side="left", padx=(0, 8))
        self.backend_var = tk.StringVar(value="auto")
        ttk.Radiobutton(self.backend_row, text="自动", value="auto",
                        variable=self.backend_var).pack(side="left", padx=4)
        ttk.Radiobutton(self.backend_row, text="GPU (OpenGL)", value="gpu",
                        variable=self.backend_var).pack(side="left", padx=4)
        ttk.Radiobutton(self.backend_row, text="CPU (pygame)", value="cpu",
                        variable=self.backend_var).pack(side="left", padx=4)
        self.backend_row.grid(row=6, column=0, columnspan=3, **pad)

        # 颜色选择
        self.color_var = tk.StringVar(value="#3CFF96")
        color_row = ttk.Frame(frm)
        ttk.Label(color_row, text="磷光颜色:").pack(side="left", padx=(0, 8))
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
        ttk.Checkbutton(chk_row, text="循环播放", variable=self.loop_var
                        ).pack(side="left", padx=(0,16))
        ttk.Checkbutton(chk_row, text="显示帧率", variable=self.show_fps_var
                        ).pack(side="left", padx=(0,16))
        ttk.Checkbutton(chk_row, text="显示频谱", variable=self.show_spec_var
                        ).pack(side="left", padx=(0,16))
        self.debug_chk = ttk.Checkbutton(chk_row, text="调试模式 (右侧实时调参)",
                                         variable=self.debug_var)
        self.debug_chk.pack(side="left")
        chk_row.grid(row=8, column=0, columnspan=3, **pad)

        ttk.Label(frm, text=f"安全停止热键: {HOTKEY_NAME}/ESC",
                  foreground="#FFD479").grid(row=9, column=0, columnspan=3, pady=(4, 8))

        ttk.Button(frm, text="▶  开始播放", command=self._start
                   ).grid(row=10, column=0, columnspan=3, pady=8, ipadx=20, ipady=4)

        self._sync()

    def _pick(self):
        path = filedialog.askopenfilename(
            title="选择音乐文件",
            filetypes=[("音频", "*.wav *.flac *.ogg *.mp3 *.aiff"), ("所有文件", "*.*")])
        if path:
            self.file_var.set(path)

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")   # tkinterdnd2 在空格路径外套花括号
        self.file_var.set(path)

    def _pick_color(self):
        result = colorchooser.askcolor(color=self.color_var.get(),
                                       title="选择磷光颜色")
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

    def _start(self):
        if not self.file_var.get():
            messagebox.showwarning("提示", "请先选择一个音乐文件")
            return
        self.cfg = {
            "file": self.file_var.get(),
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
                messagebox.showerror("出错了", str(exc))
                r.destroy()
            except Exception:
                print("Error:", exc, file=sys.stderr)
        if quit_program:
            return   # Ctrl+Alt+Q 彻底退出, 不再弹 GUI


if __name__ == "__main__":
    main()
