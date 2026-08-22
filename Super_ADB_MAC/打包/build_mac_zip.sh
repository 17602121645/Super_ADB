#!/bin/bash
# ==============================================================================
# Super_ADB — macOS 一键打包 + 签名 + ZIP 分发脚本
# ==============================================================================
# 用法：
#   cd 项目根目录
#   bash 打包/build_mac_zip.sh
#
# 产物：
#   打包/dist/Super_ADB.app          — 签名后的应用包
#   打包/dist/Super_ADB_mac.zip      — 可分发的 ZIP 压缩包
#
# 用户拿到 ZIP 后：
#   1. 解压得到 Super_ADB.app
#   2. 拖入 /Applications（或直接双击运行）
#   3. 首次启动：右键 Super_ADB → 打开（绕过 Gatekeeper）
#   4. 之后可正常双击打开
#
# 与 DMG 方案的区别：
#   - 更简单，不需要 create-dmg
#   - 用户需手动解压，没有拖拽安装的视觉体验
#   - 适合小范围快速分发
# ==============================================================================

set -e

# ── 颜色输出 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── 0. 环境检查 ─────────────────────────────────────────────────────────────
info "检查运行环境..."

if [[ "$(uname)" != "Darwin" ]]; then
    error "本脚本只能在 macOS 上运行（当前: $(uname)）"
fi

# 项目根目录 = 脚本所在目录的上一级
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_ROOT/打包/dist"
APP_PATH="$DIST_DIR/Super_ADB.app"
ZIP_PATH="$DIST_DIR/Super_ADB_mac.zip"

info "项目根目录: $PROJECT_ROOT"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    error "未找到 python3，请先安装 Python 3"
fi
PYTHON="python3"

# 检查 PyInstaller
if ! $PYTHON -c "import PyInstaller" &> /dev/null; then
    warn "未安装 PyInstaller，正在安装..."
    $PYTHON -m pip install pyinstaller
fi
ok "PyInstaller 可用"

# 检查 zip（macOS 自带）
if ! command -v zip &> /dev/null; then
    error "未找到 zip 命令（macOS 应自带，请检查系统环境）"
fi
ok "zip 可用"

# ── 1. 清理旧产物 ───────────────────────────────────────────────────────────
info "清理旧构建产物..."
rm -rf "$DIST_DIR/Super_ADB"
rm -rf "$APP_PATH"
rm -f "$ZIP_PATH"
rm -rf "$PROJECT_ROOT/打包/build"
ok "旧产物已清理"

# ── 2. PyInstaller 打包 ─────────────────────────────────────────────────────
info "开始 PyInstaller 打包（这可能需要 1-3 分钟）..."
cd "$PROJECT_ROOT"
$PYTHON 打包/精简打包exe.py

if [[ ! -d "$APP_PATH" ]]; then
    error "打包失败：未找到 $APP_PATH"
fi
ok "打包完成: $APP_PATH"

# ── 3. ad-hoc 深度签名 ──────────────────────────────────────────────────────
info "执行 ad-hoc 深度签名（递归签名 .app 内所有二进制，包括 adb/scrcpy）..."

# --deep: 递归签名所有嵌套代码
# --force: 覆盖已有签名
# --sign -: 使用 ad-hoc 签名（不需要证书）
codesign --force --deep --sign - "$APP_PATH"

# 验证签名
info "验证签名..."
if codesign --verify --deep --strict "$APP_PATH" 2>&1; then
    ok "签名验证通过"
else
    error "签名验证失败"
fi

# 显示签名信息
codesign --display --verbose=2 "$APP_PATH" 2>&1 | head -5
ok "签名信息确认"

# ── 4. 生成 ZIP ─────────────────────────────────────────────────────────────
info "生成 ZIP 压缩包..."
cd "$DIST_DIR"

# 使用 ditto 生成 zip（比 zip 命令更好地保留 macOS 元数据和符号链接）
# --keepParent: 保留 .app 父目录结构，解压后直接得到 Super_ADB.app
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

if [[ ! -f "$ZIP_PATH" ]]; then
    error "ZIP 生成失败"
fi

ZIP_SIZE=$(du -h "$ZIP_PATH" | cut -f1)
ok "ZIP 生成完成: $ZIP_PATH (${ZIP_SIZE})"

# ── 5. 完成 ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    打包完成！                                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  应用包:  $APP_PATH"
echo "  ZIP包:   $ZIP_PATH (${ZIP_SIZE})"
echo ""
echo -e "${YELLOW}── 分发给用户的说明 ──${NC}"
echo "  1. 用户解压 Super_ADB_mac.zip，得到 Super_ADB.app"
echo "  2. 将 Super_ADB.app 拖入 /Applications（或直接双击运行）"
echo "  3. 首次启动：右键 Super_ADB → 打开"
echo "     （第一次必须右键打开，绕过 Gatekeeper 安全提示）"
echo "  4. 弹窗点「打开」，之后即可正常双击启动"
echo ""
echo -e "${YELLOW}── 可选：用户彻底解除隔离标记 ──${NC}"
echo "  用户在终端执行："
echo "    xattr -d com.apple.quarantine /Applications/Super_ADB.app"
echo ""
echo -e "${YELLOW}── 校验 ZIP 完整性（可选）──${NC}"
echo "  用户可执行："
echo "    shasum -a 256 Super_ADB_mac.zip"
echo "  与你提供的 SHA256 对比，确认文件未被篡改"
echo ""

# 输出 SHA256 供分发时校验
if command -v shasum &> /dev/null; then
    SHA256=$(shasum -a 256 "$ZIP_PATH" | cut -d' ' -f1)
    echo -e "${GREEN}  SHA256: ${SHA256}${NC}"
    echo ""
fi
