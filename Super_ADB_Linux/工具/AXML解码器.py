# -*- coding: utf-8 -*-
"""
Android Binary XML (AXML) 解码器
================================
APK 里的 AndroidManifest.xml、res/*.xml 是 *编译后的二进制 XML*（不是普通文本），
直接用文本方式读会乱码。本模块按 Android `ResourceTypes.h` 的 chunk 格式，
把二进制 AXML 还原成可读的 XML 文本（与 Android Studio 的 APK Analyzer 类似）。

纯 Python 实现，无第三方依赖；解析过程全程异常保护，调用方可在失败时回退到十六进制视图。
"""
import struct

# ResChunk_header 类型
RES_STRING_POOL_TYPE = 0x0001
RES_XML_TYPE = 0x0003
RES_XML_START_NAMESPACE_TYPE = 0x0100
RES_XML_END_NAMESPACE_TYPE = 0x0101
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_END_ELEMENT_TYPE = 0x0103
RES_XML_CDATA_TYPE = 0x0104
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_XML_END_NAMESPACE_EXT_TYPE = 0x0101

# Res_value 类型
TYPE_NULL = 0x00
TYPE_REFERENCE = 0x01
TYPE_ATTRIBUTE = 0x02
TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_DIMENSION = 0x05
TYPE_FRACTION = 0x06
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12
TYPE_INT_COLOR_ARGB8 = 0x1C
TYPE_INT_COLOR_RGB8 = 0x1D
TYPE_INT_COLOR_ARGB4 = 0x1E
TYPE_INT_COLOR_RGB4 = 0x1F

# DIMENSION / FRACTION 单位后缀
_DIM_UNITS = {0: 'px', 1: 'dp', 2: 'sp', 3: 'pt', 4: 'in', 5: 'mm'}
_FRAC_UNITS = {0: '%', 1: '%p'}

# 常见命名空间 URI -> 默认前缀（用于 APK 未显式声明前缀时）
_KNOWN_NS_PREFIX = {
    'http://schemas.android.com/apk/res/android': 'android',
    'http://schemas.android.com/apk/res-auto': 'app',
    'http://schemas.android.com/tools': 'tools',
}


def is_axml(data: bytes) -> bool:
    """判断一段字节是否像 Android Binary XML。"""
    if not data or len(data) < 8:
        return False
    ctype, header_size = struct.unpack_from('<HH', data, 0)
    # AXML 顶层 chunk：type=0x0003, headerSize=0x0008
    return ctype == RES_XML_TYPE and header_size == 0x0008


