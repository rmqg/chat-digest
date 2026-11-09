# 安全策略

chat-digest 处理聊天记录、模型 API key 和网站凭证。不要在公开 issue、PR、日志或截图里贴出真实的导出目录、`runtime/` 里的文件、报告、`sources.json`、API key、Bark 地址、网站账号密码或私人头像。

## 报告漏洞

请使用 GitHub 的私密漏洞报告（仓库 Security 页的 “Report a vulnerability”），或发送邮件至 `ruomuqiuguang@icloud.com`。报告里请写：受影响的版本和平台、入口（哪个命令或服务）、风险、以及用**完全虚构的数据**做的最小复现（可以基于 `examples/synthetic-export/`）。

支持的版本：只为最新的 2.x 修复安全问题；1.x（wechat-digest）不再维护。

## 已经暴露了秘密

立刻在对应服务里撤销或轮换（模型 key、Bark 设备 key、网站密码、SSH 密钥）；从 Git 删掉文件不能消除历史里的泄露。发送相关的问题请先停掉定时任务、保留现场，不要清空 `unknown` 回执来“重来”。

详细的安全边界见 [docs/SECURITY.md](docs/SECURITY.md)。
