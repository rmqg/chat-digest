# Changelog

## 未发布

- 新增根目录中文安装向导：检查发行版、内核、Python、Docker/Compose、KVM、内存和磁盘；支持交互、`--config` 答案文件、`--non-interactive` 默认模板，私有配置权限为 600，末尾自动 doctor 并说明下一步。`scripts/install.sh` 的生产非交互参数保持兼容，根入口也能原样转发。
- 新手教程 `docs/QUICKSTART.md` 区分无需账号和模型密钥的离线演示与真实部署，配合成数据示意图；补充宿主无 venv、历史群别名、WXGF/ffmpeg、时区和自发长图排障。
- doctor 的失败与警告附可复制的修复命令；CLI 对常见配置、依赖和环境问题给出中文解释，不再只显示英文异常栈。
- 头像及时更新：日报生成前调用 `export.refresh_avatars_command` 刷新发言者头像，失败时用导出里现有的头像并记录日志。QQ 头像不再永久缓存：每次先取最新的，失败才用缓存。已生成的报告保留当时的头像。
- 报告头像：群头像也进 `avatars`（读 manifest 里会话的可选字段 `avatar`，见 `docs/CONTRACT.md`），外部渲染器画的群头像回来了；导出器没有写头像时仍按“没有头像”处理。

## 2.0.0

**新的默认渲染器和报告网站**

- 自带渲染器重写为 clean-room 实现：只参考了旧版的外观（合成数据的样图和文字描述），没有使用旧版代码。PNG 和 HTML 用同一套浅紫简约设计，长图宽 1080、比旧版短约三成，功能区块保持：群头像、统计、一句话总结、消息节奏、话题（来源可展开）、金句、活跃前 20 名。渲染接口和输入格式不变。
- 活跃榜的单位写成 `token`（原来是 `tokens`）。报告标题不再有固定前缀：取数据里的 `title_prefix`，否则取 `report_title` 或 `CHAT_DIGEST_REPORT_TITLE`，都没有时显示“群聊日报”。
- PNG 里字体缺字的字符（例如 emoji）直接略去，不再画成方框；昵称全是这类字符时显示“群友”，不留空白；HTML 不受影响。
- 新增并默认使用**本机模式**网站（`./bin/chat-digest site`）：直接读本机报告，带登录，只监听 `127.0.0.1`，微信和 QQ 报告都能看；`init` 自动创建配置，`doctor` 会检查。原来的远端模式（`publish_reports.py`、`qq_publish.py`、`image-origin.py`）保留，配置不变。
- `init` 用专门的运行用户第一次运行时不再报错：`worker_uid`、`worker_gid` 写成这个用户（照 INSTALL 3.1 操作时，系统用户的 UID 通常不是 1000）。
- 本机模式网站在后台整理报告，请求不会卡住（第一次整理时页面显示“正在整理”并自动刷新；之后整理时继续提供旧页面）。
- 报告列表、QQ 报告站、登录页、出错页统一成同一套样式。用旧渲染器生成的历史报告页面保持原有排版。
- 长图换行：连续的收尾标点（`——`、`……`、`？？？`）只悬挂一个字宽，不再被画到图外。
- 网站重建话题和来源块时，时间按报告自己的时区显示（之前固定按上海时间）。

**可选的外部渲染器（生产替换时补充）**

