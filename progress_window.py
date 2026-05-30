"""播放进度窗口（Win32 原生普通窗口）。

要求：正常窗口、有标题栏/阴影、能拖动、可置顶；不使用 tkinter，避免多 Tk 线程在
Windows 下退出时出现 Tcl_AsyncDelete / 线程亲和性问题。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

from i18n import t

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_USER = 0x0400
WM_APP_CLOSE = WM_USER + 1

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008

SW_SHOWNORMAL = 1
PM_REMOVE = 0x0001
TRANSPARENT = 1
DT_LEFT = 0x00000000
DT_RIGHT = 0x00000002
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020

_BG = (13, 20, 16)
_BG2 = (20, 31, 25)
_FG = (142, 255, 168)
_ACCENT = (42, 255, 124)
_BORDER = (30, 56, 40)


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _parse_geometry(geometry: str | None, default=(640, 140, 80, 80)):
    if not geometry:
        return default
    try:
        size, x, y = geometry.split("+")
        w, h = size.split("x")
        return int(w), int(h), int(x), int(y)
    except Exception:
        return default


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


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


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.BeginPaint.restype = wintypes.HDC
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(RECT), wintypes.HBRUSH]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(RECT), wintypes.UINT]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]


class ProgressWindow:
    def __init__(self, engine, file_path: str = "", geometry: str | None = None):
        self.engine = engine
        self.file_path = file_path
        self.geometry = geometry
        self._closed = threading.Event()
        self._should_close = False
        self._hwnd = None
        self._bar_rect = (14, 42, 100, 60)
        self._button_rect = (14, 68, 94, 96)
        self._dragging = False
        self._class_name = f"OscProgressWindow_{id(self)}"
        self._wndproc = WNDPROC(self._proc)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self):
        self._should_close = True
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, WM_APP_CLOSE, 0, 0)
            except Exception:
                pass

    def wait_closed(self, timeout: float | None = None):
        self._closed.wait(timeout)

    def _register_class(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        wc.hbrBackground = gdi32.CreateSolidBrush(_rgb(*_BG))
        wc.lpszClassName = self._class_name
        user32.RegisterClassExW(ctypes.byref(wc))
        return hinst

    def _draw(self, hwnd, hdc):
        rc = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        w = max(1, rc.right - rc.left)

        bg = gdi32.CreateSolidBrush(_rgb(*_BG))
        user32.FillRect(hdc, ctypes.byref(rc), bg)
        gdi32.DeleteObject(bg)

        current, duration, ratio = self.engine.get_time_info()
        percent = int(round(ratio * 100))
        name = os.path.basename(self.file_path) if self.file_path else t("progress_unknown_file")
        time_text = t("progress_time", current=_fmt_time(current),
                      duration=_fmt_time(duration), percent=percent)

        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, _rgb(*_ACCENT))
        name_rc = RECT(14, 10, w - 14, 34)
        user32.DrawTextW(hdc, name, -1, ctypes.byref(name_rc), DT_LEFT | DT_SINGLELINE | DT_VCENTER)

        bar_x, bar_y, bar_h = 14, 42, 18
        bar_w = max(10, w - 28)
        self._bar_rect = (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h)
        bg2 = gdi32.CreateSolidBrush(_rgb(*_BG2))
        border = gdi32.CreateSolidBrush(_rgb(*_BORDER))
        fill = gdi32.CreateSolidBrush(_rgb(*_ACCENT))
        outline = RECT(bar_x - 1, bar_y - 1, bar_x + bar_w + 1, bar_y + bar_h + 1)
        inside = RECT(bar_x, bar_y, bar_x + bar_w, bar_y + bar_h)
        done = RECT(bar_x, bar_y, bar_x + int(bar_w * ratio), bar_y + bar_h)
        user32.FillRect(hdc, ctypes.byref(outline), border)
        user32.FillRect(hdc, ctypes.byref(inside), bg2)
        if done.right > done.left:
            user32.FillRect(hdc, ctypes.byref(done), fill)
        gdi32.DeleteObject(bg2)
        gdi32.DeleteObject(border)
        gdi32.DeleteObject(fill)

        # 暂停/继续按钮
        btn_x0, btn_y0, btn_x1, btn_y1 = 14, 68, 94, 96
        self._button_rect = (btn_x0, btn_y0, btn_x1, btn_y1)
        btn_br = gdi32.CreateSolidBrush(_rgb(*_BG2))
        btn_rc = RECT(btn_x0, btn_y0, btn_x1, btn_y1)
        user32.FillRect(hdc, ctypes.byref(btn_rc), btn_br)
        gdi32.DeleteObject(btn_br)
        gdi32.SetTextColor(hdc, _rgb(*_ACCENT))
        btn_text = t("progress_resume") if getattr(self.engine, "paused", False) else t("progress_pause")
        user32.DrawTextW(hdc, btn_text, -1, ctypes.byref(btn_rc), DT_SINGLELINE | DT_VCENTER)

        gdi32.SetTextColor(hdc, _rgb(*_FG))
        time_rc = RECT(106, 68, w - 14, 96)
        user32.DrawTextW(hdc, time_text, -1, ctypes.byref(time_rc), DT_RIGHT | DT_SINGLELINE | DT_VCENTER)

    def _xy_from_lparam(self, lp):
        x = lp & 0xFFFF
        y = (lp >> 16) & 0xFFFF
        if x >= 0x8000:
            x -= 0x10000
        if y >= 0x8000:
            y -= 0x10000
        return x, y

    def _hit(self, rect, x, y) -> bool:
        x0, y0, x1, y1 = rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def _seek_from_x(self, x: int):
        x0, _y0, x1, _y1 = self._bar_rect
        if x1 <= x0:
            return
        ratio = (x - x0) / (x1 - x0)
        self.engine.seek_ratio(ratio)
        if self._hwnd:
            user32.InvalidateRect(self._hwnd, None, False)

    def _proc(self, hwnd, msg, wp, lp):
        if msg == WM_LBUTTONDOWN:
            x, y = self._xy_from_lparam(lp)
            if self._hit(self._button_rect, x, y):
                self.engine.toggle_pause()
                user32.InvalidateRect(hwnd, None, False)
                return 0
            if self._hit(self._bar_rect, x, y):
                self._dragging = True
                user32.SetCapture(hwnd)
                self._seek_from_x(x)
                return 0
        if msg == WM_MOUSEMOVE and self._dragging:
            x, _y = self._xy_from_lparam(lp)
            self._seek_from_x(x)
            return 0
        if msg == WM_LBUTTONUP and self._dragging:
            self._dragging = False
            user32.ReleaseCapture()
            x, _y = self._xy_from_lparam(lp)
            self._seek_from_x(x)
            return 0
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                self._draw(hwnd, hdc)
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg in (WM_CLOSE, WM_APP_CLOSE):
            self._should_close = True
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _run(self):
        try:
            hinst = self._register_class()
            w, h, x, y = _parse_geometry(self.geometry)
            hwnd = user32.CreateWindowExW(
                WS_EX_TOPMOST,
                self._class_name,
                t("progress_title"),
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                x, y, w, h,
                None, None, hinst, None)
            self._hwnd = hwnd
            user32.ShowWindow(hwnd, SW_SHOWNORMAL)
            user32.UpdateWindow(hwnd)

            msg = wintypes.MSG()
            last_draw = 0.0
            while not self._should_close:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                now = time.perf_counter()
                if hwnd and now - last_draw >= 0.1:
                    user32.InvalidateRect(hwnd, None, False)
                    last_draw = now
                time.sleep(0.016)
            if hwnd:
                user32.DestroyWindow(hwnd)
        finally:
            self._hwnd = None
            self._closed.set()
