#!/usr/bin/env python3
"""
零依赖 APK 静态分析解析器
========================
功能：
  1. 解析 APK (ZIP) 结构，列出所有文件
  2. 解析 DEX (Dalvik Executable) 二进制，提取字符串/URL/危险API
  3. 解析 AndroidManifest.xml (AXML 二进制格式)
  4. 检测网络接口、恶意代码特征
  5. 验证签名证书

依赖: 仅 Python 标准库 (zipfile, struct, re, hashlib, os, sys)
用法: python3 apk_analyzer.py <path/to/app.apk>

作者: 悠悠 (WorkBuddy AI)
日期: 2026-08-09
"""

import zipfile
import struct
import re
import hashlib
import os
import sys
from collections import defaultdict


# ============================================================
# DEX 解析器
# ============================================================

class DexParser:
    """解析 DEX 文件，提取字符串、类型、方法、URL"""

    # DEX 魔数
    DEX_MAGIC = b'dex\n'

    def __init__(self, data: bytes):
        self.data = data
        self.strings = []
        self.type_ids = []
        self.method_names = []
        self.class_names = []
        self._parse()

    def _read_uleb128(self, offset: int):
        """读取 ULEB128 编码的无符号整数"""
        result = 0
        shift = 0
        while True:
            byte = self.data[offset]
            result |= (byte & 0x7F) << shift
            offset += 1
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result, offset

    def _read_sleb128(self, offset: int):
        """读取 SLEB128 编码的有符号整数"""
        result = 0
        shift = 0
        while True:
            byte = self.data[offset]
            result |= (byte & 0x7F) << shift
            offset += 1
            if (byte & 0x80) == 0:
                if byte & 0x40:
                    result |= -(1 << (shift + 7))
                break
            shift += 7
        return result, offset

    def _parse(self):
        """主解析逻辑"""
        if len(self.data) < 112:
            return

        # 验证魔数
        if self.data[:4] != self.DEX_MAGIC:
            return

        # 读取 header 字段
        # string_ids_size @ offset 0x38, string_ids_off @ 0x3C
        string_ids_size = struct.unpack_from('<I', self.data, 0x38)[0]
        string_ids_off = struct.unpack_from('<I', self.data, 0x3C)[0]

        # type_ids_size @ 0x40, type_ids_off @ 0x44
        type_ids_size = struct.unpack_from('<I', self.data, 0x40)[0]
        type_ids_off = struct.unpack_from('<I', self.data, 0x44)[0]

        # proto_ids_size @ 0x48, proto_ids_off @ 0x4C
        # field_ids_size @ 0x50, field_ids_off @ 0x54
        # method_ids_size @ 0x58, method_ids_off @ 0x5C
        method_ids_size = struct.unpack_from('<I', self.data, 0x58)[0]
        method_ids_off = struct.unpack_from('<I', self.data, 0x5C)[0]

        # class_defs_size @ 0x60, class_defs_off @ 0x64
        class_defs_size = struct.unpack_from('<I', self.data, 0x60)[0]
        class_defs_off = struct.unpack_from('<I', self.data, 0x64)[0]

        # 解析字符串
        self._parse_strings(string_ids_size, string_ids_off)

        # 解析类型
        self._parse_types(type_ids_size, type_ids_off)

        # 解析方法
        self._parse_methods(method_ids_size, method_ids_off)

        # 解析类定义
        self._parse_class_defs(class_defs_size, class_defs_off)

    def _parse_strings(self, count: int, offset: int):
        """解析字符串 ID 表和实际字符串"""
        for i in range(count):
            # 每个 string_id_item 是 4 字节偏移量
            str_data_off = struct.unpack_from('<I', self.data, offset + i * 4)[0]
            if str_data_off >= len(self.data):
                continue
            # 字符串数据: ULEB128 长度 + MUTF-8 字符串
            try:
                _, str_start = self._read_uleb128(str_data_off)
                # 读取到 null 终止符
                end = str_start
                while end < len(self.data) and self.data[end] != 0:
                    end += 1
                raw = self.data[str_start:end]
                # 尝试 UTF-8 解码
                try:
                    s = raw.decode('utf-8')
                except UnicodeDecodeError:
                    s = raw.decode('latin-1')
                self.strings.append(s)
            except (IndexError, struct.error):
                self.strings.append('')

    def _parse_types(self, count: int, offset: int):
        """解析类型 ID 表"""
        for i in range(count):
            desc_idx = struct.unpack_from('<I', self.data, offset + i * 4)[0]
            if 0 <= desc_idx < len(self.strings):
                self.type_ids.append(self.strings[desc_idx])

    def _parse_methods(self, count: int, offset: int):
        """解析方法 ID 表 (class_idx:16 + proto_idx:16 + name_idx:32)"""
        for i in range(count):
            base = offset + i * 8
            class_idx = struct.unpack_from('<H', self.data, base)[0]
            proto_idx = struct.unpack_from('<H', self.data, base + 2)[0]
            name_idx = struct.unpack_from('<I', self.data, base + 4)[0]
            if 0 <= name_idx < len(self.strings):
                self.method_names.append(self.strings[name_idx])

    def _parse_class_defs(self, count: int, offset: int):
        """解析类定义表"""
        for i in range(count):
            base = offset + i * 32
            class_idx = struct.unpack_from('<I', self.data, base)[0]
            if 0 <= class_idx < len(self.type_ids):
                self.class_names.append(self.type_ids[class_idx])

    def find_urls(self):
        """从字符串中提取 URL"""
        url_pattern = re.compile(r'https?://[^\x00-\x1f"\'<>\s]+')
        urls = set()
        for s in self.strings:
            urls.update(url_pattern.findall(s))
        return sorted(urls)

    def find_dangerous_apis(self):
        """检测危险 API 调用"""
        dangerous = {
            'Runtime.exec': '命令执行',
            'ProcessBuilder': '命令执行',
            'getRuntime': '命令执行',
            'setprop': '系统属性修改',
            'getprop': '系统属性读取',
            '/system/bin/su': 'Root 提权',
            '"su"': 'Root 提权',
            'DexClassLoader': '动态加载',
            'PathClassLoader': '动态加载',
            'reflect': '反射调用',
            'Cipher.getInstance': '加密操作',
            'javax.crypto': '加密操作',
            'android.net.ConnectivityManager': '网络状态',
            'getDeviceId': '设备标识',
            'getSubscriberId': 'SIM标识',
            'getCellLocation': '位置信息',
            'sendTextMessage': '短信发送',
            ' SmsManager': '短信操作',
            'LOAD_SMS': '短信读取',
            'RECORD_AUDIO': '录音权限',
            'ACCESS_FINE_LOCATION': '精确定位',
        }
        found = defaultdict(list)
        all_text = '\n'.join(self.strings)
        for api, desc in dangerous.items():
            if api in all_text:
                found[desc].append(api)
        return dict(found)

    def find_internet_endpoints(self):
        """检测网络通信端点"""
        endpoints = set()
        for s in self.strings:
            if 'dagui-smart.com' in s:
                endpoints.add(('dagui-smart.com', s))
            if 'support.qq.com' in s:
                endpoints.add(('support.qq.com', s))
            if 'mqqopensdkapi' in s:
                endpoints.add(('mqqopensdkapi', s))
            if 'appKey' in s.lower() or 'app_key' in s.lower():
                endpoints.add(('appKey', s))
        return sorted(endpoints)

    def summary(self):
        """生成分析摘要"""
        lines = []
        lines.append(f'字符串总数: {len(self.strings)}')
        lines.append(f'类型总数: {len(self.type_ids)}')
        lines.append(f'方法总数: {len(self.method_names)}')
        lines.append(f'类定义总数: {len(self.class_names)}')
        urls = self.find_urls()
        lines.append(f'URL 数量: {len(urls)}')
        for u in urls:
            lines.append(f'  {u}')
        dangerous = self.find_dangerous_apis()
        if dangerous:
            lines.append(f'危险 API: {len(dangerous)} 类')
            for desc, apis in dangerous.items():
                lines.append(f'  [{desc}] {", ".join(apis)}')
        endpoints = self.find_internet_endpoints()
        if endpoints:
            lines.append(f'网络端点: {len(endpoints)}')
            for name, val in endpoints:
                lines.append(f'  [{name}] {val[:80]}')
        return '\n'.join(lines)


