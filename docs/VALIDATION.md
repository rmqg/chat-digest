# 实测记录（v2.0.0）

本页只记录真正执行过的验证。没有列在这里的组合，都按 [COMPATIBILITY.md](COMPATIBILITY.md) 里的“未测试 / 不保证”对待。

## 1. 单元测试和离线 demo 测试

环境：Ubuntu 22.04.5 LTS，内核 6.8.0-59-generic，x86-64。

```sh
PYTHONPATH=.:deployment:agent_console TIKTOKEN_CACHE_DIR=… python -m pytest -q
```

| Python | 结果 |
|---|---|
| 3.12.16 | 298 passed |
| 3.11.18 | 298 passed |

另外：`scripts/check-docs.py --cli` 检查全部文档内链接、示例 JSON 和 27 个脚本的 `--help`；`scripts/audit-release.py` 发布边界审计 0 项。

## 2. 干净容器按文档安装

宿主：macOS 27.2 arm64 上的 OrbStack，Docker 29.4.0，虚拟机内核 7.0.14-orbstack。amd64 用 `--platform linux/amd64` 模拟运行。

每个容器都是全新的官方镜像，只读挂载本地构建的发布包和 `SHA256SUMS`，执行 [`scripts/validate-clean.sh`](../scripts/validate-clean.sh)。脚本严格按 [INSTALL.md](INSTALL.md) 的顺序：

```sh
apt-get install -y python3 python3-venv ca-certificates
sha256sum -c SHA256SUMS                       # chat-digest-2.0.0.tar.gz: OK
tar xzf chat-digest-2.0.0.tar.gz && cd chat-digest
./scripts/install.sh                          # 只打印计划
./scripts/install.sh --apply                  # .venv、依赖、分词数据、doctor --demo-only
./bin/chat-digest demo --output /tmp/chat-digest-demo
./bin/chat-digest demo --qq --output /tmp/qq-digest-demo
./bin/chat-digest doctor --demo-only
./bin/chat-digest init                        # 然后把 project.json 指向自带的合成导出和演示发送器
./bin/chat-digest list-conversations --export-root examples/synthetic-export
./bin/chat-digest check-export --export-root examples/synthetic-export check --conversation-id g_demo_garden
./bin/chat-digest doctor --no-docker --probe-send
```

| 镜像 | 架构 | Python | 结果 |
|---|---|---|---|
| debian:12（bookworm） | arm64 | 3.11.2 | 全部步骤通过 |
| debian:12（bookworm） | amd64 | 3.11.2 | 全部步骤通过 |
| ubuntu:24.04（24.04.5 LTS） | arm64 | 3.12.3 | 全部步骤通过 |
| ubuntu:24.04（24.04.5 LTS） | amd64 | 3.12.3 | 全部步骤通过 |

每组的 doctor 输出：依赖、分词数据、字体、运行目录权限、配置、发送命令 dry run（`dry run: target and input are valid, nothing submitted`）都是 `[ OK ]`，结论 `all required checks passed`；预期内的警告有三条：自带样本目录权限是 775、样本里最后一条消息是很多天前、没有配置模型（离线 demo 不需要）。demo 生成了简报和回顾两版 HTML/PNG，演示发送器写入了 1 张 PNG，并对同一个 `request_id` 正确返回了幂等重放结果。

## 3. 运行时镜像

| 平台 | 结果 |
|---|---|
| arm64（原生） | `docker build` 成功；`docker run --network none --read-only --cap-drop ALL … /tool/digest.py --help` 正常；同样的隔离参数下，只读挂载合成导出运行 `prepare-images.py`，输出 `{"image_messages": 1, "ready_images": 1, "pending": 0, "missing": 0}` |
| amd64（模拟） | `docker build --platform linux/amd64` 成功；同样的隔离参数下 `/tool/digest.py --help` 正常 |

## 4. 隐私与许可证检查

- gitleaks v8.30.1：扫描 v1.2.4 之后的全部新提交和当前工作树。唯一命中是 `scripts/doctor.py` 里 tiktoken 分词数据文件的公开 SHA-256 校验值，属于误报，已在 `.gitleaks.toml` 精确放行（只放行这一个值、这一个文件）。
- 关键字扫描（账号 ID 前缀、内网和 Tailscale 网段、个人域名、人设名、私有路径等）：清理了一处网站反代示例里的私人站点路径和一段残留的数据库测试夹具；剩下的命中都是测试里虚构的 `…@chatroom` 编号和“拒绝私网地址”测试用的合成地址。
- 私人名单（真名、群名等，名单只保存在私有机器上）扫描：0 命中。

## 5. 新渲染器和本机模式网站

分支 `renderer-v2`，用提交 `d11d6be` 构建的发布包（SHA-256 `33cc2af644a7830e2c9b8abaa58a05646b97b28cbc0913ed1c81ffdbd7ebde97`）。本节之后的提交只改文档。

**干净容器**（内核 7.0.14-orbstack，Docker 29.4.0，一个容器跑完就删）：校验包、`install.sh`、`demo`、`demo --qq`、`doctor`、`init`、本机站点全流程探针、改密码。

