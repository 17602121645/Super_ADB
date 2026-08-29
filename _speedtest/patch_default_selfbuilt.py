# -*- coding: utf-8 -*-
"""修改 AdbHelper.__init__：配置加载失败时默认 _用自研adb=True，并打印警告。三平台同步。"""
import os

OLD = """        except Exception:
            self._用协议客户端 = False
            self._用自研adb = False
            self._用系统adb = False

        # 如果勾选了使用系统环境变量的 adb，强制用 PATH 中的 adb（排除项目自带的）"""

NEW = """        except Exception as e:
            # 配置加载失败（如干净打包后未包含 配置/ 目录）：默认自研 adb，
            # 避免静默回退到官方 adb 导致用户困惑。
            import logging as _lg
            _lg.getLogger(__name__).warning(
                'ADB 配置加载失败（%s），默认使用自研 adb。请确认 配置/Super_ADB配置.json 存在。', e)
            self._用协议客户端 = False
            self._用自研adb = True
            self._用系统adb = False

        # 如果勾选了使用系统环境变量的 adb，强制用 PATH 中的 adb（排除项目自带的）"""

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '工具', 'ADB工具.py')
    if not os.path.isfile(p):
        print('跳过:', p)
        continue
    d = open(p, encoding='utf-8').read()
    if OLD in d:
        d = d.replace(OLD, NEW, 1)
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已修改默认值为自研 adb')
    elif '_用自研adb = True' in d:
        print(f'{plat}: 已改过，跳过')
    else:
        print(f'{plat}: 未找到旧块')
