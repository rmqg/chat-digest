# 排障

先跑一次自检，它会逐项给出修法：

```sh
./bin/chat-digest doctor            # 加 --probe-send 让发送命令做一次 --dry-run；加 --json 给脚本用
```

下面按看到的症状列。所有命令都在项目目录里执行；查看状态文件不会改变任何东西。

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
