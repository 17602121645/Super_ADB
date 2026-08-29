# -*- coding: utf-8 -*-
import os, time
exe = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\打包\dist\Super_ADB\Super_ADB.exe'
spec = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\打包\Super_ADB.spec'
for f in [exe, spec]:
    if os.path.exists(f):
        mt = os.path.getmtime(f)
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt))
        print(f'{os.path.basename(f)}: 修改时间 {ts}, 大小 {os.path.getsize(f)//1024}KB')
