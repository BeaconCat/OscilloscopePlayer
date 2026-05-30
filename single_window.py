"""单窗口模式: 磷光辉光 XY 示波器 + 实时 FFT 频谱叠加。

渲染后端:
  - 'auto' : 优先 OpenGL (moderngl), 失败回退 pygame CPU
  - 'gpu'  : 强制 OpenGL, 失败时直接报错回到启动器
  - 'cpu'  : 强制 pygame CPU 渲染

颜色通过 color 参数自定义 ('#RRGGBB')。
频谱用 numpy FFT 计算 (无需 librosa), 作为半透明柱形叠加在 XY 图下方。

可选 params dict (调试面板共享): 实时调节 decay/ref/min_bright/beam_gain/
decay_floor/blend_mode/show_spec/show_fps。不传则使用默认值, 行为与旧版一致。
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np
import pygame

from i18n import t


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _get(params: dict | None, key: str, default):
    if params is None:
        return default
    v = params.get(key, default)
    return v if v is not None else default


_SEGMENTS = {
    "0": "abcedf", "1": "bc",      "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd",  "6": "afgecd", "7": "abc",   "8": "abcdefg", "9": "abfgcd",
    "F": "afge",   "P": "abfge",  "S": "afgcd", " ": "",
}


def _build_seven_seg_text(text: str, width: int, height: int,
                          px: int = 8, py: int = 8) -> np.ndarray:
    """生成右上角七段数码管文字的 OpenGL 顶点。

    顶点格式: pos.xy + alpha。完全用几何图形绘制 FPS, 避免 pygame 字体渲染、
    整屏 Surface 和每帧纹理上传。
    """
    cw, ch, th, gap = 10, 16, 2, 4
    total_w = len(text) * cw + max(0, len(text) - 1) * gap
    x0 = width - px - total_w
    y0 = py
    verts: list[float] = []

    def rect(x: float, y: float, w: float, h: float, a: float = 0.72):
        nx0 = -1.0 + (x / width) * 2.0
        nx1 = -1.0 + ((x + w) / width) * 2.0
        ny0 = 1.0 - (y / height) * 2.0
        ny1 = 1.0 - ((y + h) / height) * 2.0
        verts.extend([nx0, ny0, a, nx1, ny0, a, nx0, ny1, a,
                      nx1, ny0, a, nx1, ny1, a, nx0, ny1, a])

    # segment rects in local pixel coordinates: a top, b upper-right, c lower-right,
    # d bottom, e lower-left, f upper-left, g middle
    seg_rects = {
        "a": (2, 0, cw - 4, th),
        "b": (cw - th, 2, th, ch // 2 - 3),
        "c": (cw - th, ch // 2 + 1, th, ch // 2 - 3),
        "d": (2, ch - th, cw - 4, th),
        "e": (0, ch // 2 + 1, th, ch // 2 - 3),
        "f": (0, 2, th, ch // 2 - 3),
        "g": (2, ch // 2 - th // 2, cw - 4, th),
    }
    for i, ch_ in enumerate(text):
        ox = x0 + i * (cw + gap)
        for seg in _SEGMENTS.get(ch_, ""):
            sx, sy, sw, sh = seg_rects[seg]
            rect(ox + sx, y0 + sy, sw, sh)
    if not verts:
        return np.empty((0, 3), dtype=np.float32)
    return np.asarray(verts, dtype=np.float32).reshape(-1, 3)


def _run_opengl(engine, stop_event, width, height, density, color,
                params: dict | None, window_pos: tuple[int, int] | None = None):
    """OpenGL 渲染。失败抛异常, 由调用方决定回退。"""
    import moderngl
    ctx = None
    if window_pos is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{int(window_pos[0])},{int(window_pos[1])}"
    pygame.init()
    pygame.display.set_caption(t("gpu_caption"))
    pygame.display.set_mode((width, height), pygame.OPENGL | pygame.DOUBLEBUF)
    ctx = moderngl.create_context()
    engine.start()

    cr, cg, cb = [c / 255.0 for c in _hex_to_rgb(color)]

    # --- 光束着色器: 每顶点亮度属性, 模拟速度-亮度曲线 ---
    vert_beam = """
        #version 330
        in vec2 pos;
        in float bright;
        out float vBright;
        uniform float gain;
        void main() {
            gl_Position = vec4(pos, 0.0, 1.0);
            vBright = bright * gain;
        }
    """
    frag_beam = """
        #version 330
        in float vBright;
        uniform vec3 beamColor;
        out vec4 fragColor;
        void main() {
            fragColor = vec4(beamColor * vBright, vBright);
        }
    """
    prog_beam = ctx.program(vertex_shader=vert_beam, fragment_shader=frag_beam)
    prog_beam["beamColor"].value = (cr, cg, cb)

    # --- 频谱着色器 ---
    vert_spec = """
        #version 330
        in vec2 pos;
        in float alpha;
        out float vAlpha;
        void main() {
            gl_Position = vec4(pos, 0.0, 1.0);
            vAlpha = alpha;
        }
    """
    frag_spec = """
        #version 330
        in float vAlpha;
        uniform vec3 specColor;
        out vec4 fragColor;
        void main() {
            fragColor = vec4(specColor, vAlpha);
        }
    """
    prog_spec = ctx.program(vertex_shader=vert_spec, fragment_shader=frag_spec)
    prog_spec["specColor"].value = (cr * 0.45, cg * 0.75, cb * 0.45)

    # --- UI/FPS 着色器: 纯几何七段数码管, 不再走 pygame 字体→纹理上传 ---
    prog_ui = ctx.program(vertex_shader=vert_spec, fragment_shader=frag_spec)
    prog_ui["specColor"].value = (cr, cg, cb)

    # --- 全屏四边形: 余辉衰减 / 最终合成 ---
    # decay 为衰减系数; floor 为硬地板, 把残余微弱亮度截零, 防累积糊屏
    vert_quad = """
        #version 330
        in vec2 pos;
        in vec2 uv;
        out vec2 vUV;
        void main() { gl_Position = vec4(pos, 0, 1); vUV = uv; }
    """
    frag_quad = """
        #version 330
        in vec2 vUV;
        uniform sampler2D tex;
        uniform float decay;
        uniform float floor_;
        out vec4 c;
        void main() {
            vec4 s = texture(tex, vUV);
            vec3 rgb = s.rgb * decay;
            float a   = s.a   * decay;
            // 低于 floor 直接截零, 防止快闪累积出底色
            rgb = max(rgb - vec3(floor_), vec3(0.0));
            a   = max(a   - floor_, 0.0);
            c = vec4(rgb, a);
        }
    """
    prog_quad = ctx.program(vertex_shader=vert_quad, fragment_shader=frag_quad)
    prog_quad["tex"] = 0

    quad_data = np.array([
        -1,-1, 0,0,   1,-1, 1,0,   -1,1, 0,1,
         1,-1, 1,0,   1, 1, 1,1,   -1,1, 0,1,
    ], dtype=np.float32)
    quad_vbo = ctx.buffer(quad_data.tobytes())
    quad_vao = ctx.vertex_array(prog_quad, [(quad_vbo, "2f 2f", "pos", "uv")])

    fbo_tex_a = ctx.texture((width, height), 4)
    fbo_tex_b = ctx.texture((width, height), 4)
    for tex in (fbo_tex_a, fbo_tex_b):
        tex.filter = moderngl.LINEAR, moderngl.LINEAR
    fbo_a = ctx.framebuffer(color_attachments=[fbo_tex_a])
    fbo_b = ctx.framebuffer(color_attachments=[fbo_tex_b])
    fbo_a.use(); ctx.clear(0, 0, 0, 0)
    fbo_b.use(); ctx.clear(0, 0, 0, 0)
    fbo_write, fbo_read = fbo_a, fbo_b
    tex_write, tex_read = fbo_tex_a, fbo_tex_b

    scope_size = engine.scope_size
    n_pts = scope_size if density <= 1 else (scope_size + density - 1) // density
    beam_vbo = ctx.buffer(reserve=n_pts * 3 * 4)
    beam_vao = ctx.vertex_array(prog_beam, [(beam_vbo, "2f 1f", "pos", "bright")])

    N_BARS = 48
    spec_vbo = ctx.buffer(reserve=N_BARS * 6 * 3 * 4)
    spec_vao = ctx.vertex_array(prog_spec, [(spec_vbo, "2f 1f", "pos", "alpha")])

    # FPS UI: 最多显示 "9999 FPS"，每字符最多 7 段，每段 6 顶点，每顶点 3 float
    ui_vbo = ctx.buffer(reserve=8 * 7 * 6 * 3 * 4)
    ui_vao = ctx.vertex_array(prog_ui, [(ui_vbo, "2f 1f", "pos", "alpha")])
    ui_vertices = 0
    last_fps_text = ""
    last_fps_update = 0.0

    # 预分配每帧复用的 numpy 缓冲，避免 render loop 分配垃圾对象
    scope_full = np.empty((scope_size, 2), dtype=np.float32)
    beam_data = np.empty((n_pts, 3), dtype=np.float32)
    seg = np.empty((n_pts - 1, 2), dtype=np.float32)
    lengths = np.empty(n_pts - 1, dtype=np.float32)
    seg_bright = np.empty(n_pts - 1, dtype=np.float32)
    v_bright = np.empty(n_pts, dtype=np.float32)
    spec_data = np.empty((N_BARS * 6, 3), dtype=np.float32)
    spec_x0 = (-1.0 + np.arange(N_BARS, dtype=np.float32) * (2.0 / N_BARS))
    spec_x1 = spec_x0 + (2.0 / N_BARS) * 0.82

    u_quad_decay = prog_quad["decay"]
    u_quad_floor = prog_quad["floor_"]
    u_beam_gain = prog_beam["gain"]

    ctx.enable(moderngl.BLEND)

    clock = pygame.time.Clock()
    running = True
    try:
        while running and not stop_event.is_set():
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    running = False

            # 每帧从共享 params 读最新参数
            decay      = float(_get(params, "decay",       0.87))
            ref        = float(_get(params, "ref",         0.015))
            min_bright = float(_get(params, "min_bright",  0.16))
            gain       = float(_get(params, "beam_gain",   1.0))
            decay_floor= float(_get(params, "decay_floor", 0.012))
            blend_mode = str  (_get(params, "blend_mode",  "add"))
            show_spec  = bool (_get(params, "show_spec",   True))
            show_fps   = bool (_get(params, "show_fps",    True))

            engine.get_scope_into(scope_full)
            pts = scope_full if density <= 1 else scope_full[::density]
            n = len(pts)

            # 速度→亮度: 全部原地计算，避免 np.column_stack / astype / 临时数组
            np.subtract(pts[1:], pts[:-1], out=seg[:n-1])
            np.hypot(seg[:n-1, 0], seg[:n-1, 1], out=lengths[:n-1])
            np.divide(ref, lengths[:n-1] + ref, out=seg_bright[:n-1])
            np.clip(seg_bright[:n-1], min_bright, 1.0, out=seg_bright[:n-1])
            v_bright[0] = seg_bright[0]
            v_bright[n-1] = seg_bright[n-2]
            v_bright[1:n-1] = (seg_bright[:n-2] + seg_bright[1:n-1]) * 0.5
            beam_data[:n, :2] = pts
            beam_data[:n, 2] = v_bright[:n]

            ctx.blend_equation = moderngl.FUNC_ADD

            # ---- 余辉衰减: 读 tex_read → 乘 decay → 覆盖写 fbo_write ----
            fbo_write.use()
            ctx.clear(0.0, 0.0, 0.0, 0.0)
            ctx.blend_func = moderngl.ONE, moderngl.ZERO
            u_quad_decay.value = decay
            u_quad_floor.value = decay_floor
            tex_read.use(0)
            quad_vao.render(moderngl.TRIANGLES)

            # ---- 光束叠加 ----
            u_beam_gain.value = gain
            if blend_mode == "max":
                # 取最大值, 永不饱和 — 治糊屏
                ctx.blend_equation = moderngl.MAX
                ctx.blend_func = moderngl.ONE, moderngl.ONE
            else:
                # 经典加色叠加, 辉光感更强但易过曝
                ctx.blend_equation = moderngl.FUNC_ADD
                ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE
            beam_vbo.write(beam_data[:n])
            beam_vao.render(moderngl.LINE_STRIP, vertices=n)
            # 恢复加法叠加, 后续合成需要它
            ctx.blend_equation = moderngl.FUNC_ADD

            fbo_write, fbo_read = fbo_read, fbo_write
            tex_write, tex_read = tex_read, tex_write

            # ---- 合成到屏幕 ----
            ctx.screen.use()
            ctx.clear(0.0, 0.0, 0.0, 1.0)
            ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
            u_quad_decay.value = 1.0
            u_quad_floor.value = 0.0
            tex_read.use(0)
            quad_vao.render(moderngl.TRIANGLES)

            # ---- 频谱 ----
            if show_spec:
                mono = (pts[:, 0] + pts[:, 1]) * 0.5
                fft_mag = np.abs(np.fft.rfft(mono, n=512))[:N_BARS].astype(np.float32, copy=False)
                mx = fft_mag.max()
                if mx > 1e-6:
                    fft_mag /= mx
                y0 = -1.0
                y1 = y0 + fft_mag * 0.36
                a_bot = 0.08 + fft_mag * 0.45
                a_top = 0.02 + fft_mag * 0.18
                v = spec_data.reshape(N_BARS, 6, 3)
                v[:, 0, 0] = spec_x0; v[:, 0, 1] = y0; v[:, 0, 2] = a_bot
                v[:, 1, 0] = spec_x1; v[:, 1, 1] = y0; v[:, 1, 2] = a_bot
                v[:, 2, 0] = spec_x0; v[:, 2, 1] = y1; v[:, 2, 2] = a_top
                v[:, 3, 0] = spec_x1; v[:, 3, 1] = y0; v[:, 3, 2] = a_bot
                v[:, 4, 0] = spec_x1; v[:, 4, 1] = y1; v[:, 4, 2] = a_top
                v[:, 5, 0] = spec_x0; v[:, 5, 1] = y1; v[:, 5, 2] = a_top
                ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE
                spec_vbo.write(spec_data)
                spec_vao.render(moderngl.TRIANGLES, vertices=N_BARS * 6)

            # ---- FPS: 纯 OpenGL 七段几何文字 ----
            if show_fps:
                now = time.perf_counter()
                if now - last_fps_update >= 0.25:
                    fps_int = min(9999, max(0, int(round(clock.get_fps()))))
                    txt = f"{fps_int} FPS"
                    if txt != last_fps_text:
                        fps_data = _build_seven_seg_text(txt, width, height)
                        ui_vertices = len(fps_data)
                        if ui_vertices:
                            ui_vbo.write(fps_data)
                        last_fps_text = txt
                    last_fps_update = now
                if ui_vertices:
                    ctx.blend_equation = moderngl.FUNC_ADD
                    ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
                    ui_vao.render(moderngl.TRIANGLES, vertices=ui_vertices)
            else:
                last_fps_text = ""
                ui_vertices = 0

            pygame.display.flip()
            clock.tick()
    finally:
        try:
            if ctx is not None:
                ctx.release()
        except Exception:
            pass
        pygame.quit()


def _run_cpu(engine, stop_event, width, height, density, color,
             params: dict | None, window_pos: tuple[int, int] | None = None):
    """pygame CPU fallback。同样支持 params 实时调参。"""
    cr, cg, cb = _hex_to_rgb(color)
    BEAM = np.array([cr, cg, cb], dtype=np.float32)

    if window_pos is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{int(window_pos[0])},{int(window_pos[1])}"
    pygame.init()
    pygame.display.set_caption(t("cpu_caption"))
    screen = pygame.display.set_mode((width, height))
    clock = pygame.time.Clock()
    engine.start()

    fps_font = pygame.font.SysFont("Consolas", 15)

    phosphor = pygame.Surface((width, height)).convert()
    phosphor.fill((0, 0, 0))
    fade = pygame.Surface((width, height)).convert()
    glow = pygame.Surface((width, height)).convert()
    beam = pygame.Surface((width, height)).convert()

    cx, cy = width / 2.0, height / 2.0
    scope_scale = min(width, height) * 0.45

    N_BARS = 48
    spec_h = int(height * 0.18)
    spec_surf = pygame.Surface((width, spec_h), pygame.SRCALPHA).convert_alpha()
    scope_full = np.empty((engine.scope_size, 2), dtype=np.float32)

    running = True
    while running and not stop_event.is_set():
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                running = False

        decay      = float(_get(params, "decay",       0.87))
        ref_ndc    = float(_get(params, "ref",         0.015))
        min_bright = float(_get(params, "min_bright",  0.16))
        gain       = float(_get(params, "beam_gain",   1.0))
        decay_floor= float(_get(params, "decay_floor", 0.012))
        blend_mode = str  (_get(params, "blend_mode",  "add"))
        show_spec  = bool (_get(params, "show_spec",   True))
        show_fps   = bool (_get(params, "show_fps",    True))

        # CPU 路径用像素值: 把 NDC 的 ref 换算到像素
        REF_PX = max(1.0, ref_ndc * min(width, height) * 1.0)
        # 把 decay (0..1) 转成 BLEND_RGB_SUB 的减法值; 越接近 1 减得越少
        sub = max(0, int(round((1.0 - decay) * 255)))
        # decay_floor 只在 floor 比较大时影响, 这里粗略加到 sub 上
        sub_floor = int(round(decay_floor * 255 * 4))
        FADE_VAL = (sub + sub_floor, sub + sub_floor + 1, sub + sub_floor)
        fade.fill(FADE_VAL)

        engine.get_scope_into(scope_full)
        scope = scope_full if density <= 1 else scope_full[::density]

        xs = cx + scope[:, 0] * scope_scale
        ys = cy - scope[:, 1] * scope_scale
        pts = np.column_stack([xs, ys])

        phosphor.blit(fade, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        beam.fill((0, 0, 0))
        if len(pts) >= 2:
            seg = pts[1:] - pts[:-1]
            length = np.hypot(seg[:, 0], seg[:, 1])
            inten = np.clip(REF_PX / (length + REF_PX), min_bright, 1.0) * gain
            cols = (BEAM[None, :] * inten[:, None]).clip(0, 255).astype(np.int16)
            a, b = pts[:-1], pts[1:]
            for i in range(len(seg)):
                c = (int(cols[i, 0]), int(cols[i, 1]), int(cols[i, 2]))
                pygame.draw.aaline(beam, c,
                                   (float(a[i, 0]), float(a[i, 1])),
                                   (float(b[i, 0]), float(b[i, 1])))
            # CPU 模式下 max 用 BLEND_RGB_MAX; add 用 BLEND_RGB_ADD
            flag = (pygame.BLEND_RGB_MAX if blend_mode == "max"
                    else pygame.BLEND_RGB_ADD)
            phosphor.blit(beam, (0, 0), special_flags=flag)

        screen.blit(phosphor, (0, 0))
        small = pygame.transform.smoothscale(phosphor, (width // 4, height // 4))
        pygame.transform.smoothscale(small, (width, height), glow)
        screen.blit(glow, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

        if show_spec:
            mono = scope[:, 0] * 0.5 + scope[:, 1] * 0.5
            fft_mag = np.abs(np.fft.rfft(mono, n=512))[:N_BARS]
            mx = fft_mag.max()
            if mx > 1e-6:
                fft_mag = fft_mag / mx
            spec_surf.fill((0, 0, 0, 0))
            bar_w = width / N_BARS
            for i, mag in enumerate(fft_mag):
                bh = int(mag * spec_h * 0.92)
                if bh < 1:
                    continue
                bx = int(i * bar_w)
                bw = max(1, int(bar_w) - 1)
                alpha = int(80 + mag * 120)
                col = (int(cr * 0.4 + mag * cr * 0.6),
                       int(cg * 0.4 + mag * cg * 0.6),
                       int(cb * 0.4 + mag * cb * 0.6),
                       alpha)
                pygame.draw.rect(spec_surf, col,
                                 (bx, spec_h - bh, bw, bh))
            screen.blit(spec_surf, (0, height - spec_h),
                        special_flags=pygame.BLEND_RGBA_ADD)

        if show_fps:
            fps_val = clock.get_fps()
            fps_surf = fps_font.render(f"{fps_val:.0f} FPS", True, (cr, cg, cb))
            fps_surf.set_alpha(160)
            screen.blit(fps_surf, (width - fps_surf.get_width() - 8, 8))

        pygame.display.flip()
        clock.tick()

    pygame.quit()


def run_single_window(engine, stop_event: threading.Event,
                      width: int = 900, height: int = 900,
                      density: int = 1, color: str = "#3CFF96",
                      show_fps: bool = True, show_spec: bool = True,
                      backend: str = "auto",
                      params: dict | None = None,
                      window_pos: tuple[int, int] | None = None):
    """backend: 'auto' | 'gpu' | 'cpu'。
    params: 渲染线程每帧读取的参数 dict, 见 debug_panel.DEFAULTS。
    show_fps/show_spec 仅作为 params 缺失时的初值, 实际以 params 为准。
    """
    # 没传 params 也要构造一个, 让两条路径统一从里面读
    if params is None:
        params = {}
    params.setdefault("show_fps",  show_fps)
    params.setdefault("show_spec", show_spec)

    backend = (backend or "auto").lower()
    if backend == "cpu":
        _run_cpu(engine, stop_event, width, height, density, color, params, window_pos=window_pos)
        return

    # gpu 或 auto: 都先尝试 OpenGL
    try:
        _run_opengl(engine, stop_event, width, height, density, color, params, window_pos=window_pos)
        return
    except Exception as exc:
        if backend == "gpu":
            # 用户明确指定 GPU, 不静默回退, 让外层弹错
            raise RuntimeError(t("gpu_init_failed", error=exc)) from exc
        # auto: 静默回退到 CPU
        try:
            pygame.quit()
        except Exception:
            pass
        _run_cpu(engine, stop_event, width, height, density, color, params, window_pos=window_pos)
