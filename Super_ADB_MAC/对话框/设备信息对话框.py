# -*- coding: utf-8 -*-
"""
设备信息对话框
==============
双框展示：上面 getprop 属性（中文分组），下面标识符（多线程逐行追加）。
主题跟随主窗口，apply_theme 切换时更新全局样式。
"""
import re as _re

from PySide6.QtCore import Qt, QThreadPool, Signal, QObject, QRunnable
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QLabel,
)

from 项目UI.界面样式 import get_stylesheet


class 命令工作器信号(QObject):
    result = Signal(object)
    error = Signal(Exception)
    finished = Signal()


class 命令工作器(QRunnable):
    """后台执行函数，结果通过信号回主线程。"""
    def __init__(self, func):
        super().__init__()
        self.func = func
        self.signals = 命令工作器信号()
        self.setAutoDelete(False)

    def run(self):
        try:
            r = self.func()
            self.signals.result.emit(r)
        except Exception as e:
            self.signals.error.emit(e)
        finally:
            self.signals.finished.emit()


class 设备信息对话框(QDialog):
    """设备信息弹窗：getprop + 多线程标识符获取。"""

    def __init__(self, adb, serial, theme_id, pool=None, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.serial = serial
        self._theme_id = theme_id
        self.pool = pool or QThreadPool.globalInstance()
        self._live_workers = []

        self.setWindowTitle(f'设备信息 — 设备: {serial}')
        self.setMinimumSize(760, 620)
        self.setStyleSheet(get_stylesheet(theme_id))

        self._build_ui()
        self._启动获取()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        label1 = QLabel('设备属性 (getprop)')
        lay.addWidget(label1)
        self.edit_getprop = QTextEdit()
        self.edit_getprop.setReadOnly(True)
        self.edit_getprop.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.edit_getprop.setPlainText('正在获取 getprop…')
        lay.addWidget(self.edit_getprop, 3)

        label2 = QLabel('设备标识符 (实时获取)')
        lay.addWidget(label2)
        self.edit_ids = QTextEdit()
        self.edit_ids.setReadOnly(True)
        self.edit_ids.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.edit_ids.setPlainText('正在并发获取标识符…\n')
        lay.addWidget(self.edit_ids, 2)

    def apply_theme(self, theme_id):
        """主题切换时更新全局样式。"""
        if theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        self.update()

    # ─────────────── 数据获取 ───────────────
    def _启动获取(self):
        # getprop
        def _取getprop():
            try:
                raw = self.adb.run_shell(self.serial, 'getprop', timeout=10)
                return self._格式化getprop(raw, self.serial)
            except Exception as e:
                return f'获取失败: {e}'

        w_getprop = 命令工作器(_取getprop)
        w_getprop.signals.result.connect(self._更新getprop框)
        w_getprop.signals.finished.connect(lambda: self._丢弃工作器(w_getprop))
        self._live_workers.append(w_getprop)
        self.pool.start(w_getprop)

        # 标识符列表
        标识符列表 = [
            ('系统时间', self._获取系统时间),
            ('WiFi IP', self._获取WiFiIP),
            ('电池状态', self._获取电池信息),
            ('存储使用', self._获取存储信息),
            ('内存使用', self._获取内存信息),
            ('MAC地址', self._获取MAC),
            ('IMEI', self._获取IMEI),
            ('广告ID(GAID)', self._获取GAID),
            ('OAID', self._获取OAID),
            ('Android ID', self._获取AndroidID),
        ]
        for 名称, 函数 in 标识符列表:
            def _取标识符(名称=名称, 函数=函数):
                try:
                    return (名称, 函数(self.serial))
                except Exception as e:
                    return (名称, f'获取失败: {e}')
            w_id = 命令工作器(_取标识符)
            w_id.signals.result.connect(
                lambda r, _w=w_id: self._追加标识符行(r[0], r[1]))
            w_id.signals.finished.connect(lambda _w=w_id: self._丢弃工作器(_w))
            self._live_workers.append(w_id)
            self.pool.start(w_id)

    def _丢弃工作器(self, w):
        try:
            self._live_workers.remove(w)
        except ValueError:
            pass

    def _更新getprop框(self, 文本):
        try:
            self.edit_getprop.setPlainText(文本)
        except Exception:
            pass

    def _追加标识符行(self, 名称, 值):
        try:
            当前 = self.edit_ids.toPlainText()
            if 当前.endswith('正在并发获取标识符…\n'):
                当前 = ''
            名称对齐 = self._对齐填充(名称, 16)
            行 = f'  {名称对齐} {值}\n'
            self.edit_ids.setPlainText(当前 + 行)
            self.edit_ids.moveCursor(QTextCursor.MoveOperation.End)
        except Exception:
            pass

    # ─────────────── 各标识符获取 ───────────────
    def _获取MAC(self, serial):
        for cmd in [
            'cat /sys/class/net/wlan0/address 2>/dev/null',
            "ip link show wlan0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1",
            'settings get secure wifi_mac_address 2>/dev/null',
        ]:
            try:
                v = self.adb.run_shell(serial, cmd, timeout=3).strip()
                if v and v != '02:00:00:00:00:00':
                    return v
            except Exception:
                continue
        return 'N/A(隐私保护)'

    def _获取IMEI(self, serial):
        for cmd in [
            'getprop gsm.imei 2>/dev/null',
            'getprop ro.ril.imei 2>/dev/null',
            "timeout 3 service call iphonesubinfo 1 2>/dev/null | tr -d \"'\" | grep -oE '[0-9]{15}' | head -n1",
            "timeout 3 dumpsys telephony.registry 2>/dev/null | grep -i mImei | head -n1 | grep -oE '[0-9]{15}'",
        ]:
            try:
                v = self.adb.run_shell(serial, cmd, timeout=5).strip()
                if v:
                    return v
            except Exception:
                continue
        return 'N/A(权限受限)'

    def _获取GAID(self, serial):
        try:
            v = self.adb.run_shell(serial, 'settings get secure advertising_id 2>/dev/null', timeout=3).strip()
            if v and v != 'null':
                return v
        except Exception:
            pass
        return 'N/A'

    def _获取OAID(self, serial):
        uris = [
            'content://com.miui.idprovider/uniform_id',
            'content://com.miui.id.provider/oaid',
            'content://com.bun.miitmdid.provider/oaid',
            'content://com.mdid.msa.provider/oaid',
            'content://com.huawei.hwid.oaid/oaid',
            'content://com.heytap.openid.oaid/oaid',
            'content://com.coloros.mcs.oaid/oaid',
            'content://com.vivo.vms.oaid/oaid',
        ]
        for uri in uris:
            try:
                raw = self.adb.run_shell(serial, f'timeout 1 content query --uri {uri} 2>/dev/null', timeout=3)
                m = _re.search(
                    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', raw or '')
                if m:
                    return m.group(0)
            except Exception:
                continue
        try:
            v = self.adb.run_shell(serial, 'settings get secure oaid 2>/dev/null', timeout=2).strip()
            if v:
                return v
        except Exception:
            pass
        return 'N/A(未安装移动安全联盟SDK)'

    def _获取AndroidID(self, serial):
        try:
            v = self.adb.run_shell(serial, 'settings get secure android_id 2>/dev/null', timeout=3).strip()
            if v:
                return v
        except Exception:
            pass
        return 'N/A'

    def _获取系统时间(self, serial):
        try:
            v = self.adb.run_shell(serial, "date '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null", timeout=3).strip()
            if v:
                return v
        except Exception:
            pass
        try:
            return self.adb.run_shell(serial, 'date 2>/dev/null', timeout=3).strip()
        except Exception:
            return 'N/A'

    def _获取WiFiIP(self, serial):
        for cmd in [
            "ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1",
            "ifconfig wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | awk '{print $2}'",
            "getprop dhcp.wlan0.ipaddress 2>/dev/null",
            "ip route 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -n1",
        ]:
            try:
                v = self.adb.run_shell(serial, cmd, timeout=3).strip()
                if v and _re.match(r'^\d+\.\d+\.\d+\.\d+$', v):
                    return v
            except Exception:
                continue
        return 'N/WiFi未连接'

    def _获取电池信息(self, serial):
        try:
            raw = self.adb.run_shell(serial, 'dumpsys battery 2>/dev/null', timeout=5)
            info = {}
            for line in (raw or '').splitlines():
                line = line.strip()
                if ':' in line:
                    k, v = line.split(':', 1)
                    info[k.strip().lower()] = v.strip()
            电量 = info.get('level', '?')
            状态码 = info.get('status', '?')
            状态Map = {'2': '充电中', '3': '未充电', '4': '未接电源', '5': '充满'}
            状态 = 状态Map.get(状态码, 状态码)
            温度 = info.get('temperature', '?')
            if 温度.isdigit():
                温度 = f'{int(温度)/10:.1f}°C'
            健康码 = info.get('health', '?')
            健康Map = {'2': '良好', '3': '过热', '4': '损坏', '5': '过压', '6': '未知故障', '7': '低温'}
            健康 = 健康Map.get(健康码, 健康码)
            return f'电量{电量}% {状态} {温度} 健康:{健康}'
        except Exception:
            return 'N/A'

    def _获取存储信息(self, serial):
        try:
            raw = self.adb.run_shell(serial, 'df /data 2>/dev/null', timeout=5)
            lines = [l for l in (raw or '').splitlines() if l.strip()]
            if len(lines) >= 2:
                parts = lines[-1].split()
                if len(parts) >= 5:
                    总大小, 已用, 可用, 使用率 = parts[1], parts[2], parts[3], parts[4]
                    return f'已用{已用}/{总大小} 可用{可用} 使用率{使用率}'
        except Exception:
            pass
        return 'N/A'

    def _获取内存信息(self, serial):
        try:
            raw = self.adb.run_shell(serial, 'cat /proc/meminfo 2>/dev/null', timeout=3)
            total = avail = free = ''
            for line in (raw or '').splitlines():
                if line.startswith('MemTotal:'):
                    total = line.split()[1]
                elif line.startswith('MemAvailable:'):
                    avail = line.split()[1]
                elif line.startswith('MemFree:'):
                    free = line.split()[1]
            if total:
                total_gb = int(total) / 1024 / 1024
                if avail:
                    avail_gb = int(avail) / 1024 / 1024
                    used_gb = total_gb - avail_gb
                    pct = used_gb / total_gb * 100
                    return f'总{total_gb:.1f}GB 已用{used_gb:.1f}GB 可用{avail_gb:.1f}GB ({pct:.0f}%)'
                return f'总{total_gb:.1f}GB'
        except Exception:
            pass
        return 'N/A'

    # ─────────────── 格式化 ───────────────
    @staticmethod
    def _显示宽度(s):
        w = 0
        for c in str(s):
            cp = ord(c)
            if (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F
                    or 0xFF00 <= cp <= 0xFFEF or 0x2E80 <= cp <= 0x2EFF
                    or 0x3400 <= cp <= 0x4DBF):
                w += 2
            else:
                w += 1
        return w

    @classmethod
    def _对齐填充(cls, s, width):
        actual = cls._显示宽度(s)
        if actual >= width:
            return str(s)
        return str(s) + ' ' * (width - actual)

    def _格式化getprop(self, getprop_raw, serial):
        分组顺序 = [
            '设备基本信息', '系统版本', '硬件信息', '系统状态',
            '网络与连接', '区域与语言', '内存与虚拟机', '构建信息', '其他属性',
        ]
        属性映射 = {
            'ro.product.model': ('设备型号', '设备基本信息'),
            'ro.product.brand': ('品牌', '设备基本信息'),
            'ro.product.manufacturer': ('制造商', '设备基本信息'),
            'ro.product.device': ('设备代号', '设备基本信息'),
            'ro.product.name': ('产品名称', '设备基本信息'),
            'ro.serialno': ('序列号', '设备基本信息'),
            'ro.boot.serialno': ('启动序列号', '设备基本信息'),
            'ro.product.marketname': ('市场名称', '设备基本信息'),
            'ro.build.version.release': ('Android版本', '系统版本'),
            'ro.build.version.sdk': ('SDK版本', '系统版本'),
            'ro.build.version.incremental': ('增量版本号', '系统版本'),
            'ro.build.id': ('构建ID', '系统版本'),
            'ro.build.display.id': ('显示版本', '系统版本'),
            'ro.build.version.security_patch': ('安全补丁级别', '系统版本'),
            'ro.build.version.codename': ('版本代号', '系统版本'),
            'ro.build.version.base_os': ('基础OS版本', '系统版本'),
            'ro.product.first_api_level': ('出厂API级别', '系统版本'),
            'ro.build.version.min_supported_target_sdk': ('最低支持SDK', '系统版本'),
            'ro.product.cpu.abi': ('主CPU架构', '硬件信息'),
            'ro.product.cpu.abilist': ('支持的CPU架构', '硬件信息'),
            'ro.product.cpu.abilist32': ('32位CPU架构', '硬件信息'),
            'ro.product.cpu.abilist64': ('64位CPU架构', '硬件信息'),
            'ro.hardware': ('硬件名称', '硬件信息'),
            'ro.hardware.chipname': ('芯片名称', '硬件信息'),
            'ro.board.platform': ('主板平台', '硬件信息'),
            'ro.boot.soc_id': ('SoC型号', '硬件信息'),
            'ro.product.board': ('主板', '硬件信息'),
            'ro.sf.lcd_density': ('屏幕密度(dpi)', '硬件信息'),
            'ro.opengles.version': ('OpenGL ES版本', '硬件信息'),
            'ro.config.low_ram': ('低内存设备', '硬件信息'),
            'ro.bootloader': ('引导程序版本', '硬件信息'),
            'ro.boot.revision': ('硬件修订版本', '硬件信息'),
            'ro.baseband': ('基带版本', '硬件信息'),
            'ro.modem': ('调制解调器版本', '硬件信息'),
            'ro.hardware.egl': ('EGL渲染器', '硬件信息'),
            'ro.hardware.vulkan': ('Vulkan版本', '硬件信息'),
            'ro.build.type': ('构建类型(user/userdebug/eng)', '系统状态'),
            'ro.build.tags': ('构建标签', '系统状态'),
            'ro.build.flavor': ('构建风格', '系统状态'),
            'ro.secure': ('安全模式(1=开启)', '系统状态'),
            'ro.adb.secure': ('ADB安全模式(1=开启)', '系统状态'),
            'ro.debuggable': ('可调试(1=开启)', '系统状态'),
            'ro.build.selinux': ('SELinux状态', '系统状态'),
            'ro.boot.verifiedbootstate': ('验证启动状态', '系统状态'),
            'ro.boot.veritymode': ('dm-verity模式', '系统状态'),
            'ro.boot.warranty_bit': ('保修位(0=未root,1=已修改)', '系统状态'),
            'ro.boot.mode': ('启动模式', '系统状态'),
            'ro.boot.hardware': ('启动硬件', '系统状态'),
            'ro.telephony.default_network': ('默认网络模式', '网络与连接'),
            'sys.usb.config': ('当前USB配置', '网络与连接'),
            'sys.usb.state': ('USB状态', '网络与连接'),
            'persist.sys.usb.config': ('持久USB配置', '网络与连接'),
            'gsm.version.baseband': ('基带版本', '网络与连接'),
            'ro.ril.wifi.chip': ('WiFi芯片', '网络与连接'),
            'ro.product.locale': ('系统区域', '区域与语言'),
            'ro.product.locale.language': ('系统语言', '区域与语言'),
            'ro.product.locale.region': ('系统地区', '区域与语言'),
            'persist.sys.timezone': ('时区', '区域与语言'),
            'persist.sys.language': ('当前语言', '区域与语言'),
            'persist.sys.country': ('当前国家', '区域与语言'),
            'dalvik.vm.heapsize': ('虚拟机堆大小', '内存与虚拟机'),
            'dalvik.vm.heapstartsize': ('堆起始大小', '内存与虚拟机'),
            'dalvik.vm.heapgrowthlimit': ('堆增长限制', '内存与虚拟机'),
            'dalvik.vm.heaptargetutilization': ('堆目标利用率', '内存与虚拟机'),
            'dalvik.vm.heapminfree': ('堆最小空闲', '内存与虚拟机'),
            'dalvik.vm.heapmaxfree': ('堆最大空闲', '内存与虚拟机'),
            'ro.build.fingerprint': ('构建指纹', '构建信息'),
            'ro.build.description': ('构建描述', '构建信息'),
            'ro.build.date': ('构建日期', '构建信息'),
            'ro.build.date.utc': ('构建日期(UTC秒)', '构建信息'),
            'ro.build.host': ('构建主机', '构建信息'),
            'ro.build.user': ('构建用户', '构建信息'),
            'ro.build.product': ('构建产品', '构建信息'),
            'ro.build.version.all_codenames': ('所有版本代号', '构建信息'),
        }

        props = {}
        for line in (getprop_raw or '').splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('[') and ']:' in line:
                key = line[1:line.index(']')]
                val_part = line[line.index(']:') + 2:].strip()
                if val_part.startswith('[') and val_part.endswith(']'):
                    val_part = val_part[1:-1]
                props[key] = val_part

        分组数据 = {g: [] for g in 分组顺序}
        已映射 = set()
        for key, (中文名, 分组) in 属性映射.items():
            if key in props:
                分组数据[分组].append((中文名, props[key]))
                已映射.add(key)
        for key, val in sorted(props.items()):
            if key not in 已映射:
                分组数据['其他属性'].append((key, val))

        lines_out = [f'设备序列号: {serial}', f'属性总数: {len(props)}', '=' * 50, '']
        for 分组 in 分组顺序:
            items = 分组数据[分组]
            if not items:
                continue
            lines_out.append(f'【{分组}】')
            lines_out.append('-' * 40)
            for 中文名, val in items:
                lines_out.append(f'  {self._对齐填充(中文名, 18)} {val}')
            lines_out.append('')
        return '\n'.join(lines_out)
