# 从 v1.2.x 迁移到 v2.0.0

v2.0.0 是破坏性更新。v1 一个仓库里同时做了“挂微信、提取密钥、解密数据库、解析消息、发送到群”和“总结、出报告”；v2 把前一半拆了出去，本仓库只做后一半：

| 能力 | v1.2.x（rmqg/wechat-digest） | v2.0.0（chat-digest） |
|---|---|---|
| 运行微信桌面、提取密钥、解密数据库、读消息和图片缓存 | 本仓库 `memory/`、`tools/wcdb-key-tool`、`deployment/vendor/wxextract`、`capture_41.py`、`scan-image-key.py`、`docker-compose.yml` | 移到 [rmqg/wechat-export-send](https://github.com/rmqg/wechat-export-send)，它把聊天记录导出成 [chat-export/v1](CONTRACT.md) 目录 |
| 往微信群发图片和文字 | 本仓库 `send-report.py`（X11 操作微信窗口） | 移到 wechat-export-send 的 `wechat-send` 命令，本仓库按 [chat-send/v1](CONTRACT.md) 调用 |
| 微信登录看门狗 | 本仓库 `wechat-watchdog.py` | 移到 wechat-export-send |
| 补下载原图 | 本仓库 `fetch-originals.py` | 由导出工具负责；本仓库只读导出里 `status: ready` 的原图 |
| 总结、两版报告、HTML/PNG、网站、@ 请求、Bark 投递监控 | 本仓库 | 仍在本仓库 |
| QQ（OneBot v11）输入和发送 | 本仓库 | 仍在本仓库，不变 |

仓库也从 `rmqg/wechat-digest` 改名为 `rmqg/chat-digest`，保留全部历史和 v1.x 的 release；GitHub 会把旧地址自动转到新地址。

## 迁移前

1. 先把 v1 的日报停掉，避免切换中途重复生成或发送：停掉每分钟的 `daily-job.py` 计划任务，以及 `wechat-images`、`wechat-summary-commands` 服务。
2. 备份整个 `runtime/`。v2 不会删除 v1 的文件，但回执和状态的格式变了。
3. 装好 wechat-export-send，确认它已经在持续产出 chat-export/v1 目录，`wechat-send --dry-run` 能找到目标群。具体步骤看那个仓库的文档。
4. 选一个**没有在途发送**的时刻切换：`runtime/digest/delivery-state.json` 里当天的 `status` 是 `sent`，或者还没开始生成。

## 迁移步骤

### 1. 代码和依赖

```sh
cd /opt/wechat-digest          # 你的 v1 目录
git fetch origin && git checkout v2.0.0   # 或者解压 v2.0.0 release 包到新目录
./scripts/install.sh --apply   # 建 .venv、装依赖、预取分词数据
docker build -t chat-digest-runtime:2.0.0 .
```

v2 的运行时镜像不再包含 gdb、X11 库和 wcdb-key-tool，`requirements.txt` 去掉了 pycryptodome 和 zstandard。

### 2. 改 `runtime/project.json`

| v1 字段 | v2 怎么办 |
|---|---|
| `group.id`（例如 `…@chatroom`） | 改成 `group.conversation_id`（值来自导出目录，用 `./bin/chat-digest list-conversations --export-root <导出目录>` 查），并把旧值放进 `group.aliases`，网站历史和旧回执才能继续对上 |
| `group.name` | 保留 |
| （无） | 新增 `export.root`：导出目录的绝对路径 |
| `bot.username` | 删掉。机器人账号在导出里的 ID 写进 `bot.sender_ids`（可选） |
| `bot.names` | 保留 |
| `desktop_container`、`images.desktop`、`images.keyscan` | 删掉 |
| `images.runtime` | 改成 `"chat-digest-runtime:2.0.0"` |
| （无） | 新增 `send`：`{"command": ["wechat-send"], "to": "群名", "timeout": 180}`，命令按你的部署写，例如 `["docker", "exec", "wechat", "wechat-send"]` |
| `report_prefix` | 想让网站继续认旧报告，就保留 v1 的值（默认值从 `wechat-digest` 变成了 `chat-digest`） |

配置里留着 `group.id` 却没有 `group.conversation_id` 时，v2 会直接报错并指向本文，不会猜。完整说明见 [CONFIGURATION.md](CONFIGURATION.md)。

`runtime/summary-trigger.json`：`chat_id` 改成新的 conversation_id，删掉 `bot_username`。`runtime/report-site-access.json`：`chat_id` 同样改成 conversation_id。

### 3. 不再使用的运行时文件

`runtime/gui/`、`runtime/keyscan/`、`runtime/selection.json`、`runtime/ui-calibration*`、`runtime/wechat-watchdog*` 是 v1 的微信部分用的，迁到 wechat-export-send 以后由那边管理；v2 不读它们，也不会删除它们。**`runtime/gui/` 和 `runtime/keyscan/` 里有登录会话和数据库密钥**，确认 wechat-export-send 不再需要后，请自己安全删除。v1 运行中留下的 `runtime/digest/.digest-*` 临时目录里可能有解密后的数据库，也应删除。

### 4. 环境变量改名

`WECHAT_DIGEST_*` 全部改成 `CHAT_DIGEST_*`（例如 `CHAT_DIGEST_STORAGE_MOUNT`、`CHAT_DIGEST_MACHINE`），旧名字不再生效。检查你的 systemd/procd/cron 里有没有设置过。

### 5. 锁定镜像、自检、试跑

```sh
./bin/chat-digest pin-images      # 若已有 v1 的 image-pins.json，改用 deployment/bootstrap.py --refresh-pins
./bin/chat-digest doctor --probe-send
./bin/chat-digest prepare-only    # 生成当天两版报告，不发送
```

`doctor --probe-send` 只让发送命令做一次 `--dry-run`，按约定不会提交任何消息。

### 6. 换服务

v1 的 OpenWrt 服务名是 `wechat-images`、`wechat-summary-commands`、`wechat-image-origin`；v2 改成 `chat-digest-images`、`chat-digest-commands`、`chat-digest-image-origin`，另外提供 systemd unit。先停用并删除旧服务，再按 [INSTALL.md](INSTALL.md) 安装新服务和计划任务。

## 状态和回执的兼容性

- 已发回执里的 `server_id`（微信消息 ID）在 v2 换成了 `send_request_id`（chat-send/v1 的幂等键）。v1 留下的带 `server_id` 的回执，v2 认作“v1 已确认送达”，记成 `legacy-v1:<server_id>`，不会重发。仍建议在当天日报完成以后、第二天出报以前切换。
- 报告是否“自己发出去的”在 v2 用导出里的 `is_self` 加图片哈希判断。v1 时期发出的报告图片若被 wechat-export-send 导出为 `is_self: true` 且哈希一致，也会被排除；哈希不一致（例如客户端重新压缩过）时，那一条会作为一张普通图片出现在报告里。
- 摘要缓存的键包含消息内容，v2 消息 ID 的生成方式变了，所以切换后第一次生成会重新请求模型，属于正常现象。
- 历史报告来源卡片里的原图，靠 `runtime/digest/prepared-image-cache.json` 的条目和 `runtime/digest/images/decoded/` 里的文件。要保留它们，就把 v1 的这两样合并到 v2：缓存条目只添加 v2 没有的键，图片只添加 v2 没有的文件（同一个分区上可以用硬链接，不占额外空间）。不合并也能运行，新报告不受影响，但历史报告的来源卡片会显示“暂不可用”。

## 回滚

v2 不改写 v1 的文件。回滚就是停掉 v2 服务、恢复迁移前备份的 `runtime/`、切回 v1.2.4 的代码并重启 v1 服务。
