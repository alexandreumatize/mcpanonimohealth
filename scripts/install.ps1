$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not $Uv) {
    Write-Host "Instalando o gerenciador local uv..."
    Invoke-RestMethod https://astral.sh/uv/0.8.7/install.ps1 | Invoke-Expression
    $LocalUv = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".local\bin\uv.exe"
    if (Test-Path $LocalUv) {
        $UvPath = $LocalUv
    } else {
        $Uv = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $Uv) { throw "Não foi possível localizar o uv após a instalação." }
        $UvPath = $Uv.Source
    }
} else {
    $UvPath = $Uv.Source
}

Write-Host "Preparando o mcpanonimohealth..."
& $UvPath sync --locked --project $ProjectRoot --extra openmed
& $UvPath run --project $ProjectRoot python -m mcpanonimohealth.cli models install

function Install-McpAnonimoSkill([string]$Destination) {
    $Agents = Join-Path $Destination "agents"
    New-Item -ItemType Directory -Force -Path $Agents | Out-Null
    Copy-Item (Join-Path $ProjectRoot "skill\mcpanonimohealth\SKILL.md") (Join-Path $Destination "SKILL.md") -Force
    Copy-Item (Join-Path $ProjectRoot "skill\mcpanonimohealth\agents\openai.yaml") (Join-Path $Agents "openai.yaml") -Force
}

$UserProfilePath = [Environment]::GetFolderPath("UserProfile")
Install-McpAnonimoSkill (Join-Path $UserProfilePath ".codex\skills\mcpanonimohealth")
Install-McpAnonimoSkill (Join-Path $UserProfilePath ".claude\skills\mcpanonimohealth")

$Codex = Get-Command codex -ErrorAction SilentlyContinue
if ($Codex) {
    & $Codex.Source mcp remove mcpanonimohealth 2>$null
    & $Codex.Source mcp add mcpanonimohealth -- $UvPath run --project $ProjectRoot python -m mcpanonimohealth.cli serve
    Write-Host "MCP registrado no Codex."
} else {
    Write-Host "Codex não encontrado; a skill foi instalada e o registro do MCP foi ignorado."
}

$Claude = Get-Command claude -ErrorAction SilentlyContinue
if ($Claude) {
    & $Claude.Source mcp remove --scope user mcpanonimohealth 2>$null
    & $Claude.Source mcp add --scope user mcpanonimohealth -- $UvPath run --project $ProjectRoot python -m mcpanonimohealth.cli serve
    Write-Host "MCP registrado no Claude Code."
} else {
    Write-Host "Claude Code não encontrado; a skill foi instalada e o registro do MCP foi ignorado."
}

& $UvPath run --project $ProjectRoot python -m mcpanonimohealth.cli doctor

Write-Host ""
Write-Host "Instalação concluída. Reinicie o Codex/Claude Code antes do primeiro uso."
Write-Host "Depois diga: use o mcpanonimohealth para abrir a interface local e desidentificar meu documento."
Write-Host "Nunca anexe nem cole dados de pacientes no chat."
