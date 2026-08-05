# -*- coding: UTF-8 -*-
"""
@author:JCS
@file:pyinstall_y.py
@time:2022/11/26
"""
import os
import time

#生成配置文件
def install(main):
    name =f"Super_ADB{time.strftime('%H%M%S')}"
    # upx_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'upx-5.2.0-win64')
    # install = f'pyinstaller --clean -w -i cf_s3.ico -n {name} --upx-dir "{upx_dir}" --add-data "data/;data" {main}'  #压缩打包

    install = f'pyinstaller --clean -w -i adb.png -n {name} {main}'
    os.system(install)
    print('配置文件生成成功')
def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')

if __name__ == '__main__':
    install("Super_ADB_Main.py")
