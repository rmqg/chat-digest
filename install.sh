#!/bin/sh
# Friendly entry point; legacy installer options retain their exact behavior.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "${1:-}" in
    -h|--help)
        printf '%s\n' '首次安装：./install.sh（交互向导）' \
            '自动配置：./install.sh --config examples/install.env.example' \
            '默认模板：./install.sh --non-interactive' \
            '可与下方原有参数组合；仅给原有参数时直接调用兼容安装器。'
        exec "$HERE/scripts/install.sh" "$@" ;;
esac
WIZARD=no
[ "$#" -gt 0 ] || WIZARD=yes
for arg in "$@"; do
    case "$arg" in --config|--non-interactive) WIZARD=yes ;; esac
done
if [ "$WIZARD" = no ]; then
    exec "$HERE/scripts/install.sh" "$@"
fi
PY=python3
previous=
for arg in "$@"; do
    if [ "$previous" = --python ]; then PY=$arg; fi
    previous=$arg
done
if ! command -v "$PY" >/dev/null 2>&1; then
    printf '%s\n' '缺少 Python。Debian/Ubuntu 可复制：sudo apt-get update && sudo apt-get install -y python3 python3-venv' >&2
    exit 1
fi
exec "$PY" "$HERE/scripts/install-wizard.py" "$@"
