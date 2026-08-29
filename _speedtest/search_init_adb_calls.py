# -*- coding: utf-8 -*-
"""搜索主入口 __init__ 和 showEvent 里直接调用的 self.adb. 方法。"""
import re

p = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\项目启动入口\Super_ADB_主入口.py'
d = open(p, encoding='utf-8').read()

# 找 __init__ 方法体
i = d.find('class Super_ADB')
init_start = d.find('def __init__', i)
# 找 __init__ 结束（下一个 def 或 showEvent）
init_end = d.find('def showEvent', init_start)
if init_end < 0:
    init_end = d.find('    def ', init_start + 10)

init_body = d[init_start:init_end]
print('=== __init__ 里的 self.adb. 调用 ===')
for m in re.finditer(r'self\.adb\.(\w+)', init_body):
    s = max(0, m.start()-40)
    print(f'  {m.group(1)}: ...{init_body[s:m.start()+30].strip()!r}')

print()
print('=== showEvent 里的 self.adb. 调用 ===')
show_start = d.find('def showEvent', i)
show_end = d.find('    def ', show_start + 10)
show_body = d[show_start:show_end]
for m in re.finditer(r'self\.adb\.(\w+)', show_body):
    s = max(0, m.start()-40)
    print(f'  {m.group(1)}: ...{show_body[s:m.start()+30].strip()!r}')

# 也搜索所有混入类的 __init__
print()
print('=== 所有混入类的 __init__ ===')
for m in re.finditer(r'class \w+Mixin', d):
    print(f'  {m.group(0)}')
