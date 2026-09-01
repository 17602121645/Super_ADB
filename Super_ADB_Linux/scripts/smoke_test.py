# -*- coding: UTF-8 -*-
"""冻结 exe 启动冒烟测试：offscreen 平台下启动，确认不崩溃。"""
import os
import sys
import time
import subprocess

here = os.path.dirname(os.path.abspath(__file__))
exe = os.path.join(here, 'build_tools', 'dist', 'Super_ADB', 'Super_ADB.exe')
if not os.path.exists(exe):
    print('ERROR: 找不到', exe)
    sys.exit(2)

env = dict(os.environ)
env['QT_QPA_PLATFORM'] = 'offscreen'
日志 = os.path.join(here, 'smoke_stdout.日志')

print('启动:', exe)
try:
    p = subprocess.Popen(
        [exe],
        env=env,
        stdout=open(日志, 'wb'),
        stderr=subprocess.STDOUT,
        creationflags=0x00000008,  # CREATE_NO_WINDOW (等同 -w)
    )
except Exception as e:
    print('启动失败:', e)
    sys.exit(3)

print('PID:', p.pid, '等待 6s ...')
time.sleep(6)
rc = p.poll()
if rc is None:
    print('SMOKE TEST PASS: 进程存活(未崩溃)，pid=', p.pid)
    alive = True
else:
    print('SMOKE TEST FAIL: 启动即退出，returncode=', rc)
    alive = False

# 读取可能的 Qt 报错输出
try:
    with open(日志, 'rb') as f:
        out = f.read().decode('utf-8', 'replace')
    if out.strip():
        print('--- 捕获输出(前 1500 字符) ---')
        print(out[:1500])
    else:
        print('(无 stdout/stderr 捕获)')
except Exception as e:
    print('读取日志失败:', e)

if alive:
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    print('已终止进程')
print('SMOKE TEST', 'PASS' if alive else 'FAIL')
sys.exit(0 if alive else 1)
