"""音频引擎: 播放音乐并实时提供 XY 示波器采样缓冲。

示波器音乐的本质: 左声道 -> X, 右声道 -> Y, 在 XY 模式下绘制出矢量图形。
本引擎在 sounddevice 的音频回调里把"正在输出"的样本同步写入一个共享环形缓冲,
可视化线程随时读取最新窗口的样本, 保证画面与声音严格同步。

MP3 兼容策略:
  首选 soundfile 直接解码 (依赖 libsndfile, 新版已内置 MP3 支持);
  若 soundfile 失败 (旧版/缺编解码器), 自动 fallback 到 pygame.mixer 解码,
  将解码结果转为 float32 ndarray 交给同一套引擎处理, 对上层透明。
"""
from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd
import soundfile as sf


def _load_audio(path: str):
    """加载音频文件 -> (data: float32 ndarray shape=(N,2), samplerate: int)。
    优先 soundfile, 失败后 fallback 到 pygame.mixer。
    """
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        return data, sr
    except Exception as sf_err:
        try:
            import pygame
            pygame.mixer.init()
            snd = pygame.mixer.Sound(path)
            raw = pygame.sndarray.array(snd)          # int16 ndarray
            sr = pygame.mixer.get_init()[0]
            data = raw.astype(np.float32) / 32768.0   # 归一化到 [-1, 1]
            if data.ndim == 1:
                data = data[:, None]                   # mono -> (N,1)
            return data, sr
        except Exception as pg_err:
            raise RuntimeError(
                f"无法解码音频文件 '{path}'。\n"
                f"  soundfile 错误: {sf_err}\n"
                f"  pygame 错误: {pg_err}\n"
                "请尝试将文件转换为 WAV / FLAC / OGG 格式。"
            ) from sf_err


class AudioEngine:
    def __init__(self, path: str, scope_size: int = 2048, blocksize: int = 512):
        data, self.samplerate = _load_audio(path)
        if data.shape[1] == 1:                     # 单声道 -> 复制成立体声
            data = np.repeat(data, 2, axis=1)
        elif data.shape[1] > 2:                    # 多声道 -> 取前两路
            data = data[:, :2]
        self.data = np.ascontiguousarray(data)
        self.frames = len(self.data)

        self.scope_size = scope_size
        self.blocksize = blocksize
        self.loop = True

        self._pos = 0
        self._lock = threading.Lock()
        self._scope = np.zeros((scope_size, 2), dtype=np.float32)
        self._stream: sd.OutputStream | None = None
        self.finished = threading.Event()

    # ---- 音频回调 (实时线程, 必须轻量) ----
    def _callback(self, outdata, frames, time_info, status):
        start = self._pos
        end = start + frames
        if end <= self.frames:
            chunk = self.data[start:end]
            self._pos = end
        else:
            first = self.data[start:]
            rest = end - self.frames
            if self.loop:
                chunk = np.vstack([first, self.data[:rest]])
                self._pos = rest
            else:
                pad = np.zeros((rest, 2), dtype=np.float32)
                chunk = np.vstack([first, pad])
                self._pos = self.frames
                outdata[:] = chunk
                self.finished.set()
                raise sd.CallbackStop

        outdata[:] = chunk

        n = min(self.scope_size, frames)
        with self._lock:
            self._scope[:-n] = self._scope[n:]
            self._scope[-n:] = chunk[-n:]

    def start(self):
        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=2,
            dtype="float32",
            blocksize=self.blocksize,
            callback=self._callback,
            latency="low",
        )
        self._stream.start()

    def get_scope(self) -> np.ndarray:
        """返回最新一窗口的 (N,2) 立体声样本副本。"""
        with self._lock:
            return self._scope.copy()

    def get_scope_into(self, out: np.ndarray) -> np.ndarray:
        """把最新一窗口样本复制到调用方提供的缓冲区, 避免渲染循环每帧分配新数组。"""
        with self._lock:
            np.copyto(out, self._scope)
        return out

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
