#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zero-dependency APK analyzer:
 - parses classes.dex (header, string_ids, type_ids, method_ids, proto_ids)
 - extracts all string constants -> searches URLs/IPs/domains/suspicious keywords
 - resolves method references to detect dangerous API usage
 - parses binary AndroidManifest.xml (AXML) for permissions & components
"""
import struct, re, sys, os

BASE = "C:/Users/57676/Desktop/adb/_apk_extract/"
DEX = BASE + "classes.dex"
MAN = BASE + "AndroidManifest.xml"

def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def u64(b, o): return struct.unpack_from("<Q", b, o)[0]
def sleb128(b, o):
    result = 0; shift = 0; size = 0
    while True:
        byte = b[o + size]
        result |= (byte & 0x7f) << shift
        size += 1
        if (byte & 0x80) == 0: break
        shift += 7
    if shift < 64 and (byte & 0x40):
        result |= -(1 << shift)
    return result, size
def uleb128(b, o):
    result = 0; shift = 0; size = 0
    while True:
        byte = b[o + size]
        result |= (byte & 0x7f) << shift
        size += 1
        if (byte & 0x80) == 0: break
        shift += 7
    return result, size

with open(DEX, "rb") as f:
    data = f.read()

assert data[:4] == b"dex\n", "not a dex"
print("=== DEX HEADER ===")
string_ids_size = u32(data, 0x38)
string_ids_off  = u32(data, 0x3c)
type_ids_size   = u32(data, 0x40)
type_ids_off    = u32(data, 0x44)
proto_ids_size  = u32(data, 0x48)
proto_ids_off   = u32(data, 0x4c)
field_ids_size  = u32(data, 0x50)
field_ids_off   = u32(data, 0x54)
method_ids_size = u32(data, 0x58)
method_ids_off  = u32(data, 0x5c)
class_defs_size = u32(data, 0x60)
class_defs_off  = u32(data, 0x64)
print(f"strings={string_ids_size} types={type_ids_size} protos={proto_ids_size} "
      f"fields={field_ids_size} methods={method_ids_size} class_defs={class_defs_size}")

# --- string pool ---
strings = []
for i in range(string_ids_size):
    off = u32(data, string_ids_off + i*4)
    # uleb128 size (in UTF-16 units), then MUTF-8 data
    _, s = uleb128(data, off)
    # read until 0 byte (MUTF-8)
    end = off + s
    j = end
    while data[j] != 0:
        j += 1
    raw = data[end:j]
    try:
        sval = raw.decode("utf-8", "replace")
    except Exception:
        sval = raw.decode("latin-1", "replace")
    strings.append(sval)

print(f"\n=== TOTAL STRINGS: {len(strings)} ===")

# --- type pool (class names) ---
type_names = []
for i in range(type_ids_size):
    desc_off = u32(data, type_ids_off + i*4)
    type_names.append(strings[desc_off])
# deobfuscate descriptor -> java name
def desc_to_java(d):
    if not d.startswith("L"): return d
    return d[1:-1].replace("/", ".")
classes = [desc_to_java(t) for t in type_names if t.startswith("L")]

# --- proto pool ---
protos = []
for i in range(proto_ids_size):
    base = proto_ids_off + i*12
    shorty = strings[u32(data, base)]
    ret = desc_to_java(type_names[u32(data, base+4)])
    param_off = u32(data, base+8)
    params = []
    if param_off != 0:
        pcount = u32(data, param_off)
        for k in range(pcount):
            tidx = u16(data, param_off+4 + k*2)
            params.append(desc_to_java(type_names[tidx]))
    protos.append((shorty, ret, params))

# --- method pool ---
methods = []
for i in range(method_ids_size):
    base = method_ids_off + i*8
    cid = u16(data, base)
    pid = u16(data, base+2)
    nid = u32(data, base+4)
    cname = desc_to_java(type_names[cid]) if cid < len(type_names) else "?"
    p = protos[pid] if pid < len(protos) else ("","","")
    methods.append((cname, strings[nid], p[1], p[2]))

# ---- helpers to print ----
def banner(t):
    print("\n" + "="*70 + "\n" + t + "\n" + "="*70)

# ===== 1. Suspicious string patterns =====
banner("1. SUSPICIOUS STRINGS (URLs / IPs / domains / endpoints / keywords)")

url_re   = re.compile(r'https?://[^\s"\'<>\\]+', re.I)
ip_re    = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b')
host_re  = re.compile(r'([a-zA-Z0-9][a-zA-Z0-9\-]{1,63}\.(?:com|net|org|cn|io|info|xyz|top|ru|tk|link|click|edu|gov|co|me|app|dev|su|cc|tv|biz|ws|su|pw|nu)[/:?\s])')
# paths that look like API endpoints
api_re   = re.compile(r'(/api/|/v\d+/|/cgi-bin/|/admin|/upload|/cmd|/command|/data|/config|/gate|/server|/client|/push|/pull|/exec|/shell|/remote|/control)', re.I)
susp_kw  = ['password','passwd','token','secret','encrypt','decrypt','base64','xor',
            'http','url','socket','gethost','dns','ping','exec','runtime','root',
            'su ','shell','command','upload','download','send','sms','telegram','bot',
            'ftp','ssh','proxy','url','http','https','post','get','request','response',
            'keylog','clipboard','contact','location','camera','record','screenshot',
            'inject','hook','hook','loadlibrary','dexclassloader','reflection','reflect',
            'broadcast','receiver','service','foreground','persist','boot','wake','alarm',
            'intercept','intercept','窃取','回传','上报','后台','隐藏','木马','后门','远控','监控']

found_urls = set()
found_ips  = set()
found_hosts= set()
found_api  = set()
kw_hits    = {}

for s in strings:
    if not s: continue
    for m in url_re.findall(s):
        found_urls.add(m)
    for m in ip_re.findall(s):
        found_ips.add(m)
    for m in host_re.findall(s):
        found_hosts.add(m[0].rstrip('/').rstrip(':'))
    for m in api_re.findall(s):
        found_api.add(m)
    sl = s.lower()
    for kw in susp_kw:
        if kw in sl:
            kw_hits.setdefault(kw, set()).add(s)

print("\n-- URLs --")
for u in sorted(found_urls): print("   ", u)
print("\n-- IPs --")
for u in sorted(found_ips): print("   ", u)
print("\n-- Hosts (domains) --")
for u in sorted(found_hosts): print("   ", u)
print("\n-- API-like paths --")
for u in sorted(found_api): print("   ", u)

banner("2. SUSPICIOUS KEYWORD HITS (sample per keyword, max 8)")
for kw in sorted(kw_hits):
    print(f"\n[{kw}] ({len(kw_hits[kw])} hits):")
    for s in list(kw_hits[kw])[:8]:
        print("   ", repr(s)[:160])

# ===== 2. Dangerous API usage via method references =====
banner("3. DANGEROUS API METHOD REFERENCES (network / telephony / exec / reflection / privacy)")

danger_patterns = [
    (r'java/net/HttpURLConnection', 'HTTP'),
    (r'java/net/URL', 'URL'),
    (r'java/net/Socket', 'SOCKET'),
    (r'java/net/InetAddress', 'DNS'),
    (r'javax/net/ssl', 'SSL'),
    (r'okhttp', 'OKHTTP'),
    (r'retrofit', 'RETROFIT'),
    (r'apache/http', 'APACHE_HTTP'),
    (r'volley', 'VOLLEY'),
    (r'WebView', 'WEBVIEW'),
    (r'telephony/SmsManager', 'SMS'),
    (r'telephony/TelephonyManager', 'TELEPHONY'),
    (r'getDeviceId|getSubscriberId|getImei|getSimSerial', 'DEVICE_ID'),
    (r'location/LocationManager|Location', 'LOCATION'),
    (r'Camera', 'CAMERA'),
    (r'MediaRecorder', 'RECORD'),
    (r'getClipboard|ClipboardManager', 'CLIPBOARD'),
    (r'Runtime\.exec|ProcessBuilder', 'EXEC'),
    (r'DexClassLoader|PathClassLoader|BaseDexClassLoader', 'DEXLOAD'),
    (r'loadLibrary|System\.load', 'NATIVE_LOAD'),
    (r'reflect|Method\.invoke|Class\.forName', 'REFLECTION'),
    (r'SharedPreference|SharedPreferences', 'PREFS'),
    (r'ContentResolver', 'CONTENT_RESOLVER'),
    (r'PackageManager', 'PKG_MGR'),
    (r'BroadcastReceiver|sendBroadcast|registerReceiver', 'BROADCAST'),
    (r'AccountManager', 'ACCOUNT'),
    (r'getInstalledPackages|getInstalledApplications', 'PKG_ENUM'),
    (r'Settings\$Secure|Settings\.Secure', 'SETTINGS'),
]
import re as _re
compiled = [( _re.compile(p, _re.I), label) for p, label in danger_patterns]

api_hits = {}
for (cname, mname, ret, params) in methods:
    sig = f"{cname}.{mname}"
    for pat, label in compiled:
        if pat.search(sig) or pat.search(cname):
            api_hits.setdefault(label, set()).add(sig)

for label in [l for _,l in danger_patterns]:
    if label in api_hits:
        print(f"\n[{label}] referenced ({len(api_hits[label])}):")
        for s in list(api_hits[label])[:25]:
            print("   ", s)

# ===== 3. Class inventory (top-level packages) =====
banner("4. CLASS PACKAGE INVENTORY (non-framework, app classes)")
# Count by first 3 segments
from collections import Counter
pkgc = Counter()
app_classes = []
for c in classes:
    if c.startswith(("android.","java.","kotlin.","androidx.","com.google.","dalvik.","javax.","org.apache.","okio.","okhttp3.","retrofit2.","com.squareup.")):
        # still record but mark framework
        pass
    else:
        app_classes.append(c)
    parts = c.split(".")
    pkg = ".".join(parts[:3]) if len(parts)>=3 else c
    pkgc[pkg] += 1

print("Total unique classes:", len(classes))
print("App (non-framework) classes:", len(app_classes))
print("\nTop packages:")
for pkg, cnt in pkgc.most_common(25):
    print(f"   {cnt:4d}  {pkg}")

print("\n-- Sample of app classes (first 60) --")
for c in sorted(app_classes)[:60]:
    print("   ", c)

# persist results to file
with open(BASE + "_analysis.txt","w",encoding="utf-8") as out:
    out.write("=== DEX ANALYSIS ===\n")
    out.write(f"strings={string_ids_size} types={type_ids_size} methods={method_ids_size} classes={class_defs_size}\n")
