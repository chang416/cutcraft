#!/usr/bin/env bash
# Install CutCraft as a native Claude Code personal skill: /cutcraft.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/../skills/cutcraft" && pwd)"
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
TARGET_DIR="$CLAUDE_HOME/skills/cutcraft"

if [ -e "$TARGET_DIR" ] && [ ! -L "$TARGET_DIR" ]; then
  printf 'Refusing to replace existing non-symlink skill: %s\n' "$TARGET_DIR" >&2
  printf 'Move it aside or install CutCraft in a different Claude configuration directory.\n' >&2
  exit 1
fi

mkdir -p "$CLAUDE_HOME/skills"
ln -sfn "$SOURCE_DIR" "$TARGET_DIR"
printf 'CutCraft installed for Claude Code. Restart Claude Code if this is a new session, then type /cutcraft.\n'
