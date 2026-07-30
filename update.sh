#!/usr/bin/env bash
# update.sh — pulls the latest changes and re-runs the installer, for
# anyone who already has Florinda set up.
#
# WHY re-running install.sh instead of just `git pull`: several fixes have
# landed that touch REQUIRED SYSTEM PACKAGES (build toolchain, fd/ripgrep,
# Docker's apt repo, ...) — a plain `git pull` alone wouldn't install any
# newly-required package, only the code that now expects it. install.sh/
# install.py is safe to re-run on top of an existing install: every step
# either confirms before touching anything or is naturally idempotent
# (already-installed packages are skipped, the venv is reused not
# recreated, .env is left alone unless you explicitly agree to overwrite
# it) — see SETUP.md's "Updating an existing install" section.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: $REPO_ROOT isn't a git checkout — can't update. Re-clone the repo instead." >&2
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "You have uncommitted local changes in this checkout:"
    git status --short
    read -rp "Continue with 'git pull' anyway? [y/N] " reply
    case "$reply" in
        [yY]*) ;;
        *) echo "Aborted — commit, stash, or discard your changes first."; exit 1 ;;
    esac
fi

echo "Pulling latest changes..."
git pull --ff-only

echo
echo "Re-running the installer to pick up any newly-required system packages..."
exec ./install.sh "$@"
