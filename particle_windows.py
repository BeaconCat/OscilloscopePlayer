"""多窗口粒子模式: 在屏幕上铺一堆会发光的小窗口, 每个窗口就是示波器上的一个采样点。

性能与"探索 Windows 边界"的关键手法:
1. 窗口池: 一次性创建 N 个无边框分层(置顶)小窗口, 全程复用, 绝不在循环里反复
   CreateWindow/DestroyWindow (那是最大的卡顿来源)。
2. 批量平滑移动: 用 BeginDeferWindowPos / DeferWindowPos / EndDeferWindowPos 把这一帧
   所有窗口的新坐标打包成"一次"原子重定位, 由 DWM 在一帧内统一刷新, 避免逐个 SetWindowPos
   造成的撕裂和抖动。
3. 移动标志位 SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_NOREDRAW: 不改大小、不动 Z 序、
   不抢焦点、不触发重绘, 把每次移动的开销压到最低。
4. 鼠标穿透 WS_EX_TRANSPARENT + WS_EX_NOACTIVATE: 粒子窗口铺满屏也不挡操作。
5. 平滑创建/卸载: 创建时把所有窗口先建好再统一渐显 (alpha 0->目标), 退出时统一渐隐再销毁,
   而不是一次性全冒出来 / 全消失。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- 常量 ----
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOREDRAW = 0x0008
SWP_SHOWWINDOW = 0x0040

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

LWA_ALPHA = 0x00000002
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001

WM_DESTROY = 0x0002
VK_ESCAPE = 0x1B

# ---- 函数签名 (64 位安全) ----
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.BeginDeferWindowPos.restype = wintypes.HANDLE
user32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
user32.DeferWindowPos.restype = wintypes.HANDLE
user32.DeferWindowPos.argtypes = [wintypes.HANDLE, wintypes.HWND, wintypes.HWND,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT]
user32.EndDeferWindowPos.restype = wintypes.BOOL
user32.EndDeferWindowPos.argtypes = [wintypes.HANDLE]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                              wintypes.BYTE, wintypes.DWORD]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def _rgb(r, g, b):       # COLORREF = 0x00BBGGRR
    return r | (g << 8) | (b << 16)


def _hex_to_rgb(hex_color: str):
    """'#RRGGBB' -> (r, g, b)"""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


_CLASS_NAME = "OscParticleCell"
_wndproc_ref = None      # 防止 WNDPROC 被 GC
_class_registered = False
_registered_color = None  # 上次注册时使用的颜色


def _ensure_class(color_hex: str = "#3CFF8C"):
    global _wndproc_ref, _class_registered, _registered_color
    r, g, b = _hex_to_rgb(color_hex)
    colorref = _rgb(r, g, b)

    # 颜色变化时需要重新注册(先注销旧类)
    if _class_registered and _registered_color != color_hex:
        user32.UnregisterClassW(_CLASS_NAME, kernel32.GetModuleHandleW(None))
        _class_registered = False

    if _class_registered:
        return
    hinst = kernel32.GetModuleHandleW(None)

    def _proc(hwnd, msg, wp, lp):
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    _wndproc_ref = WNDPROC(_proc)
    wc = WNDCLASSEX()
    wc.cbSize = ctypes.sizeof(WNDCLASSEX)
    wc.style = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = hinst
    wc.hCursor = None
    wc.hbrBackground = gdi32.CreateSolidBrush(colorref)
    wc.lpszClassName = _CLASS_NAME
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err not in (0, 1410):
            raise ctypes.WinError(err)
    _class_registered = True
    _registered_color = color_hex


def _pump():
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def run_particles(engine, stop_event: threading.Event,
                  count: int = 120, cell: int = 8,
                  target_alpha: int = 210, fps: int = 60,
                  color: str = "#3CFF8C"):
    """count: 窗口(粒子)数量 = 分辨率;  cell: 每个粒子边长(px);
    color: 粒子颜色 '#RRGGBB'。
    """
    _ensure_class(color)
    hinst = kernel32.GetModuleHandleW(None)

    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    cx, cy = sw / 2.0, sh / 2.0
    scale = min(sw, sh) * 0.45

    ex_style = (WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
                | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)

    # 1) 一次性建好整个窗口池 (隐藏, alpha=0)
    hwnds = []
    for _ in range(count):
        hwnd = user32.CreateWindowExW(
            ex_style, _CLASS_NAME, None, WS_POPUP,
            int(cx), int(cy), cell, cell,
            None, None, hinst, None)
        if not hwnd:
            continue
        user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        hwnds.append(hwnd)
    n = len(hwnds)
    if n == 0:
        return
    arr = (wintypes.HWND * n)(*hwnds)

    idx = np.linspace(0, engine.scope_size - 1, n).astype(np.int32)
    half = cell // 2
    frame_dt = 1.0 / fps

    # 预分配整型坐标缓冲, 避免每帧 Python list 分配
    px_buf = np.empty(n, dtype=np.int32)
    py_buf = np.empty(n, dtype=np.int32)

    def move_batch(px: np.ndarray, py: np.ndarray):
        hdwp = user32.BeginDeferWindowPos(n)
        if not hdwp:
            return
        flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOREDRAW
        for i in range(n):
            hdwp = user32.DeferWindowPos(hdwp, arr[i], None,
                                         int(px[i]), int(py[i]), 0, 0, flags)
            if not hdwp:
                return
        user32.EndDeferWindowPos(hdwp)

    def set_alpha_all(a: int):
        """批量设置所有窗口 alpha (循环不可避免, 但只在渐显/渐隐时调用)。"""
        for h in hwnds:
            user32.SetLayeredWindowAttributes(h, 0, a, LWA_ALPHA)

    try:
        # 2) 窗口池已就绪, 启动音频 → 平滑渐显
        engine.start()
        scope = engine.get_scope()[idx]
        px_buf[:] = (cx + scope[:, 0] * scale - half).astype(np.int32)
        py_buf[:] = (cy - scope[:, 1] * scale - half).astype(np.int32)
        move_batch(px_buf, py_buf)
        step = max(1, target_alpha // 24)
        for a in range(0, target_alpha + 1, step):
            set_alpha_all(a)
            _pump()
            time.sleep(0.012)
        set_alpha_all(target_alpha)

        # 3) 主循环 — 所有坐标运算全向量化, 无 Python for 循环
        while not stop_event.is_set():
            t0 = time.perf_counter()
            if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                break
            scope = engine.get_scope()[idx]
            px_buf[:] = (cx + scope[:, 0] * scale - half).astype(np.int32)
            py_buf[:] = (cy - scope[:, 1] * scale - half).astype(np.int32)
            move_batch(px_buf, py_buf)
            _pump()
            dt = time.perf_counter() - t0
            if dt < frame_dt:
                time.sleep(frame_dt - dt)
    finally:
        # 4) 平滑渐隐后统一销毁
        step = max(1, target_alpha // 20)
        for a in range(target_alpha, -1, -step):
            set_alpha_all(a)
            _pump()
            time.sleep(0.010)
        for h in hwnds:
            user32.DestroyWindow(h)
        _pump()
