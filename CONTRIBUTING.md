# 贡献指南

感谢你对 Super_ADB 的关注！我们欢迎任何形式的贡献，包括但不限于：提交 Bug 报告、功能建议、代码改进、文档完善等。

在参与贡献之前，请阅读以下指南。

## 📋 行为准则

参与本项目即表示你同意遵守 [行为准则](CODE_OF_CONDUCT.md)。请确保所有互动都保持尊重和专业。

## 🐛 提交 Bug 报告

提交 Issue 前，请先：

1. 搜索现有 [Issues](https://github.com/17602121645/Super_ADB/issues)，确认是否已有相同问题
2. 确认使用的是最新版本
3. 准备好以下信息：
   - 操作系统及版本（Windows/macOS/Linux）
   - Super_ADB 版本号
   - 复现步骤
   - 预期行为与实际行为
   - 相关截图或日志

## 💡 功能建议

我们欢迎新功能建议！提交功能建议时请说明：

- 功能描述与使用场景
- 为什么这个功能对项目有价值
- 可能的实现思路（可选）

## 🔧 代码贡献

### 开发环境搭建

```bash
# Fork 并克隆你的仓库
git clone https://github.com/<your-username>/Super_ADB.git
cd Super_ADB

# 安装依赖
pip install -r requirements.txt

# 创建功能分支
git checkout -b feature/your-feature-name
```

### 代码规范

- **Python**：遵循 PEP 8，使用 4 空格缩进
- **命名**：变量和函数使用蛇形命名（snake_case），类使用大驼峰（PascalCase）
- **注释**：关键逻辑必须有中文注释
- **编码**：所有源文件使用 UTF-8 编码，文件头声明 `# -*- coding: utf-8 -*-`

### 提交信息规范

使用清晰的中文提交信息，格式建议：

```
<类型>: <简要描述>

类型包括：
- feat: 新功能
- fix: 修复 Bug
- docs: 文档更新
- style: 代码格式调整
- refactor: 代码重构
- perf: 性能优化
- test: 测试相关
- chore: 构建/工具/依赖等杂项
```

示例：
```
feat: 新增 WiFi 密码查看功能
fix: 修复文件管理大文件上传进度显示异常
docs: 更新 README 安装说明
```

### 提交 Pull Request

1. 确保你的分支与上游 `master` 保持同步
2. 确保代码在三个平台（Windows/macOS/Linux）上均能正常运行
3. 更新相关文档（如新增功能需更新 `docs/USAGE.md`）
4. 提交 PR，在描述中说明：
   - 改动内容与目的
   - 关联的 Issue（如有）
   - 测试验证情况

## 📚 文档贡献

文档同样重要！如果你发现文档有误、过时或不够清晰，欢迎提交 PR 改进。主要文档包括：

- `README.md` — 项目主页
- `docs/USAGE.md` — 详细功能说明
- `CONTRIBUTING.md` — 本文件
- `CHANGELOG.md` — 变更日志

## ❓ 有问题？

如果在贡献过程中遇到问题，可以：

- 提交 [Issue](https://github.com/17602121645/Super_ADB/issues)
- 关注公众号 **Super_ADB** 留言反馈

再次感谢你的贡献！🎉
