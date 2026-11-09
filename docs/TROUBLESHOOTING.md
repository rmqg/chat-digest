# 排障

先跑一次自检，它会逐项给出修法：

```sh
./bin/chat-digest doctor            # 加 --probe-send 让发送命令做一次 --dry-run；加 --json 给脚本用
```

下面按看到的症状列。所有命令都在项目目录里执行；查看状态文件不会改变任何东西。

首次安装的完整顺序见 [QUICKSTART.md](QUICKSTART.md)。`[WARN]` 不总是故障（例如只出报告时未配置发送器）；`[FAIL]` 要按后面的修复命令处理。诊断输出、来源 JSON、模型响应和答案文件都可能含私有内容，求助前先脱敏，**不要贴 API key、密码、完整聊天或整个 runtime/**。

## 报告网站（本机模式）

| 症状 | 原因 | 处理 |
|---|---|---|
| 打不开 `http://127.0.0.1:8787/` | 站点没启动，或者你在另一台设备上访问（站点只监听本机） | `./bin/chat-digest site --check-config`，再 `site --serve`；别的设备要访问时看 [INSTALL.md](INSTALL.md#37-报告网站) |
| 忘了密码 | 明文不保存 | `./bin/chat-digest site --set-password` |
| 登录后马上又回到登录页 | 走 HTTP 访问，但 `cookie_secure` 是 `true` | 通过 HTTPS 访问，或把它改回 `false` 后重启 |
| 显示“尝试太频繁” | 一分钟内失败超过 10 次 | 等一分钟再试 |
| 显示“报告暂时不可用” | 还没有成对的简报和回顾，或者最近一次整理失败 | 看站点进程的标准错误；`./bin/chat-digest doctor` |
| 首页没有微信报告 | 没配 `group.conversation_id`，或改过 `report_prefix` | 检查 `runtime/project.json`，改完重启站点 |

## 安装和 demo

| 症状 | 原因 | 处理 |
|---|---|---|
| `install.sh` 报 `Python 3.11 or newer is required` | 系统 Python 太旧 | 装 3.11+，再 `./scripts/install.sh --apply --python /usr/bin/python3.11` |
| `Could not create the virtualenv` | 缺 venv 模块 | Debian/Ubuntu：`sudo apt install python3-venv` |
| `doctor` 报 `tokenizer-cache` 失败 | 分词数据没预取，或被改过 | 联网执行一次：`TIKTOKEN_CACHE_DIR=.cache/tiktoken .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"` |
| demo 报 `demo digest failed` | 多数是依赖或分词数据问题 | 先 `./bin/chat-digest doctor --demo-only`，按提示修 |
| demo 连不上 127.0.0.1 | 环境里设了 HTTP 代理且没排除本机 | demo 会自动去掉代理变量；若仍失败，检查是否有强制代理（例如透明代理拦截回环） |
| `./install.sh` 提示没有交互终端 | 在脚本、重定向或无 TTY 环境运行交互入口 | 用 `./install.sh --non-interactive`（默认 demo），真实部署用 `./install.sh --config ./install.env` |
| 真实安装最后 doctor 失败，但提示“配置已写入 runtime/” | 尚未构建/锁定运行时镜像 | 按 [QUICKSTART B4](QUICKSTART.md#b4-构建镜像建立网络并锁定镜像) 构建和 pin 后重新 doctor；不必反复重填答案 |
| 根目录安装命令只打印计划，不提问 | 只给了旧参数，已转交底层安装器 | `./install.sh --apply` 保留底层语义；裸 `./install.sh` 才默认交互，或同时指定 `--non-interactive` / `--config` |
| `SITE_PASSWORD` 被拒绝 | 非空密码少于 8 位 | 用至少 8 位的独立强密码，或在答案文件设 `SITE_PASSWORD=''` 生成随机密码；默认用户名 `reader` |

### Docker-only 宿主与 --no-venv

OpenWrt/iStoreOS 等机器可以只让宿主跑标准库调度，把生成、分词、渲染、视觉等依赖全部留在 Docker 镜像里：

```sh
./scripts/install.sh --apply --no-venv
./bin/chat-digest doctor --host-only
```

这**不安装** `.venv`、pip 包和本机 tiktoken 数据。`doctor --host-only` 跳过这些本来属于容器的检查，并不是跳过导出、配置、镜像或权限检查；生产仍需 Python 3.11+、Docker、已构建/锁定的运行时镜像。默认 doctor 或本机 demo 提示缺 `PIL` / `tiktoken` 不说明容器有问题，也不要为了消除提示往受管系统 Python 中 `sudo pip install`。若要在宿主跑离线 demo，改在普通 Linux 上使用正常的 `.venv` 安装。

根目录向导也可读真实答案文件：`./install.sh --config ./install.env --no-venv`；它会跳过自动演示，生成配置后用 `--host-only` 自检。宿主镜像存在旧 `.venv` 时，命令入口仍会优先使用它；想明确选系统 Python 可用 `CHAT_DIGEST_PYTHON=python3 ./bin/chat-digest doctor --host-only`。

### 宿主没有 tzdata / `ZoneInfoNotFoundError`

宿主的日报窗口、定时任务和网站也要解析时区，只在容器里装 tzdata 不会补齐宿主。`./bin/chat-digest doctor --host-only` 也会检查配置的时区：

- `Asia/Shanghai`、UTC、日本、韩国、新加坡等内置支持的非夏令时时区在没有数据库时回退到固定偏移，doctor 会说明；不影响导出中的 UTC 时间戳。
- `Europe/Berlin`、`America/New_York` 等需要夏令时规则的时区**不能用固定偏移凑合**。Debian/Ubuntu 用 `sudo apt install tzdata`；OpenWrt 用 `opkg update && opkg install zoneinfo-core`，再安装该发行版提供的对应地区 zoneinfo 包。
- 如果选择在 `.venv` 里用 Python 包提供数据库：`./.venv/bin/python -m pip install tzdata`；务必安装到实际运行调度脚本的 Python 环境。`--no-venv` 宿主优先使用系统的 zoneinfo 软件包。

装好后用同一个运行用户重跑 doctor；不要把配置时区改成 UTC 只为隐藏错误，那会改变日报窗口。

## 配置

| 症状 | 处理 |
|---|---|
| `group.id is a v1 WeChat field` | 按 [MIGRATION-v2.md](MIGRATION-v2.md) 改成 `group.conversation_id` |
| `explicit group.conversation_id is required` | `./bin/chat-digest list-conversations --export-root <导出目录>` 查 ID 填进 `runtime/project.json` |
| `export.root must name the chat-export/v1 directory` | 填导出目录的绝对路径 |
| `send.command must be a non-empty list of strings` | 写成数组：`["wechat-send"]`，不是字符串 `"wechat-send"` |
| `doctor` 报 project.json 或 runtime 目录权限 | `chmod 700 runtime && chmod 600 runtime/*.json` |
| `Image pins are missing` / `stale` | 构建镜像后 `./bin/chat-digest pin-images`；有意升级后 `python3 deployment/bootstrap.py --refresh-pins` |

## 导出

| 症状 | 原因 | 处理 |
|---|---|---|
| `manifest.json not found in export root` | 路径不对，或导出工具还没写出第一份 | 看导出工具的日志；`ls <导出目录>` |
| `unsupported export schema` | 不是 chat-export/v1 | 升级导出工具，或确认指的是导出根目录而不是它的子目录 |
| `…:行号: invalid JSON line` | 导出文件中间有坏行 | 导出文件坏了，交给导出工具修复；chat-digest 不会跳过坏行去猜内容（只忽略最后一行还没写完的情况） |
| `export path escapes the export root or points into state/` | manifest 里的路径不安全 | 导出工具的 bug，修好前不要使用这份导出 |
| `media file does not match its sha256` | 媒体文件被改动或没写完 | 等导出工具重写，或让它重新导出该文件 |
| `doctor` 警告 `last message in the export is N h old` | 导出工具停了，或群里确实没人说话 | 检查导出工具是否在运行、是否还登录着 |

### WXGF / HEVC 图片打不开、原图有却标记缺失

微信导出的某些图片以 `wxgf` 开头，内部是 HEVC，**不是把扩展名改成 `.jpg` 就能打开的 JPEG**。当前 `prepare-images.py` 用 ffmpeg 取首帧转成 PNG 供模型使用；导出原件保持只读，不改写。ffmpeg 必须在**实际执行 prepare-images 的运行时容器内**，只在宿主 `apt install ffmpeg` 没用。

先查看正在使用的镜像 ID，再在那个镜像内检查：

```sh
python3 -m json.tool runtime/image-pins.json
# 将下面的 sha256:... 换为上面 images.runtime.id 的完整值
docker run --rm --network none --read-only --entrypoint ffmpeg sha256:... -version
```

预期打印 ffmpeg 版本；报不存在/无法执行说明镜像过旧或构建不完整。默认 Dockerfile 已装 ffmpeg，无需开启 OCR；有意升级时：

```sh
docker build -t chat-digest-runtime:2.0.0 .
./.venv/bin/python deployment/bootstrap.py --refresh-pins
./bin/chat-digest doctor
```

`--no-venv` 宿主把第二行解释器换成 `python3`、第三行加 `--host-only`。已有 pins 才用 `--refresh-pins`；首次构建用 `./bin/chat-digest pin-images`。**重新打 tag 不会改变已锁定的运行时 ID**。如果 ffmpeg 正常仍无法解码，可能是导出媒体截断或不受支持，交由导出工具检查原件；不要编辑 manifest 的 SHA-256 或拿缩略图冒充原图。

### 迁移后网站历史消失 / 已发送的报告又被当作新内容

`group.conversation_id` 选择当前导出的会话。`group.aliases` 保存**同一个会话过去的 ID**，例如 v1 的旧微信群 ID；它不是群名别称，不会替你合并不同群，也不会把旧 ID 的聊天作为当前导出读取。

在私有 `runtime/project.json` 的 `group` 内保留当前 ID，并添加旧 ID（下面全部是合成占位值，按自己的迁移记录替换）：

```json
{
  "group": {
    "conversation_id": "g_current_example",
    "name": "虚构演示群",
    "aliases": ["legacy-example@chatroom"]
  }
}
```

这是局部字段示例，不要拿它覆盖整份项目配置。迁移时保留旧的 `runtime/reports/` 和 `runtime/digest/` 中回执：网站可识别 aliases 对应的旧报告，调度器会认旧确认回执，图片流水线也能用旧报告的指纹/平台消息 ID 排除自发内容。改完运行 doctor 并重启站点。还要核对 `report_prefix`，改前缀会使旧文件不被当前站点识别。**不要通过删旧回执、改 hash 或伪造发送状态来“恢复历史”。**

### 机器人把自己的长报告图又送进视觉模型

长图本身不是排除条件，群友发的长截图仍是有效内容。程序只排除能确认为**本项目已发送、由自己发出**的报告/访问说明，避免循环总结和额外视觉费用：

1. 导出消息应有正确的 `is_self`、稳定的 `message_id`；发送成功回执应包含可关联的平台消息 ID（如 `confirmation.extra.receipt.server_id`）。客户端可能重新编码 PNG，使导出图的 SHA-256 不同，所以消息 ID 很重要。
2. 保留 `runtime/digest/sent-receipts/`、`on-demand-receipts/`、`site-access-receipts/`；迁移过 ID 时补齐 `group.aliases`。这些目录记录“哪些内容确实由本项目发过”，不是可随意清掉的临时缓存。
3. 图片准备阶段在调用视觉模型**之前**按已确认回执的消息 ID 和 `is_self` 排除自发报告；最终总结还会排除已记录的报告图/访问说明指纹和消息 ID。可在对应作业目录的 `exclude-message-ids.json`、`exclude-own-image-sha256.json` 中核对是否记录，但不要粘贴私有 ID 到公开 issue。
4. `bot.sender_ids` 用于过滤机器人自己触发的 @总结请求，**不能代替发送回执来过滤所有图片**。手工发送、回执缺失或无法匹配消息 ID 的旧图，不保证能在视觉阶段识别成自发报告；先修导出与发送器的回执关联，不要按图片高度一刀切过滤群友内容。

最终报告 JSON 的 `stats.excluded_own_reports` 可反映总结阶段排除数，但不是视觉调用次数证明；排查视觉费用要同时看作业的图片 manifest 和回执关联。不要为排障删投递状态导致重发。

## 生成卡住或失败

先看状态：

```sh
python3 -m json.tool runtime/digest/delivery-state.json
tail -n 5 runtime/digest/stage-failures.jsonl      # 每行一个阶段失败：脚本、退出码、输出尾部
python3 -m json.tool runtime/digest/daily-images-result.json
```

| 症状 | 原因 | 处理 |
|---|---|---|
| `status: retry_wait` | 上次生成失败或在等图片，正在按退避等待（最多 900 秒） | 正常现象，会一直重试；想立刻再试：`./bin/chat-digest daily-tick --force` |
| `images_pending`，`counts.export_pending` 大于 0 | 导出里有图片原图还是 `pending` | 等导出工具下载；超过 `image_pending_grace_seconds` 会按“原图缺失”继续 |
| `stage-failures.jsonl` 里 `caption-images.py` 失败 | 图片模型不可用 | 看 `runtime/digest/vision-diagnostics/`；主模型 5xx 时会自动冷却并用备用，冷却 60 秒起最长 900 秒 |
| 报 `timed out after Ns` | 阶段超时，容器已被 `docker rm -f` 清掉 | 网页很多时正常，下一轮会继续处理剩下的页；持续超时检查网络和模型速度 |
| `AI request failed with HTTP 400` | 请求本身被拒（不切备用） | 看 `runtime/digest/summary-diagnostics/` 最新文件里的 `body`；常见是模型不支持 `json_schema` 输出，换支持结构化输出的模型 |
| `AI stream incomplete: …` | 流式响应中途断开 | 自动切到 fallbacks 里的下一个模型；全部失败就按退避重试 |
| `Model declined the request` | 模型拒答 | 不切换、不编造；换模型或检查内容 |
| `summary validation: topic references an unknown source message` | 模型引用了不存在的消息 | 这一版不出也不缓存，下次重试会重新请求 |
| 报告里有“（图片描述模型拒绝处理这张图片……）” | 模型对这张图拒答 | 报告照发，看门狗会提醒一次 |
| 报告里有“（导出里没有这张图片的原图……）” | 导出只有缩略图或原图缺失 | 检查导出工具的原图下载 |
| `docker: permission denied` | 运行用户不在 docker 组 | `sudo usermod -aG docker chat-digest` 后重新登录或重启服务 |
| `network chat-digest_default not found` | 没建 Docker 网络 | `docker network create chat-digest_default`，或把 `docker_network` 改成已有网络 |

## 发送

```sh
python3 -m json.tool runtime/digest/delivery-state.json   # 看 editions.brief / editions.review 的 status 和 confirmation
ls runtime/digest/sent-receipts/
```

| `status` | 含义 | 处理 |
|---|---|---|
| `sent` | 已确认送达 | 无 |
| `failed`，`error_kind: failed` | 发送前就失败了（找不到群、窗口不对等），确定没发出去 | 下一分钟自动重试；手动排查：`<发送命令> --to <群> --text test --request-id cd-manualtest --dry-run --json` |
| `failed`，`error_kind: invalid` | 发送命令说参数错误 | 修 `send` 配置；配置没变时不会重试 |
| `unknown` | 已经或可能已经提交，但没确认 | **不会自动重发。** 去群里看有没有收到。没收到又确认要补发时，手动用发送命令发 `runtime/reports/` 里对应的 PNG；不要删回执来“重来” |
| `sending`（进程停在这里） | 发送中途被杀 | 下次运行会改记 `unknown`，同上处理 |

`doctor --probe-send` 报 `command not found`：发送命令不在 PATH，写绝对路径，或像 `["docker", "exec", "<容器>", "wechat-send"]` 这样通过容器调用。

## 看门狗没通知 / 通知太多

- 没通知：`python3 deployment/daily-watchdog.py --check-config`；故障要持续满 `fault_grace_minutes`（默认 10 分钟）才推送。
- 同一故障反复推：每个故障只推一次，阶段变化后才再推；检查 `runtime/daily-watchdog/state.json` 是否可写。

## @ 请求没反应

```sh
./bin/chat-digest on-demand --status
```

检查：`runtime/summary-trigger.json` 的 `chat_id` 是否等于 `group.conversation_id`；消息时间是否晚于 `enabled_from`；消息里是否同时有 `@`、机器人名字和“总结/汇总”；发消息的人是不是机器人自己（`is_self` 或 `bot.sender_ids`）。