| 镜像 | 架构 | Python | 结果 |
|---|---|---|---|
| debian:12 | arm64 | 3.11.2 | 全部通过；探针 0 失败 |
| ubuntu:24.04 | arm64 | 3.12.3 | 全部通过；探针 0 失败 |
| debian:12 | amd64（qemu 模拟） | 3.11.2 | 全部通过；探针第一轮有 1 项“首页 200”实际是 503，见下 |
| ubuntu:24.04 | amd64（qemu 模拟） | 3.12.3 | 同上 |

模拟环境很慢，探针登录后第一次整理还没结束，站点先回 503（带 `Retry-After`），3～11 秒后就绪，这正是后台整理的设计；重启站点后第二轮探针 0 失败。

全新解压的包里运行全部测试：372 passed，1 skipped（跳过的一项要求非 root，已在普通用户下单独通过）；`check-docs` 0 错，`audit-release` 0 项。

探针覆盖：未登录跳转和 401、错误密码、站外跳转地址被拒、Cookie 属性、缓存头、安全响应头、路径穿越和非哈希资源一律 404、资源文件带长期缓存、只监听回环地址。

**服务和反向代理**

| 对象 | 怎么测的 | 结果 |
|---|---|---|
| `chat-digest-site.service`（systemd 252，debian:12，专门的系统用户） | `install.sh --services systemd`、`enable --now`、全流程探针、`kill -9`、错误配置 | 正常运行；崩溃后自动重启；`bind` 改成 `0.0.0.0` 时拒绝启动 |
| `chat-digest-site`（procd，OpenWrt 24.10-SNAPSHOT，aarch64，没有 Pillow、没有时区库） | `install.sh --no-venv --services procd`、`enable`、`start`、登录、`kill -9` 后比对 PID、`stop`、错误配置 | 运行正常；重启后是新 PID；`stop` 后端口关闭；错误配置不启动 |
| Nginx 1.22.1 | 从本文档提取示例原样启动，自签证书，`cookie_secure: true` | 探针 0 失败，Cookie 带 Secure，5 MB 的字体资源正常 |
| Caddy 2.6.2 | 从本文档提取示例，加 `tls internal` 和测试端口 | 探针 0 失败，Cookie 带 Secure |
| Cloudflare Tunnel（cloudflared 2026.10.7） | `tunnel ingress validate`、`ingress rule` | 配置和路由规则正确。**没有账号，实际隧道未测** |

**真实日报离线校验**（只渲染不发送，在无网络、只读挂载、限 1 GB 内存的容器里运行，产物留在数据所在的机器上，这里只记录数字）：微信 5 天、QQ 3 天，共 16 版。校验直接对比报告数据和渲染结果：

- 统计、话题（标题、概述、要点、时间、顺序）、金句、活跃榜（名字、数值、顺序、单位 token）：HTML 和 PNG 都与数据一致，0 处差异。PNG 通过记录每段绘制的文字来核对，字体缺字导致丢中文 0 处，超出画布 0 处。
- 长图宽 1080，高度是旧版的 51%～57%，文件大小是旧版的 52%～57%。最长一张高 35664 像素，单张渲染峰值内存约 250 MB。
- 头像：报告里标记可用的头像全部解码成功并画上（共 987 次，失败 0）；报告里没有头像的成员画姓名首字圆标。
- 经过真实的站点整理后（1 天 2 版，含原图）：页面内容不丢；原图 14 张，集合与现有线上页面的图片哈希完全相同；图片标签、来源行数、“原图不可用”提示的数量和线上页面完全相等；资源文件哈希和文件名一致、图片都能解码。

**发现并修复的问题**：连续收尾标点（`——`、`……`）会画到图外；网站重建的话题和来源时间固定按上海时间；昵称全是字体缺字符时活跃榜留空白；非 root 的第一次 `init` 因为 `worker_uid` 默认 1000 而失败；首次整理卡住请求，反向代理会超时；`init` 写的占位群被当成已配置。每一项都加了测试。

**隐私扫描**：gitleaks v8.30.1 扫描工作树和 v2.0.0 之后的提交：0 命中；全部历史 2 处，都是 v1.2.4 之前的合成测试夹具 `examples/synthetic/fixture-key.json`，当前树里已经没有。私人名单（共 1645 条，只保存在私有机器上）和关键字以 v2.0.0 为基线扫描新增内容：关键字 0 命中；名单命中的 4 条都是 `test`、`cookie` 这类常用词的片段，没有真实名字。

**没有验证的**：Cloudflare Tunnel 实际连通；在整段真实历史上运行本机模式（校验只跑了 1 天；一天约 17 秒，长历史的第一次整理可能要几分钟，并且会在 `runtime/local-site` 里多占一份图片空间）；真实数据的画面没有人眼看过（校验只比对数据，产物不离开数据所在的机器）；新渲染器在生产上的实际发送。

## 6. 没有验证的


- 带真实模型的完整生成流程（容器内各阶段串起来调真实模型）。
- 与真实导出工具、真实 `wechat-send` 的联调。
- 除 `chat-digest-site` 以外的 systemd、procd 服务在真实系统上的启用；所有服务的长期运行。
- OCR 层（`WITH_OCR=1`）。