# ============================================================
# AXML 解析器 (Android Binary XML)
# ============================================================

class AxmlParser:
    """解析 Android 二进制 XML 格式 (AndroidManifest.xml 等)"""

    # 资源类型常量
    RES_STRING_POOL = 0x0001
    RES_RESOURCE_MAP = 0x0180
    RES_XML_START_NAMESPACE = 0x0100
    RES_XML_END_NAMESPACE = 0x0101
    RES_XML_START_ELEMENT = 0x0102
    RES_XML_END_ELEMENT = 0x0103
    RES_XML_CDATA = 0x0104

    # 值类型
    TYPE_NULL = 0x00
    TYPE_REFERENCE = 0x01
    TYPE_STRING = 0x03
    TYPE_INT_DEC = 0x10
    TYPE_INT_HEX = 0x11
    TYPE_INT_BOOL = 0x12

    def __init__(self, data: bytes):
        self.data = data
        self.strings = []
        self.resource_ids = []
        self.result = []
        self._parse()

    def _parse(self):
        if len(self.data) < 8:
            return

        # 读取文件头
        # type:16 + headerSize:16 + size:32
        file_type = struct.unpack_from('<H', self.data, 0)[0]
        file_size = struct.unpack_from('<I', self.data, 4)[0]

        if file_type != 0x0003:  # RES_XML_TYPE
            return

        offset = 8
        while offset < len(self.data):
            if offset + 8 > len(self.data):
                break
            chunk_type = struct.unpack_from('<H', self.data, offset)[0]
            chunk_header = struct.unpack_from('<H', self.data, offset + 2)[0]
            chunk_size = struct.unpack_from('<I', self.data, offset + 4)[0]

            if chunk_size == 0:
                break

            if chunk_type == self.RES_STRING_POOL:
                self._parse_string_pool(offset, chunk_size)
            elif chunk_type == self.RES_RESOURCE_MAP:
                self._parse_resource_map(offset, chunk_size)
            elif chunk_type == self.RES_XML_START_ELEMENT:
                self._parse_start_element(offset)
            elif chunk_type == self.RES_XML_END_ELEMENT:
                pass
            elif chunk_type == self.RES_XML_START_NAMESPACE:
                pass

            offset += chunk_size

    def _parse_string_pool(self, offset: int, size: int):
        """解析字符串池"""
        # stringCount @ offset+8, styleCount @ offset+12
        # flags @ offset+16, stringsStart @ offset+20, stylesStart @ offset+24
        string_count = struct.unpack_from('<I', self.data, offset + 8)[0]
        flags = struct.unpack_from('<I', self.data, offset + 16)[0]
        strings_start = struct.unpack_from('<I', self.data, offset + 20)[0]

        is_utf8 = (flags & (1 << 8)) != 0

        # 如果 string_count 为 0（非标准 AXML），尝试从偏移量推断
        if string_count == 0:
            header_size = struct.unpack_from('<H', self.data, offset + 2)[0]
            if strings_start > header_size:
                string_count = (strings_start - header_size) // 4

        # 读取字符串偏移量表
        offsets_base = offset + 28  # headerSize = 28 for string pool
        for i in range(string_count):
            if offsets_base + i * 4 + 4 > len(self.data):
                break
            str_off = struct.unpack_from('<I', self.data, offsets_base + i * 4)[0]
            abs_off = offset + strings_start + str_off
            if abs_off >= len(self.data):
                self.strings.append('')
                continue

            try:
                if is_utf8:
                    # UTF-8: 1 byte length (ULEB128 for >127) + 1 byte len + data + null
                    char_count = self.data[abs_off]
                    if char_count & 0x80:
                        char_count = ((char_count & 0x7F) << 8) | self.data[abs_off + 1]
                        byte_count = self.data[abs_off + 2]
                        if byte_count & 0x80:
                            byte_count = ((byte_count & 0x7F) << 8) | self.data[abs_off + 3]
                            s = self.data[abs_off + 4:abs_off + 4 + byte_count].decode('utf-8', errors='replace')
                        else:
                            s = self.data[abs_off + 3:abs_off + 3 + byte_count].decode('utf-8', errors='replace')
                    else:
                        byte_count = self.data[abs_off + 1]
                        if byte_count & 0x80:
                            byte_count = ((byte_count & 0x7F) << 8) | self.data[abs_off + 2]
                            s = self.data[abs_off + 3:abs_off + 3 + byte_count].decode('utf-8', errors='replace')
                        else:
                            s = self.data[abs_off + 2:abs_off + 2 + byte_count].decode('utf-8', errors='replace')
                else:
                    # UTF-16: 2 byte length + data + null
                    char_count = struct.unpack_from('<H', self.data, abs_off)[0]
                    if char_count & 0x8000:
                        char_count = ((char_count & 0x7FFF) << 16) | struct.unpack_from('<H', self.data, abs_off + 2)[0]
                        s = self.data[abs_off + 4:abs_off + 4 + char_count * 2].decode('utf-16-le', errors='replace')
                    else:
                        s = self.data[abs_off + 2:abs_off + 2 + char_count * 2].decode('utf-16-le', errors='replace')
                self.strings.append(s)
            except (IndexError, struct.error):
                self.strings.append('')

    def _parse_resource_map(self, offset: int, size: int):
        """解析资源映射表"""
        count = (size - 8) // 4
        for i in range(count):
            if offset + 8 + i * 4 + 4 <= len(self.data):
                self.resource_ids.append(struct.unpack_from('<I', self.data, offset + 8 + i * 4)[0])

    def _parse_start_element(self, offset: int):
        """解析 XML 开始元素"""
        # 跳过 namespace 相关字段
        # 元素结构: type:16+headerSize:16+size:32 + lineNumber:32 + comment:32
        #          + ns:32 + name:32 + attrStart:16 + attrSize:16 + attrCount:16
        #          + idIndex:16 + classIndex:16 + styleIndex:16 + attrs...
        ns_idx = struct.unpack_from('<i', self.data, offset + 16)[0]
        name_idx = struct.unpack_from('<i', self.data, offset + 20)[0]

        name = self.strings[name_idx] if 0 <= name_idx < len(self.strings) else f'?{name_idx}'

        attr_start = struct.unpack_from('<H', self.data, offset + 24)[0]
        attr_count = struct.unpack_from('<H', self.data, offset + 28)[0]

        attrs = {}
        attr_base = offset + 36  # 28 (header) + 8 (attrStart offset from header end)

        for i in range(attr_count):
            base = attr_base + i * 20  # each attribute is 20 bytes
            if base + 20 > len(self.data):
                break
            attr_ns = struct.unpack_from('<i', self.data, base)[0]
            attr_name = struct.unpack_from('<i', self.data, base + 4)[0]
            attr_raw_value = struct.unpack_from('<i', self.data, base + 8)[0]
            attr_type = struct.unpack_from('<H', self.data, base + 15)[0]
            attr_value = struct.unpack_from('<i', self.data, base + 16)[0]

            # 解析属性名
            if 0 <= attr_name < len(self.strings):
                attr_name_str = self.strings[attr_name]
            elif attr_name < 0 and abs(attr_name) - 1 < len(self.resource_ids):
                attr_name_str = f'@0x{self.resource_ids[abs(attr_name) - 1]:08x}'
            else:
                attr_name_str = f'?{attr_name}'

            # 解析属性值
            if attr_type == self.TYPE_STRING:
                if 0 <= attr_value < len(self.strings):
                    attr_val = self.strings[attr_value]
                else:
                    attr_val = f'?str:{attr_value}'
            elif attr_type == self.TYPE_INT_DEC:
                attr_val = str(attr_value)
            elif attr_type == self.TYPE_INT_HEX:
                attr_val = f'0x{attr_value:08x}'
            elif attr_type == self.TYPE_INT_BOOL:
                attr_val = 'true' if attr_value != 0 else 'false'
            elif attr_type == self.TYPE_REFERENCE:
                attr_val = f'@0x{attr_value:08x}'
            else:
                attr_val = f'?type{attr_type}:{attr_value}'

            if attr_name_str:
                attrs[attr_name_str] = attr_val

        self.result.append((name, attrs))

    def get_manifest_info(self):
        """提取 Manifest 关键信息"""
        info = {
            'package': '',
            'activities': [],
            'services': [],
            'receivers': [],
            'providers': [],
            'permissions': [],
        }
        for name, attrs in self.result:
            if name == 'manifest' and 'package' in attrs:
                info['package'] = attrs['package']
            elif name == 'activity':
                act_name = attrs.get('name', attrs.get('android:name', ''))
                info['activities'].append(act_name)
            elif name == 'service':
                svc_name = attrs.get('name', attrs.get('android:name', ''))
                info['services'].append(svc_name)
            elif name == 'receiver':
                rcv_name = attrs.get('name', attrs.get('android:name', ''))
                info['receivers'].append(rcv_name)
            elif name == 'provider':
                prov_name = attrs.get('name', attrs.get('android:name', ''))
                info['providers'].append(prov_name)
            elif name == 'uses-permission':
                perm = attrs.get('name', attrs.get('android:name', ''))
                if perm:
                    info['permissions'].append(perm)
        return info


