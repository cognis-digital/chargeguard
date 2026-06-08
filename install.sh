#!/usr/bin/env sh
# Universal installer for chargeguard. Prefers uv > pipx > pip; installs from the repo.
set -e
SRC="git+https://github.com/cognis-digital/chargeguard.git"
echo "Installing chargeguard ..."
if command -v uv >/dev/null 2>&1; then uv tool install "$SRC"
elif command -v pipx >/dev/null 2>&1; then pipx install "$SRC"
elif command -v python3 >/dev/null 2>&1; then python3 -m pip install --user "$SRC"
else echo "Need uv, pipx, or python3+pip"; exit 1; fi
echo "Done. Run: chargeguard --help"
