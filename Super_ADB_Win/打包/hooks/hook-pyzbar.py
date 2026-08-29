# -*- coding: UTF-8 -*-
"""
PyInstaller 钩子：把 pyzbar 包内的预编译 DLL（libzbar-64.dll / libiconv.dll）
一并收进打包产物。

背景：
- pyzbar 在运行期通过 ctypes 加载 zbar 的 DLL，PyInstaller 不会自动收集
  包目录内的 .dll，必须显式用 collect_dynamic_libs 收集，否则打包后扫码会
  报“找不到 libzbar”而崩溃。collect_dynamic_libs 会把 DLL 放到与 pyzbar 包
  相同的相对目录（_internal/pyzbar/），正好匹配 zbar_library.load() 的
  `Path(__file__).parent` 回退路径。
- 注意：不要用 collect_submodules('pyzbar')，否则会把 pyzbar.tests 也收进来，
  而 pyzbar.tests.test_pyzbar 顶层 import cv2 / numpy，会把整个 cv2（~111MB）
  重新拉回包里，体积暴涨。运行期所需子模块由 `hidden-import pyzbar` 的相对
  import 自动带齐，这里只负责 DLL。
"""
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs('pyzbar')
