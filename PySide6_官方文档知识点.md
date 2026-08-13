# PySide6 官方文档知识点

> 来源：Qt for Python 官方文档 <https://doc.qt.io/qtforpython-6/>
> 整理日期：2026-08-11
> 适用：Super_ADB 项目（GUI 基于 PySide6）

## 1. 项目定位

- **PySide6** 是 Qt for Python 项目的官方 Python 绑定，让你用 Python 调用 Qt 6 的完整 API。
- 项目两大组件：
  - **PySide6**：Qt6 的 Python 绑定。
  - **Shiboken6**：绑定生成器，可把 C++ 项目暴露给 Python。
- 许可证：开源 LGPLv3 / GPLv3 + Qt 商业许可。PyPI 上的 wheel 同时适用于两种授权。
- 安装：`pip install pyside6`（推荐用 venv 虚拟环境，避免污染系统 Python）。

## 2. 两种 UI 技术路线

| 技术 | 范式 | 适用 |
|------|------|------|
| **Qt Widgets** | 命令式、面向对象，历史悠久稳定 | 传统桌面工具类应用（Super_ADB 选用） |
| **Qt Quick (QML)** | 声明式，用 QML 描述流畅界面 | 触摸/动态界面 |

- Qt Widgets 配套可视化设计器：`pyside6-designer`（安装 PySide6 自带）。
- Qt Quick 配套：`Qt Design Studio`。

## 3. 最小 Qt Widgets 应用骨架

```python
import sys
import random
from PySide6 import QtCore, QtWidgets, QtGui

class MyWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.hello = ["Hallo Welt", "Hei maailma", "Hola Mundo"]
        self.button = QtWidgets.QPushButton("Click me!")
        self.text = QtWidgets.QLabel("Hello World",
                                     alignment=QtCore.Qt.AlignCenter)
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(self.text)
        self.layout.addWidget(self.button)
        self.button.clicked.connect(self.magic)

    @QtCore.Slot()
    def magic(self):
        self.text.setText(random.choice(self.hello))

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    widget = MyWidget()
    widget.resize(800, 600)
    widget.show()
    sys.exit(app.exec())
```

要点：
- 每个 GUI 程序必须有且仅有一个 `QApplication` 实例。
- 主循环 `app.exec()`；`sys.exit()` 保证退出码正确回传。
- 类属性信号/槽需在类内定义（见第 5 节）。

## 4. 核心模块（PySide6 Essentials wheel 包含）

`QtCore`、`QtGui`、`QtWidgets`、`QtNetwork`、`QtConcurrent`、`QtDBus`、
`QtDesigner`、`QtOpenGL`、`QtOpenGLWidgets`、`QtPrintSupport`、`QtQml`、
`QtQuick`、`QtQuickControls2`、`QtQuickWidgets`、`QtXml`、`QtTest`、
`QtSql`、`QtSvg`、`QtSvgWidgets`、`QtUiTools`、`QtHelp` 等。

常用三件套：`QtCore`（非 GUI 核心：事件循环、信号槽、定时器、线程）、
`QtGui`（绘图、字体、图标）、`QtWidgets`（控件与布局）。

## 5. 信号与槽（Signals & Slots）——最核心机制

Qt 的通信机制：对象状态变化时**发射（emit）信号**，连接到该信号的**槽（slot）**被调用。

### 5.1 声明信号

信号是类级别的 `Signal()` 变量，类需继承自 `QObject`（直接或间接，如 QWidget）。

```python
from PySide6.QtCore import Qt, Signal

class Button(QtWidgets.QWidget):
    clicked = Signal(Qt.MouseButton)          # 单个 Qt 类型
    speak = Signal((int,), (str,))            # 多类型重载（兼容旧写法）
    sumResult = Signal(int, arguments=['sum'])

    def mousePressEvent(self, event):
        self.clicked.emit(event.button())     # 发射信号
```

- `Signal(int)` / `Signal(QUrl)` / `Signal(int, str, int)` 多参数。
- `Signal((float,), (QDate,))` 可选类型重载。
- `name='rangeChanged'` 显式命名；`arguments=[...]` 便于 QML 按名取值。

### 5.2 声明槽

槽用 `@QtCore.Slot()` 装饰器标记；同样可传类型签名、`name`、`result`。

```python
@QtCore.Slot(str)
def slot_function(self, s):
    ...

@Slot(int)
@Slot(str)
def say_something(self, arg):
    ...
```

> **官方建议**：所有被信号连接调用的方法都加上 `@Slot()`。不加会在建立连接时把方法加入 QMetaObject，带来运行时开销；注册到 QML 的 QObject 类缺装饰器还可能引入 bug。可用环境变量诊断：
> `QT_LOGGING_RULES="qt.pyside.libpyside.warning=true"`

### 5.3 连接与断开

