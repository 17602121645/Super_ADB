#!/bin/bash
# ==============================================================================
# Super_ADB — macOS 一键打包 + 签名 + ZIP 分发脚本（自包含版）
# ==============================================================================
# 用法（在项目根目录任意位置执行均可）：
#   bash build_tools/build_mac_zip.sh
#
# 自包含说明：
#   本脚本会自动解析「用于构建的 Python 解释器」并确保其装好所有依赖
#   （PyInstaller / PySide6 / cryptography / pyusb / libusb），再直接执行
#   唯一的打包配置 build_tools/Super_ADB_mac.spec（由 spec 负责把 libusb 的 dylib
#   随包内置、挂运行时钩子、声明全部隐藏依赖），最终产出可直接分发的 ZIP。
#   若你的环境已经存在可用解释器（见下方优先级），不会重复下载任何东西。
#
# 构建用 Python 解析优先级（取第一个「能 import PyInstaller + PySide6」的）：
#   1) 环境变量 SUPER_ADB_PYTHON 指定的路径
#   2) 本脚本同目录下的 .build_venv/bin/python3（首次自动创建并装依赖）
#   3) 系统 python3（若已具备 PyInstaller + PySide6）
#   若以上都不满足，则自动在 build_tools/.build_venv 创建 venv 并安装全部依赖。
#
# 产物：
#   build_tools/dist/Super_ADB_MAC.app     — 深度签名后的应用包
#   build_tools/dist/Super_ADB_mac.zip     — 可分发的 ZIP 压缩包
#
# 用户拿到 ZIP 后：
#   1. 解压得到 Super_ADB.app
#   2. 拖入 /Applications（或直接双击运行）
#   3. 首次启动：右键 Super_ADB → 打开（绕过 Gatekeeper）
#   4. 之后可正常双击打开
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
DIST_DIR="$PROJECT_ROOT/build_tools/dist"
APP_PATH="$DIST_DIR/Super_ADB_MAC.app"
ZIP_PATH="$DIST_DIR/Super_ADB_mac.zip"

info "项目根目录: $PROJECT_ROOT"

# ── 1. 解析构建用 Python 并确保依赖 ──────────────────────────────────────────
PYTHON=""
# 候选列表（按优先级）
CANDIDATES=()
[[ -n "$SUPER_ADB_PYTHON" ]] && CANDIDATES+=("$SUPER_ADB_PYTHON")
CANDIDATES+=("$SCRIPT_DIR/.build_venv/bin/python3")
CANDIDATES+=("python3")

for c in "${CANDIDATES[@]}"; do
    [[ -z "$c" ]] && continue
    if "$c" -c "import PyInstaller, PySide6" >/dev/null 2>&1; then
        PYTHON="$c"
        break
    fi
done

# 均无 → 创建 .build_venv
if [[ -z "$PYTHON" ]]; then
    warn "未找到已具备 PyInstaller + PySide6 的解释器，将在 build_tools/.build_venv 创建并安装依赖（首次较慢）。"
    PYTHON="$SCRIPT_DIR/.build_venv/bin/python3"
    if [[ ! -x "$PYTHON" ]]; then
        python3 -m venv "$SCRIPT_DIR/.build_venv"
    fi
fi

info "构建用 Python: $PYTHON ($("$PYTHON" --version 2>&1))"

# 把该解释器所在目录置于 PATH 最前，确保 pyinstaller / pip 都指向它
export PATH="$(dirname "$PYTHON"):$PATH"

# 确保可选但必需的依赖已安装（缺失才装，已装则跳过）
ensure_dep() {
    local mod="$1" pkg="$2"
    if "$PYTHON" -c "import $mod" >/dev/null 2>&1; then
        ok "依赖已具备: $mod"
    else
        warn "缺少 $mod，正在安装 $pkg ..."
        "$PYTHON" -m pip install --upgrade "$pkg"
    fi
}
ensure_dep "PyInstaller" "pyinstaller"
ensure_dep "PySide6"    "PySide6"
ensure_dep "cryptography" "cryptography"
ensure_dep "usb"        "pyusb"
# libusb：打包版随包 dylib 的来源（仅构建期需要，运行时用随包 dylib；
# 具体 dylib 路径由 Super_ADB_mac.spec 按本机架构动态定位并随包）
ensure_dep "libusb"     "libusb"

# ── 2. 清理旧产物（移到 /tmp 回收目录，可恢复，且避免环境批量删除限制）────────
info "清理旧构建产物..."
TRASH="/tmp/super_adb_trash_$(date +%s)"
mkdir -p "$TRASH"
moved=0
for p in "$DIST_DIR/Super_ADB_MAC.app" "$DIST_DIR/Super_ADB_MAC" "$DIST_DIR/Super_ADB.app" "$DIST_DIR/Super_ADB" "$ZIP_PATH" "$PROJECT_ROOT/build_tools/build"; do
    if [[ -e "$p" ]]; then
        mv "$p" "$TRASH/" && moved=$((moved+1))
    fi
done
# TRIM_MOVE 产生的回收目录位于 dist 内，移走以免干扰后续构建/打包
for t in "$DIST_DIR"/_trimmed_trash_*; do
    [[ -e "$t" ]] && mv "$t" "$TRASH/" && moved=$((moved+1))
