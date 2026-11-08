# 参与贡献

欢迎改进总结质量、报告渲染、部署体验和文档。

1. 先读 [开发说明](docs/DEVELOPMENT.md)、[安全边界](docs/SECURITY.md) 和 [接口约定](docs/CONTRACT.md)。
2. 改动必须保留这些不变量：导出目录只读；发送目标明确；可能已提交的发送一律记 `unknown`、不自动重发；所有模型调用走同一条 fallback 链；媒体按哈希核对、缩略图不冒充原图；报告引用必须对应真实消息。不要用“假成功”、默认目标或放宽校验来修报错。
3. 只提交原创代码，或有明确再分发许可的代码，并在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 写明来源、固定版本和许可证。没有许可证的上游代码一律不收。
4. 测试和样本只能用虚构数据。不要提交真实聊天、成员列表、头像、截图或任何凭证。
5. 提交前运行：`pytest`、`scripts/check-docs.py --cli`、`scripts/audit-release.py`、`./bin/chat-digest demo`。改了配置或命令行，要同步 `examples/`、[CONFIGURATION.md](docs/CONFIGURATION.md) 和 `doctor`。
6. PR 里写清楚：解决什么问题、行为变化、兼容性、实际跑过哪些验证、没覆盖哪些平台。没有实测过的不要写成“支持”。

贡献按本项目的 MIT 许可证提交。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。
