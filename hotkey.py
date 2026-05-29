"""全局安全停止热键 (Ctrl+Alt+Q)。

在独立线程中注册系统级热键并跑消息循环, 任何时候按下都会触发 stop_event,
即使粒子窗口铺满屏幕、鼠标点不到东西, 也能一键安全退出。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_Q = 0x51

HOTKEY_ID = 0xB001
HOTKEY_NAME = "Ctrl+Alt+Q"


def _listener(stop_event: threading.Event):
    mod = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
    if not user32.RegisterHotKey(None, HOTKEY_ID, mod, VK_Q):
        # MOD_NOREPEAT 在旧系统可能不支持, 退一步重试
        user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_Q)

    msg = wintypes.MSG()
    try:
        while not stop_event.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    stop_event.set()
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)


def start_hotkey(stop_event: threading.Event) -> threading.Thread:
    t = threading.Thread(target=_listener, args=(stop_event,), daemon=True)
    t.start()
    return t
