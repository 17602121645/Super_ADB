# -*- coding: utf-8 -*-
"""
OpenGL 投屏视图
==============
QOpenGLWidget + GLSL 着色器渲染 YUV420p，GPU 直接读 AVFrame 的 C 内存，零拷贝。

线程模型:
  解码线程 → 帧就绪信号(Qt队列连接) → GUI线程取帧 → update() → paintGL

PBO 环形池:
  Y/U/V 每个平面独立 3 个 PBO 环形缓冲，glFenceSync/glClientWaitSync 追踪
  GPU 使用状态，CPU 映射下一个 PBO 时若 GPU 还在用则短暂等待，彻底消灭
  CPU-GPU 同步等待。glTexSubImage2D 从 PBO 异步读取。

依赖: pip install PyOpenGL PyOpenGL_accelerate av
"""

import ctypes
from typing import Optional

from PySide6.QtWidgets import QWidget, QSizePolicy, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QMouseEvent, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

try:
    from OpenGL import GL
except ImportError:
    GL = None


# ─────────────────── GLSL 着色器 ───────────────────
_VERTEX_SHADER = """
#version 330 core
in vec2 aPos;
in vec2 aTexCoord;
out vec2 TexCoord;
void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
    TexCoord = aTexCoord;
}
"""

_FRAGMENT_SHADER = """
#version 330 core
in vec2 TexCoord;
out vec4 FragColor;
uniform sampler2D texY;
uniform sampler2D texU;
uniform sampler2D texV;
void main() {
    float y = texture(texY, TexCoord).r;
    float u = texture(texU, TexCoord).r - 0.5;
    float v = texture(texV, TexCoord).r - 0.5;
    // BT.601 色彩矩阵（SD 分辨率 <720p 使用 BT.601）
    float r = y + 1.402 * v;
    float g = y - 0.344 * u - 0.714 * v;
    float b = y + 1.772 * u;
    FragColor = vec4(r, g, b, 1.0);
}
"""

_PBO_POOL_SIZE = 3  # 每个平面的环形 PBO 数量


