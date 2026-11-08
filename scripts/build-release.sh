#!/bin/sh
# Build the release archive and SHA256SUMS from a committed tree. CI calls exactly this script.
#   ./scripts/build-release.sh            # builds dist/chat-digest-<VERSION>.tar.gz from HEAD
#   ./scripts/build-release.sh v2.0.0     # also checks that the tag matches VERSION
set -eu
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$PROJECT"
VERSION=$(cat VERSION)
if [ "$#" -ge 1 ] && [ "$1" != "v$VERSION" ]; then
    printf 'tag %s does not match VERSION %s\n' "$1" "$VERSION" >&2
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    printf '%s\n' 'refusing to build: tracked files have uncommitted changes' >&2
    exit 1
fi
NAME=chat-digest-$VERSION
mkdir -p dist
rm -f "dist/$NAME.tar.gz" dist/SHA256SUMS
# git archive uses the commit time for every entry, and Python's gzip with mtime=0 gives the same bytes on
# Linux and macOS (system gzip implementations differ), so one commit always yields one archive.
PYTHON=${PYTHON:-python3}
git archive --format=tar --prefix="$NAME/" HEAD | "$PYTHON" -c 'import gzip, sys; sys.stdout.buffer.write(gzip.compress(sys.stdin.buffer.read(), 9, mtime=0))' > "dist/$NAME.tar.gz"
(cd dist && "$PYTHON" -c 'import hashlib, sys; name = sys.argv[1]; print(hashlib.sha256(open(name, "rb").read()).hexdigest() + "  " + name)' "$NAME.tar.gz" > SHA256SUMS)
cat dist/SHA256SUMS
