# 配置参考

chat-digest 的配置全部是 `runtime/` 下的 JSON 文件和少量环境变量。`runtime/` 是私有目录（权限 700），里面的文件权限应为 600，不进 Git、不进 Docker 构建上下文。所有示例模板在 `examples/`，复制后再改。

| 文件 | 必需吗 | 谁读 |
|---|---|---|
| [`runtime/project.json`](#runtimeprojectjson) | 必需 | 所有组件 |
| [`runtime/gemini-model.json`](#runtimegemini-modeljson) | 生成真实报告时必需 | 摘要、网页摘要、OCR 摘要、@请求解析、图片描述主模型 |
| [`runtime/vision-qwen.json`](#runtimevision-qwenjson) | 可选 | 图片描述的备用模型 |
| [`runtime/summary-trigger.json`](#runtimesummary-triggerjson) | 开启群内 @ 请求时必需 | `on-demand.py` |
| [`runtime/daily-watchdog.json`](#runtimedaily-watchdogjson) | 开启 Bark 投递监控时必需 | `daily-watchdog.py` |
| [`runtime/report-site.json`](#runtimereport-sitejson) | 可选 | `publish_reports.py`（把网页报告同步到远端站点） |
| [`runtime/report-site-access.json`](#runtimereport-site-accessjson) | 可选 | `site_access_notice.py`（两版报告送达后发一次网站入口说明） |
| [`runtime/image-origin.json`](#runtimeimage-originjson) | 可选 | `image-origin.py`（站点原图回源） |
| [`runtime/image-pins.json`](#runtimeimage-pinsjson) | 自动生成 | `bootstrap.py` 写，所有容器调用读 |
| QQ 相关 | 只在用 QQ 时 | 见 [QQ.md](QQ.md) |

## `runtime/project.json`

模板：[`examples/project.example.json`](../examples/project.example.json)。`./bin/chat-digest init` 会在没有这个文件时复制模板并设好权限。

| 参数 | 类型 | 默认值 | 含义 | 示例 | 什么时候改 |
|---|---|---|---|---|---|
| `export.root` | 字符串（路径） | 无，必填 | chat-export/v1 导出目录在宿主上的路径，chat-digest 只读它。相对路径按项目根目录解析 | `"/srv/wechat-export/acct1/export"` | 导出工具换了输出目录时 |
| `group.conversation_id` | 字符串 | 无，必填 | 要总结的会话在导出里的 ID（manifest 的 `conversations[].id`），只能含 `A-Z a-z 0-9 _ . -` | `"g_1a2b3c"` | 换总结目标时；用 `chat-digest list-conversations` 查 |
| `group.name` | 字符串 | 无，必填 | 会话名，只用来提示和核对。程序按 `conversation_id` 选会话；只有命令行没给 ID、配置里也没有 ID 时才按名字精确匹配，重名时要求给 ID。报告里显示的名字取自导出 | `"产品讨论群"` | 群改名后顺手改一下 |
| `group.aliases` | 字符串数组 | `[]` | 同一个会话以前用过的 ID（例如 v1 的微信群 ID）。带这些 ID 的旧报告继续出现在网站首页，旧回执里的报告图片指纹继续用来排除自发报告 | `["…@chatroom"]` | 从 v1 迁移时填旧的 `group.id` |
| `send.command` | 字符串数组 | 无；不配置就不能发送 | 实现 chat-send/v1 的发送命令前缀，后面由程序追加 `--to … --image … --request-id … --json` | `["wechat-send"]`，`["docker","exec","wechat","wechat-send"]` | 换发送工具或容器名时 |
| `send.to` | 字符串 | 无，发送时必填 | 交给发送命令的目标会话（会话名或 conversation_id，由发送工具解释） | `"产品讨论群"` | 换投递目标时 |
| `send.timeout` | 整数（秒） | `180` | 单次发送的超时，取值 10–3600。超时一律记为 `unknown`，不会自动重发 | `300` | 大图在慢机器上经常超时时调大 |
| `bot.names` | 字符串数组 | `[]` | 群里 @ 机器人时用的显示名。文本里同时有 `@` 和其中任一名字、并且要求“总结/汇总”，才算按需请求 | `["小助手"]` | 开启按需请求时必填 |
| `bot.sender_ids` | 字符串数组 | `[]` | 机器人账号在导出里的 `sender_id`；这些人发的消息不当作请求，`mentions` 里出现它们也算 @ 到了机器人 | `["u_9f8e"]` | 导出里机器人账号的 ID 确定后填 |
| `report_prefix` | 字符串 | `"chat-digest"` | 报告文件名前缀，例如 `chat-digest-20261108-0500-brief.png` | `"team-digest"` | 同一台机器跑多份配置时区分文件名；改了之后旧报告不会被网站识别 |
| `docker_network` | 字符串 | `"chat-digest_default"` | 需要联网的阶段容器（调模型、抓网页）使用的 Docker 网络；不联网的阶段固定用 `none` | `"bridge"` | 你给容器单独建了出口受控的网络时 |
| `public_url` | 字符串或 `null` | `null` | 报告网站的 HTTPS 地址，只有网站入口说明会用到 | `"https://reports.example.org/"` | 启用 `report-site-access.json` 时必填 |
| `image_origin_host` | 字符串或 `null` | `null` | 保留字段，程序不读；部署网站反代时可以记在这里备查 | `null` | 一般不用改 |
| `daily_hour` | 整数 0–23 | `5` | 每天几点生成日报；日报窗口是“前一天这个时刻到今天这个时刻”的 24 小时 | `8` | 想换出报时间时 |
| `timezone` | IANA 时区名 | `"Asia/Shanghai"` | 报告显示时间、日报窗口、时长解析使用的时区。导出里的时间一律是 UTC 秒，不受影响 | `"Europe/Berlin"` | 不在东八区时 |
| `worker_uid` / `worker_gid` | 整数 | `1000` / `1000` | 阶段容器以这个 UID/GID 运行，必须能读导出目录、读写 `runtime/digest` 和 `runtime/reports` | `1001` | 服务用户不是 1000 时 |
| `images.runtime` | 字符串 | `"chat-digest-runtime:2.0.0"` | 本地构建的运行时镜像引用。实际执行时用 `image-pins.json` 里锁定的不可变 ID | `"chat-digest-runtime:2.0.0"` | 升级镜像后，配合 `bootstrap.py --refresh-pins` |
| `image_pending_grace_seconds` | 非负整数（秒） | `21600`（6 小时） | 导出里图片原图还是 `pending` 时最多等多久；超过就按“原图缺失”标注，报告照出，不会永远卡住 | `3600` | 导出工具下载原图很慢或很快时 |

v1.x 的 `group.id`、`bot.username`、`desktop_container`、`images.desktop`、`images.keyscan` 在 v2 里已经没有了。配置里还留着 `group.id` 而没有 `group.conversation_id` 时，程序会直接报错并指向 [迁移指南](MIGRATION-v2.md)，不会猜。

## `runtime/gemini-model.json`

模板：[`examples/gemini-model.example.json`](../examples/gemini-model.example.json)。文件名沿用 v1，接口不限厂商：任何 OpenAI 兼容的 `/chat/completions` 都可以。

| 参数 | 类型 | 默认值 | 含义 | 示例 |
|---|---|---|---|---|
| `base_url` | 字符串 | 无，必填 | 接口根地址，程序在后面加 `/chat/completions` | `"https://api.example.org/v1"` |
| `model` | 字符串 | 无，必填 | 模型名 | `"gemini-3-flash"` |
| `api_key` | 字符串 | 无，必填 | 以 `Authorization: Bearer` 发送 | — |
| `fallbacks` | 对象数组 | `[]` | 全局备用链。每项同样有 `base_url`、`model`、`api_key`，按顺序尝试；缺的字段继承主配置 | `[{"base_url": "...", "model": "backup-model", "api_key": "..."}]` |

**所有模型调用都照这条链走**：日报摘要、网页摘要、OCR 摘要、群内 @ 请求的时长解析、QQ 的自然语言请求。程序不去判断某个模型“现在好不好”，只按下面的规则切换：

- 会切到下一个：HTTP 401、403、404、408、425、429、500、502、503、504、529，连接错误、超时、半路断开（`IncompleteRead` 等），流式响应没有结束标记或带上游错误事件。
- 不会切换：HTTP 400、422（请求本身有问题，换模型也一样），模型明确拒答（`refusal`、`content_filter`），输出被截断。
- 失败后不放弃：网页摘要和 OCR 摘要按 120 秒起指数退避、上限 900 秒，一直重试直到成功；日报生成失败按 60 秒起、上限 900 秒重试。

什么时候改：换模型、加备用模型、换 key 时。改完不用重启服务，下一次调用就会读新文件。

## `runtime/vision-qwen.json`

可选的图片描述备用端点，字段同上（`base_url`、`model`、`api_key`）。图片描述先用 `gemini-model.json` 的主模型；它返回 5xx 或连接失败时进入冷却（60 秒起指数退避、上限 900 秒，到期自动再试），期间用这里的模型。文件名沿用 v1，不限于某个厂商。

## `runtime/summary-trigger.json`

模板：[`examples/summary-trigger.example.json`](../examples/summary-trigger.example.json)。只有运行 `on-demand.py --serve`（群内 @ 请求）时需要。

| 参数 | 类型 | 含义 | 示例 |
|---|---|---|---|
| `chat_id` | 字符串 | 必须和 `project.json` 的 `group.conversation_id` 相同 | `"g_1a2b3c"` |
| `bot_names` | 非空字符串数组 | 认哪些名字的 @，必须都在 `project.json` 的 `bot.names` 里 | `["小助手"]` |
| `enabled_from` | 非负整数（Unix 秒） | 只处理这个时刻以后的请求，避免开启时把历史请求全部重放 | `1794043483` |

请求只认普通文本消息（导出里 `type` 是 `text`），机器人自己（`is_self` 或 `bot.sender_ids`）发的不算。只说“@小助手 总结”时默认总结最近 24 小时；带了时长的由模型解析，解析失败不会偷偷退回 24 小时。

## `runtime/daily-watchdog.json`

模板：[`examples/daily-watchdog.example.json`](../examples/daily-watchdog.example.json)。可选的 Bark 投递监控，只读状态、只发通知，不改回执、不补发。

| 参数 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `bark_url` | 字符串 | 无，必填 | 你的 Bark 推送地址（含设备 key，属于秘密） |
| `group` | 字符串 | `"chat-digest"` | Bark 通知分组名 |
| `fail_level` | 字符串 | `"active"` | 故障通知的 Bark level（`active`、`passive`、`timeSensitive` 等，按 Bark 支持的值） |
| `recovery_level` | 字符串 | `"passive"` | 恢复和提醒类通知的 level |
| `daily_hour` / `daily_minute` | 整数 | 取自 project.json / `0` | 日报应该开始生成的时刻 |
| `timezone` | 字符串 | 取自 project.json | 判断“今天”的时区 |
| `overdue_minutes` | 整数 1–1440 | `60` | 过了出报时刻这么久还没两版都送达，就报“超期” |
| `fault_grace_minutes` | 整数 0–1440 | `10` | 故障持续满这么久才推送（失败后常在几分钟内自己重试成功）；故障换了阶段，同样等满这么久再推一次 |

另外两类提醒（每个窗口只推一次）：有图片被模型拒绝描述、有图片原图在导出里缺失。

## `runtime/report-site.json`

模板：[`examples/report-site.example.json`](../examples/report-site.example.json)。缺这个文件时不做远端发布。所有字段必填：`host`（主机名或 IP）、`user`（远端受限 SSH 用户）、`port`（1–65535）、`identity_file`（私钥文件，相对路径按配置文件所在目录）、`known_hosts`（主机公钥文件）、`remote_path`（远端相对路径，不能是绝对路径或含 `..`）。连接强制 BatchMode 和严格主机公钥校验，只上传 HTML 和报告引用到的图片，不上传 `sources.json`、配置或模型文件。

## `runtime/report-site-access.json`

模板：[`examples/report-site-access.example.json`](../examples/report-site-access.example.json)。缺文件时不发网站说明。`chat_id` 必须等于 `group.conversation_id`；`url` 必须等于 `public_url`（结尾带 `/`）；`username`、`password` 是网站的访问凭证；`enabled_from` 是非负 Unix 秒，早于它送达的报告不补发说明。说明文字里含带凭证的链接，所以只在两版报告都确认 `sent` 后发一次，`unknown` 不重发。

## `runtime/image-origin.json`

模板：[`examples/image-origin.example.json`](../examples/image-origin.example.json)。`assets_dir`（必填，网站资源目录）、`token`（必填，随机长字符串，反代用 Bearer 携带）、`bind`（默认且只能是 `127.0.0.1`）、`port`（默认 8099）。只响应回环地址上带正确 token 的 `GET`/`HEAD` 按哈希命名的图片请求。反代示例见 `deployment/services/chat-digest-image-origin.caddy`。

## `runtime/image-pins.json`

由 `./bin/chat-digest pin-images`（即 `bootstrap.py --pin-images`）生成，记录 `images.runtime` 解析出来的不可变镜像 ID：`{"schema_version": 1, "pinned_at": …, "images": {"runtime": {"reference": "chat-digest-runtime:2.0.0", "id": "sha256:…"}}}`。不要手改。有意升级镜像后运行 `bootstrap.py --refresh-pins`。

## 环境变量

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CHAT_DIGEST_ROOT` | 代码所在目录 | 项目根目录 |
| `CHAT_DIGEST_RUNTIME_ROOT` | `$CHAT_DIGEST_ROOT/runtime` | 私有运行目录 |
| `CHAT_DIGEST_CONFIG` | `$CHAT_DIGEST_RUNTIME_ROOT/project.json` | project.json 的路径 |
| `CHAT_DIGEST_EXPORT_ROOT` | 无 | 覆盖 `export.root` |
| `CHAT_DIGEST_STORAGE_MOUNT` | 无 | 设了以后，这个路径不是挂载点时日报和按需任务直接停（防止数据盘没挂上时写到系统盘） |
| `CHAT_DIGEST_PYTHON` | `.venv/bin/python`，再退到 `python3` | `bin/chat-digest` 和 procd 服务使用的解释器 |
| `CHAT_DIGEST_FONT` | 自带 Resource Han Rounded | PNG 报告字体 |
| `CHAT_DIGEST_TIMEZONE` | `Asia/Shanghai` | 报告元数据里没有时区时的渲染时区 |
| `CHAT_DIGEST_MACHINE` | 主机名 | 看门狗通知里显示的机器名 |
| `CHAT_DIGEST_IMAGE_ORIGIN_CONFIG` | `runtime/image-origin.json` | procd 服务里 image-origin 的配置路径 |
| `TIKTOKEN_CACHE_DIR` | `bin/chat-digest` 设为 `.cache/tiktoken` | 预取好的 o200k_base 分词数据目录；只读，不在线下载 |

v1 的 `WECHAT_DIGEST_*` 环境变量都改名为 `CHAT_DIGEST_*`，旧名字不再生效。

## OCR 依赖

`deployment/ocr-requirements.txt` 和 `ocr-wheel-lock.json` 固定了可选 OCR 的依赖和 wheel 哈希。构建镜像时加 `--build-arg WITH_OCR=1` 才会装（只在 Linux x86-64、Python 3.12 的镜像里验证过），不装时 OCR 阶段把图片记为 `unavailable`（OCR 不可用），不影响图片描述和报告生成。