- 新增 `render` 配置（微信写在 `runtime/project.json`，QQ 写在服务配置）：`render.module` 指向宿主上的渲染器 `.py` 文件，`render.function` 画 PNG，可选的 `render.html_function` 画单期 HTML；不配置时照旧用自带渲染器。渲染器在调用模型之前加载，写错立刻失败；`doctor` 和 QQ `--check-config` 会检查。微信日报把渲染器目录只读挂到容器的 `/render`。新增 [docs/REPORT-JSON.md](docs/REPORT-JSON.md) 说明报告字典的格式。
- 运行时镜像加装 `libraqm0`：Pillow 用复杂文字排版画中文，标点和字距与带 raqm 的系统一致。
- 修复：微信私有的 WXGF（HEVC）图片被当成“原图缺失”，实测一天 30 张里有 9 张因此没有图片描述。`prepare-images` 恢复解码，运行时镜像加装 `ffmpeg`；解码失败或超时只影响那一张。
- 修复：宿主没有时区数据库（OpenWrt/iStoreOS）时，`daily-job`、`daily-watchdog`、报告网站等宿主侧脚本一启动就报 `ZoneInfoNotFoundError`。不走夏令时的常用时区改用固定偏移，其他时区给出明确的安装提示，`doctor` 增加时区检查。
- 迁移文档更正：要保留历史报告来源卡片里的原图，需要把 v1 的 `prepared-image-cache.json` 条目和 `digest/images/decoded/` 文件合并到 v2。
- OCR 依赖下载失败时自动重试（最多 6 次，HTTP 4xx 不重试）。走代理或网络不稳时，大文件常在传输中途断开，不再让整次镜像构建失败。
- 外部渲染器加载时先登记模块，渲染器里用 `dataclass` 也能正常导入。
- 调度脚本以 root 运行时（OpenWrt cron），新建的图片任务目录交给 `worker_uid`，阶段容器才能写入。`init` 改用 `project.json` 里的 `worker_uid`。
- 自发报告排除：除图片指纹外，按消息 ID 排除（v1 回执的 `server_id`，以及 chat-send 结果里 `extra.receipt.server_id`）。聊天客户端会重新编码发出的 PNG，导出里的图片指纹和发出时不同，只靠指纹认不出自己的报告。图片阶段也用这份名单跳过自己发过的报告图，不再把它们送去做图片描述（很长的报告图会让模型持续失败、把日报卡住）。
- 迁移：`daily-job` 和网站入口说明把 `group.aliases` 下的回执（例如 v1 留下的当天回执）当作同一个会话的，切换当天不会报回执无效，也不会重发报告或说明。

破坏性更新：仓库由 `rmqg/wechat-digest` 改名为 `rmqg/chat-digest`，只负责总结和出报告。升级步骤见 [docs/MIGRATION-v2.md](docs/MIGRATION-v2.md)。

**拆分**

- 移除微信的运行、密钥提取、数据库解密、消息/媒体解析、X11 发送和登录看门狗（`memory/`、`tools/wcdb-key-tool`、`deployment/vendor/wxextract`、`capture_41.py`、`scan-image-key.py`、`send-report.py`、`wechat-watchdog.py`、`fetch-originals.py`、`prepare-collection.py`、wechat-selkies compose）。这些功能移到 wechat-export-send。
- 输入改为只读 chat-export/v1 目录（`deployment/chat_export.py`）：按消息 ID 去重、后出现的行覆盖前面的、忽略未写完的最后一行、坏行和越界路径直接报错、媒体按 SHA-256 核对、缩略图不冒充原图。
- 发送改为调用可配置的 chat-send/v1 命令（`deployment/chat_send.py`）：`sent` 写回执；`failed` 重试；`invalid` 等配置修好；`unknown`、超时、输出不可解析、退出码与状态不一致一律不重发。每次发送带固定的 `send_request_id`。回执里的 `server_id` 换成 `send_request_id`；v1 留下的回执被认作已送达。
- 头像只取导出里的成员头像文件，不再联网下载；@ 请求扫描、网页链接、来源图片都改为读导出字段。QQ（OneBot v11）输入和发送不变。
- 配置：`group.id` → `group.conversation_id`，新增 `export.root`、`send`、`bot.sender_ids`、`docker_network`、`image_pending_grace_seconds`；删除 `bot.username`、`desktop_container`、`images.desktop`、`images.keyscan`。环境变量 `WECHAT_DIGEST_*` 改名为 `CHAT_DIGEST_*`。

**合入 v1.2.4 之后生产环境的修复**

- 所有模型调用照全局 fallback 链走：网页摘要、日报摘要、@ 请求解析、QQ 自然语言请求都接上备用模型；HTTP 403/404 和 `IncompleteRead` 等半路断开也切备用，400/422、拒答、截断不切。流式响应缺结束标记或带上游错误事件时切备用。
- 网页摘要和 OCR 摘要失败按 120 秒起指数退避、上限 900 秒，永不放弃；图片描述主模型 5xx 或断连时冷却 60 秒起、上限 900 秒，到期自动再试。
- 图片阶段的三路处理改为串行，避免小内存机器同时运行三个 1 GB 容器；先收集链接，实时轮次输入不变时复用上次结果。
- 调度读取网页每一页的 `retry_at`；日报和 @ 请求生成失败按 60–900 秒退避，等图时按阶段给出的时间等；实时预处理两次之间至少隔 `MIN_GAP_SECONDS`（120 秒）。
- 阶段失败的脚本、退出码和输出尾部写进 `runtime/digest/stage-failures.jsonl`（600，1 MB 轮转）；阶段超时会删除残留容器；生成前清理 6 小时以上的残留临时目录；按需任务目录保留期从 30 天缩短到 7 天。
- 图片被模型拒绝描述、或导出里原图缺失时不再卡住日报，报告照发并标注，看门狗各提醒一次。镜像没装 OCR 时 OCR 记为不可用而不是一直等待。
- 日报当天完成后不再每分钟重写状态，日志只在结果变化时写，5 MB 轮转；看门狗故障防抖 10 分钟，故障换阶段再推一次，从报错里识别错误类型。

