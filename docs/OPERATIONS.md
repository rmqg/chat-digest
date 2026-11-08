# 运行维护

安装和服务启用见 [INSTALL.md](INSTALL.md)，出问题先看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## 日常命令

| 想做的事 | 命令 |
|---|---|
| 自检 | `./bin/chat-digest doctor`（加 `--probe-send` 做一次发送 dry run） |
| 立刻生成当天日报但不发 | `./bin/chat-digest prepare-only` |
| 不等到点，立刻跑一次日报流程（会发送） | `./bin/chat-digest daily-tick --force` |
| 生成任意时间窗的报告（不发送） | `./bin/chat-digest run --hours 6` |
| 看 @ 请求队列 | `./bin/chat-digest on-demand --status` |
| 看实时预处理状态 | `python3 deployment/realtime-images.py --status` |
| 看当天投递状态 | `python3 -m json.tool runtime/digest/delivery-state.json` |
| 看阶段失败原因 | `tail runtime/digest/stage-failures.jsonl` |

## 日报的节奏

- `daily-job.py --tick` 每分钟被叫醒一次。到 `daily_hour` 以后才生成；生成成功写 `prepared`，然后先发简报、再发回顾；当天两版都 `sent` 以后直接返回，不再写文件。
- 结果只在变化时写一行日志（`logged_at` 是写日志的时间）；`runtime/digest/*.log` 超过 5 MB 轮转一代。
- 等图或失败时按 `next_generate_at` 等待：等图按阶段给出的时间（60–900 秒），失败按 60、120、240… 秒，上限 900 秒，**永不放弃**；看门狗会在故障持续 10 分钟后提醒。
- 简报没确认送达之前，回顾一定不发。

## 实时预处理（可选）

`chat-digest-images` 服务白天持续处理新图片和链接，出报时只剩下摘要这一步：

- 导出没有变化、也没到每小时复查时，不跑；有变化时两次之间至少隔 `MIN_GAP_SECONDS`（120 秒），避免消息不停进来时整窗重复处理。
- 先跑取图和网页收集，输入和上一次完整运行一样就复用结果，不再起后面的容器（每小时强制完整跑一次）。
- 每轮最多处理 4 张图、4 个网页、2 个网页摘要，剩下的下一轮继续。

## 群内 @ 请求（可选）

`chat-digest-commands` 服务每分钟扫一次导出（导出没变化时 10 分钟才复扫），把“@机器人 总结……”记成任务，按时间顺序一个一个生成并发送。只说“总结”默认最近 24 小时，带时长的由模型解析（v2 对时长没有额外上限，请求很长的窗口会产生相应的模型费用）；失败按 60–900 秒退避重试。

## Bark 投递监控（可选）

`daily-watchdog.py --tick` 只读 `delivery-state.json`，在“过了出报时间还没两版都送达”“生成或发送失败”“状态是 unknown”时推送一次，故障换阶段再推一次，恢复后推一次恢复通知；另外对“图片被模型拒绝”“原图缺失”各提醒一次。它不改回执、不补发。

## 网站（可选）

- `publish_reports.py` 在每次生成成功后被自动触发，把成对的简报/回顾 HTML 和引用到的图片同步到 `report-site.json` 指定的远端；不上传 `sources.json`。
- 原图回源 `image-origin.py` 只在本机回环地址提供按哈希命名的图片，前面需要你自己的反代和认证，示例 `deployment/services/chat-digest-image-origin.caddy`。

## 存储和清理

| 位置 | 增长 | 清理 |
|---|---|---|
| `runtime/digest/image-jobs/` | 每次处理一个目录 | 实时 10 分钟、日报 7 天、按需 7 天后自动删除 |
| `runtime/digest/` 下的 `.digest-*`、`.images-*` 等临时目录 | 容器被强杀时残留 | 生成前自动删除 6 小时以上的 |
| `runtime/digest/images/decoded/`、各类 `*-cache` | 随图片和网页数量增长 | 不自动删除；可在停服务后整体删除，代价是重新请求模型 |
| `runtime/reports/` | 每天两版 | 不自动删除，按你的保留期限清理 |

这些目录都含聊天内容派生数据，删除前确认不再需要；备份要加密。
