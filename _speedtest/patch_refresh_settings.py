# -*- coding: utf-8 -*-
"""修改 刷新设置 方法的默认值为自研 adb。三平台同步。"""
import os

OLD = """        except Exception:
            self._用协议客户端 = False
            self._用自研adb = False
            self._用系统adb = False

        # 如果勾选了使用系统环境"""

NEW = """        except Exception as e:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                '刷新设置时配置加载失败（%s），默认使用自研 adb。', e)
            self._用协议客户端 = False
            self._用自研adb = True
            self._用系统adb = False

        # 如果勾选了使用系统环境"""

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '工具', 'ADB工具.py')
    if not os.path.isfile(p):
        print('跳过:', p)
        continue
    d = open(p, encoding='utf-8').read()
    if OLD in d:
        d = d.replace(OLD, NEW, 1)
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已修改 刷新设置 默认值')
    else:
        print(f'{plat}: 未找到旧块（可能已改过或格式不同）')