**修复 v1.2.4 的缺陷**

- `digest.py` 写报告前缺少报告路径赋值，真实生成会出错。
- `image_pipeline.py` 使用了未赋值的图片描述结果。

**新增**

- 对齐 chat-send/v1 最终版：`--dry-run` 的结果是独立的 `dry_run` 状态（退出码 0，不算发送）；忽略提供方私有的 `extra` 字段；导出媒体可带 `source_quality`（`original` / `client_full`）。
- `group.aliases`：同一会话以前的 ID（例如 v1 的微信群 ID），网站历史和旧回执继续有效。
- `install.sh --no-venv`（只用 Docker 的主机，例如 OpenWrt，宿主不装 Python 包）和 `--prefix`（写进服务文件的项目路径）；`doctor --host-only`。
- `bin/chat-digest` 统一入口；`scripts/install.sh` 一条命令安装；`doctor` 自检并给出修法；离线 demo 改为从合成 chat-export 目录经真实生成流程出报告，并用演示发送器走一遍 chat-send/v1。
- systemd unit 和 OpenWrt procd 服务；`scripts/build-release.sh` 构建可复现的发布包和 SHA256SUMS；release workflow 生成构建来源证明。
- 全新的中文文档：安装、配置（逐个参数）、排障、架构、安全边界、兼容性、实测记录、迁移指南、接口约定。

## 1.2.4

- Resolve explicit QQ on-demand durations through the natural-language duration parser rather than silently replacing unrecognized duration text with a 24-hour window; only a bare summary request uses the default. Parse complete fenced JSON responses safely while still rejecting truncated/incomplete output.
- Send the same independent site-access/use notice after both images are confirmed sent for either a daily task or an on-demand request. Derive request notice dates from the request window end; treat `cancelled` as terminal and do not reopen or resend cancelled notices.
- Synchronize public runtime/template references to 1.2.4. Evidence claims are limited to synthetic flows and read-only checks; no corrected real on-demand report or notice is claimed.


## 1.2.3

- Automatically send the QQ site-access/use notice only after both editions for a daily task have confirmed `sent` receipts. Track notice delivery separately so restarts do not duplicate confirmed notices and unknown submissions are not blindly retried.
- Expose independent notification receipts through `status.site_access_notices`; recover interrupted `sending` notices to `unknown`, retry only confirmed pre-submit failures, and never alter image receipts or regenerate/resend reports.
- Add an optional private per-group `site_access_file` configuration for the notice; retain independent group authorization and existing report image receipts.
- Synchronize public runtime/template references to 1.2.3. No real domains, credentials, group identifiers, URLs or private chat data are published.

## 1.2.2

- Fix shared-domain QQ report access in browsers that may reuse cached HTTP Basic credentials: route `/qq/*` to the QQ-specific authenticator before username-based legacy routing, while preserving the existing WeChat root route and per-group authorization boundaries.
- Return a standards-compliant HTML 401 response with a Basic challenge so browsers can present their native authentication prompt. Safari may require that prompt; automatic login from URL user-info is not guaranteed.
- Synchronize public runtime/template references to 1.2.2. No production account, credentials, private URL, group identifier or report artifact is included.

## 1.2.1

- Fix GitHub Actions workflow cache initialization: use `runner.temp` only in a step environment, persist the cache path through `GITHUB_ENV`, and reuse the same offline tokenizer cache in subsequent test steps.
- Synchronize public runtime/template version references to 1.2.1. No production image, dependency, model or runtime was changed.
- This corrective release supersedes v1.2.0 for use. The v1.2.0 tag and published assets remain unchanged.

## 1.2.0