def _read_uleb128(buf, off):
    """读取一个无符号 LEB128 整数，返回 (value, new_offset)。"""
    result = 0
    shift = 0
    while True:
        if off >= len(buf):
            raise ValueError('ULEB128 越界')
        b = buf[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, off


class _StringPool:
    """解析 AXML 的 StringPool chunk。"""

    def __init__(self, buf, offset):
        self.strings = []
        (ctype, header_size, size) = struct.unpack_from('<HHI', buf, offset)
        if ctype != RES_STRING_POOL_TYPE:
            raise ValueError('不是 StringPool chunk')
        # ResStringPool_header（位于 8 字节 ResChunk_header 之后）：
        # stringCount(4) styleCount(4) flags(4) stringsStart(4) stylesStart(4)
        (string_count, style_count, flags,
         strings_start, styles_start) = struct.unpack_from('<IIIII', buf, offset + 8)
        # UTF8_FLAG = 0x0100；旧代码误用 0x0001（SORTED_FLAG）导致 UTF-16 文件解析越界
        self.is_utf8 = bool(flags & 0x0100)
        base = offset + strings_start
        offsets = []
        # 字符串偏移表紧跟在 28 字节 header 之后
        for i in range(string_count):
            (o,) = struct.unpack_from('<I', buf, offset + 28 + i * 4)
            offsets.append(o)
        for o in offsets:
            pos = base + o
            if pos >= len(buf):
                self.strings.append('')
                continue
            if self.is_utf8:
                utf8_len, pos = _read_uleb128(buf, pos)
                # utf16 长度（我们并不真正用到，但需跳过）
                _, pos = _read_uleb128(buf, pos)
                raw = buf[pos:pos + utf8_len]
                # 去掉结尾的 null 终止符
                if raw.endswith(b'\x00'):
                    raw = raw[:-1]
                try:
                    self.strings.append(raw.decode('utf-8', errors='replace'))
                except Exception:
                    self.strings.append(raw.decode('latin-1', errors='replace'))
            else:
                (length,) = struct.unpack_from('<H', buf, pos)
                pos += 2
                raw = buf[pos:pos + length * 2]
                if raw.endswith(b'\x00\x00'):
                    raw = raw[:-2]
                try:
                    self.strings.append(raw.decode('utf-16-le', errors='replace'))
                except Exception:
                    self.strings.append(raw.decode('latin-1', errors='replace'))

    def get(self, idx):
        if idx is None or idx < 0 or idx >= len(self.strings):
            return None
        return self.strings[idx]


def _format_value(vtype, data, pool: _StringPool):
    """把 Res_value 的 (type, data) 格式化为可读字符串。"""
    if vtype == TYPE_NULL:
        return ''
    if vtype == TYPE_STRING:
        return pool.get(data) or ''
    if vtype == TYPE_REFERENCE:
        return '@0x%08X' % data
    if vtype == TYPE_ATTRIBUTE:
        return '?0x%08X' % data
    if vtype == TYPE_FLOAT:
        return '%.4g' % struct.unpack('<f', struct.pack('<I', data))[0]
    if vtype == TYPE_INT_BOOLEAN:
        return 'true' if data else 'false'
    if vtype == TYPE_INT_DEC:
        # 有符号 32 位
        if data & 0x80000000:
            data = data - 0x100000000
        return str(data)
    if vtype == TYPE_INT_HEX:
        return '0x%08X' % data
    if vtype in (TYPE_INT_COLOR_ARGB8, TYPE_INT_COLOR_RGB8,
                 TYPE_INT_COLOR_ARGB4, TYPE_INT_COLOR_RGB4):
        return '#%08X' % data
    if vtype == TYPE_DIMENSION:
        unit = (data >> 24) & 0xFF
        value = data & 0x00FFFFFF
        suf = _DIM_UNITS.get(unit & 0x0F, '')
        return '%g%s' % (value, suf)
    if vtype == TYPE_FRACTION:
        unit = (data >> 24) & 0xFF
        value = data & 0x00FFFFFF
        suf = _FRAC_UNITS.get(unit & 0x0F, '')
        return '%g%s' % (value, suf)
    return '0x%08X' % data


class _Element:
    def __init__(self, ns, name):
        self.ns = ns
        self.name = name
        self.attrs = []          # list of (ns, aname, value_str)
        self.children = []
        self.text = None         # CDATA


def decode_axml(data: bytes) -> str:
    """把二进制 AXML 解码为缩进的 XML 文本。"""
    if not is_axml(data):
        raise ValueError('不是有效的 AXML 数据')

    pool = None
    namespaces = []          # list of (prefix, uri)
    uri_to_prefix = {}
    root = None
    stack = []              # 元素栈

    off = 0
    n = len(data)
    _max_iter = 100000  # 安全上限：防止异常 AXML 导致死循环
    _iter = 0
    while off + 8 <= n:
        _iter += 1
        if _iter > _max_iter:
            raise ValueError('AXML 解析超出安全迭代上限，文件可能损坏')
        ctype, header_size, size = struct.unpack_from('<HHI', data, off)
        if size <= 0:
            break
        # 顶层 RES_XML_TYPE 是容器 chunk，需要“进入”它继续解析子 chunk，
        # 不能按 size 整体跳过（否则会跳过整个文件）。
        if ctype == RES_XML_TYPE:
            off += header_size
            continue
        if ctype == RES_STRING_POOL_TYPE:
            pool = _StringPool(data, off)
        elif ctype == RES_XML_RESOURCE_MAP_TYPE:
            pass  # 跳过，仅需名称字符串池即可
        elif ctype == RES_XML_START_NAMESPACE_TYPE:
            # ResXMLTree_namespace_ext: header(8) + prefixIdx(4) + uriIdx(4)
            prefix_idx, uri_idx = struct.unpack_from('<II', data, off + 8)
            prefix = (pool.get(prefix_idx) if pool else '') or ''
            uri = (pool.get(uri_idx) if pool else '') or ''
            namespaces.append((prefix, uri))
            if uri:
                uri_to_prefix[uri] = prefix
        elif ctype == RES_XML_END_NAMESPACE_TYPE:
            pass
        elif ctype == RES_XML_START_ELEMENT_TYPE:
            line, comment, ns_idx, name_idx = struct.unpack_from('<IIII', data, off + 8)
            # 属性头：attributeStart(2) attributeSize(2) attributeCount(2) ...(6×uint16)
            (_attr_start, attr_size, attr_count,
             _id_idx, _class_idx, _style_idx) = struct.unpack_from(
                '<HHHHHH', data, off + 24)
            elem_name = pool.get(name_idx) if pool else ('#%d' % name_idx)
            ns_uri = pool.get(ns_idx) if (pool and ns_idx >= 0) else None
            elem = _Element(ns_uri, elem_name)
            # 属性区紧跟在 chunk 头(header_size)之后，不依赖 attributeStart 字段
            apos = off + header_size
            for i in range(attr_count):
                aoff = apos + i * attr_size
                a_ns, a_name, a_raw = struct.unpack_from('<III', data, aoff)
                # Res_value: size(uint16) res0(uint8) type(uint8) data(uint32)
                _res_size, _res0, a_type = struct.unpack_from(
                    '<HBB', data, aoff + 12)
                a_data = struct.unpack_from('<I', data, aoff + 16)[0]
                # 跳过无意义的占位属性（如元素自身同名、值为空的 TYPE_NULL）
                if a_type == TYPE_NULL and a_data == 0:
                    continue
                a_ns_uri = pool.get(a_ns) if (pool and a_ns >= 0) else None
                a_label = pool.get(a_name) if pool else ('#%d' % a_name)
                if not a_label:
                    continue
                prefix = (uri_to_prefix.get(a_ns_uri)
                          if a_ns_uri else None) or _KNOWN_NS_PREFIX.get(a_ns_uri)
                full = (prefix + ':' + a_label) if prefix else a_label
                elem.attrs.append((a_ns_uri, full, _format_value(a_type, a_data, pool)))
            if stack:
                stack[-1].children.append(elem)
            else:
                root = elem
            stack.append(elem)
        elif ctype == RES_XML_END_ELEMENT_TYPE:
            if stack:
                stack.pop()
        elif ctype == RES_XML_CDATA_TYPE:
            line, comment, data_idx = struct.unpack_from('<III', data, off + 8)
            text = pool.get(data_idx) if pool else None
            if text and stack:
                stack[-1].text = text
        else:
            # 未知 chunk（如 RES_XML_END_NAMESPACE 已处理），跳过
            pass
        off += size

    if root is None:
        raise ValueError('未解析到任何 XML 元素')

    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    _render(root, lines, 0, namespaces)
    return '\n'.join(lines)


def _render(elem: _Element, lines, depth, namespaces):
    pad = '    ' * depth
    tag = elem.name
    # 命名空间前缀（先用文件中声明的，再用默认映射）
    if elem.ns:
        prefix = uri_to_prefix_of(elem.ns, namespaces) or _KNOWN_NS_PREFIX.get(elem.ns)
        if prefix:
            tag = prefix + ':' + tag
    # 根元素上声明全部命名空间；URI 为空的不声明，prefix 为空的使用默认前缀
    xmlns = ''
    if depth == 0 and namespaces:
        decls = []
        for prefix, uri in namespaces:
            if not uri:
                continue
            prefix = prefix or _KNOWN_NS_PREFIX.get(uri)
            if prefix:
                decls.append('xmlns:%s="%s"' % (prefix, _escape_attr(uri)))
            else:
                decls.append('xmlns="%s"' % _escape_attr(uri))
        if decls:
            xmlns = ' ' + ' '.join(decls)
    attr_str = ''
    for _, full, value in elem.attrs:
        attr_str += ' %s="%s"' % (full, _escape_attr(value))
    if elem.children or elem.text:
        lines.append('%s<%s%s%s>' % (pad, tag, xmlns, attr_str))
        if elem.text:
            lines.append('%s    %s' % (pad, _escape_text(elem.text)))
        for ch in elem.children:
            _render(ch, lines, depth + 1, namespaces)
        lines.append('%s</%s>' % (pad, tag))
    else:
        lines.append('%s<%s%s%s/>' % (pad, tag, xmlns, attr_str))


def uri_to_prefix_of(uri, namespaces):
    for prefix, u in namespaces:
        if u == uri:
            return prefix
    return None


def _escape_attr(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _escape_text(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
