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

## 5. 没有验证的

- 带真实模型的完整生成流程（容器内各阶段串起来调真实模型）。
- 与真实导出工具、真实 `wechat-send` 的联调。
- systemd、procd 服务在真实系统上的启用和长期运行。
- OCR 层（`WITH_OCR=1`）。
