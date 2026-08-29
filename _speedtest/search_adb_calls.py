# -*- coding: utf-8 -*-
"""搜索项目中所有可能调起官方 adb.exe 的 subprocess/os 调用，且没有 _用自研adb 保护的。"""
import os, re

root = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win'
results = []

for dp, dn, fn in os.walk(root):
    if '打包' in dp or '__pycache__' in dp or 'build' in dp or 'dist' in dp:
        continue
    for f in fn:
        if not f.endswith('.py'):
            continue
        p = os.path.join(dp, f)
        try:
            d = open(p, encoding='utf-8', errors='ignore').read()
        except:
            continue
        lines = d.split('\n')
        for i, line in enumerate(lines, 1):
            # 找含 adb 的 subprocess/os 调用
            if re.search(r'(subprocess\.(run|Popen|call)|os\.popen|os\.system|_run\()', line) and 'adb' in line.lower():
                # 检查前后 5 行是否有 _用自研adb 保护
                context = '\n'.join(lines[max(0,i-6):i+2])
                has_protect = '_用自研adb' in context or 'self_built' in context
                results.append((os.path.relpath(p, root), i, line.strip()[:120], has_protect))

print(f'共找到 {len(results)} 处含 adb 的 subprocess 调用：')
print()
for path, lineno, line, protected in results:
    tag = '✓ 有保护' if protected else '✗ 无保护'
    print(f'{tag}  {path}:{lineno}')
    print(f'       {line}')
    print()
