#!/bin/bash
# ==============================================================================
# Super_ADB — macOS 一键打包 + 签名 + DMG 分发脚本
# ==============================================================================
# 用法：
#   cd 项目根目录
#   bash build_tools/build_mac_dmg.sh
#
# 产物：
#   build_tools/dist/Super_ADB.app          — 签名后的应用包
#   build_tools/dist/Super_ADB_mac.dmg      — 可分发的 DMG 镜像
#
# 用户拿到 DMG 后：
#   1. 双击打开 DMG
#   2. 把 Super_ADB 拖入 Applications
#   3. 首次启动：右键 Super_ADB → 打开（绕过 Gatekeeper）
#   4. 之后可正常双击打开
# ==============================================================================

set -e

# ── 颜色输出 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
DIST_DIR="$PROJECT_ROOT/build_tools/dist"
APP_PATH="$DIST_DIR/Super_ADB.app"
DMG_PATH="$DIST_DIR/Super_ADB_mac.dmg"

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

# 检查 create-dmg（可选，没有则用 hdiutil 兜底）
USE_CREATE_DMG=false
if command -v create-dmg &> /dev/null; then
    USE_CREATE_DMG=true
    ok "create-dmg 可用（将生成带 Applications 快捷方式的精美 DMG）"
else
    warn "未安装 create-dmg，将使用系统 hdiutil 生成基础 DMG"
    warn "如需精美 DMG，执行: brew install create-dmg"
fi

# ── 1. 清理旧产物 ───────────────────────────────────────────────────────────
info "清理旧构建产物..."
rm -rf "$DIST_DIR/Super_ADB"
rm -rf "$DIST_DIR/Super_ADB.app"
rm -f "$DMG_PATH"
rm -rf "$PROJECT_ROOT/build_tools/build"
ok "旧产物已清理"

# ── 2. PyInstaller 打包 ─────────────────────────────────────────────────────
info "开始 PyInstaller 打包（这可能需要 1-3 分钟）..."
cd "$PROJECT_ROOT"
$PYTHON build_tools/build_exe.py

if [[ ! -d "$APP_PATH" ]]; then
    error "打包失败：未找到 $APP_PATH"
fi
ok "打包完成: $APP_PATH"

# ── 3. ad-hoc 深度签名 ──────────────────────────────────────────────────────
info "执行 ad-hoc 深度签名（递归签名 .app 内所有二进制，包括 adb/scrcpy）..."

# --deep: 递归签名所有嵌套代码
# --force: 覆盖已有签名
# --sign -: 使用 ad-hoc 签名（不需要证书）
# --options runtime: 启用 Hardened Runtime（可选，ad-hoc 下可省略）
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

# ── 4. 生成 DMG ─────────────────────────────────────────────────────────────
info "生成 DMG 镜像..."

if $USE_CREATE_DMG; then
    # 使用 create-dmg：带 Applications 快捷方式 + 窗口布局
    create-dmg \
        --volname "Super_ADB" \
        --volicon "$PROJECT_ROOT/resources/Super_ADB.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "Super_ADB.app" 150 190 \
        --hide-extension "Super_ADB.app" \
        --app-drop-link 450 190 \
        "$DMG_PATH" \
        "$APP_PATH"
else
    # 使用 hdiutil 兜底：基础 DMG，手动创建 Applications 软链接
    STAGING_DIR="$DIST_DIR/_dmg_staging"
    rm -rf "$STAGING_DIR"
    mkdir -p "$STAGING_DIR"
    cp -R "$APP_PATH" "$STAGING_DIR/"
    ln -s /Applications "$STAGING_DIR/Applications"

    hdiutil create \
        -volname "Super_ADB" \
        -srcfolder "$STAGING_DIR" \
        -ov -format UDZO \
        -imagekey zlib-level=9 \
        "$DMG_PATH"

    rm -rf "$STAGING_DIR"
fi

if [[ ! -f "$DMG_PATH" ]]; then
    error "DMG 生成失败"
fi

DMG_SIZE=$(du -h "$DMG_PATH" | cut -f1)
ok "DMG 生成完成: $DMG_PATH (${DMG_SIZE})"

# ── 5. 完成 ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    打包完成！                                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  应用包:  $APP_PATH"
echo "  DMG镜像: $DMG_PATH (${DMG_SIZE})"
echo ""
echo -e "${YELLOW}── 分发给用户的说明 ──${NC}"
echo "  1. 用户双击打开 Super_ADB_mac.dmg"
echo "  2. 把 Super_ADB 图标拖入 Applications 文件夹"
echo "  3. 首次启动：在 Launchpad/应用程序中 右键 Super_ADB → 打开"
echo "     （第一次必须右键打开，绕过 Gatekeeper 安全提示）"
echo "  4. 弹窗点「打开」，之后即可正常双击启动"
echo ""
echo -e "${YELLOW}── 可选：彻底解除隔离标记 ──${NC}"
echo "  用户也可在终端执行："
echo "    xattr -d com.apple.quarantine /Applications/Super_ADB.app"
echo ""
