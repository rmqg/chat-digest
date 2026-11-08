# 报告 JSON 格式（渲染器的输入）

每一期报告分两版：简报（`brief`）和回顾（`review`）。每一版都会存成 `<名字>-<版本>.json`，同一个字典会交给渲染器画出 `<名字>-<版本>.html` 和 `<名字>-<版本>.png`。自己写渲染器（见 [CONFIGURATION.md 的外部渲染器一节](CONFIGURATION.md#外部渲染器render)）时，函数收到的就是这里描述的字典。

规矩：

- 字段可能增加。渲染器要忽略不认识的字段，缺少可选字段时照常出图。
- 所有文字都是聊天内容或模型写的总结，渲染器负责转义（HTML）和折行（PNG），不要信任其中的标记。
- 时间：`*_unix` 是 Unix 秒（UTC）；显示用 `timezone`（缺省按 `Asia/Shanghai`）。已经排好的显示文字（`range_text`、`label`、`time`）可以直接用。
- 微信日报和 QQ 日报的字典大体相同，下表“来源”一列注明只在一边出现的字段。离线 demo（`./bin/chat-digest demo`、`demo --qq`）生成的 JSON 是完全虚构的样例，可以拿来对照。

## 顶层字段

| 字段 | 类型 | 来源 | 含义 |
|---|---|---|---|
| `edition` | 字符串 | 都有 | `"brief"`（简报）或 `"review"`（回顾）。两版的统计相同，话题条数和每个话题的细节多少不同：回顾更细 |
| `chat_username` | 字符串 | 都有 | 会话的稳定 ID（微信是 conversation_id，QQ 是 `qq:<群号>`） |
| `chat_display` | 字符串 | 都有 | 会话显示名 |
| `title_prefix` | 字符串 | QQ；微信可选 | 标题前缀，例如“QQ群日报”。没有时由渲染器自定标题 |
| `platform` | 字符串 | 微信 | 导出的平台：`wechat`、`wecom` 等 |
| `day` | 字符串 | 都有 | 报告所属日期，`YYYY-MM-DD`（窗口结束时刻所在的日期） |
| `range_text` | 字符串 | 都有 | 已排好的时间范围，例如 `2026.10.28 08:00 — 2026.10.29 08:00` |
| `window` | 对象 | 都有 | `{"start_unix", "end_unix_exclusive"}`，统计窗口，左闭右开 |
| `timezone` | 字符串 | 都有 | 显示时区，例如 `Asia/Shanghai` |
| `generated_at` | 字符串 | 都有 | 生成时间（ISO 8601）；离线样例里可能是一段说明文字 |
| `one_line` | 字符串 | 都有 | 一句话总结 |
| `stats` | 对象 | 都有 | 统计数字，见下文 |
| `user_type_counts` | 对象 | QQ | 按消息类型计数，例如 `{"text": 220, "image": 70, "forward": 1, "unknown": 12}` |
| `topics` | 数组 | 都有 | 话题，见下文 |
| `quotes` | 数组 | 都有 | 金句，见下文；可以为空 |
| `insights` | 数组 | QQ | 预留，目前为空数组 |
| `timeline` | 数组 | QQ | 预留，目前为空数组 |
| `participants` | 数组 | 都有 | 发过言的成员，`{"username", "name", "tokens"}`；`tokens` 是这个人正文的词元数，用来衡量“说了多少” |
| `active_rank` | 数组 | 都有 | 按 `tokens` 从多到少排好的活跃榜，元素同上 |
| `leaders` | 对象 | 都有 | 几个“之最”：`talk`（说得最多，元素同上）、`peak`（最热闹的时段，`{"name": 时段文字, "count": 消息数}`）；QQ 另有 `media`（发图最多）、`link`（发链接最多），都是 `{"name", "count"}`。任何一项都可能缺 |
| `rhythm_buckets` | 数组 | 都有 | 消息节奏：按固定时长切的格子，`{"start_unix", "end_unix_exclusive", "count", "label"}`，`label` 是格子开始时刻的显示文字。默认 10 分钟一格，24 小时共 144 格 |
| `rhythm_bucket_seconds` | 整数 | 都有 | 每格的秒数，例如 `600` |
| `avatars` | 对象 | 都有 | 头像，键是成员 ID（与 `participants[].username`、`speaker_id`、`author_id` 对应）：`{"available": 布尔, "data_uri": "data:image/png;base64,…", "source": "export"}`。`available` 为假或缺这一项时画占位头像 |
| `media_understanding` | 对象 | 都有 | 媒体理解情况：`images` 里有 `total`（图片总数）、`understood`（被模型看懂的张数），微信还有 `models`、`thumbnail_limitations`（只有缩略图的张数）、`refused`、`missing`；`voice`、`video` 目前固定为 `"not analyzed"` |
| `ai_summary_used` | 布尔 | 微信 | 这一期是否用了模型总结（窗口里没有消息时为假） |
| `request` | 对象 | 微信，可选 | 只在群内 @ 请求生成的报告里出现：`{"kind": "on_demand", "id", "trigger_timestamp"}` |

## `stats`

| 字段 | 类型 | 含义 |
|---|---|---|
| `messages` | 整数 | 统计窗口里的消息数（已去掉本项目自己发的报告和说明） |
| `members` | 整数 | 发过言的人数 |
| `text`、`images`、`links`、`stickers`、`videos` | 整数 | 各类消息数（QQ 全有；微信按导出情况出现） |
| `excluded_own_reports` | 整数 | 微信：被排除的自发报告条数 |
| `image_understood` | 整数 | 微信：被模型看懂的图片数 |

## `topics[]`

| 字段 | 类型 | 含义 |
|---|---|---|
| `topic` | 字符串 | 话题标题 |
| `summary` | 字符串 | 话题概述 |
| `details` | 字符串数组 | 细节要点；简报里常为空，回顾里较多 |
| `count` | 整数 | 话题涉及的消息数 |
| `start_unix`、`end_unix` | 整数 | 话题的起止时间 |
| `message_ids`、`reference_ids` | 字符串数组 | 话题引用的消息 ID |
| `participant_ids` | 字符串数组 | 参与这个话题的成员 ID |
| `samples` | 字符串数组 | 几条代表性原话 |
| `references` | 数组 | 引用的原始消息：`{"message_uid", "reference", "author", "author_id", "timestamp", "type", "text"}`；QQ 报告在补完原图后可能多一个 `image_sources`（相对报告目录的图片路径数组，例如 `assets/<sha256>.jpg`） |

`type` 的取值同 chat-export/v1（`text`、`image`、`link`、`quote`、`forward`、`system` 等）。

## `quotes[]`

| 字段 | 类型 | 含义 |
|---|---|---|
| `text` | 字符串 | 金句原文 |
| `speaker` | 字符串 | 说话人显示名 |
| `speaker_id` | 字符串 | 说话人 ID（对应 `avatars` 的键） |
| `time` | 字符串 | 显示用的时间 |
| `message_id`、`reference` | 字符串 | 原消息 ID |
| `source` | 对象 | 原消息，字段同 `topics[].references[]` |

## 渲染器要产出什么

- PNG：写到给定的路径，宽度和长度由渲染器决定（会原样发到群里，太长的图在手机上不好看）。
- HTML（可选）：一个完整的、不依赖外部资源的 HTML 字符串（图片和字体可以内联成 data URI）。这个文件会被网站当作单期页面提供，报告站点自己的页面不受影响。
