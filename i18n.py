"""简单 i18n 文本表。

当前默认中文。后续扩展语言时, 在 TEXTS 中新增语言字典即可。
代码侧统一使用 t("key", **kwargs) 获取文本。
"""
from __future__ import annotations

LANG = "zh-CN"

TEXTS = {
    "zh-CN": {
        # Launcher / main.py
        "app_title": "示波器音乐播放器",
        "launcher_title": "示波器音乐播放器",
        "music_file": "音乐文件:",
        "browse": "浏览…",
        "mode": "播放模式:",
        "mode_single": "单窗口 (流畅)",
        "mode_particles": "粒子多窗口 (吃性能)",
        "resolution": "分辨率:",
        "particle_count": "窗口数量(分辨率):",
        "particle_size": "粒子大小:",
        "density": "描线抽稀(性能):",
        "backend": "渲染后端:",
        "backend_auto": "自动",
        "backend_gpu": "GPU (OpenGL)",
        "backend_cpu": "CPU (pygame)",
        "phosphor_color": "磷光颜色:",
        "loop": "循环播放",
        "show_fps": "显示帧率",
        "show_spec": "显示频谱",
        "debug_mode": "调试模式 (右侧实时调参)",
        "hotkey_hint": "安全停止热键: {hotkey}/ESC",
        "start_play": "▶  开始播放",
        "select_music_title": "选择音乐文件",
        "filetype_audio": "音频",
        "filetype_all": "所有文件",
        "select_color_title": "选择磷光颜色",
        "warning_title": "提示",
        "warning_no_file": "请先选择一个音乐文件",
        "error_title": "出错了",

        # pygame window captions / single_window.py
        "gpu_caption": "示波器音乐 · GPU(OpenGL)  (Ctrl+Alt+Q / ESC 返回)",
        "cpu_caption": "示波器音乐 · CPU(pygame)  (Ctrl+Alt+Q / ESC 返回)",
        "gpu_init_failed": "GPU(OpenGL) 渲染初始化失败: {error}",

        # Debug panel / debug_panel.py
        "debug_panel_title": "调试面板 · 实时调节",
        "debug_header": "实时渲染参数",
        "debug_decay": "余辉衰减 decay",
        "debug_decay_hint": "越大拖尾越久; 太大快闪曲线会糊屏",
        "debug_decay_floor": "余辉硬地板 floor",
        "debug_decay_floor_hint": "把残余的微弱辉光直接截零, 防累积糊屏",
        "debug_ref": "速度→亮度参考 ref",
        "debug_ref_hint": "越大整体越亮 (慢速段也会亮)",
        "debug_min_bright": "最低亮度 min",
        "debug_min_bright_hint": "高速段不至于完全黑掉的下限",
        "debug_beam_gain": "光束亮度倍率 gain",
        "debug_beam_gain_hint": "总开关; 觉得整张图太亮就调小",
        "debug_blend_mode": "光束叠加方式",
        "debug_blend_add": "累加 add (经典辉光, 易过曝)",
        "debug_blend_max": "取最大值 max (永不饱和, 治糊屏)",
        "debug_display_switches": "显示开关",
        "debug_reset": "重置为默认值",

        # Progress window / progress_window.py
        "progress_title": "播放进度",
        "progress_unknown_file": "未知音频",
        "progress_time": "{current} / {duration}    {percent}%",
        "progress_pause": "暂停",
        "progress_resume": "继续",
    }
}


def t(key: str, **kwargs) -> str:
    """取当前语言文本。key 不存在时返回 key 本身, 方便开发时定位缺失翻译。"""
    text = TEXTS.get(LANG, {}).get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
