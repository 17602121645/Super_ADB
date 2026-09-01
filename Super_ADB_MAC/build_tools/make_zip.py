# -*- coding: UTF-8 -*-
"""
UTF-8 安全的 .app -> zip 打包助手（替代 macOS 自带 ditto）。

为什么不用 ditto：历史上目录名含中文时，macOS 自带 ditto/zip 会整目录丢失。
目录名现已全部英文化，该顾虑不再成立，但本脚本可精确控制权限与符号链接，
故保留。本脚本用 Python zipfile（UTF-8 路径）打包，并保留：
  - Unix 文件权限（含启动器 EXE 的 +x 位，否则解压后无法启动）
  - 符号链接（如 外部扩展 内、Resources/libusb-1.0.dylib -> Frameworks 的链接）

用法：
    python3 make_zip.py <app路径> <zip输出路径>
"""
import os
import sys
import stat
import zipfile


def _mode_to_attr(mode):
    """把 Unix st_mode 编码为 zip external_attr（高 16 位）。

    必须用完整 mode（含 S_IFREG/S_IFLNK 等文件类型位），不能只取权限位：
    zipfile 解压时靠 external_attr 里的文件类型位判断是否 chmod / 建符号链接，
    若类型位被抹掉，普通文件不会恢复 +x、符号链接会被存成普通文件。
    """
    return (mode & 0xFFFF) << 16


def main():
    if len(sys.argv) < 3:
        print('用法: python3 make_zip.py <app> <zip>')
        sys.exit(1)
    app = sys.argv[1]
    zip_path = sys.argv[2]
    app_parent = os.path.dirname(os.path.abspath(app))

    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(app):
            # 目录条目（带正确权限，目录名以 '/' 结尾）
            rel_dir = os.path.relpath(root, app_parent)
            dinfo = zipfile.ZipInfo(rel_dir + '/')
            dinfo.external_attr = _mode_to_attr(os.stat(root).st_mode)
            dinfo.create_system = 3  # Unix
            dinfo.compress_type = zipfile.ZIP_DEFLATED  # 关键：writestr 不继承归档压缩
            zf.writestr(dinfo, b'')
            count += 1

            # os.walk 把「指向目录的符号链接」归到 dirs，而 followlinks=False 时
            # 既不下沉、也不会出现在 files 里 —— 不单独捞出来就会被整条丢掉。
            # .app 里这类链接是必需的，丢了直接起不来：
            #   Contents/Frameworks/python3.X -> python3__dot__X
            #       （丢 → ModuleNotFoundError: No module named '_struct'）
            #   Contents/Frameworks/*.framework/Versions/Current -> A
            #   Contents/Frameworks/vendor/scrcpy/<ver> -> <ver 带 __dot__>
            link_dirs = [d for d in dirs if os.path.islink(os.path.join(root, d))]
            if link_dirs:
                dirs[:] = [d for d in dirs if d not in link_dirs]

            for fn in sorted(link_dirs) + sorted(files):
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, app_parent)
                st = os.lstat(fp)
                finfo = zipfile.ZipInfo(rel)
                finfo.create_system = 3
                finfo.compress_type = zipfile.ZIP_DEFLATED  # 关键：writestr 不继承归档压缩
                if stat.S_ISLNK(st.st_mode):
                    # 符号链接：数据写链接目标（类型位已在 st.st_mode 中）
                    finfo.external_attr = _mode_to_attr(st.st_mode)
                    zf.writestr(finfo, os.readlink(fp).encode('utf-8'))
                else:
                    finfo.external_attr = _mode_to_attr(st.st_mode)
                    with open(fp, 'rb') as fh:
                        zf.writestr(finfo, fh.read())
                count += 1

    print('zip 完成: %s （%d 个条目）' % (zip_path, count))


if __name__ == '__main__':
    main()
