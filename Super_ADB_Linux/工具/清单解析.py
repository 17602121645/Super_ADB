#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal Android binary XML (AXML) parser -> dump permissions & components."""
import struct, sys

PATH = "C:/Users/57676/Desktop/adb/_apk_extract/AndroidManifest.xml"
raw = open(PATH, "rb").read()

def u16(o): return struct.unpack_from("<H", raw, o)[0]
def u32(o): return struct.unpack_from("<I", raw, o)[0]

assert raw[:4] == b"\x03\x00\x08\x00", "not AXML (got %r)" % raw[:4]

# --- string pool --- (find chunk type 0x0001)
strpool_off = None
o = 8
while o < len(raw) - 8:
    ct = u16(o)
    cs = u32(o+4)
    if ct == 0x0001:
        strpool_off = o
        break
    if cs <= 0:
        break
    o += cs

strings = []
if strpool_off is not None:
    o = strpool_off
    header_size = u16(o+2)            # usually 28
    flags = u32(o+8)
    strings_start_rel = u32(o+20)     # offset from chunk start
    # string count field is unreliable (observed 0); derive from offsets array
    off_arr_abs = o + header_size
    n = (strings_start_rel - header_size) // 4
    offsets = [u32(off_arr_abs + i*4) for i in range(n)]
    str_off_base = o + strings_start_rel
    utf8 = bool(flags & 0x100)
    for so in offsets:
        p = str_off_base + so
        if utf8:
            length, k = 0, 0
            shift = 0
            while True:
                b = raw[p+k]; k += 1
                length |= (b & 0x7f) << shift
                if not (b & 0x80): break
                shift += 7
            start = p+k
            end = start
            while raw[end] != 0:
                end += 1
            val = raw[start:end].decode("utf-8", "replace")
        else:
            l0 = u16(p)
            if l0 & 0x8000:
                length = ((l0 & 0x7fff) << 16) | u16(p+2)
                start = p+4
            else:
                length = l0
                start = p+2
            val = raw[start:start+length*2].decode("utf-16-le", "replace")
        strings.append(val)

# --- iterate chunks to find elements ---
# We'll walk the chunk structure
RES_START_NS = 0x0100
RES_END_NS   = 0x0101
RES_START_EL = 0x0102
RES_END_EL   = 0x0103
RES_CDATA    = 0x0104
RES_LAST_URI = 0x0180  # resource map (skip)

def parse_elements(at, end):
    els = []
    o = at
    while o < end:
        ctype = u16(o)
        csize = u32(o+4)
        if csize <= 0:
            break
        if ctype == RES_START_EL:
            # line=o+8(4), comment=o+12(4), ns=o+16(4), name=o+20(4), attrStart=o+24(2), attrSize=o+26(2), attrCount=o+28(2), idIdx?...
            name_idx = u32(o+20)
            attr_start = u16(o+24)
            attr_count = u16(o+28)
            name = strings[name_idx] if 0 <= name_idx < len(strings) else "?"
            attrs = []
            ap = o + attr_start
            for i in range(attr_count):
                # attr: ns(4) name(4) rawvalue(4) typedvalue(8)
                a_ns = u32(ap)
                a_name = u32(ap+4)
                a_raw = u32(ap+8)
                # typed value header: size(2) res0(1) type(1) data(4)
                a_type = raw[ap+13]
                a_data = u32(ap+14)
                aname = strings[a_name] if 0 <= a_name < len(strings) else "?"
                # resolve value: if type==3 (string) -> string pool
                if a_type == 3:
                    aval = strings[a_data] if 0 <= a_data < len(strings) else ""
                elif a_type == 1:  # int dec
                    aval = str(struct.unpack("<i", struct.pack("<I", a_data))[0])
                elif a_type == 0x12:  # bool
                    aval = "true" if a_data else "false"
                elif a_type == 0x10:  # int hex
                    aval = "0x%08x" % a_data
                else:
                    aval = "<typ=%d val=%d>" % (a_type, a_data)
                attrs.append((aname, aval))
                ap += 20
            els.append((name, attrs))
        o += csize
    return els

# walk top-level chunks
o = 8
end = len(raw)
elements = []
while o < end:
    ctype = u16(o)
    if ctype in (RES_START_EL, RES_END_EL, RES_START_NS, RES_END_NS, RES_CDATA):
        csize = u32(o+4)
        if ctype == RES_START_EL:
            name_idx = u32(o+20)
            attr_start = u16(o+24)
            attr_count = u16(o+28)
            name = strings[name_idx] if 0 <= name_idx < len(strings) else "?"
            attrs = []
            ap = o + attr_start
            for i in range(attr_count):
                a_ns = u32(ap)
                a_name = u32(ap+4)
                a_raw = u32(ap+8)
                a_type = raw[ap+13]
                a_data = u32(ap+14)
                aname = strings[a_name] if 0 <= a_name < len(strings) else "?"
                if a_type == 3:
                    aval = strings[a_data] if 0 <= a_data < len(strings) else ""
                elif a_type == 1:
                    aval = str(struct.unpack("<i", struct.pack("<I", a_data))[0])
                elif a_type == 0x12:
                    aval = "true" if a_data else "false"
                elif a_type == 0x10:
                    aval = "0x%08x" % a_data
                else:
                    aval = "<typ=%d val=%d>" % (a_type, a_data)
                attrs.append((aname, aval))
                ap += 20
            elements.append((name, attrs))
        if csize <= 0:
            break
        o += csize
    else:
        # unknown/skip chunk (resource map etc): need size
        csize = u32(o+4)
        if csize <= 0:
            break
        o += csize

print("=== PARSED STRINGS:", len(strings))
print("=== ELEMENTS:", len(elements))

# Filter interesting
perms = [v for (n,attrs) in elements if n=="uses-permission" for (k,v) in attrs if k=="name"]
print("\n########## PERMISSIONS (%d) ##########" % len(perms))
for p in perms:
    print("  -", p)

comp_types = ["activity","activity-alias","service","receiver","provider","application","meta-data","intent-filter","action","category","data"]
print("\n########## COMPONENTS & KEY TAGS ##########")
for n, attrs in elements:
    if n in ("activity","activity-alias","service","receiver","provider","application","meta-data"):
        d = dict(attrs)
        line = f"\n[{n}] name={d.get('name')}"
        if 'android:exported' in d: line += f"  exported={d['android:exported']}"
        if 'android:permission' in d: line += f"  permission={d['android:permission']}"
        if 'android:enabled' in d: line += f"  enabled={d['android:enabled']}"
        print(line)
    elif n in ("action","category","data"):
        d = dict(attrs)
        val = d.get('android:name') or d.get('android:scheme') or d.get('android:host') or d.get('android:mimeType')
        if val: print(f"    ({n}) {val}")

# print application-level attrs fully
print("\n########## APPLICATION TAG DETAIL ##########")
for n, attrs in elements:
    if n == "application":
        for k,v in attrs:
            print(f"  {k} = {v}")
