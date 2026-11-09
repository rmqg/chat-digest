# 安装

本文用于安装细节和长期运维：从 Linux 服务器到“每天自动出日报并发到群里”。**第一次接触请先看 [QUICKSTART.md](QUICKSTART.md)**：A 路线约 5 分钟看离线演示（依赖下载另计），无账号、key 或 Docker；B 路线从真实导出做到本机密码网站，不启用自动发送。真实部署需要 Linux 命令行和 Docker 基础。

支持的系统、版本和实测情况见 [COMPATIBILITY.md](COMPATIBILITY.md)。

## 0. 准备

- Linux，x86-64 或 arm64；Python 3.11 或更新（Debian 12 自带 3.11，Ubuntu 24.04 自带 3.12）。
- Debian/Ubuntu 上：`sudo apt update && sudo apt install -y python3 python3-venv ca-certificates tzdata`。正式运行要求 Python 3.11+；根目录安装向导的轻量预检可在 3.10 上执行，但不会使 3.10 获得完整运行支持。
- 生产运行另外需要 Docker Engine（阶段处理都在只读、无特权、限内存的容器里跑），以及一个能产出 [chat-export/v1](CONTRACT.md) 目录的导出工具和一个实现 chat-send/v1 的发送命令，例如 [wechat-export-send](https://github.com/rmqg/wechat-export-send)。

## 1. 下载并校验

```sh
cd /opt
# 从 GitHub release 下载 chat-digest-2.0.0.tar.gz 和 SHA256SUMS 后：
sha256sum -c SHA256SUMS
gh attestation verify chat-digest-2.0.0.tar.gz --repo rmqg/chat-digest   # 可选：校验构建来源证明
tar xzf chat-digest-2.0.0.tar.gz
mv chat-digest-2.0.0 chat-digest
cd chat-digest
```

`sha256sum -c` 应该输出 `chat-digest-2.0.0.tar.gz: OK`。`gh attestation verify` 证明这个文件是 GitHub Actions 在 rmqg/chat-digest 仓库里由 release workflow 构建的。也可以直接 `git clone https://github.com/rmqg/chat-digest.git` 后 `git checkout v2.0.0`。

## 2. 安装入口与离线 demo

**新手入口是根目录 `./install.sh`**，默认交互：选择 `1`（demo）或 `2`（real），按提示填写配置和网站密码（至少 8 位；留空自动生成）。用普通运行用户执行，不要用 `sudo` 把虚拟环境和配置装成 root 属主。

```sh
./install.sh
```

演示模式会建立 `.venv/`、安装 `requirements.txt`、预取 tiktoken 的 o200k_base 数据到 `.cache/tiktoken`、初始化私有配置、自检，然后自动生成 `chat-digest-demo/`。安装下载需要网络，演示不访问外部模型、账号或聊天客户端。打开 `chat-digest-demo/index.html` 查看简报和回顾的 HTML/PNG；模拟发送只写本地 `outbox/`，不发群。**演示输出目录会被重建，不要在里面存自己的文件。**

无人值守或答案文件方式，任选其一：

```sh
./install.sh --non-interactive           # 默认 demo，自动生成离线演示
# 或：先复制、编辑并保护答案文件
umask 077
cp examples/install.env.example ./install.env
chmod 600 ./install.env
nano ./install.env
./install.sh --config ./install.env
```

答案文件是 `KEY=value` 数据，不是要执行的 shell 脚本。`MODE=demo|real`；真实模式填 `EXPORT_ROOT`、`CONVERSATION_ID`、`GROUP_NAME`、`MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`，`SITE_PASSWORD` 可留空自动生成。它含密钥和可能的明文站点密码，绝不提交或分享。向导写出的 `runtime/` 权限为 `700`、配置 JSON 为 `600`，网站用户名默认 `reader`。

真实模式只安装、写配置和运行 doctor，**不会自动 build/pin 镜像、请求真实模型出报、启动网站或发群消息**。第一次 doctor 可能因镜像未构建/锁定返回非零；“配置已写入 runtime/”之后按提示构建、锁定，再复查即可。完整顺序见 [QUICKSTART B 路线](QUICKSTART.md#b-真实导出到本机密码网站)。

**原底层入口仍保留，适合只装依赖或复制服务文件：**

```sh
./scripts/install.sh                     # 只显示计划，不修改文件
./scripts/install.sh --apply             # .venv、依赖、分词缓存、doctor --demo-only
./bin/chat-digest demo --output ./chat-digest-demo   # 底层安装器不自动生成 demo
```

根目录入口收到旧参数（例如 `./install.sh --apply --services systemd`）时会转交底层入口，保留原语义；要在向导中加参数，应同时写 `--config` 或 `--non-interactive`，例如 `./install.sh --non-interactive --python /usr/bin/python3.11`。

安装成功的 doctor 结尾为 `doctor: 所有必需检查通过`。演示模型只是本机 127.0.0.1 上的替身，总结是虚构示例，不代表真实 AI 效果。以后可单独重跑 demo；QQ 的离线样例用 `./bin/chat-digest demo --qq --output ./qq-digest-demo`。

`--no-venv` 用于 OpenWrt 等仅运行宿主调度、重计算都走 Docker 的机器：它跳过 `.venv`、pip、分词缓存和自动演示，用 `doctor --host-only` 检查宿主。**它不是在没有依赖的宿主直接运行离线 demo 的办法**，详见 [排障](TROUBLESHOOTING.md#docker-only-宿主与---no-venv)。

## 3. 生产部署

下面假设项目放在 `/opt/chat-digest`，由专门的用户 `chat-digest` 运行（systemd unit 默认这样写；用别的用户就相应改 unit）。

### 3.1 创建运行用户和私有目录

```sh
sudo useradd --system --home /opt/chat-digest --shell /usr/sbin/nologin chat-digest
sudo usermod -aG docker chat-digest          # 需要调 Docker；注意 docker 组约等于 root 权限
sudo chown -R chat-digest:chat-digest /opt/chat-digest
sudo -u chat-digest ./bin/chat-digest init     # 建 runtime/（700）和 runtime/project.json（600）
```

`runtime/` 存放配置、模型 key、报告、回执和缓存，属于私有数据，不要放进 Git 或网站目录。

如果从 QUICKSTART 的普通用户试用切换到专用服务用户，要同时把 `runtime/project.json` 的 `worker_uid` / `worker_gid` 改成该服务用户的数字 UID/GID（可用 `id chat-digest` 查看），再运行一次该用户的 `init`。只 `chown` 项目而保留旧 worker ID，会让容器无法读写私有运行目录。下面是手动部署流程；已由向导填好的导出/模型配置无需再复制模板覆盖。

### 3.2 构建并锁定运行时镜像

```sh
sudo -u chat-digest docker build -t chat-digest-runtime:2.0.0 .
# 需要 OCR（只在 x86-64 上验证过）：docker build --build-arg WITH_OCR=1 -t chat-digest-runtime:2.0.0 .
sudo -u chat-digest ./bin/chat-digest pin-images
```

`pin-images` 把 `images.runtime` 解析成不可变的镜像 ID 写进 `runtime/image-pins.json`，之后所有容器都用这个 ID，`--pull never`，不会自动换镜像。有意升级时改完镜像再 `python3 deployment/bootstrap.py --refresh-pins`。

如果 `docker_network`（默认 `chat-digest_default`）不存在，先建一个：`docker network create chat-digest_default`。需要调模型、抓网页的阶段走这个网络，其余阶段都是 `--network none`。

### 3.3 接上导出和发送

1. 让导出工具把 chat-export/v1 写到一个目录，例如 `/srv/chat-export/acct1`，权限 700，并保证 `chat-digest` 用户（或 `worker_uid`）能读。
2. 列出会话，找到要总结的那个：

   ```sh
   ./bin/chat-digest list-conversations --export-root /srv/chat-export/acct1
   ./bin/chat-digest check-export --export-root /srv/chat-export/acct1 check --conversation-id g_1a2b3c
   ```

   `check` 只输出条数和时间范围，不打印消息内容。
3. 编辑 `runtime/project.json`：`export.root`、`group.conversation_id`、`group.name`、`send.command`、`send.to`。每个字段的说明见 [CONFIGURATION.md](CONFIGURATION.md)。
4. 模型配置：

   ```sh
   cp examples/gemini-model.example.json runtime/gemini-model.json
   chmod 600 runtime/gemini-model.json
   # 用编辑器填 base_url、model、api_key，需要的话加 fallbacks
   ```

### 3.4 自检和试跑

```sh
sudo -u chat-digest ./bin/chat-digest doctor --probe-send
sudo -u chat-digest ./bin/chat-digest prepare-only
```

`doctor` 逐项检查依赖、权限、配置、导出、模型链、发送命令和 Docker，失败项都给出修法；`--probe-send` 让发送命令做一次 `--dry-run`（按约定只定位目标、校验输入，不提交）。`prepare-only` 生成当天两版报告但不发送，会产生模型费用。确认 `runtime/reports/` 里的 PNG 没问题，再装定时任务。

### 3.5 定时任务和服务

**systemd（Debian、Ubuntu 等）**

```sh
sudo ./scripts/install.sh --apply --services systemd --prefix /opt/chat-digest   # 只复制 unit 文件，不启用
sudo systemctl daemon-reload
sudo systemctl enable --now chat-digest-daily.timer      # 每分钟一次：到点生成，生成好就发送
sudo systemctl enable --now chat-digest-images.service   # 白天预处理新图片和链接，出报时更快（可选）
sudo systemctl enable --now chat-digest-commands.service # 群内“@机器人 总结”（可选，需要 summary-trigger.json）
sudo systemctl enable --now chat-digest-watchdog.timer   # Bark 投递监控（可选，需要 daily-watchdog.json）
```

**OpenWrt / iStoreOS（procd + cron）**

```sh
# 宿主只需要 python3（标准库），重活都在运行时镜像里，所以不建 .venv；--prefix 写进服务文件
./scripts/install.sh --apply --no-venv --services procd --prefix /opt/chat-digest   # 复制到 /etc/init.d，不启用
./bin/chat-digest doctor --host-only
/etc/init.d/chat-digest-images enable && /etc/init.d/chat-digest-images start
/etc/init.d/chat-digest-commands enable && /etc/init.d/chat-digest-commands start
```

再把下面两行加进 `/etc/crontabs/root`（第二行可选），然后 `/etc/init.d/cron restart`：

```cron
* * * * * cd /opt/chat-digest && /usr/bin/python3 deployment/daily-job.py --tick >> runtime/digest/daily-job.log 2>&1
* * * * * cd /opt/chat-digest && /usr/bin/python3 deployment/daily-watchdog.py --tick >> runtime/digest/watchdog.log 2>&1
```

宿主上跑的这些脚本只用 Python 标准库，所以 OpenWrt 上装 `python3` 就够，不需要 `.venv`；重活都在 Docker 容器里。`daily-job` 只在结果变化时写日志，日志超过 5 MB 会自动轮转一代。

### 3.6 发送的安全规则

- 发送命令返回 `unknown`（已经或可能已经提交、但没能确认）时，chat-digest **不会自动重发**，当天状态停在 `unknown`，看门狗会提醒你。人工确认群里有没有收到，再决定怎么处理；不要为了重发去删回执。
- `failed`（确定没发出去）会在下一分钟重试；`invalid`（参数错误）在你改好发送配置之前不会重试。

### 3.7 报告网站

默认用**本机模式**：chat-digest 自带一个只用标准库的站点进程，直接读本机 `runtime/` 里的报告和图片，不需要发布、rsync、原图回源或隧道。微信和 QQ 的报告都能看。

```sh
./bin/chat-digest init            # 第一次运行时创建 runtime/local-site.json，并只显示一次账号和随机密码
./bin/chat-digest site --serve    # 启动，浏览器打开 http://127.0.0.1:8787/
./bin/chat-digest site --set-password   # 以后想换密码时
```

站点启动后在后台整理报告：第一次整理时页面显示“正在整理”并自动刷新（报告多、机器慢时可能要几分钟），以后有新报告时继续提供旧页面，整理完自动换新。

已经 `init` 过的旧部署，用 `./bin/chat-digest site --init` 单独创建配置。长期运行用 `deployment/services/` 里的 `chat-digest-site` 服务（systemd 或 procd，`install.sh --services` 会复制过去，但不会替你启用）。QQ 群写进配置的 `qq_groups`，见 [CONFIGURATION.md](CONFIGURATION.md#runtimelocal-sitejson)。

站点只监听 `127.0.0.1`，并且要登录。想从别的设备访问，自己在前面加反代或隧道，**并把 `cookie_secure` 改成 `true`**（走 HTTPS 时）。下面三种任选一种，都不要去掉站点自己的登录：

**Caddy**（自动申请 HTTPS 证书）：

```caddyfile
reports.example.org {
    reverse_proxy 127.0.0.1:8787
}
```

**Nginx**（证书自备）：

```nginx
server {
    listen 443 ssl;
    server_name reports.example.org;
    ssl_certificate     /etc/ssl/reports.example.org.crt;
    ssl_certificate_key /etc/ssl/reports.example.org.key;
    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
    }
}
```

**Cloudflare Tunnel**（机器没有公网 IP 时），`~/.cloudflared/config.yml`：

```yaml
tunnel: REPLACE_WITH_TUNNEL_ID
credentials-file: /etc/cloudflared/REPLACE_WITH_TUNNEL_ID.json
ingress:
  - hostname: reports.example.org
    service: http://127.0.0.1:8787
  - service: http_status:404
```

**什么时候才需要远端模式**：网站必须放在另一台公网服务器上（报告机器不能对外开放，也不方便跑隧道），或者你已经有一套静态站点要沿用。远端模式用 `publish_reports.py`、`qq_publish.py` 把网页同步过去，原图留在本机由 `image-origin.py` 回源，配置见 `runtime/report-site.json` 和 `runtime/image-origin.json`。两种模式互不影响，可以同时用。

## 4. 升级和卸载

升级：停服务 → 换代码 → `./scripts/install.sh --apply` → 重新构建镜像并 `bootstrap.py --refresh-pins` → `doctor` → 启服务。从 v1.2.x 升级见 [MIGRATION-v2.md](MIGRATION-v2.md)。

卸载：停用并删除服务和计划任务，删除 `/opt/chat-digest`。`runtime/` 里有报告、回执和模型 key，删除前确认不再需要；导出目录归导出工具管理，chat-digest 不会动它。