- Rank QQ speakers by accumulated original-text tokens using pinned offline `tiktoken` data; include literal captions, exclude generated media/OCR/AI/web context, and omit tokenizer details from reports.
- Refresh QQ CDN `rkey` only from the verified account's group ticket after a CDN 400, preserving resource identity, original-image handling, cache and prepared-delivery receipts; reject only structured summary-provider 400 responses permanently.
- Add verified literal speaker quotes and a ranked activity/token section to the end of brief reports. Brief and review choose source-grounded quotes independently; empty candidates stay empty, and visible quotes participate in the Han-character count.
- Use the platform-neutral project name **WeChat Digest** and repository `rmqg/wechat-digest`; update clone instructions and repository links.
- Check configured desktop availability before launching report workers; treat absent or ambiguous worker receipts as unknown, never as retryable pre-submit failures.
- Let the live Weixin window start at any fully visible screen origin while preserving calibration reference, exact template size, dialog guards and stable-instance checks; verify the target header before submission.
- Include sanitized source/stage and report-day/edition context in delivery Bark notices, retaining failure context for deduplicated recovery without exposing raw errors or secrets.
- Drop stale active edition errors only after validated success receipts, including terminal replays, while retaining historical logs and forbidding regeneration or duplicate submission.
- Keep historical token counts and confirmed PNG fingerprints unchanged; publish only synthetic examples, not production accounts, conversations, reports or operational history.

## 1.1.1

- Persist each QQ task's prepared report paths and PNG SHA-256 fingerprints before sending. Resume only unsent editions from those fixed artifacts; late history sync never regenerates or overwrites a confirmed edition.
- Stop on missing or changed prepared files, refuse unsafe partial-delivery recovery without a manifest, and preserve completion when both editions already have confirmed receipts.
- Give separate requests for the same window separate output namespaces; the generation CLI accepts a validated task ID or creates a fresh UUID.

## 1.1.0

- Add configurable QQ OneBot v11/NapCat history collection, image/OCR/web context, independently generated brief/review reports, real message-receipt verification and a deterministic offline fictional QQ demo.
- Keep per-group messages, tasks, reports and receipt databases independent; collect while model work is running, honor a seven-day request bound, prioritize fresh requests, and stop cancelled, permanent-error or ambiguous-send jobs safely.
- Recognize native QQ mentions or text containing both `@` and a configured bot name; interpret intent separately, ignore bot-authored notifications and avoid replaying historical triggers on activation.
- Add a standard-library report website with per-group scrypt credentials, root-to-account-group routing, cross-group/resource denial, safe static paths, optional private Unix sockets, and restricted single-group SSH publishing with minimal metadata.
- Reuse a responsive web reading layer, expose verified source images within their own group, preserve delivered PNG fingerprints, and provide group-home/brief-review navigation without mixing website credentials.
- Prevent empty generation and cached-empty delivery; keep confirmed daily state stable during receipt rechecks and eliminate false overdue/recovery Bark alarms without hiding real failures.
- Require a single locked QQ service instance; status reads do not reset in-flight state, and recovery marks interrupted sends unknown only under the serving process lock.
- Support QQ `title_prefix` in the independent renderer and complete JSON inside an unclosed Markdown fence while still rejecting truncation, trailing prose and incomplete streams.
- Add OCR-summary and provider-fallback support, pin `websockets==16.0`, and provide an explicitly selected, checksum-verified Linux x86-64/Python 3.12 OCR image layer with an actual OCR CI smoke check.
- Retain original third-party notices; publish only synthetic examples, not production accounts, passwords, raw conversations, browser sessions or private operational history.


## 1.0.0

Initial public MIT release of the authorized personal-WeChat digest pipeline.

- Full database-key source for Linux/macOS/Windows under retained MIT notices, plus native Linux 4.x key/passphrase recovery source.
- Independent authenticated SQLCipher/WCDB data foundation, committed-WAL handling, message export/indexing, local DAT media decoding, and independent HTML/Pillow reporting.
- Portable explicit project configuration, immutable local image pins, account selection and fail-closed delivery targets.
- Separate complete brief/review summaries with source attribution, media/OCR/web evidence, per-edition caches, durable delivery receipts and unknown-state safeguards.
- Static website publication, same-origin authenticated image proxying, loopback-only image origin, and safe source/quote display.
- Wholly synthetic sample conversations, images and an authenticated encrypted SQLite fixture with public test-only keys.
- Verified V1/V2 hybrid DAT envelopes with AES prefixes, clear middles and XOR tails; strict raster completeness validation and HEIF decoding support.
- Consistent configured report/CLI timezones, HTML styles isolated from visible content, and PNG count/time axes. Synthetic source cards link to generated full-size images.
- Detailed deployment, configuration, database, media, architecture, operations, troubleshooting, security and development documentation.

This public history does not import private operational notes, personal data or the earlier unlicensed reference application's Git history. Third-party permissions are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
