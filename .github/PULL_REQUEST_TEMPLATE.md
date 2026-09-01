## 变更内容 / What changed

<!-- 一句话说明这个 PR 做了什么 -->

## 关联 issue / Related issue

<!-- Closes #123 -->

## 验证方式 / How was this tested

<!-- 说明你怎么验证的：跑了什么命令、在什么平台、看到什么结果 -->

- [ ] `ruff check .` 通过
- [ ] `python -m compileall` 通过
- [ ] 在真机 / 模拟器上手动验证过受影响的功能

## 检查清单 / Checklist

- [ ] 改动范围最小化，没有顺手重构无关代码
- [ ] 文件名、目录名、模块名使用 ASCII（`vendor/` 下的第三方产物除外）
- [ ] 没有提交密钥、安装包、构建产物或 IDE 配置
- [ ] 三棵平台树（`Super_ADB_MAC` / `Super_ADB_Linux` / `Super_ADB_Win`）中需要同步的部分已同步
