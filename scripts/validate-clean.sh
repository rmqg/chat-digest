#!/bin/sh
# Run inside a fresh debian:12 or ubuntu:24.04 container, with the release directory mounted at /release.
# Follows docs/INSTALL.md step by step and prints every command. Nothing is sent anywhere.
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv ca-certificates >/dev/null
uname -m
python3 --version
. /etc/os-release && echo "$PRETTY_NAME"
mkdir -p /opt && cd /opt
cp /release/chat-digest-*.tar.gz /release/SHA256SUMS .
sha256sum -c SHA256SUMS
tar xzf chat-digest-*.tar.gz
mv chat-digest-*/ chat-digest
cd chat-digest
./scripts/install.sh
./scripts/install.sh --apply
./bin/chat-digest version
./bin/chat-digest demo --output /tmp/chat-digest-demo
test -s /tmp/chat-digest-demo/reports/chat-digest-demo-20261028-0800-brief.png
./bin/chat-digest demo --qq --output /tmp/qq-digest-demo
./bin/chat-digest doctor --demo-only
# Full doctor against the bundled export and the offline demo sender (no Docker in this container).
./bin/chat-digest init
python3 - <<'PY'
import json, pathlib
root = pathlib.Path('/opt/chat-digest')
project = json.loads((root / 'runtime/project.json').read_text())
project['export']['root'] = str(root / 'examples/synthetic-export')
project['group'] = {'conversation_id': 'g_demo_garden', 'name': '合成示例群（虚构）'}
project['send'] = {'command': [str(root / '.venv/bin/python'), str(root / 'scripts/demo-chat-send.py'), '--outbox', '/tmp/outbox'],
                   'to': '合成示例群（虚构）', 'timeout': 60}
(root / 'runtime/project.json').write_text(json.dumps(project, ensure_ascii=False))
PY
./bin/chat-digest list-conversations --export-root examples/synthetic-export
./bin/chat-digest check-export --export-root examples/synthetic-export check --conversation-id g_demo_garden
./bin/chat-digest doctor --no-docker --probe-send || true
echo VALIDATION-OK
