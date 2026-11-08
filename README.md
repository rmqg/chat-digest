# chat-digest

把一个群聊每天的聊天记录总结成**简报**和**回顾**两版报告（HTML + PNG），按时发回群里；群里有人 “@机器人 总结最近 3 小时” 时也能临时生成。报告按时间线分话题，每个要点都能展开看到原文出处，附群友金句和发言排行。

> 本仓库原名 `rmqg/wechat-digest`（v1.x）。v2.0.0 起只负责“总结和出报告”，微信的提取、解密和发送移到了 [wechat-export-send](https://github.com/rmqg/wechat-export-send)。从 v1.2.x 升级请看 [迁移指南](docs/MIGRATION-v2.md)。

## 它是什么

- **读导出，不碰客户端**：输入是 [chat-export/v1](docs/CONTRACT.md) 目录（一种简单的 JSONL 聊天记录导出格式），由导出工具产出。微信用 [wechat-export-send](https://github.com/rmqg/wechat-export-send)，企业微信用 [wecom-export](https://github.com/rmqg/wecom-export)，它们都产出这个格式；其他遵守格式的导出工具也可以。chat-digest 只读这个目录。
- **发送走标准命令**：发送时调用你配置的 [chat-send/v1](docs/CONTRACT.md) 命令（例如 `wechat-send`），并严格区分“已送达 / 确定没发出 / 不确定”：不确定时**绝不自动重发**。
- **QQ 直连**：QQ 群通过你自己的 OneBot v11 服务（如 NapCat）读取和发送，见 [QQ.md](docs/QQ.md)。
- **多模态证据**：图片先由视觉模型描述（可选 OCR），链接抓取正文后摘要，这些都作为“辅助证据”标明来源，不冒充群友发言。
- **模型可替换、可兜底**：任何 OpenAI 兼容接口；所有模型调用都按同一条 fallback 链切换，失败后指数退避（上限 900 秒）一直重试。
- **隔离运行**：每个处理阶段都在只读、无特权、限内存的一次性 Docker 容器里跑，不需要网络的阶段完全断网。

## 它不是什么

- 不是聊天客户端、机器人框架或官方 SDK；不登录账号，不提取密钥，不读客户端数据库或内存。
- 不保证总结完全准确：报告可能遗漏或误读，所以每条都附原文出处供核对。
- 不替你决定能不能处理一个群的聊天记录：只处理你有权处理的数据，见 [安全边界](docs/SECURITY.md)。
- 不发布镜像、不托管服务；模型费用由你自己的模型账号承担。

## 支持情况摘要

完整矩阵和测试细节见 [COMPATIBILITY.md](docs/COMPATIBILITY.md) 和 [VALIDATION.md](docs/VALIDATION.md)。

| 项目 | 实测通过 | 未测试 / 不保证 |
|---|---|---|
| 宿主系统 | Debian 12、Ubuntu 24.04 的全新容器（安装、demo、doctor） | 其他发行版；OpenWrt/iStoreOS 上 v2 尚未实机运行 |
| CPU 架构 | arm64、x86-64 | 其他 |
| Python | 3.11.2（Debian 12）、3.12.3（Ubuntu 24.04）、3.12.16（单元测试） | 3.13 只在 CI 配置里，未在本地实测；3.10 及以下不支持 |
| Docker | 29.4.0（OrbStack）：arm64 与 amd64 都能构建运行时镜像，arm64 上跑通断网取图阶段 | 带真实模型的完整容器流程；OCR 层（`WITH_OCR=1`）在 v2 尚未重新实测 |
| 输入 | 合成的 chat-export/v1 目录 | 真实导出工具的输出与 v2 的联调 |
| 发送 | 合成的 chat-send/v1 演示命令 | 真实的 `wechat-send` 与 v2 的联调 |

## 快速开始：离线 demo（不用账号、不联网）

```sh
git clone https://github.com/rmqg/chat-digest.git && cd chat-digest   # 或解压 release 包，见 docs/INSTALL.md
./scripts/install.sh --apply        # 建 .venv、装依赖、预取分词数据（只有这一步需要网络）
./bin/chat-digest demo --output ./chat-digest-demo
```

打开 `./chat-digest-demo/index.html`。demo 读取仓库自带的虚构导出 `examples/synthetic-export/`，用本机 127.0.0.1 上的演示模型走一遍真实的生成流程，再用演示发送器“发送”到本地 `outbox/` 目录。总结内容是演示数据，不代表真实模型效果。

## 快速开始：接入真实数据

```sh
./bin/chat-digest init                                  # 建 runtime/ 和 project.json
docker build -t chat-digest-runtime:2.0.0 . && ./bin/chat-digest pin-images
./bin/chat-digest list-conversations --export-root /srv/chat-export/acct1
# 编辑 runtime/project.json（export.root、group.conversation_id、send）和 runtime/gemini-model.json
./bin/chat-digest doctor --probe-send                   # 自检，发送只做 dry run
./bin/chat-digest prepare-only                          # 生成当天两版报告，不发送
```

确认报告没问题后再启用定时任务和服务，见 [INSTALL.md](docs/INSTALL.md)。

## 文档

- [安装](docs/INSTALL.md)：从零到每天自动出报，systemd 和 OpenWrt procd 两种方式
- [配置](docs/CONFIGURATION.md)：每个参数的含义、类型、默认值、示例和什么时候改
- [排障](docs/TROUBLESHOOTING.md)：按症状给命令
- [架构](docs/ARCHITECTURE.md)：数据流、阶段、状态文件
- [报告 JSON 格式](docs/REPORT-JSON.md)：报告字典的字段，换用自己的渲染器时看
- [安全边界](docs/SECURITY.md)：读什么、写什么、要什么权限、风险
- [兼容性](docs/COMPATIBILITY.md) 与 [实测记录](docs/VALIDATION.md)
- [接口约定 chat-export/v1 与 chat-send/v1](docs/CONTRACT.md)
- [运行维护](docs/OPERATIONS.md)、[开发](docs/DEVELOPMENT.md)、[QQ 接入](docs/QQ.md)
- [从 v1.2.x 迁移](docs/MIGRATION-v2.md)、[版本记录](CHANGELOG.md)

## 验证下载

```sh
sha256sum -c SHA256SUMS
gh attestation verify chat-digest-2.0.0.tar.gz --repo rmqg/chat-digest
```

## 许可

原创代码使用 [MIT](LICENSE)。随附字体使用 SIL OFL 1.1，运行时依赖保留各自许可证，见 [第三方声明](THIRD_PARTY_NOTICES.md)。本项目与腾讯无关；微信、企业微信、QQ 是腾讯的商标。漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。
