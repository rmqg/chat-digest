#!/usr/bin/env python3
"""Print the CHANGELOG section of one version as release notes."""
import re
import sys
from pathlib import Path

version = sys.argv[1].removeprefix("v")
text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
match = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
if not match:
    raise SystemExit(f"CHANGELOG.md has no section for {version}")
print(match.group(1).strip())
readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
support = re.search(r"^## 支持情况摘要\n(.*?)(?=^## )", readme, re.M | re.S)
if support:
    print("\n## 支持情况（实测通过 / 未测试）\n" + support.group(1).strip())
print("\n验证下载：`sha256sum -c SHA256SUMS`，`gh attestation verify chat-digest-%s.tar.gz --repo rmqg/chat-digest`" % version)
