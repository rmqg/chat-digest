# 接口约定：chat-export/v1 与 chat-send/v1

本文件由 `wechat-export-send`、`wecom-export`、`chat-digest` 三个仓库共用，三边 `docs/CONTRACT.md` 内容相同。要修改时三边同时改，并升 schema 版本。

## 1. chat-export/v1（导出格式，生产方：wechat-export-send、wecom-export；消费方：chat-digest）

导出是一个目录，可以增量追加：

```
<export-root>/
  manifest.json
  members/<conversation_id>.json        # 可选
  conversations/<conversation_id>.jsonl # 每行一条消息，按 ts 递增追加
  media/<sha256 前两位>/<sha256>.<ext>  # 可选，按内容寻址
  state/                                # 生产方自用的游标，消费方不读
```

`conversation_id` 只能含 `[A-Za-z0-9_.-]`。生产方把平台原始 ID 做成稳定的 ID，可以哈希，也可以原样保留；同一个会话不论哪次运行都必须得到同一个 ID。

### manifest.json

```json
{
  "schema": "chat-export/v1",
  "producer": {"name": "wechat-export-send", "version": "1.0.0"},
  "source": {"platform": "wechat", "account_id": "<稳定的不透明 ID，可以是哈希>"},
  "generated_at": "2026-11-08T12:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "conversations": [
    {"id": "g_1a2b3c", "kind": "group", "name": "示例群", "file": "conversations/g_1a2b3c.jsonl",
     "members_file": "members/g_1a2b3c.json", "last_ts": 1794043483, "message_count": 1234}
  ]
}
```

- `platform` 取值：`wechat`、`wecom`、`qq`、`other`。
- `kind` 取值：`group`、`private`、`channel`、`other`。
- manifest 必须原子替换：先写临时文件，再 rename。

### conversations/*.jsonl（每行一个 JSON 对象）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 会话内唯一且稳定。重复出现时以最后一行为准（消息被编辑、撤回时会这样） |
| `ts` | integer | 是 | Unix 秒（UTC） |
| `sender_id` | string | 是 | 会话内稳定的发送者 ID；系统消息用 `"system"` |
| `sender_name` | string | 否 | 当时的显示名（优先用群昵称） |
| `is_self` | boolean | 否 | 是不是导出账号自己发的 |
| `type` | string | 是 | `text`、`image`、`file`、`link`、`voice`、`video`、`sticker`、`quote`、`forward`、`system`、`revoke`、`other` |
| `text` | string | 否 | 可读正文。没有正文时不写，或写空串。不得用占位文字冒充正文 |
| `reply_to` | object | 否 | `{"id": "<被引用消息 id，可缺>", "sender_name": "...", "text": "..."}` |
| `forward` | array | 否 | 合并转发的条目，每项 `{"sender_name","ts","type","text"}` |
| `link` | object | 否 | `{"url","title","description"}` |
| `media` | array | 否 | 每项 `{"kind":"image|file|voice|video|sticker","path":"media/ab/<sha256>.jpg","sha256","mime","bytes","name","status":"ready|pending|missing","is_thumbnail":false}`；`path` 相对 export-root；没下载到原图时 `status` 写 `pending` 或 `missing`，**不得**拿缩略图冒充原图（是缩略图就写 `is_thumbnail: true`） |
| `mentions` | array | 否 | 被 @ 的 `sender_id` 列表；@所有人写 `"@all"` |
| `raw_type` | string | 否 | 平台原始类型号，便于排查 |
| `extra` | object | 否 | 生产方私有字段，消费方必须忽略不认识的字段 |

### members/*.json

```json
{"conversation_id": "g_1a2b3c", "updated_at": 1794043483,
 "members": [{"id": "u_9f8e", "name": "群昵称", "display_name": "本名或备注",
              "avatar": "media/cd/<sha256>.jpg"}]}
```

### 规矩

- 只追加；消费方按 `(conversation_id, id)` 去重，后出现的行覆盖先出现的。
- 消费方用 `last_ts` 和自己记的游标做增量，不读生产方的 `state/`。
- 文件编码 UTF-8，不带 BOM，每行以 `\n` 结尾；生产方写行时先写完整行再 flush，消费方忽略最后一行不完整的情况。
- 时间一律存 UTC 秒，显示时区看 manifest 的 `timezone`。
- 导出里含真实聊天内容和媒体，属于隐私数据：目录权限 700，不放进任何仓库。

## 2. chat-send/v1（发送接口，提供方：wechat-export-send；调用方：chat-digest）

命令行（调用方通过配置项指定命令前缀，例如 `wechat-send` 或 `docker exec wechat wechat-send`）：

```
wechat-send --to <会话名或 conversation_id> (--text <文字> | --text-file <路径> | --image <路径>) \
            [--request-id <幂等键>] [--timeout <秒>] [--dry-run] --json
```

- `--request-id`：同一个键已经确认发送过的，直接返回上一次的结果，不会重发。
- `--dry-run`：只定位目标会话、校验输入，不提交。
- 标准输出是一个 JSON 对象：

```json
{"schema": "chat-send/v1", "status": "sent", "request_id": "...", "to": "...",
 "conversation_id": "...", "submitted_at": 1794043483, "detail": "..."}
```

| status | 退出码 | 含义 | 调用方该怎么做 |
|---|---|---|---|
| `sent` | 0 | 已提交并确认送达 | 记为完成 |
| `failed` | 1 | 在提交**之前**失败（目标会话找不到、窗口不对、文件不合法等），确定没发出去 | 可以重试 |
| `unknown` | 2 | 已经提交或可能已经提交，但没能确认 | **不许自动重试**，交给人工确认 |
| `invalid` | 3 | 参数错误 | 修配置，不重试 |

- 提供方必须保证：凡是可能已经点了「发送」的情况，一律报 `unknown`，不报 `failed`。
