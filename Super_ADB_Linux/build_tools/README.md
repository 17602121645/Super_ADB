# Super_ADB 打包说明

## 目录结构

```
build_tools/
├── build_linux.sh          # Linux 一键打包脚本（推荐）
├── Super_ADB_linux.spec    # Linux 专用 PyInstaller 配置
├── requirements_linux.txt  # Linux 运行依赖清单
├── build_exe.py           # 跨平台 Python 打包脚本（Windows/macOS/Linux）
├── trim_qt.py               # 构建后 Qt 库裁剪工具（跨平台）
├── Super_ADB.spec          # Windows spec 文件
├── Super_ADB_mac.spec      # macOS spec 文件
├── hooks/                   # PyInstaller hooks（pyzbar 等）
├── build/                   # PyInstaller 构建临时目录（自动生成）
└── dist/                    # 打包产物目录（自动生成）
```

## Linux 打包（推荐方式）

### 前置要求

- Python 3.8+
- pip
- 系统库（Ubuntu/Debian 示例）：
  ```bash
  sudo apt install libxcb-xinerama0 libxkbcommon0 libzbar0
  ```

### 一键打包

```bash
# 进入项目根目录
cd Super_ADB_Linux

# 常规打包（依赖已安装时）
bash build_tools/build_linux.sh

# 自动安装依赖后打包
bash build_tools/build_linux.sh --install-deps

# 清理旧构建后打包
bash build_tools/build_linux.sh --clean

# 打包并生成 tar.gz 压缩包
bash build_tools/build_linux.sh --archive

# 完整流程：安装依赖 + 清理 + 打包 + 压缩
bash build_tools/build_linux.sh --install-deps --clean --archive
```

### 脚本参数

| 参数 | 说明 |
|------|------|
| `--install-deps` | 打包前自动安装 Python 依赖 |
| `--clean` | 打包前清理旧的 build/ 和 dist/ 目录 |
| `--archive` | 打包完成后生成 tar.gz 压缩包 |
| `--skip-exec-fix` | 跳过外部工具执行权限修复步骤 |
| `--help` | 显示帮助信息 |

### 打包产物

```
build_tools/dist/Super_ADB/
├── Super_ADB              # 主可执行文件
├── _internal/             # 依赖库和资源
│   ├── PySide6/
│   ├── resources/
│   ├── vendor/
│   │   ├── adb/           # Android 平台工具
│   │   └── scrcpy/        # 投屏工具
│   └── ...
└── ...
```

### 运行

```bash
cd build_tools/dist/Super_ADB
./Super_ADB
```

## Linux 打包（手动方式）

如果一键脚本不适用，可以手动执行：

```bash
# 1. 安装依赖
pip install -r build_tools/requirements_linux.txt

# 2. 使用 spec 文件打包
pyinstaller --clean --noconfirm build_tools/Super_ADB_linux.spec

# 3. 修复执行权限
chmod +x build_tools/dist/Super_ADB/Super_ADB
find build_tools/dist/Super_ADB -name "adb" -o -name "scrcpy" | xargs chmod +x

# 4. 运行
./build_tools/dist/Super_ADB/Super_ADB
```

## 跨平台 Python 打包脚本

`build_exe.py` 支持 Windows/macOS/Linux 三平台：

```bash
python build_tools/build_exe.py
```

该脚本会自动检测当前平台并选择对应的打包参数。

## 常见问题

### Q: 打包后运行报错 "could not load the Qt platform plugin"

A: 确保系统安装了 X11 相关库：
```bash
sudo apt install libxcb-xinerama0 libxcb-cursor0 libxkbcommon-x11-0
```

### Q: 二维码扫码功能不可用

A: 确保系统安装了 libzbar：
```bash
sudo apt install libzbar0
```

### Q: 投屏功能不可用

A: scrcpy 需要系统安装 SDL2、ffmpeg 等依赖：
```bash
sudo apt install libsdl2-2.0-0 libavformat58 libavcodec58 libavutil56
```

### Q: 从 Windows 拷贝的脚本运行报错 "$'\r': command not found"

A: 这是 Windows CRLF 换行符问题，转换为 LF 即可：
```bash
sed -i 's/\r$//' build_tools/build_linux.sh
# 或
dos2unix build_tools/build_linux.sh
```

### Q: 打包体积太大

A: 运行 Qt 库裁剪工具：
```bash
python build_tools/trim_qt.py
```
该工具会删除未使用的 Qt 模块、插件和翻译文件。

## 系统依赖速查

### Ubuntu / Debian
```bash
sudo apt install python3-pip python3-pyside6 libxcb-xinerama0 \
    libxkbcommon-x11-0 libzbar0 libsdl2-2.0-0
```

### Fedora / RHEL
```bash
sudo dnf install python3-pip python3-pyside6 libxcb \
    libxkbcommon-x11 zbar SDL2
```

### Arch Linux
```bash
sudo pacman -S python-pip python-pyside6 libxcb \
    libxkbcommon zbar sdl2
```