class _平面PBO池:
    """单个平面的环形 PBO 池，带 sync 追踪 GPU 使用状态。"""

    def __init__(self):
        self.pbos = [0] * _PBO_POOL_SIZE
        self.syncs = [None] * _PBO_POOL_SIZE
        self.sizes = [0] * _PBO_POOL_SIZE
        self.index = 0

    def init(self, size):
        """创建 PBO。"""
        self.pbos = GL.glGenBuffers(_PBO_POOL_SIZE)
        for i in range(_PBO_POOL_SIZE):
            GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, self.pbos[i])
            GL.glBufferData(GL.GL_PIXEL_UNPACK_BUFFER, size, None, GL.GL_STREAM_DRAW)
            self.sizes[i] = size
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, 0)

    def ensure_size(self, size):
        """确保所有 PBO 足够大，不够则扩容。"""
        need_resize = any(s < size for s in self.sizes)
        if not need_resize:
            return
        for i in range(_PBO_POOL_SIZE):
            GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, self.pbos[i])
            GL.glBufferData(GL.GL_PIXEL_UNPACK_BUFFER, size, None, GL.GL_STREAM_DRAW)
            self.sizes[i] = size
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, 0)

    def upload(self, data_ptr, data_size, tex_id, width, height, stride, internal_format):
        """把数据上传到纹理，返回当前使用的 PBO 索引。

        流程:
          1. 取下一个 PBO
          2. glClientWaitSync 等该 PBO 上一次 GPU 操作完成
          3. glMapBufferRange 映射，memmove 数据
          4. glUnmapBuffer
          5. glTexSubImage2D 从 PBO 异步读数据到纹理
          6. glFenceSync 插入栅栏，标记 GPU 正在用该 PBO
        """
        idx = self.index
        self.index = (self.index + 1) % _PBO_POOL_SIZE
        pbo = self.pbos[idx]

        # 等该 PBO 上一次 GPU 操作完成（超时 1ms，通常已完成）
        if self.syncs[idx] is not None:
            GL.glClientWaitSync(self.syncs[idx], GL.GL_SYNC_FLUSH_COMMANDS_BIT, 1_000_000)
            GL.glDeleteSync(self.syncs[idx])
            self.syncs[idx] = None

        # 映射 PBO，丢弃旧内容
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, pbo)
        ptr = GL.glMapBufferRange(GL.GL_PIXEL_UNPACK_BUFFER, 0, data_size,
                                  GL.GL_MAP_WRITE_BIT | GL.GL_MAP_INVALIDATE_BUFFER_BIT)
        if ptr:
            ctypes.memmove(ptr, data_ptr, data_size)
            GL.glUnmapBuffer(GL.GL_PIXEL_UNPACK_BUFFER)

        # 从 PBO 异步读数据到纹理
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, stride)
        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, width, height,
                          GL.GL_RED, GL.GL_UNSIGNED_BYTE, ctypes.c_void_p(0))
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
        GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, 0)

        # 插入栅栏，标记 GPU 正在用该 PBO
        self.syncs[idx] = GL.glFenceSync(GL.GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
        return idx

    def cleanup(self):
        """清理所有 PBO 和 sync。"""
        for i in range(_PBO_POOL_SIZE):
            if self.syncs[i] is not None:
                GL.glDeleteSync(self.syncs[i])
                self.syncs[i] = None
        if any(p for p in self.pbos):
            GL.glDeleteBuffers(_PBO_POOL_SIZE, self.pbos)
            self.pbos = [0] * _PBO_POOL_SIZE


class _帧信息:
    """一帧的信息，持有 AVFrame 引用防止 C 内存被释放。"""
    __slots__ = ('frame', 'width', 'height',
                 'y_ptr', 'u_ptr', 'v_ptr',
                 'stride_y', 'stride_u', 'stride_v')

    def __init__(self, frame):
        self.frame = frame
        self.width = frame.width
        self.height = frame.height
        self.y_ptr = int(frame.planes[0].buffer_ptr)
        self.u_ptr = int(frame.planes[1].buffer_ptr)
        self.v_ptr = int(frame.planes[2].buffer_ptr)
        self.stride_y = frame.planes[0].line_size
        self.stride_u = frame.planes[1].line_size
        self.stride_v = frame.planes[2].line_size


class OpenGL投屏视图(QWidget):
    """OpenGL 投屏视图控件，GPU 零拷贝渲染 YUV 帧。"""

    帧更新 = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = None
        self._当前帧: Optional[_帧信息] = None
        self._gl_widget: Optional['_GL渲染控件'] = None
        self._按下位置 = None

        if GL is None:
            self._占位 = QLabel("需要安装 PyOpenGL:\npip install PyOpenGL PyOpenGL_accelerate", self)
            self._占位.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._占位.setStyleSheet("color: gray;")
            layout = QVBoxLayout(self)
            layout.addWidget(self._占位)
            return

        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(0)
        fmt.setStencilBufferSize(0)
        fmt.setSwapInterval(0)  # 关闭垂直同步
        QSurfaceFormat.setDefaultFormat(fmt)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._gl_widget = _GL渲染控件(self)
        layout.addWidget(self._gl_widget)

        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def 绑定客户端(self, client):
        self.client = client
        if hasattr(client, '帧就绪'):
            client.帧就绪.connect(self._有新帧)

    def _有新帧(self):
        if not self.client:
            return
        frame = self.client.获取原始帧()
        if frame is None:
            return
        self._当前帧 = _帧信息(frame)  # 覆盖旧帧，自动 unref
        if self._gl_widget:
            self._gl_widget.update()
        self.帧更新.emit()

    def 获取当前帧尺寸(self):
        if self._当前帧:
            return (self._当前帧.width, self._当前帧.height)
        return (0, 0)

    def _坐标转换(self, pos: QPoint):
        if not self.client:
            return (0, 0)
        pw, ph = self.获取当前帧尺寸()
        if pw == 0 or ph == 0:
            return (0, 0)
        ww, wh = self._gl_widget.width(), self._gl_widget.height()
        scale = min(ww / pw, wh / ph)
        dw, dh = int(pw * scale), int(ph * scale)
        ox, oy = (ww - dw) // 2, (wh - dh) // 2
        x = int((pos.x() - ox) / scale)
        y = int((pos.y() - oy) / scale)
        return (max(0, min(x, pw)), max(0, min(y, ph)))

    def mousePressEvent(self, event: QMouseEvent):
        if not self.client:
            return
        self._按下位置 = event.position().toPoint()
        x, y = self._坐标转换(self._按下位置)
        self.client.点击(x, y)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self.client or not self._按下位置:
            return
        x, y = self._坐标转换(event.position().toPoint())
        self.client.滑动(self._按下位置.x(), self._按下位置.y(), x, y)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._按下位置 = None


class _GL渲染控件(QOpenGLWidget):
    """内部 OpenGL 渲染控件。"""

    def __init__(self, parent: OpenGL投屏视图):
        super().__init__(parent)
        self._父视图 = parent
        self._program = 0
        self._texY = 0
        self._texU = 0
        self._texV = 0
        self._vao = 0
        self._vbo = 0
        self._tex_width = 0
        self._tex_height = 0
        # 每个平面独立的环形 PBO 池
        self._pbo_y = _平面PBO池()
        self._pbo_u = _平面PBO池()
        self._pbo_v = _平面PBO池()

    def initializeGL(self):
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)

        vertices = [
            -1.0,  1.0,  0.0, 0.0,
            -1.0, -1.0,  0.0, 1.0,
             1.0,  1.0,  1.0, 0.0,
             1.0, -1.0,  1.0, 1.0,
        ]
        vertices_arr = (ctypes.c_float * len(vertices))(*vertices)

        self._vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self._vao)

        self._vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, ctypes.sizeof(vertices_arr),
                       vertices_arr, GL.GL_STATIC_DRAW)

        self._program = self._编译着色器(_VERTEX_SHADER, _FRAGMENT_SHADER)

        pos_loc = GL.glGetAttribLocation(self._program, b'aPos')
        tex_loc = GL.glGetAttribLocation(self._program, b'aTexCoord')
        stride = 4 * ctypes.sizeof(ctypes.c_float)
        GL.glEnableVertexAttribArray(pos_loc)
        GL.glVertexAttribPointer(pos_loc, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, None)
        GL.glEnableVertexAttribArray(tex_loc)
        GL.glVertexAttribPointer(tex_loc, 2, GL.GL_FLOAT, GL.GL_FALSE, stride,
                                ctypes.c_void_p(2 * ctypes.sizeof(ctypes.c_float)))

        # 纹理（初始分配 1x1，后续用 glTexSubImage2D 更新）
        self._texY, self._texU, self._texV = GL.glGenTextures(3)
        for tex in (self._texY, self._texU, self._texV):
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8, 1, 1, 0,
                           GL.GL_RED, GL.GL_UNSIGNED_BYTE, None)

        # PBO 池初始化（4MB 初始）
        init_size = 4 * 1024 * 1024
        self._pbo_y.init(init_size)
        self._pbo_u.init(init_size)
        self._pbo_v.init(init_size)

        GL.glBindVertexArray(0)

    def resizeGL(self, w, h):
        GL.glViewport(0, 0, w, h)

    def showEvent(self, event):
        """QOpenGLWidget 在对话框中首次显示时，显式触发一次重绘。"""
        super().showEvent(event)
        self.update()

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)

        帧 = self._父视图._当前帧
        if 帧 is None or not self._program:
            return

        try:
            self._上传帧纹理(帧)
            self._绘制()
        except Exception as e:
            print(f'[投屏GL] 渲染错误: {e}')

    def cleanupGL(self):
        """上下文销毁前清理所有 OpenGL 资源。"""
        self.makeCurrent()
        try:
            self._pbo_y.cleanup()
            self._pbo_u.cleanup()
            self._pbo_v.cleanup()
            if self._vao:
                GL.glDeleteVertexArrays(1, [self._vao])
                self._vao = 0
            if self._vbo:
                GL.glDeleteBuffers(1, [self._vbo])
                self._vbo = 0
            if self._texY:
                GL.glDeleteTextures(3, [self._texY, self._texU, self._texV])
                self._texY = self._texU = self._texV = 0
            if self._program:
                GL.glDeleteProgram(self._program)
                self._program = 0
        except Exception as e:
            print(f'[投屏GL] cleanup错误: {e}')
        finally:
            self.doneCurrent()

    def _编译着色器(self, vertex_src, fragment_src):
        def 编译单个(src, shader_type):
            shader = GL.glCreateShader(shader_type)
            GL.glShaderSource(shader, src)
            GL.glCompileShader(shader)
            if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
                log = GL.glGetShaderInfoLog(shader)
                print(f'[投屏GL] 着色器编译失败: {log}')
                GL.glDeleteShader(shader)
                return 0
            return shader

        vs = 编译单个(vertex_src, GL.GL_VERTEX_SHADER)
        fs = 编译单个(fragment_src, GL.GL_FRAGMENT_SHADER)
        if not vs or not fs:
            return 0

        program = GL.glCreateProgram()
        GL.glAttachShader(program, vs)
        GL.glAttachShader(program, fs)
        GL.glLinkProgram(program)
        GL.glDeleteShader(vs)
        GL.glDeleteShader(fs)

        if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
            log = GL.glGetProgramInfoLog(program)
            print(f'[投屏GL] 程序链接失败: {log}')
            GL.glDeleteProgram(program)
            return 0
        return program

    def _上传帧纹理(self, 帧: _帧信息):
        """三平面各自走独立 PBO 池，glTexSubImage2D 异步上传。"""
        w, h = 帧.width, 帧.height
        hw, hh = (w + 1) // 2, (h + 1) // 2

        # 尺寸变化时重新分配纹理内存
        if w != self._tex_width or h != self._tex_height:
            self._tex_width, self._tex_height = w, h
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._texY)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8, w, h, 0,
                           GL.GL_RED, GL.GL_UNSIGNED_BYTE, None)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._texU)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8, hw, hh, 0,
                           GL.GL_RED, GL.GL_UNSIGNED_BYTE, None)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._texV)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8, hw, hh, 0,
                           GL.GL_RED, GL.GL_UNSIGNED_BYTE, None)

        # 确保 PBO 足够大
        y_size = 帧.stride_y * h
        u_size = 帧.stride_u * hh
        v_size = 帧.stride_v * hh
        self._pbo_y.ensure_size(y_size)
        self._pbo_u.ensure_size(u_size)
        self._pbo_v.ensure_size(v_size)

        # 三平面各自独立 PBO 池异步上传
        self._pbo_y.upload(帧.y_ptr, y_size, self._texY, w, h, 帧.stride_y, GL.GL_R8)
        self._pbo_u.upload(帧.u_ptr, u_size, self._texU, hw, hh, 帧.stride_u, GL.GL_R8)
        self._pbo_v.upload(帧.v_ptr, v_size, self._texV, hw, hh, 帧.stride_v, GL.GL_R8)

    def _绘制(self):
        GL.glUseProgram(self._program)
        GL.glUniform1i(GL.glGetUniformLocation(self._program, b'texY'), 0)
        GL.glUniform1i(GL.glGetUniformLocation(self._program, b'texU'), 1)
        GL.glUniform1i(GL.glGetUniformLocation(self._program, b'texV'), 2)

        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texY)
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texU)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texV)

        GL.glBindVertexArray(self._vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        GL.glBindVertexArray(0)