done
[[ "$moved" -gt 0 ]] && warn "已移走 $moved 个旧产物到 $TRASH（如需可手动删除）" || ok "无旧产物需清理"

# ── 3. PyInstaller 打包（直接执行唯一的 spec 配置）──────────────────────────
info "开始 PyInstaller 打包（直接执行 build_tools/Super_ADB_mac.spec，这可能需要 1-3 分钟）..."
cd "$PROJECT_ROOT"
"$PYTHON" -m PyInstaller --noconfirm \
    --distpath "$DIST_DIR" --workpath "$PROJECT_ROOT/build_tools/build" \
    build_tools/Super_ADB_mac.spec

if [[ ! -d "$APP_PATH" ]]; then
    error "打包失败：未找到 $APP_PATH（请查看上方 PyInstaller 日志）"
fi
ok "打包完成: $APP_PATH"

# ── 4. 写入打包信息 + 构建后裁剪 Qt 无用插件/翻译 ───────────────────────────
# libusb 已由 spec 的 binaries 随包内置，无需手动复制。
# build_info.json（下载地址/版本）写入应用内 config/，供关于页读取。
info "写入打包信息（config/build_info.json）..."
"$PYTHON" -c "import sys; sys.path.insert(0, r'$PROJECT_ROOT/build_tools'); import build_exe; build_exe._写入打包完成时间(r'$PROJECT_ROOT', 'Super_ADB_MAC')" 2>&1 | tail -2 || true

info "构建后裁剪 Qt 无用插件/翻译（减小体积）..."
# TRIM_MOVE=1：裁剪改为移动到回收目录（dist/_trimmed_trash_*），而非直接删除。
# 既便于误删恢复，也避免构建环境禁用批量删除时（SAFE_DELETE_BULK）整脚本中断。
export TRIM_MOVE=1
( cd "$PROJECT_ROOT" && "$PYTHON" -c "import sys; sys.path.insert(0, r'$PROJECT_ROOT/build_tools'); import trim_qt; trim_qt.main()" )

# ── 5. ad-hoc 深度签名 ──────────────────────────────────────────────────────
info "执行 ad-hoc 深度签名（递归签名 .app 内所有二进制，含 adb/scrcpy/libusb）..."
codesign --force --deep --sign - "$APP_PATH"

info "验证签名..."
if codesign --verify --deep --strict "$APP_PATH" 2>&1; then
    ok "签名验证通过"
else
    error "签名验证失败"
fi
codesign --display --verbose=2 "$APP_PATH" 2>&1 | head -5
ok "签名信息确认"

# ── 6. 生成 ZIP ─────────────────────────────────────────────────────────────
info "生成 ZIP 压缩包（UTF-8 安全，保留权限/符号链接）..."
"$PYTHON" "$SCRIPT_DIR/make_zip.py" "$APP_PATH" "$ZIP_PATH"

if [[ ! -f "$ZIP_PATH" ]]; then
    error "ZIP 生成失败"
fi
# 校验：ZIP 必须含 config/build_info.json 与 libusb（避免 ditto 丢中文目录的坑）
"$PYTHON" - "$ZIP_PATH" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
has_info = any('config/build_info.json' in n for n in names)
has_libusb = any('libusb-1.0.dylib' in n for n in names)
print('  zip 含 config/build_info.json:', has_info)
print('  zip 含 libusb-1.0.dylib  :', has_libusb)
if not (has_info and has_libusb):
    print('FAIL: zip 缺关键文件')
    sys.exit(1)
print('OK: zip 关键文件齐全')
PY
if [[ $? -ne 0 ]]; then
    error "ZIP 缺少关键文件（config/build_info.json 或 libusb），请检查 make_zip.py"
fi
ZIP_SIZE=$(du -h "$ZIP_PATH" | cut -f1)
ok "ZIP 生成完成: $ZIP_PATH (${ZIP_SIZE})"

# ── 7. 完成 ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    打包完成！                                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  应用包:  $APP_PATH"
echo "  ZIP包:   $ZIP_PATH (${ZIP_SIZE})"
echo ""
echo -e "${YELLOW}── 分发给用户的说明 ──${NC}"
echo "  1. 用户解压 Super_ADB_mac.zip，得到 Super_ADB_MAC.app"
echo "  2. 将 Super_ADB_MAC.app 拖入 /Applications（或直接双击运行）"
echo "  3. 首次启动：右键 Super_ADB_MAC → 打开"
echo "     （第一次必须右键打开，绕过 Gatekeeper 安全提示）"
echo "  4. 弹窗点「打开」，之后即可正常双击启动"
echo "     USB 直连（libusb 后端）已随包内置，无需目标机安装 libusb"
echo ""
echo -e "${YELLOW}── 可选：用户彻底解除隔离标记 ──${NC}"
echo "  用户在终端执行："
echo "    xattr -d com.apple.quarantine /Applications/Super_ADB_MAC.app"
echo ""
echo -e "${YELLOW}── 校验 ZIP 完整性（可选）──${NC}"
echo "  用户可执行："
echo "    shasum -a 256 Super_ADB_mac.zip"
echo "  与你提供的 SHA256 对比，确认文件未被篡改"
echo ""

if command -v shasum &> /dev/null; then
    SHA256=$(shasum -a 256 "$ZIP_PATH" | cut -d' ' -f1)
    echo -e "${GREEN}  SHA256: ${SHA256}${NC}"
    echo ""
fi
