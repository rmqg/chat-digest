# 开发

## 环境

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export TIKTOKEN_CACHE_DIR=$PWD/.cache/tiktoken
.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
```

## 测试

```sh
PYTHONPATH=.:deployment:agent_console .venv/bin/python -m pytest -q
.venv/bin/python scripts/check-docs.py --cli     # 文档内链接、示例 JSON、每个脚本的 --help
.venv/bin/python scripts/audit-release.py         # 发布边界：不允许数据库、密钥、样本以外的图片
```

测试全部离线：模型用替身，发送用 `deployment/fixture_chat_send.py` 模拟的 chat-send/v1 命令，导出用 `deployment/fixture_export.py` 现场生成的虚构目录。`tests/test_synthetic_demo.py` 会真正跑一遍 demo（本机回环的演示模型 + 演示发送器）。

## 代码地图

| 路径 | 作用 |
|---|---|
| `deployment/chat_export.py` | chat-export/v1 读取与校验（只读） |
| `deployment/chat_send.py` | chat-send/v1 调用与结果分类 |
| `deployment/digest.py` | 两版摘要、校验、渲染入口 |
| `deployment/image_pipeline.py` 与 `prepare-images.py`、`caption-images.py`、`ocr_images.py`、`web_context.py`、`web_summary.py`、`ocr_summary.py` | 图片和网页证据阶段 |
| `deployment/model_fallback.py` | 全局 fallback 链与切换规则 |
| `deployment/daily-job.py`、`on-demand.py`、`realtime-images.py`、`daily-watchdog.py` | 调度、发送状态机、监控 |
| `deployment/local_site.py` | 默认的本机报告网站（登录、路由、按哈希提供图片） |
| `deployment/report_web_style.py` | 各网站页面共用的浅紫样式 |
| `deployment/report_site.py`、`publish_reports.py`、`source_media.py`、`image-origin.py` | 网页报告整理和远端站点 |
| `deployment/qq_*.py` | QQ OneBot 部分 |
| `agent_console/daily_report.py` | HTML/PNG 渲染（名字沿用 v1，与任何控制台无关） |
| `scripts/` | demo、doctor、安装、发布构建、文档检查 |
| `deployment/services/` | systemd 和 procd 服务文件 |

## 修改约定

- 接口格式以 [CONTRACT.md](CONTRACT.md) 为准；三个仓库共用这份文件，要改格式先提 issue，升 schema 版本。
- 任何可能已经提交的发送都必须记 `unknown`，不能写成 `failed`；新增发送路径要有对应测试。
- 模型调用一律经过 `model_fallback.providers()`；只有 400/422、拒答和截断不切换。
- 新的配置字段要同时写进 `examples/`、[CONFIGURATION.md](CONFIGURATION.md) 和 `doctor`。
- 样本只能是虚构数据；改了 `scripts/generate-demo.py` 里的样本要 `--write-fixture examples/synthetic-export` 重新生成并提交，测试会核对两者一致。

## 构建发布包

```sh
./scripts/build-release.sh v2.0.0   # 需要工作区干净；输出 dist/chat-digest-2.0.0.tar.gz 和 dist/SHA256SUMS
```

同一个提交总是得到逐字节相同的压缩包。打 `v*` tag 推到 GitHub 后，`.github/workflows/release.yml` 调用同一个脚本构建，生成构建来源证明并创建 release；验证方法见 [INSTALL.md](INSTALL.md#1-下载并校验)。干净容器里的实测脚本是 `scripts/validate-clean.sh`，记录见 [VALIDATION.md](VALIDATION.md)。
