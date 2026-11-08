#!/bin/sh
# chat-digest installer. Default is a dry run that only prints the plan.
#   ./scripts/install.sh                 # show what would happen
#   ./scripts/install.sh --apply         # .venv + dependencies + tokenizer cache + doctor --demo-only
#   ./scripts/install.sh --apply --services systemd|procd   # also copy service files (root; not enabled/started)
set -eu

usage() {
    cat <<'USAGE'
usage: install.sh [--apply] [--python PATH] [--services none|systemd|procd] [--root PATH]
  --apply      perform the steps (default: dry run)
  --python     Python 3.11+ interpreter for the virtualenv (default: python3)
  --services   also install service definitions (default: none). Never enables or starts them.
  --root       staging filesystem root for service files (default: /)
USAGE
}

MODE=dry-run
PYTHON=python3
SERVICES=none
ROOT=/
while [ "$#" -gt 0 ]; do
    case "$1" in
        --apply) MODE=apply; shift ;;
        --dry-run) MODE=dry-run; shift ;;
        --python) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; PYTHON=$2; shift 2 ;;
        --services) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; SERVICES=$2; shift 2 ;;
        --root) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; ROOT=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$SERVICES" in none|systemd|procd) ;; *) printf 'unknown --services value: %s\n' "$SERVICES" >&2; exit 2 ;; esac
case "$ROOT" in /*) ;; *) printf '%s\n' '--root must be absolute' >&2; exit 2 ;; esac

PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
VENV=$PROJECT/.venv
CACHE=$PROJECT/.cache/tiktoken

printf 'chat-digest %s installer (%s)\n' "$(cat "$PROJECT/VERSION")" "$MODE"
printf '  project:    %s\n' "$PROJECT"
printf '  virtualenv: %s (python: %s)\n' "$VENV" "$PYTHON"
printf '  packages:   %s\n' "$PROJECT/requirements.txt"
printf '  tokenizer:  %s (downloaded once, then used offline)\n' "$CACHE"
printf '  services:   %s\n' "$SERVICES"
if [ "$MODE" != apply ]; then
    printf '%s\n' 'Dry run only. Re-run with --apply to perform these steps.'
    exit 0
fi

"$PYTHON" - <<'PY' || { printf '%s\n' 'Python 3.11 or newer is required (use --python /path/to/python3.11).' >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON" -m venv "$VENV" || { printf '%s\n' 'Could not create the virtualenv. On Debian/Ubuntu: apt install python3-venv' >&2; exit 1; }
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q -r "$PROJECT/requirements.txt"
mkdir -p "$CACHE"
TIKTOKEN_CACHE_DIR=$CACHE "$VENV/bin/python" -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
chmod -R a-w "$CACHE" 2>/dev/null || true
chmod 755 "$PROJECT/bin/chat-digest"

case "$SERVICES" in
    systemd)
        target=${ROOT%/}/etc/systemd/system
        mkdir -p "$target"
        for unit in "$PROJECT"/deployment/services/systemd/*; do
            dest=$target/$(basename "$unit")
            [ ! -L "$dest" ] || { printf 'refusing symlink destination: %s\n' "$dest" >&2; exit 1; }
            install -m 0644 "$unit" "$dest"
        done
        printf '%s\n' "systemd units copied to $target (not enabled)." \
            'They expect the project at /opt/chat-digest and a "chat-digest" user; see docs/INSTALL.md.' ;;
    procd)
        target=${ROOT%/}/etc/init.d
        mkdir -p "$target"
        for service in "$PROJECT"/deployment/services/procd/*.initd; do
            dest=$target/$(basename "$service" .initd)
            [ ! -L "$dest" ] || { printf 'refusing symlink destination: %s\n' "$dest" >&2; exit 1; }
            install -m 0755 "$service" "$dest"
        done
        printf '%s\n' "procd services copied to $target (not enabled). Add the cron lines from docs/INSTALL.md." ;;
esac

"$PROJECT/bin/chat-digest" doctor --demo-only
printf '%s\n' 'Installed. Try the offline demo:  ./bin/chat-digest demo --output ./chat-digest-demo'
