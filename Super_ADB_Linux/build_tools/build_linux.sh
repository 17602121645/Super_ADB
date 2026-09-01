#!/usr/bin/env bash
# ==============================================================================
# Super_ADB Linux 一键打包脚本
# ==============================================================================
# 用法：
#   bash build_tools/build_linux.sh              # 常规打包
#   bash build_tools/build_linux.sh --install-deps  # 先安装依赖再打包
#   bash build_tools/build_linux.sh --clean      # 清理旧构建后重新打包
#   bash build_tools/build_linux.sh --archive    # 打包完成后生成 tar.gz 压缩包
#   bash build_tools/build_linux.sh --help       # 显示帮助
#
# 产物：
#   build_tools/dist/Super_ADB/          可执行文件目录（onedir 模式）
#   build_tools/dist/Super_ADB/Super_ADB 主程序
# ==============================================================================

set -euo pipefail

# ── 颜色定义 ────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' NC=''
fi

# ── 日志函数 ────────────────────────────────────────────────────────────────
log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()    { echo -e "\n${CYAN}${BOLD}═══ $* ═══${NC}"; }

# ── 帮助信息 ────────────────────────────────────────────────────────────────
show_help() {
    cat <<'EOF'
Super_ADB Linux 一键打包脚本

用法:
  bash build_tools/build_linux.sh [选项]

选项:
  --install-deps   打包前自动安装 Python 依赖（pip install -r requirements_linux.txt）
  --clean          打包前清理旧的 build/ 和 dist/ 目录
  --archive        打包完成后生成 Super_ADB_linux_x86_64.tar.gz 压缩包
  --skip-exec-fix  跳过外部工具执行权限修复步骤
  --help           显示此帮助信息

示例:
  bash build_tools/build_linux.sh                          # 常规打包
  bash build_tools/build_linux.sh --clean --archive        # 清理后打包并生成压缩包
  bash build_tools/build_linux.sh --install-deps --clean   # 安装依赖、清理、打包

产物位置:
  build_tools/dist/Super_ADB/Super_ADB    主可执行文件
  build_tools/dist/Super_ADB/              完整运行目录（含依赖和外部工具）
EOF
}

# ── 参数解析 ────────────────────────────────────────────────────────────────
INSTALL_DEPS=false
CLEAN_BUILD=false
MAKE_ARCHIVE=false
SKIP_EXEC_FIX=false

for arg in "$@"; do
    case "$arg" in
        --install-deps) INSTALL_DEPS=true ;;
        --clean)        CLEAN_BUILD=true ;;
        --archive)      MAKE_ARCHIVE=true ;;
        --skip-exec-fix) SKIP_EXEC_FIX=true ;;
        --help|-h)      show_help; exit 0 ;;
        *)
            log_error "未知参数: $arg"
            echo "使用 --help 查看帮助"
            exit 1
            ;;
    esac
done

# ── 脚本目录与项目根目录 ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$SCRIPT_DIR/dist/Super_ADB"
BUILD_DIR="$SCRIPT_DIR/build"
SPEC_FILE="$SCRIPT_DIR/Super_ADB_linux.spec"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements_linux.txt"

cd "$PROJECT_ROOT"

# ── 横幅 ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat <<'EOF'
   _____                         _    ____  ____  ____
  / ____|                       | |  |  _ \|  _ \|  _ \
 | (___  _   _ _ __   ___ _ __ | |  | |_) | | | | |_) |
  \___ \| | | | '_ \ / _ \ '_ \| |  |  _ <| | | |  _ <
  ____) | |_| | |_) |  __/ | | | |  | |_) | |_| | |_) |
 |_____/ \__,_| .__/ \___|_| |_|_|  |____/|____/|____/
              | |
              |_|   Linux 一键打包脚本
EOF
echo -e "${NC}"

log_info "项目根目录: $PROJECT_ROOT"
log_info "打包目录:   $SCRIPT_DIR"

# ═══════════════════════════════════════════════════════════════════════════
# 1. 平台检查
# ═══════════════════════════════════════════════════════════════════════════
log_step "1/7 平台检查"

if [[ "$(uname -s)" != "Linux" ]]; then
    log_error "本脚本仅支持 Linux 平台，当前平台: $(uname -s)"
    exit 1
fi
log_ok "平台: Linux ($(uname -m))"

# ═══════════════════════════════════════════════════════════════════════════
# 2. Python 环境检查
# ═══════════════════════════════════════════════════════════════════════════
log_step "2/7 Python 环境检查"

# 选择 Python 解释器
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    log_error "未找到 Python 解释器，请安装 Python 3.8+"
    exit 1
fi

PYTHON_VERSION="$($PYTHON --version 2>&1 | awk '{print $2}')"
PYTHON_MAJOR="$($PYTHON -c 'import sys; print(sys.version_info.major)')"
PYTHON_MINOR="$($PYTHON -c 'import sys; print(sys.version_info.minor)')"

