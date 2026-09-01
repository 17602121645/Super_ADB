# -*- coding: utf-8 -*-
"""验证 _config_path 重构：配置统一放 config/ 子目录 + adb_shell_config.json → super_adb_config.json 迁移"""
import os
import sys
import json
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Super_ADB_Win
sys.path.insert(0, os.path.abspath(BASE))
sys.path.insert(0, os.path.abspath(os.path.join(BASE, "tools")))

from adb_tools import _config_path, 加载json配置, 保存json配置

results = []

def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))

# ---- 1. 路径解析：主配置名 ----
p1 = _config_path('config/super_adb_config.json')
check("主配置路径含 config/ 子目录",
      p1 and p1.endswith(os.path.join('config', 'super_adb_config.json')),
      f"resolved={p1}")

# ---- 2. 路径解析：裸文件名（secondary configs）----
p2 = _config_path('wifi_debug_history.json')
check("裸名 wifi_debug_history.json 也落在 config/ 下",
      p2 and 'config' in p2 and p2.endswith(os.path.join('config', 'wifi_debug_history.json')),
      f"resolved={p2}")

p3 = _config_path('wifi_paired_devices.json')
check("裸名 wifi_paired_devices.json 也落在 config/ 下",
      p3 and 'config' in p3 and p3.endswith(os.path.join('config', 'wifi_paired_devices.json')),
      f"resolved={p3}")

# ---- 3. 迁移测试：adb_shell_config.json → Super_ADB配置.json ----
old_main = os.path.join(BASE, 'config', 'adb_shell_config.json')
new_main = os.path.join(BASE, 'config', 'super_adb_config.json')

# 备份现有文件（如果存在）
backup = {}
old_content = None
if os.path.isfile(old_main):
    with open(old_main, 'r', encoding='utf-8') as f:
        old_content = f.read()
    backup['old_main'] = old_content
    print(f"\n--- 发现旧主配置 {old_main}（{len(old_content)} 字节），将触发迁移 ---")
elif os.path.isfile(new_main):
    with open(new_main, 'r', encoding='utf-8') as f:
        old_content = f.read()
    print(f"\n--- 新主配置已存在 {new_main}（{len(old_content)} 字节），迁移应跳过 ---")
else:
    print("\n--- 无现有主配置，跳过迁移测试 ---")

# 删除新文件以强制迁移（如果有旧文件）
if old_content is not None and os.path.isfile(old_main) and not os.path.isfile(new_main):
    # 迁移应该发生在 加载json配置 调用时
    cfg = 加载json配置('config/super_adb_config.json')
    check("迁移后旧文件 adb_shell_config.json 不再存在", not os.path.isfile(old_main))
    check("迁移后新文件 super_adb_config.json 已创建", os.path.isfile(new_main))
    # 验证内容一致
    with open(new_main, 'r', encoding='utf-8') as f:
        new_content = f.read()
    migrated_data = json.loads(new_content) if new_content.strip() else {}
    original_data = json.loads(old_content) if old_content.strip() else {}
    check("迁移后 JSON 内容一致", migrated_data == original_data,
          f"keys={sorted(migrated_data.keys()) if isinstance(migrated_data, dict) else 'N/A'}")
elif old_content is not None and os.path.isfile(new_main):
    # 新文件已存在，迁移应跳过
    cfg = 加载json配置('config/super_adb_config.json')
    check("新文件已存在时跳过迁移", os.path.isfile(new_main))

# ---- 4. 写入/读取往返测试 ----
test_cfg = {"_smoke_test": True, "value": 42, "nested": {"a": 1}}
保存json配置('config/super_adb_config.json', test_cfg)
loaded = 加载json配置('config/super_adb_config.json')
check("save→load 往返正确", loaded == test_cfg,
      f"keys={sorted(loaded.keys()) if isinstance(loaded, dict) else 'N/A'}")

# 恢复原配置内容（如果之前有备份）
if old_content is not None:
    保存json配置('config/super_adb_config.json', json.loads(old_content) if old_content.strip() else {})
    print("\n--- 已恢复原配置内容到 super_adb_config.json ---")

# ---- 5. 基目录散落文件迁移测试 ----
# 模拟旧 frozen 行为：文件直接散落在 base 下
scatter_name = 'test_scatter_config.json'
scatter_base = os.path.join(BASE, scatter_name)
scatter_target = os.path.join(BASE, 'config', scatter_name)

# 清理
for p in (scatter_base, scatter_target):
    if os.path.isfile(p):
        os.remove(p)

# 创建散落文件
with open(scatter_base, 'w', encoding='utf-8') as f:
    json.dump({"scatter": True}, f)

# 触发 _config_path（应迁移）
resolved = _config_path(scatter_name)
check("散落在 base 下的文件迁移到 config/", not os.path.isfile(scatter_base) and os.path.isfile(scatter_target),
      f"resolved={resolved}")
if os.path.isfile(scatter_target):
    os.remove(scatter_target)

# ---- 汇总 ----
passed = sum(1 for s, _, _ in results if s == "PASS")
failed = sum(1 for s, _, _ in results if s == "FAIL")
print(f"\n{'='*50}")
print(f"总计: {passed} PASS / {failed} FAIL")
if failed:
    sys.exit(1)