```python
button.clicked.connect(say_hello)        # 连接到自由函数 / 可调用对象
a.valueChanged.connect(b.setValue)        # 信号连信号连接
someone.speak[int].connect(say_something) # 重载信号按类型选择
someone.speak[str].emit("Hello!")         # 重载信号按类型发射
obj.disconnect()                          # 断开连接
```

- 也可使用旧式 C++ 风格字符串签名 `SIGNAL("clicked(Qt::MouseButton)")`，但不推荐，多用于 `registerField()` 之类特殊场景。
- 多个槽连接同一信号时，按连接顺序依次执行；默认连接为同步（发射后槽立即执行，发射点之后的代码随后继续）。

### 5.4 线程中的信号槽

跨线程通信的标准范式：子线程 `QThread` 中持有继承自 `QObject` 的信号对象，主线程槽接收。

```python
class MySignals(QObject):
    signal_str = Signal(str)
    signal_int = Signal(int)

class WorkerThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = MySignals()
        self.signals.signal_str.connect(parent.update_str_field)

    def run(self):
        self.signals.signal_int.emit(2)
        self.signals.signal_str.emit("来自子线程的消息")
```

## 6. Python 化特性（来自 `__feature__`）

让接口更像 Python：

```python
from __feature__ import snake_case       # camelCase → snake_case
from __feature__ import true_property    # getter/setter → property
# 例：
self.table.horizontal_header().section_resize_mode = QHeaderView.Stretch
```

## 7. Python 枚举（6.3+ 新枚举，6.6 起强制）

- 新代码推荐直接写完整枚举：`Qt.AlignmentFlag.AlignCenter`。
- **Forgiveness Mode**：旧写法（如 `Qt.AlignCenter`）仍被静默转换，但类型提示只认新写法。
- 限制：全局枚举（如 `QtMsgType`）无宽容模式，必须写全 `QtMsgType.QtDebugMsg`。

## 8. 其余重要 API 注意事项

- **`qApp`**：导入 `PySide6` 后可直接用，等价于 `QApplication.instance()`。推荐 `qApp or QtWidgets.QApplication()`。嵌入式（C++ 预创建 App）场景下不可用 `qApp`，须用 `QApplication.instance()`。
- **QString 已 Python 化**：相关方法接收不可变 `str` 并返回 `str`（如 `QValidator.validate` 返回 `[State, string, int]`）；`QFileDialog.getOpenFileName()` 返回元组。
- **QVariant 已移除**：接收任意 Python 对象，`None` 视为无效；返回时自动转回原 Python 类型。
- **哈希**：`QDate`/`QDateTime`/`QTime`/`QUrl` 的哈希基于其字符串表示，值相同则哈希相同。

## 9. 权限 API 部署注意（6.5+）

涉及相机、麦克风等跨平台权限时：
- **解释模式（`python main.py`）不工作**，必须打包部署。
- Android：`pyside6-android-deploy`；macOS：`pyside6-deploy` 生成带 `Info.plist` 的 bundle。
- 解释模式下可用 `if "__compiled__" in globals():` 跳过权限代码。

## 10. 自带命令行工具

`pyside6-designer`（可视化 UI 设计）、`pyside6-uic`（.ui → .py）、
`pyside6-rcc`（资源编译）、`pyside6-deploy`（部署打包）、
`pyside6-android-deploy`、`pyside6-assistant`、`pyside6-project`（自动建项目）。

## 11. 学习资源导航

| 类型 | 入口 |
|------|------|
| 第一个应用 | gettingstarted.html |
| API 参考 | api.html |
| 教程 | tutorials/index.html |
| 示例 | examples/index.html |
| 部署 | deployment/index.html |
| 开发者笔记 / 已知问题 | developer/index.html / considerations.html |

---

## 12. 线程模型（深入）

Qt 主线程即 **GUI 线程**（事件循环所在）。任何耗时操作（ADB 命令、文件扫描、哈希计算）都必须移出主线程，否则界面卡死。PySide6 提供三种常用范式：

### 12.1 `QThread` 子类（重写 `run()`）

最简单直接，适合单个后台任务。

```python
from PySide6.QtCore import QThread, Signal

class HashWorker(QThread):
    progress = Signal(int)
    finished = Signal(str)

    def run(self):
        # 耗时逻辑写在 run() 内
        self.progress.emit(50)
        self.finished.emit("done")
```

要点：
- 不要重写 `QThread.exec_()` 之外的事件循环；耗时逻辑放 `run()`。
- 通过信号把结果/进度回传主线程；**禁止直接操作 UI 控件**（跨线程访问 QWidget 不安全）。
- 启动 `worker.start()`，结束连接 `finished` 信号清理（`worker.deleteLater()`）。

### 12.2 `QObject` + `moveToThread`（worker/controller 分离）

官方推荐范式：worker 本身不是线程，而是被移到一个 `QThread` 中，通过信号槽与 UI 通信。

