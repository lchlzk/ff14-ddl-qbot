[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("sync", "status", "help")]
    [string]$Action = "help",

    [switch]$ForceMenu,

    [string]$BotId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeFile = Join-Path $projectDir "compose.yaml"

function Resolve-DockerExe {
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($null -ne $dockerCommand) {
        return $dockerCommand.Source
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $userInstall = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
        if (Test-Path -LiteralPath $userInstall) {
            return $userInstall
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $systemInstall = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
        if (Test-Path -LiteralPath $systemInstall) {
            return $systemInstall
        }
    }

    throw "docker.exe was not found. Start Docker Desktop or add Docker to PATH."
}

function Show-Help {
    @"
QQ menu and command-panel manager

Usage:
  .\menu.cmd status
  .\menu.cmd sync
  .\menu.cmd sync -ForceMenu

Edit qq-menu.json to add commands. The script creates or updates the managed
C2C/group/channel command panels. It refuses to replace a different existing C2C
custom menu unless -ForceMenu is supplied.
"@ | Write-Host
}

if ($Action -eq "help") {
    Show-Help
    return
}

if ($Action -eq "status" -and $ForceMenu) {
    throw "-ForceMenu is only valid with the sync action."
}

$dockerExe = Resolve-DockerExe
$arguments = @(
    "compose",
    "--project-directory", $projectDir,
    "--file", $composeFile,
    "run", "--rm", "--no-deps",
    "qqbot", "python", "qq_menu.py", $Action
)
if ($ForceMenu) {
    $arguments += "--force-menu"
}
if (-not [string]::IsNullOrWhiteSpace($BotId)) {
    $arguments += @("--bot-id", $BotId)
}

& $dockerExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "QQ menu command failed with exit code $LASTEXITCODE."
}
