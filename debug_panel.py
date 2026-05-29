"""调试面板: 在独立线程里跑 tkinter, 渲染线程通过共享 dict 实时读参数。

可调参数:
- decay        余辉衰减系数 (0.50-0.99) — 越小越快消散; 调高会出现"糊屏"
- ref          速度→亮度参考 (0.005-0.05) — 越大整体越亮
- min_bright   最低亮度下限 (0.02-0.60)
- beam_gain    光束总亮度倍率 (0.2-3.0)
- decay_floor  余辉硬地板 (0.0-0.05) — 把背景里残余的微弱亮度截零, 防止快速闪动累积糊屏
- blend_mode   "add" (经典累加, 辉光感强但容易过曝) / "max" (取最大值, 不会饱和)
- show_spec    显示频谱
- show_fps     显示帧率
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk


# 渲染线程读这些 key 即可
DEFAULTS: dict = {
    "decay":       0.87,
    "ref":         0.015,
    "min_bright":  0.16,
    "beam_gain":   1.0,
    "decay_floor": 0.012,
    "blend_mode":  "add",   # or "max"
    "show_spec":   True,
    "show_fps":    True,
}


def make_params(overrides: dict | None = None) -> dict:
    p = dict(DEFAULTS)
    if overrides:
        p.update(overrides)
    return p


# 与 main.py 启动器一致的暗绿主题
_BG     = "#0d1410"
_BG2    = "#141f19"
_BG3    = "#1c2e24"
_FG     = "#8EFFA8"
_ACCENT = "#2aff7c"
_BORDER = "#1e3828"


class DebugPanel:
    """tkinter 在独立线程跑 mainloop。简单直白:
    - 滑块的 command 直接写 params[...]
    - 渲染线程每帧 params[key] 读取
    CPython 简单赋值/读取在 GIL 下原子, 无需加锁。
    """

    def __init__(self, params: dict, geometry: str | None = None,
                 on_close: callable = None):
        self.params = params
        self.geometry = geometry
        self.on_close = on_close
        self._closed = threading.Event()
        self._should_close = False        # 外部线程通过它请求关闭
        self._root: tk.Tk | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---------- 公共 ----------
    def close(self):
        """从其他线程调用: 安排面板关闭。

        关键: 不能跨线程直接动 tk, Tcl 解释器有线程亲和性, 在 Windows 上跨线程
        调 _root.after / _root.destroy 会冻结。这里只设布尔标志, 实际 destroy
        由 tk 线程自己的轮询任务触发。
        """
        self._should_close = True

    def wait_closed(self, timeout: float | None = None):
        self._closed.wait(timeout)

    # ---------- 内部 ----------
    def _style(self, root: tk.Tk):
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=_BG, foreground=_FG,
                        fieldbackground=_BG2, troughcolor=_BG2,
                        bordercolor=_BORDER, lightcolor=_BG3, darkcolor=_BG2,
                        font=("Segoe UI", 9))
        style.configure("TFrame", background=_BG)
        style.configure("TLabel", background=_BG, foreground=_FG)
        style.configure("Header.TLabel", background=_BG, foreground=_ACCENT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", background=_BG, foreground="#5da874",
                        font=("Segoe UI", 8))
        style.configure("TCheckbutton", background=_BG, foreground=_FG,
                        indicatorbackground=_BG2, focuscolor=_BG)
        style.map("TCheckbutton",
                  background=[("active", _BG)],
                  foreground=[("active", _ACCENT)],
                  indicatorbackground=[("selected", _BG3)],
                  indicatorforeground=[("selected", _ACCENT)])
        style.configure("TRadiobutton", background=_BG, foreground=_FG,
                        indicatorbackground=_BG2, focuscolor=_BG)
        style.map("TRadiobutton",
                  background=[("active", _BG)],
                  foreground=[("active", _ACCENT)],
                  indicatorbackground=[("selected", _BG3)],
                  indicatorforeground=[("selected", _ACCENT)])
        style.configure("TButton", background=_BG3, foreground=_ACCENT,
                        bordercolor=_BORDER, padding=4)
        style.map("TButton",
                  background=[("active", _BG3), ("pressed", _BG2)],
                  foreground=[("active", "#ffffff")])
        style.configure("Horizontal.TScale", background=_BG, troughcolor=_BG2)
        root.configure(bg=_BG)

    def _slider(self, parent, label: str, key: str, lo: float, hi: float,
                fmt: str = "{:.3f}", hint: str = ""):
        frm = ttk.Frame(parent)
        frm.pack(fill="x", padx=10, pady=(4, 0))
        top = ttk.Frame(frm); top.pack(fill="x")
        ttk.Label(top, text=label).pack(side="left")
        val_lbl = ttk.Label(top, text=fmt.format(self.params[key]),
                            foreground=_ACCENT)
        val_lbl.pack(side="right")
        var = tk.DoubleVar(value=float(self.params[key]))

        def _on_change(v):
            f = float(v)
            self.params[key] = f
            val_lbl.configure(text=fmt.format(f))

        scl = ttk.Scale(frm, from_=lo, to=hi, variable=var,
                        orient="horizontal", command=_on_change)
        scl.pack(fill="x")
        if hint:
            ttk.Label(frm, text=hint, style="Hint.TLabel").pack(anchor="w")
        # 返回 (var, val_lbl, fmt) 供重置时刷新
        return (var, val_lbl, fmt)

    def _run(self):
        root = tk.Tk()
        self._root = root
        root.title("调试面板 · 实时调节")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        if self.geometry:
            try:
                root.geometry(self.geometry)
            except Exception:
                pass
        self._style(root)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="实时渲染参数", style="Header.TLabel"
                  ).pack(anchor="w", pady=(0, 4))

        # 保存滑块引用, 供重置时刷新
        sliders = {}  # key -> (DoubleVar, Label, fmt_str)
        sliders["decay"] = self._slider(outer, "余辉衰减 decay", "decay", 0.50, 0.995,
                     fmt="{:.3f}",
                     hint="越大拖尾越久; 太大快闪曲线会糊屏")
        sliders["decay_floor"] = self._slider(outer, "余辉硬地板 floor", "decay_floor", 0.0, 0.05,
                     fmt="{:.4f}",
                     hint="把残余的微弱辉光直接截零, 防累积糊屏")
        sliders["ref"] = self._slider(outer, "速度→亮度参考 ref", "ref", 0.003, 0.05,
                     fmt="{:.4f}",
                     hint="越大整体越亮 (慢速段也会亮)")
        sliders["min_bright"] = self._slider(outer, "最低亮度 min", "min_bright", 0.02, 0.60,
                     fmt="{:.2f}",
                     hint="高速段不至于完全黑掉的下限")
        sliders["beam_gain"] = self._slider(outer, "光束亮度倍率 gain", "beam_gain", 0.2, 3.0,
                     fmt="{:.2f}",
                     hint="总开关; 觉得整张图太亮就调小")

        # 叠加方式
        sec = ttk.Frame(outer); sec.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(sec, text="光束叠加方式", style="Header.TLabel"
                  ).pack(anchor="w")
        blend_var = tk.StringVar(value=self.params.get("blend_mode", "add"))
        def _set_blend(*_):
            self.params["blend_mode"] = blend_var.get()
        ttk.Radiobutton(sec, text="累加 add (经典辉光, 易过曝)",
                        value="add",  variable=blend_var,
                        command=_set_blend).pack(anchor="w")
        ttk.Radiobutton(sec, text="取最大值 max (永不饱和, 治糊屏)",
                        value="max",  variable=blend_var,
                        command=_set_blend).pack(anchor="w")

        # 显示开关
        sw = ttk.Frame(outer); sw.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(sw, text="显示开关", style="Header.TLabel"
                  ).pack(anchor="w")
        spec_var = tk.BooleanVar(value=bool(self.params.get("show_spec", True)))
        fps_var  = tk.BooleanVar(value=bool(self.params.get("show_fps",  True)))
        def _sync_sw(*_):
            self.params["show_spec"] = bool(spec_var.get())
            self.params["show_fps"]  = bool(fps_var.get())
        ttk.Checkbutton(sw, text="显示频谱", variable=spec_var,
                        command=_sync_sw).pack(anchor="w")
        ttk.Checkbutton(sw, text="显示帧率", variable=fps_var,
                        command=_sync_sw).pack(anchor="w")

        # 重置按钮
        btns = ttk.Frame(outer); btns.pack(fill="x", padx=10, pady=(12, 0))
        def _reset():
            for k, v in DEFAULTS.items():
                self.params[k] = v
            # 刷新所有滑块位置和数值标签
            for key, (var, lbl, f) in sliders.items():
                val = float(DEFAULTS[key])
                var.set(val)
                lbl.configure(text=f.format(val))
            # 刷新叠加方式和显示开关
            blend_var.set(DEFAULTS["blend_mode"])
            spec_var.set(DEFAULTS["show_spec"])
            fps_var.set(DEFAULTS["show_fps"])
        ttk.Button(btns, text="重置为默认值", command=_reset
                   ).pack(side="left")

        def _on_close():
            try:
                if callable(self.on_close):
                    self.on_close()
            finally:
                root.destroy()
        root.protocol("WM_DELETE_WINDOW", _on_close)

        # 轮询 _should_close 标志 — 外部线程通过 close() 设置它,
        # 这里在 tk 自己的线程里安全地 destroy
        def _poll_close():
            if self._should_close:
                root.destroy()
            else:
                root.after(100, _poll_close)
        root.after(100, _poll_close)

        try:
            root.mainloop()
        finally:
            self._root = None
            self._closed.set()