```python
from PySide6.QtCore import QObject, QThread, Signal, Slot

class Worker(QObject):
    result = Signal(str)
    def do_work(self):
        self.result.emit("ok")

thread = QThread()
worker = Worker()
worker.moveToThread(thread)
thread.started.connect(worker.do_work)
worker.result.connect(ui_handler)
worker.result.connect(thread.quit)   # 完成后退出线程
thread.finished.connect(thread.deleteLater)
thread.start()
```

要点：
- worker 的方法由线程的 `started` 信号触发，而非主动调用。
- 比“子类化 QThread”更清晰地分离了“任务逻辑”与“线程生命周期”。
- Super_ADB 的 `wifi_dialog.py`、`qr_connect_page.py`、`lan_scanner_dialog.py` 即用此范式。

### 12.3 `QRunnable` + `QThreadPool`（短任务池）

适合大量轻量、独立任务（如并发执行多条 ADB 命令）。

```python
from PySide6.QtCore import QRunnable, QThreadPool, Signal, QObject

class _CmdWorker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
    def run(self):
        self.fn()

pool = QThreadPool()
pool.start(_CmdWorker(some_task))
```

要点：
- `QRunnable` **没有内置信号槽**，需借助一个 `QObject` 承载 `Signal` 来回传结果（Super_ADB 的 `file_manager_page.py` / `log_viewer_page.py` 即为此做法）。
- 线程池自动管理数量（默认上限 = CPU 核数 × 2），比手动 `QThread` 更省心。
- `QRunnable` 不支持信号，故“结果回传”必须外包给信号对象。

> **三条铁律**：①耗时逻辑绝不放主线程；②子线程不直接碰 UI，只通过信号回填；③线程结束要 `quit()`/`deleteLater()`，防止泄漏。

## 13. UI 文件（`.ui`）加载（深入）

Qt Designer 生成的 `.ui` 是 XML；PySide6 有两种使用方式。

### 13.1 编译期：`pyside6-uic` 生成 Python（Super_ADB 采用）

```bash
pyside6-uic Super_ADB.ui -o Super_ADB.py
```

生成的 `Super_ADB.py` 含 `Ui_MainWindow` 类，使用方式：

```python
from Super_ADB import Ui_MainWindow

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)              # 把 .ui 里的控件挂到本窗口
        self.ui.pushButton.clicked.connect(self.on_click)
```

- 优点：纯 Python、可被 PyInstaller 静态分析、运行无 XML 解析开销。
- Super_ADB 主窗口即此模式（`Super_ADB_Main.py` 驱动 `Ui_MainWindow`）。
- 改了 `.ui` 后**必须重新执行 `pyside6-uic`** 才能生效。

### 13.2 运行期：`QUiLoader` 动态加载（无需预编译）

```python
from PySide6.QtUiTools import QUiLoader

loader = QUiLoader()
ui = loader.load("Super_ADB.ui", self)
ui.show()
```

- 适合插件式/运行时换肤；但 PyInstaller 打包时需额外收集 `.ui` 文件与 `uiTools` 模块。
- 动态加载拿不到强类型属性提示，控件需 `ui.findChild(QPushButton, "pushButton")` 检索。

### 13.3 资源文件 `.qrc` → `png_rc.py`

图标、图片等通过 `pyside6-rcc` 编译进二进制：

```bash
pyside6-rcc png.qrc -o png_rc.py
```

随后 `import png_rc` 即可在代码中以 `:/prefix/name` 引用资源。Super_ADB 的 `png_rc.py` 即由此生成。

## 14. 与本项目（Super_ADB）关联性（更新）

- Super_ADB 采用 **Qt Widgets** 路线，源码位于 `Super_ADB_Main/`，UI 定义在 `ui/Super_ADB.ui`。
- **UI 加载**：主窗口走 `pyside6-uic` 编译产物 `Super_ADB.py`（`Ui_MainWindow.setupUi`）；部分页面（如 `file_manager_page`、`log_viewer_page`）把 `.ui` 预定义控件“注入”到动态构建的容器里（两套控件并存、以 `.ui` 为准）。
- **线程**：项目三种范式都在用——
  - `QThread` 子类：`md5_dialog.py`（HashWorker）、`install_zip_dialog.py`（TaskThread/LoadPackageThread/BuildTreeThread）。
  - `QObject` + `moveToThread`：`wifi_dialog.py`、`qr_connect_page.py`、`lan_scanner_dialog.py`（并特意规避 `threading.Thread` + `QTimer` 跨线程投递，统一为 Qt 线程模型）。
  - `QRunnable` + `QThreadPool`：`file_manager_page.py`、`log_viewer_page.py`（用内嵌 `QObject` 承载信号回传结果）。
- 跨线程任务（ADB 命令执行、设备轮询、局域网扫描）均通过信号槽回填 UI，符合第 12 节铁律。
- 打包发布用 `pyside6-deploy`（注意权限 API 部署限制，见第 9 节）。
