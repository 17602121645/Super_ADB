# -*- coding: UTF-8 -*-
"""
Chart.js 本地化加载。

导出 HTML 性能报告时，把 chart.umd.min.js 内联进 HTML，避免依赖公网 CDN
（cdn.jsdelivr.net）。Super_ADB 大量场景是给电视盒子 / 离线环境抓数据，
离线打开报告时 CDN 加载失败会导致图表全空，故改为随包分发 + 内联。
"""
import os
import sys

_CHART_JS_FILENAME = 'chart.umd.min.js'


def load_chart_js():
    """返回 chart.umd.min.js 文本；找不到时返回空串（报告仍可生成，图表降级）。

    路径解析：
      - 冻结运行（PyInstaller）：sys._MEIPASS/resources/chart.umd.min.js
      - 源码运行：本文件在 Super_ADB_Win/tools/，资源在 Super_ADB_Win/resources/
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 冻结包与源码目录名均为 resources；'资源' 为改英文名之前的旧目录，保留兜底
    for _sub in ('resources', '资源'):
        p = os.path.join(base, _sub, _CHART_JS_FILENAME)
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return f.read()
        except OSError:
            continue
    return ''
