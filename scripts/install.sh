#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="$(command -v uv || true)"

if [[ -z "$UV_BIN" ]]; then
  echo "Instalando o gerenciador local uv..."
  curl -LsSf https://astral.sh/uv/0.8.7/install.sh | sh
  UV_BIN="$([ -x "${HOME}/.local/bin/uv" ] && echo "${HOME}/.local/bin/uv" || command -v uv || true)"
fi

if [[ -z "$UV_BIN" ]]; then
  echo "Não foi possível localizar o uv após a instalação." >&2
  exit 1
fi

echo "Preparando o mcpanonimohealth..."
"$UV_BIN" sync --locked --project "$PROJECT_ROOT" --extra openmed
"$UV_BIN" run --project "$PROJECT_ROOT" python -m mcpanonimohealth.cli models install

install_skill() {
  local destination="$1"
  mkdir -p "$destination/agents"
  cp "$PROJECT_ROOT/skill/mcpanonimohealth/SKILL.md" "$destination/SKILL.md"
  cp "$PROJECT_ROOT/skill/mcpanonimohealth/agents/openai.yaml" "$destination/agents/openai.yaml"
}

install_skill "${HOME}/.codex/skills/mcpanonimohealth"
install_skill "${HOME}/.claude/skills/mcpanonimohealth"

SERVER_COMMAND=("$UV_BIN" run --project "$PROJECT_ROOT" python -m mcpanonimohealth.cli serve)

if command -v codex >/dev/null 2>&1; then
  codex mcp remove mcpanonimohealth >/dev/null 2>&1 || true
  codex mcp add mcpanonimohealth -- "${SERVER_COMMAND[@]}"
  echo "MCP registrado no Codex."
else
  echo "Codex não encontrado; a skill foi instalada e o registro do MCP foi ignorado."
fi

if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user mcpanonimohealth >/dev/null 2>&1 || true
  claude mcp add --scope user mcpanonimohealth -- "${SERVER_COMMAND[@]}"
  echo "MCP registrado no Claude Code."
else
  echo "Claude Code não encontrado; a skill foi instalada e o registro do MCP foi ignorado."
fi

"$UV_BIN" run --project "$PROJECT_ROOT" python -m mcpanonimohealth.cli doctor

echo
echo "Instalação concluída. Reinicie o Codex/Claude Code antes do primeiro uso."
echo "Depois diga: use o mcpanonimohealth; oriente-me a não anexar e abra a interface local."
echo "Nunca anexe nem cole dados de pacientes no chat."