log_info "Python 版本: $PYTHON_VERSION ($PYTHON)"

if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 8 ]]; then
    log_error "需要 Python 3.8 或更高版本，当前版本: $PYTHON_VERSION"
    exit 1
fi
log_ok "Python 版本满足要求 (>=3.8)"

# 检查 pip
if ! $PYTHON -m pip --version &>/dev/null; then
    log_error "未找到 pip，请先安装 pip"
    exit 1
fi
log_ok "pip 可用"

# ═══════════════════════════════════════════════════════════════════════════
# 3. 依赖检查与安装
# ═══════════════════════════════════════════════════════════════════════════
log_step "3/7 依赖检查"

check_module() {
    $PYTHON -c "import $1" 2>/dev/null
}

MISSING_MODULES=()
for mod in PySide6 PIL segno pyzbar zeroconf ifaddr; do
    if ! check_module "$mod"; then
        MISSING_MODULES+=("$mod")
    fi
done

# PyInstaller 检查
if ! check_module PyInstaller; then
    MISSING_MODULES+=("pyinstaller")
fi

if [[ ${#MISSING_MODULES[@]} -gt 0 ]]; then
    log_warn "缺少以下依赖: ${MISSING_MODULES[*]}"
    if [[ "$INSTALL_DEPS" == true ]]; then
        log_info "正在安装依赖..."
        if [[ -f "$REQUIREMENTS_FILE" ]]; then
            $PYTHON -m pip install -r "$REQUIREMENTS_FILE"
        else
            log_warn "未找到 requirements_linux.txt，直接安装核心依赖"
            $PYTHON -m pip install PySide6 Pillow segno pyzbar zeroconf ifaddr pyinstaller
        fi
        log_ok "依赖安装完成"
    else
        log_error "缺少依赖，打包可能失败。"
        log_info "使用 --install-deps 参数自动安装依赖，或手动执行:"
        echo "  $PYTHON -m pip install -r $REQUIREMENTS_FILE"
        exit 1
    fi
else
    log_ok "所有运行依赖已安装"
fi

# 检查系统库 libzbar（pyzbar 依赖）
if command -v ldconfig &>/dev/null; then
    if ! ldconfig -p 2>/dev/null | grep -q libzbar; then
        log_warn "未检测到系统库 libzbar0，二维码扫码功能可能不可用"
        log_info "Debian/Ubuntu: sudo apt install libzbar0"
        log_info "Fedora/RHEL:  sudo dnf install zbar"
        log_info "Arch Linux:    sudo pacman -S zbar"
    else
        log_ok "系统库 libzbar 已安装"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# 4. 外部工具检查
# ═══════════════════════════════════════════════════════════════════════════
log_step "4/7 外部工具检查"

ADB_DIR="$PROJECT_ROOT/vendor/adb"
SCRCPY_DIR="$PROJECT_ROOT/vendor/scrcpy"

if [[ -d "$ADB_DIR" ]]; then
    ADB_BIN="$(find "$ADB_DIR" -name "adb" -type f 2>/dev/null | head -1)"
    if [[ -n "$ADB_BIN" ]]; then
        log_ok "找到 ADB: $ADB_BIN"
    else
        log_warn "ADB 目录存在但未找到 adb 二进制文件"
    fi
else
    log_warn "未找到 ADB 目录: $ADB_DIR"
    log_info "ADB 功能将不可用，可从 https://developer.android.com/tools/releases/platform-tools 下载"
fi

if [[ -d "$SCRCPY_DIR" ]]; then
    SCRCPY_BIN="$(find "$SCRCPY_DIR" -name "scrcpy" -type f 2>/dev/null | head -1)"
    if [[ -n "$SCRCPY_BIN" ]]; then
        log_ok "找到 scrcpy: $SCRCPY_BIN"
    else
        log_warn "scrcpy 目录存在但未找到 scrcpy 二进制文件"
    fi
else
    log_warn "未找到 scrcpy 目录: $SCRCPY_DIR"
    log_info "投屏功能将不可用，可从 https://github.com/Genymobile/scrcpy/releases 下载"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 5. 清理旧构建
# ═══════════════════════════════════════════════════════════════════════════
log_step "5/7 构建准备"

if [[ "$CLEAN_BUILD" == true ]]; then
    if [[ -d "$BUILD_DIR" ]]; then
        log_info "清理旧构建目录: $BUILD_DIR"
        rm -rf "$BUILD_DIR"
    fi
    if [[ -d "$DIST_DIR" ]]; then
        log_info "清理旧产物目录: $DIST_DIR"
        rm -rf "$DIST_DIR"
    fi
    log_ok "旧构建已清理"
else
    if [[ -d "$DIST_DIR" ]]; then
        log_warn "产物目录已存在，将覆盖。使用 --clean 可彻底清理"
    fi
fi

# 确保输出目录存在
mkdir -p "$SCRIPT_DIR/dist"

# ═══════════════════════════════════════════════════════════════════════════
# 6. 执行 PyInstaller 打包
# ═══════════════════════════════════════════════════════════════════════════
log_step "6/7 执行打包 (PyInstaller)"

if [[ -f "$SPEC_FILE" ]]; then
    log_info "使用 spec 文件: $SPEC_FILE"
    # 显式指定输出目录：PyInstaller 默认写到 CWD 下的 dist/，而本脚本
    # 在 $SCRIPT_DIR/dist 里找产物（cd 到 PROJECT_ROOT 后两者并不相同）。
    $PYTHON -m PyInstaller --clean --noconfirm \
        --distpath "$SCRIPT_DIR/dist" --workpath "$BUILD_DIR" \
        "$SPEC_FILE"
else
    log_warn "未找到 spec 文件，使用精简打包脚本"
    $PYTHON "$SCRIPT_DIR/build_exe.py"
fi

if [[ ! -f "$DIST_DIR/Super_ADB" ]]; then
    log_error "打包失败：未找到产物 $DIST_DIR/Super_ADB"
    exit 1
fi
log_ok "打包成功: $DIST_DIR/Super_ADB"

# ═══════════════════════════════════════════════════════════════════════════
# 7. 后处理：执行权限修复 + 产物信息
# ═══════════════════════════════════════════════════════════════════════════
log_step "7/7 后处理"

# 修复外部工具执行权限
if [[ "$SKIP_EXEC_FIX" != true ]]; then
    log_info "修复外部工具执行权限..."
    FIXED_COUNT=0
    while IFS= read -r -d '' binfile; do
        if [[ ! -x "$binfile" ]]; then
            chmod +x "$binfile"
            log_info "  chmod +x $(realpath --relative-to="$DIST_DIR" "$binfile" 2>/dev/null || echo "$binfile")"
            ((FIXED_COUNT++)) || true
        fi
    done < <(find "$DIST_DIR" -type f \( -name "adb" -o -name "scrcpy" -o -name "scrcpy-server" \) -print0 2>/dev/null)

    # 也修复主程序执行权限
    if [[ -f "$DIST_DIR/Super_ADB" && ! -x "$DIST_DIR/Super_ADB" ]]; then
        chmod +x "$DIST_DIR/Super_ADB"
        ((FIXED_COUNT++)) || true
    fi

    if [[ $FIXED_COUNT -gt 0 ]]; then
        log_ok "已修复 $FIXED_COUNT 个文件的执行权限"
    else
        log_ok "执行权限正常，无需修复"
    fi
else
    log_info "跳过执行权限修复 (--skip-exec-fix)"
fi

# 计算产物大小
if command -v du &>/dev/null; then
    DIST_SIZE="$(du -sh "$DIST_DIR" 2>/dev/null | cut -f1)"
    EXE_SIZE="$(du -h "$DIST_DIR/Super_ADB" 2>/dev/null | cut -f1)"
else
    DIST_SIZE="未知"
    EXE_SIZE="未知"
fi

# 生成压缩包
if [[ "$MAKE_ARCHIVE" == true ]]; then
    ARCHIVE_NAME="Super_ADB_linux_$(uname -m)_$(date +%Y%m%d_%H%M%S).tar.gz"
    ARCHIVE_PATH="$SCRIPT_DIR/dist/$ARCHIVE_NAME"
    log_info "生成压缩包: $ARCHIVE_NAME"
    tar -czf "$ARCHIVE_PATH" -C "$SCRIPT_DIR/dist" "Super_ADB"
    ARCHIVE_SIZE="$(du -h "$ARCHIVE_PATH" 2>/dev/null | cut -f1)"
    log_ok "压缩包已生成: $ARCHIVE_PATH ($ARCHIVE_SIZE)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 完成
# ═══════════════════════════════════════════════════════════════════════════
echo -e "\n${GREEN}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  打包完成！${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  产物目录:   ${CYAN}$DIST_DIR${NC}"
echo -e "  主程序:     ${CYAN}$DIST_DIR/Super_ADB${NC}"
echo -e "  目录大小:   $DIST_SIZE"
echo -e "  主程序大小: $EXE_SIZE"
echo ""
echo -e "  运行方式:"
echo -e "    cd $DIST_DIR"
echo -e "    ./Super_ADB"
echo ""
if [[ "$MAKE_ARCHIVE" == true ]]; then
    echo -e "  压缩包:     ${CYAN}$ARCHIVE_PATH${NC} ($ARCHIVE_SIZE)"
    echo ""
fi
echo -e "${YELLOW}  注意:${NC}"
echo -e "    - 首次运行可能需要系统安装 libxcb、libxkbcommon 等 X11 库"
echo -e "    - 二维码扫码功能需要系统安装 libzbar0"
echo -e "    - WiFi 密码查看功能需要 NetworkManager (nmcli)"
echo -e "    - 投屏功能需要 scrcpy 及其依赖 (SDL2、ffmpeg 等)"
echo ""
