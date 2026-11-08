# 第三方声明与发布边界

仓库根目录的 [MIT 许可证](LICENSE) 只覆盖本项目的原创代码，不替代其他作者的版权和许可证。重新分发下面列出的文件时，请保留对应声明。

## 随仓库分发的第三方文件

| 路径 | 来源与固定版本 | 许可证 | 保留的声明 |
|---|---|---|---|
| `assets/fonts/ResourceHanRoundedCN-*.ttf`、`*.woff2` | [Resource Han Rounded v0.990](https://github.com/CyanoHao/Resource-Han-Rounded/releases/tag/v0.990) | SIL Open Font License 1.1 | `assets/fonts/OFL-License.txt` |

字体的版权声明和保留字体名称限制只适用于字体文件本身，字体不因放在本仓库而改用 MIT。用这套字体渲染出来的报告不受 OFL 约束，但单独再分发字体文件时必须带上 OFL 声明。

v2.0.0 起，仓库里**不再包含**任何借用的源码文件。v1.x 里随附的 `tools/wcdb-key-tool`（TANGandXUE/wcdb-key-tool，MIT）、`deployment/vendor/wxextract`（boujuan/extract-wechat-messages-linux，MIT）以及改编自后者的 DAT 解码部分，都随微信提取功能一起移出了本仓库；它们的声明保留在 v1.x 的历史版本和 release 里。

## 运行时依赖（不随仓库分发，由 pip 或 Docker 在你的机器上安装）

| 依赖 | 用途 | 许可证 |
|---|---|---|
| Python 3.11+ | 运行环境 | PSF License |
| Pillow 11.3.0 | 图片读写、PNG 报告渲染 | MIT-CMU（HPND） |
| pillow-heif 1.8.1 | 读取 HEIC 图片 | BSD-3-Clause；它的二进制 wheel 内含 libheif、libde265（LGPL-3.0）和 x265（GPL-2.0），这些库保留各自许可证 |
| tiktoken 0.14.0 | 发言排行的 token 计数 | MIT |
| tiktoken 的传递依赖 regex、requests、urllib3、certifi、idna、charset-normalizer | — | 分别为 Apache-2.0 与 CNRI-Python、Apache-2.0、MIT、MPL-2.0、BSD-3-Clause、MIT |
| websockets 16.0 | QQ OneBot v11 连接 | BSD-3-Clause |
| o200k_base 分词数据 | tiktoken 首次使用时从 OpenAI 公开地址下载，缓存后离线使用 | 由 OpenAI 通过 tiktoken 公开提供；本仓库不分发该文件 |
| 可选 OCR：`deployment/ocr-requirements.txt` 与 `ocr-wheel-lock.json` 里固定的 rapidocr-onnxruntime 及其依赖 | 图片文字识别 | 各自上游许可证（rapidocr-onnxruntime 为 Apache-2.0，onnxruntime 为 MIT，opencv 为 Apache-2.0 等），模型文件许可证由上游决定 |
| Docker 基础镜像 `python:3.12-slim` 与 Debian 软件包（procps、libgl1、libglib2.0-0、fontconfig、fonts-noto-cjk） | 隔离的运行时镜像 | 各自许可证；镜像由你在本地构建，本项目不发布镜像 |

安装或构建这些依赖不会把它们改成 MIT 许可。

## 只是对接、不包含的外部项目

- 聊天记录来自任何能产出 [chat-export/v1](docs/CONTRACT.md) 目录的导出工具，例如 [rmqg/wechat-export-send](https://github.com/rmqg/wechat-export-send)（微信）。本仓库不包含它们的代码，只读它们的导出。格式里的 `wecom` 等平台取值，可以由其他兼容的导出工具提供。
- 发送通过外部命令完成，命令需要实现 [chat-send/v1](docs/CONTRACT.md)，例如 wechat-export-send 提供的 `wechat-send`。
- QQ 群总结对接你自己部署的 OneBot v11 实现（例如 NapCat），本仓库不包含它们。
- 微信（WeChat）、企业微信、QQ 是腾讯的产品和商标；本项目与腾讯无关，使用官方客户端须遵守其条款。

## 明确排除的材料

早期私有工作树里有来自 [xiaoguiwucan/linux-wechat-agent](https://github.com/xiaoguiwucan/linux-wechat-agent) 的代码和文档，以及来源同样没有许可证的 `tools/wechat-decrypt`。这些材料没有可核实的再分发许可，从来没有进入公开仓库，v2.0.0 也没有参考或收录它们。

## 合成样本

`examples/synthetic-export/` 和 `scripts/generate-demo.py` 生成的全部人物、消息、头像和图片都是原创的虚构内容，不是对真实聊天做的替换或改写。不要把真实导出放进这个目录再提交。
