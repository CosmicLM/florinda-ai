#!/usr/bin/env bash
# install.sh — thin bootstrap for install.py.
#
# WHY this exists separately from install.py: everything past "is python3
# available" needs real logic (distro package-name mapping, interactive
# prompts, .env generation) that's much cleaner in Python — but python3
# itself might not be installed yet on a truly fresh machine, and bash is
# the one thing guaranteed to already be there. This script's only job is
# to get python3 installed if it's missing, then hand off to install.py for
# everything else.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$REPO_ROOT/install.py" "$@"
fi

echo "python3 not found — installing it first."
if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm python
elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3
else
    echo "No supported package manager found (pacman/apt-get/dnf)." >&2
    echo "Install python3 manually, then run: python3 install.py" >&2
    exit 1
fi

exec python3 "$REPO_ROOT/install.py" "$@"
