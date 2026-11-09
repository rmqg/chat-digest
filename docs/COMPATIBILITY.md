# 兼容性

“实测通过”只写真正跑过的组合，命令和输出见 [VALIDATION.md](VALIDATION.md)。其他一律算“未测试 / 不保证”，不代表一定不能用。

## 宿主（安装、离线 demo、doctor、宿主侧调度脚本）

| 项目 | 实测通过 | 未测试 / 不保证 |
|---|---|---|
| 发行版 | Debian 12（bookworm）、Ubuntu 24.04 LTS：全新官方容器里按 [INSTALL.md](INSTALL.md) 安装、跑 demo 和 doctor；Ubuntu 22.04.5 LTS：全量单元测试 | 其他发行版；OpenWrt / iStoreOS 上 v2 还没有实机运行（v1 曾在 iStoreOS 24.10 上生产运行，v2 的宿主脚本只用标准库，procd 服务文件已提供） |
| 内核 | 7.0.14-orbstack（OrbStack 虚拟机，容器实测）；6.8.0-59-generic x86-64（单元测试） | 其他内核 |
| CPU 架构 | arm64（aarch64）、x86-64（amd64） | 32 位 ARM、其他架构 |
| Python | 3.11.2（Debian 12 自带）、3.12.3（Ubuntu 24.04 自带）、3.11.18 与 3.12.16（全量单元测试） | 3.13（CI 矩阵里有，本地未实测）；3.10 及以下不支持 |

## Docker 和运行时镜像

| 项目 | 实测通过 | 未测试 / 不保证 |
|---|---|---|
| Docker | 29.4.0（OrbStack，macOS arm64 主机） | Docker 24 以下、Podman、rootless Docker |
| 运行时镜像 `chat-digest-runtime:2.0.0`（基于 `python:3.12-slim`） | arm64 原生构建；在 `--network none --read-only --cap-drop ALL` 的容器里读只读挂载的合成导出，取图阶段输出 1 张就绪原图；amd64（模拟）构建成功并在同样隔离参数下启动 | 带模型的完整阶段链在容器里端到端运行（用真实或替身模型）；OCR 层 `WITH_OCR=1`（锁定的 wheel 只适用于 x86-64、Python 3.12，v1 在 CI 里验证过，v2 未重新实测） |

## 上下游

| 项目 | 实测通过 | 未测试 / 不保证 |
|---|---|---|
| 输入 chat-export/v1 | 仓库自带的合成导出；测试里现场生成的各种边界情况（去重、未写完的末行、坏行、越界路径、媒体哈希） | wechat-export-send、wecom-export 等真实导出工具的输出与 v2 联调（微信主账号的生产切换记录见 VALIDATION.md 后续更新） |
| 发送 chat-send/v1 | 合成的演示发送器 `scripts/demo-chat-send.py`；测试里模拟的四种状态、超时、输出不一致 | 真实的 `wechat-send` 与 v2 联调 |
| 模型 | 本机回环的演示模型（OpenAI 兼容流式接口） | 具体厂商。任何支持 `/chat/completions`、流式输出和 `json_schema` 结构化输出的接口理论上可用；v1 在生产中用过 Gemini、Qwen 等 OpenAI 兼容端点，v2 的模型调用代码与生产版相同 |
| QQ | 离线虚构样例渲染 | NapCat / OneBot v11 与 v2 的实机联调（这部分代码与 v1.2.4 基本相同，只合入了自然语言请求的 fallback） |
| 服务管理 | `chat-digest-site`：systemd 252（Debian 12 容器）、procd（OpenWrt 24.10 容器，aarch64）上实际启用、崩溃后自动重启、错误配置拒绝启动 | 其余服务在 systemd、procd 上的实际启用和长期运行 |
| 反向代理 | 文档里的示例原样启动并走完登录和页面：Nginx 1.22.1、Caddy 2.6.2（测试时改用 `tls internal`，未走公网证书申请） | Cloudflare Tunnel 实际连通（只用 cloudflared 2026.10.7 校验了配置和路由规则）；其他版本 |

## 已知限制

- 运行时镜像在 arm64 上构建通过，但可选 OCR 层的锁定依赖只适用于 x86-64。
- `image_pending_grace_seconds` 过后，还没下载到的原图按“缺失”处理，报告照出；之后再到的原图不会补进已发出的报告。
- v1 的附件正文提取（从微信本地文件读 PDF/Word 正文）没有迁到 v2：这需要导出工具在 chat-export 里提供文件，后续版本再考虑。
