# -*- coding: utf-8 -*-
"""统一的 JSON 读写封装。

- load_json: 文件缺失返回 default；解析/IO 异常记录 warning 并返回 default（不再无声吞掉）。
- save_json: 原子写（先写临时文件再 os.replace），避免写一半崩溃导致配置损坏；
  统一 ensure_ascii=False 以保留中文。
"""
import json
import logging
import os
import tempfile

_log = logging.getLogger(__name__)


def load_json(path, default=None):
    """读取 JSON 文件；缺失返回 default，解析失败记录 warning 并返回 default。"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        _log.warning('读取 JSON 失败 %s: %s', path, e)
        return default


def save_json(path, obj, *, mkdir=True):
    """原子写 JSON 到 path（先写临时文件再 os.replace），避免写一半崩溃损坏。

    返回 True/False。统一 ensure_ascii=False 保留中文。
    """
    try:
        if mkdir:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
        dir_name = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return True
    except Exception as e:
        _log.warning('保存 JSON 失败 %s: %s', path, e)
        return False
