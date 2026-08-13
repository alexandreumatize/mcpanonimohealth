#!/usr/bin/env bash
set -euo pipefail

if command -v codex >/dev/null 2>&1; then
  codex mcp remove mcpanonimohealth >/dev/null 2>&1 || true
fi

if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user mcpanonimohealth >/dev/null 2>&1 || true
fi

remove_owned_skill() {
  local parent="$1"
  local target="${parent}/mcpanonimohealth"
  if [[ -d "$target" && "$(basename "$target")" == "mcpanonimohealth" ]]; then
    rm -rf -- "$target"
  fi
}

remove_owned_skill "${HOME}/.codex/skills"
remove_owned_skill "${HOME}/.claude/skills"

echo "mcpanonimohealth removido do Codex e do Claude Code."
echo "O repositório, o uv e todas as outras configurações foram preservados."