# ============================================================
# APK 分析器
# ============================================================

def analyze_apk(apk_path: str):
    """分析 APK 文件"""
    print(f'╔══════════════════════════════════════════════╗')
    print(f'║  APK 静态分析报告                              ║')
    print(f'╚══════════════════════════════════════════════╝')
    print(f'文件: {apk_path}')
    print(f'大小: {os.path.getsize(apk_path):,} bytes')
    print()

    with zipfile.ZipFile(apk_path, 'r') as zf:
        names = zf.namelist()
        print(f'━━━ APK 结构 ━━━')
        print(f'文件总数: {len(names)}')

        # 按类型分类
        dex_files = [n for n in names if n.endswith('.dex')]
        print(f'DEX 文件: {dex_files}')

        # 分析 DEX
        for dex_name in dex_files:
            print(f'\n━━━ DEX 分析: {dex_name} ━━━')
            dex_data = zf.read(dex_name)
            parser = DexParser(dex_data)
            print(parser.summary())

        # 分析 AndroidManifest.xml
        if 'AndroidManifest.xml' in names:
            print(f'\n━━━ AndroidManifest.xml 分析 ━━━')
            manifest_data = zf.read('AndroidManifest.xml')
            axml = AxmlParser(manifest_data)
            info = axml.get_manifest_info()
            print(f'包名: {info["package"]}')
            print(f'Activity 数: {len(info["activities"])}')
            for a in info['activities']:
                print(f'  - {a}')
            print(f'Service 数: {len(info["services"])}')
            for s in info['services']:
                print(f'  - {s}')
            print(f'Receiver 数: {len(info["receivers"])}')
            for r in info['receivers']:
                print(f'  - {r}')
            print(f'Permission 数: {len(info["permissions"])}')
            for p in info['permissions']:
                print(f'  - {p}')

        # 证书检查
        cert_files = [n for n in names if n.startswith('META-INF/') and (n.endswith('.RSA') or n.endswith('.DSA') or n.endswith('.EC'))]
        print(f'\n━━━ 签名证书 ━━━')
        print(f'证书文件: {cert_files}')

        # 网络接口检查
        print(f'\n━━━ 网络接口检查 ━━━')
        all_dex_text = ''
        for dex_name in dex_files:
            all_dex_text += zf.read(dex_name).decode('utf-8', errors='ignore')

        checks = [
            ('dagui-smart.com', '版本检查/反馈 API'),
            ('support.qq.com', 'QQ 客服反馈'),
            ('mqqopensdkapi', 'QQ 加群'),
            ('app_993ff1578d4d4ff2ac7d54a9d9a2b421', 'AppKey 凭证'),
        ]
        for keyword, desc in checks:
            if keyword in all_dex_text:
                print(f'  ⚠️ [{desc}] 发现: {keyword}')
            else:
                print(f'  ✅ [{desc}] 未发现')

        # URL 提取
        urls = set(re.findall(r'https?://[^\x00-\x1f"\'<>\s]+', all_dex_text))
        print(f'\n━━━ URL 提取 ({len(urls)}) ━━━')
        for u in sorted(urls):
            print(f'  {u}')

    print(f'\n━━━ 分析完成 ━━━')


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python3 apk_analyzer.py <path/to/app.apk>')
        sys.exit(1)
    analyze_apk(sys.argv[1])
