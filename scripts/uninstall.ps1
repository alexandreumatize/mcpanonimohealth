$ErrorActionPreference = "Continue"

$Codex = Get-Command codex -ErrorAction SilentlyContinue
if ($Codex) { & $Codex.Source mcp remove mcpanonimohealth 2>$null }

$Claude = Get-Command claude -ErrorAction SilentlyContinue
if ($Claude) { & $Claude.Source mcp remove --scope user mcpanonimohealth 2>$null }

$UserProfilePath = [Environment]::GetFolderPath("UserProfile")
$OwnedSkillPaths = @(
    (Join-Path $UserProfilePath ".codex\skills\mcpanonimohealth"),
    (Join-Path $UserProfilePath ".claude\skills\mcpanonimohealth")
)
foreach ($SkillPath in $OwnedSkillPaths) {
    if ((Split-Path $SkillPath -Leaf) -eq "mcpanonimohealth" -and (Test-Path $SkillPath)) {
        Remove-Item $SkillPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "mcpanonimohealth removido do Codex e do Claude Code."
Write-Host "O repositório, o uv e todas as outras configurações foram preservados."
