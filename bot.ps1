[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "otter-setup", "fflogs-setup", "start", "restart", "build", "stop", "status", "logs", "admin", "web", "help")]
    [string]$Action = "help",

    [switch]$Force,
    [Parameter(Position = 1)][string]$AdminAction = "list",
    [Parameter(Position = 2)][string]$IdentityCode = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeFile = Join-Path $projectDir "compose.yaml"
$environmentFile = Join-Path $projectDir ".env"
$environmentExample = Join-Path $projectDir ".env.example"
$imageName = "local/nonebot-qq:1.7.2"
$imageArchive = Join-Path $projectDir "nonebot-qq.tar"
$expectedImageIdFile = Join-Path $projectDir "image-id.txt"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

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

    throw "docker.exe was not found. Install or start Docker Desktop first."
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $script:dockerExe compose --project-directory $projectDir --file $composeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Read-HiddenText {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    $secureValue = Read-Host $Prompt -AsSecureString
    $valuePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($valuePointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($valuePointer)
    }
}

function Get-LocalImageId {
    # A missing image is expected on a fresh machine. Windows PowerShell 5.1
    # turns redirected native stderr into a terminating error under Stop, so
    # probing with `image inspect ... 2>$null` aborts before docker load.
    # `image ls` instead succeeds with empty stdout when the tag is absent.
    $imageIdOutput = & $script:dockerExe image ls --no-trunc --quiet $imageName
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query Docker images. Check that Docker Desktop is running."
    }
    return (@($imageIdOutput) -join "`n").Trim()
}

function Import-PackagedImageIfNeeded {
    $currentImageId = Get-LocalImageId
    $archiveExists = Test-Path -LiteralPath $imageArchive -PathType Leaf
    $manifestExists = Test-Path -LiteralPath $expectedImageIdFile -PathType Leaf
    if (-not $archiveExists -and -not $manifestExists) {
        # Source checkouts may build through compose; offline installs must
        # contain the archive if no usable local image is available.
        if ([string]::IsNullOrWhiteSpace($currentImageId) -and
            -not (Test-Path -LiteralPath (Join-Path $projectDir "Dockerfile") -PathType Leaf)) {
            throw "nonebot-qq.tar is missing. Extract the complete offline package into this folder, then run bot.cmd start again."
        }
        return
    }

    $expectedImageId = ""
    if ($manifestExists) {
        $expectedImageId = [IO.File]::ReadAllText($expectedImageIdFile).Trim()
        if ($expectedImageId -notmatch '^sha256:[a-fA-F0-9]{64}$') {
            throw "image-id.txt is invalid. Extract the complete offline package again."
        }
    }
    $needsImport = [string]::IsNullOrWhiteSpace($currentImageId)
    if (-not [string]::IsNullOrWhiteSpace($expectedImageId) -and
        $currentImageId -ne $expectedImageId) {
        $needsImport = $true
    }
    if (-not $needsImport) {
        return
    }
    if (-not $archiveExists) {
        throw "nonebot-qq.tar is missing and the installed image does not match this package. Extract the complete offline package again."
    }

    Write-Host "Importing the packaged Docker image (first install or package update)..." -ForegroundColor Yellow
    & $script:dockerExe load --input $imageArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to import $imageArchive."
    }
    $loadedImageId = Get-LocalImageId
    if ([string]::IsNullOrWhiteSpace($loadedImageId) -or
        (-not [string]::IsNullOrWhiteSpace($expectedImageId) -and $loadedImageId -ne $expectedImageId)) {
        throw "The imported image does not match this package. Extract the complete offline package again."
    }
}

function Initialize-Environment {
    param([switch]$Overwrite)

    if ((Test-Path -LiteralPath $environmentFile) -and -not $Overwrite) {
        Write-Host ".env already exists; it was not changed." -ForegroundColor Yellow
        return
    }

    $template = [System.IO.File]::ReadAllText($environmentExample)
    $temporaryFile = Join-Path $projectDir (".env." + [IO.Path]::GetRandomFileName() + ".tmp")
    try {
        [System.IO.File]::WriteAllText($temporaryFile, $template, $utf8NoBom)
        Move-Item -Force -LiteralPath $temporaryFile -Destination $environmentFile
    }
    finally {
        if (Test-Path -LiteralPath $temporaryFile) {
            Remove-Item -Force -LiteralPath $temporaryFile
        }
    }
    Write-Host ".env created without QQ credentials." -ForegroundColor Green
    Write-Host "Start the service, run '.\bot.cmd web account', then add one or more bots in http://127.0.0.1:8080/admin." -ForegroundColor Cyan
}

function Initialize-OtterEnvironment {
    if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
        throw ".env does not exist. Run '.\bot.cmd setup' first."
    }

    $personalQq = (Read-Host "Personal numeric QQ used for the Otter API token").Trim()
    if ($personalQq -notmatch '^[0-9]{5,20}$') {
        throw "The Otter API QQ must be your personal numeric QQ number."
    }

    $otterToken = Read-HiddenText -Prompt "Otter API Token (input is hidden)"
    if ($otterToken -notmatch '^[A-Za-z0-9._~-]{1,16}$') {
        throw "Use a 1-16 character Otter API Token containing only letters, digits, '.', '_', '~', or '-'. A random 12-16 character token is recommended."
    }

    $lines = [System.IO.File]::ReadAllLines($environmentFile)
    $updatedLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($line -notmatch '^\s*OTTER_API_(QQ|TOKEN|BASE)\s*=' -and $line -notmatch '^\s*OTTER_INCLUDE_URLS\s*=') {
            [void]$updatedLines.Add($line)
        }
    }
    while ($updatedLines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($updatedLines[$updatedLines.Count - 1])) {
        $updatedLines.RemoveAt($updatedLines.Count - 1)
    }
    [void]$updatedLines.Add("")
    [void]$updatedLines.Add("# Optional FFXIV queries powered by OtterBot WebAPI.")
    [void]$updatedLines.Add("OTTER_API_QQ=$personalQq")
    [void]$updatedLines.Add("OTTER_API_TOKEN=$otterToken")
    [void]$updatedLines.Add("OTTER_API_BASE=https://xn--v9x.net/api/")
    [void]$updatedLines.Add("OTTER_INCLUDE_URLS=false")

    $temporaryFile = Join-Path $projectDir (".env." + [IO.Path]::GetRandomFileName() + ".tmp")
    try {
        $content = ($updatedLines -join [Environment]::NewLine) + [Environment]::NewLine
        [System.IO.File]::WriteAllText($temporaryFile, $content, $utf8NoBom)
        Move-Item -Force -LiteralPath $temporaryFile -Destination $environmentFile
    }
    finally {
        if (Test-Path -LiteralPath $temporaryFile) {
            Remove-Item -Force -LiteralPath $temporaryFile
        }
        $otterToken = $null
    }
    Write-Host "OtterBot WebAPI settings saved. Run '.\bot.cmd start' to load them." -ForegroundColor Green
}

function Initialize-FFLogsEnvironment {
    if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
        throw ".env does not exist. Run '.\bot.cmd setup' first."
    }

    $clientId = (Read-Host "FF Logs Client ID").Trim()
    if ($clientId -notmatch '^[A-Za-z0-9._~-]{3,256}$') {
        throw "FF Logs Client ID is empty or contains unsupported characters."
    }

    $clientSecret = Read-HiddenText -Prompt "FF Logs Client Secret (input is hidden)"
    if ($clientSecret -notmatch '^[A-Za-z0-9._~-]{3,512}$') {
        throw "FF Logs Client Secret is empty or contains unsupported characters."
    }

    $defaultRegion = (Read-Host "Default FF Logs region [CN]").Trim().ToUpperInvariant()
    if ([string]::IsNullOrWhiteSpace($defaultRegion)) {
        $defaultRegion = "CN"
    }
    if ($defaultRegion -notin @("CN", "JP", "NA", "EU", "KR", "OC")) {
        throw "Region must be a short code such as CN, JP, NA, EU, KR, or OC."
    }

    $defaultZone = (Read-Host "Default FF Logs zone ID [automatic]").Trim()
    if (-not [string]::IsNullOrWhiteSpace($defaultZone)) {
        $parsedZone = 0
        if (-not [int]::TryParse($defaultZone, [ref]$parsedZone) -or $parsedZone -lt 1 -or $parsedZone -gt 100000) {
            throw "Zone ID must be blank or an integer from 1 to 100000."
        }
    }

    $lines = [System.IO.File]::ReadAllLines($environmentFile)
    $updatedLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($line -notmatch '^\s*FFLOGS_(CLIENT_ID|CLIENT_SECRET|DEFAULT_REGION|DEFAULT_ZONE_ID|TIMEOUT|CACHE_TTL|GLOBAL_INTERVAL)\s*=') {
            [void]$updatedLines.Add($line)
        }
    }
    while ($updatedLines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($updatedLines[$updatedLines.Count - 1])) {
        $updatedLines.RemoveAt($updatedLines.Count - 1)
    }
    [void]$updatedLines.Add("")
    [void]$updatedLines.Add("# Optional modern DPS and raid queries powered by the official FF Logs API v2.")
    [void]$updatedLines.Add("FFLOGS_CLIENT_ID=$clientId")
    [void]$updatedLines.Add("FFLOGS_CLIENT_SECRET=$clientSecret")
    [void]$updatedLines.Add("FFLOGS_DEFAULT_REGION=$defaultRegion")
    [void]$updatedLines.Add("FFLOGS_DEFAULT_ZONE_ID=$defaultZone")
    [void]$updatedLines.Add("FFLOGS_TIMEOUT=18")
    [void]$updatedLines.Add("FFLOGS_CACHE_TTL=600")
    [void]$updatedLines.Add("FFLOGS_GLOBAL_INTERVAL=0.5")

    $temporaryFile = Join-Path $projectDir (".env." + [IO.Path]::GetRandomFileName() + ".tmp")
    try {
        $content = ($updatedLines -join [Environment]::NewLine) + [Environment]::NewLine
        [System.IO.File]::WriteAllText($temporaryFile, $content, $utf8NoBom)
        Move-Item -Force -LiteralPath $temporaryFile -Destination $environmentFile
    }
    finally {
        if (Test-Path -LiteralPath $temporaryFile) {
            Remove-Item -Force -LiteralPath $temporaryFile
        }
        $clientSecret = $null
    }
    Write-Host "FF Logs API settings saved. Run '.\bot.cmd start' to load them." -ForegroundColor Green
}

function Wait-BotHealthy {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $idOutput = & $script:dockerExe compose --project-directory $projectDir --file $composeFile ps --all --quiet qqbot
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to query the qqbot container."
        }

        $containerId = (@($idOutput) -join "`n").Trim()
        if (-not [string]::IsNullOrWhiteSpace($containerId)) {
            $stateOutput = & $script:dockerExe inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" $containerId
            if ($LASTEXITCODE -eq 0) {
                $state = (@($stateOutput) -join "`n").Trim()
                $parts = $state -split "\|", 2
                if ($parts[0] -eq "running" -and $parts[1] -in @("healthy", "none")) {
                    Write-Host "QQ bot is running and healthy." -ForegroundColor Green
                    return
                }
                if ($parts[0] -in @("restarting", "exited", "dead") -or $parts[1] -eq "unhealthy") {
                    break
                }
            }
        }
        Start-Sleep -Seconds 2
    }

    & $script:dockerExe compose --project-directory $projectDir --file $composeFile logs --no-color --tail 100 qqbot
    throw "QQ bot did not become healthy. The last 100 log lines are shown above."
}

function Show-Help {
    @"
NoneBot QQ one-click manager

Usage:
  .\bot.cmd setup          Create a .env without QQ credentials
  .\bot.cmd otter-setup    Securely add OtterBot FFXIV WebAPI credentials
  .\bot.cmd fflogs-setup   Securely add FF Logs API v2 credentials
  .\bot.cmd start          Start, or reload a changed .env
  .\bot.cmd restart        Same safe recreate operation as start
  .\bot.cmd build          Rebuild the image, then start it
  .\bot.cmd status         Show container status
  .\bot.cmd logs           Follow bot logs (Ctrl+C to stop following)
  .\bot.cmd stop           Stop and remove the bot container
  .\bot.cmd admin token    本机生成一次性授权码 / Create one-time private invitation
  .\bot.cmd admin add CODE 确认私聊身份，绑定总管理员 / Confirm verified private identity
  .\bot.cmd admin remove CODE 撤销权限 / Revoke identity
  .\bot.cmd admin list     查看管理员 / List authorized identities
  .\bot.cmd web account    设置或重置网页后台账号密码 / Set or reset web account
  .\bot.cmd web status     查看网页会话数量 / Show active web sessions
  .\bot.cmd web revoke-all 撤销全部网页会话 / Revoke every web session

The .cmd launcher works when direct PowerShell script execution is blocked.
QQ AppID/AppSecret are encrypted and managed in the web administration page.
Use '.\bot.cmd setup -Force' only when you intend to replace an existing .env.
"@ | Write-Host
}

if ($Action -eq "help") {
    Show-Help
    return
}

if ($Action -eq "setup") {
    Initialize-Environment -Overwrite:$Force
    return
}

if ($Action -eq "otter-setup") {
    if (-not (Test-Path -LiteralPath $environmentFile)) {
        Write-Host "No .env found; starting the QQ setup wizard first." -ForegroundColor Yellow
        Initialize-Environment
    }
    Initialize-OtterEnvironment
    return
}

if ($Action -eq "fflogs-setup") {
    if (-not (Test-Path -LiteralPath $environmentFile)) {
        Write-Host "No .env found; starting the QQ setup wizard first." -ForegroundColor Yellow
        Initialize-Environment
    }
    Initialize-FFLogsEnvironment
    return
}

if (-not (Test-Path -LiteralPath $environmentFile)) {
    Write-Host "No .env found; starting the setup wizard." -ForegroundColor Yellow
    Initialize-Environment
}

$script:dockerExe = Resolve-DockerExe
& $script:dockerExe version --format "{{.Server.Version}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start it and try again."
}
& $script:dockerExe compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The Docker Compose plugin is not available."
}

switch ($Action) {
    "admin" {
        if ($AdminAction -notin @("token", "add", "remove", "list")) { throw "Use admin token/add/remove/list." }
        $adminArguments = @("exec", "-T", "qqbot", "python", "-m", "bot_tools.admin", $AdminAction)
        if ($AdminAction -in @("add", "remove")) {
            if ($IdentityCode -notmatch '^[a-fA-F0-9]{16}$') { throw "Use the 16-character identity code. Bind: admin token -> private /bot whoami TOKEN -> admin add CODE." }
            $adminArguments += $IdentityCode
        } elseif ($IdentityCode) {
            throw "This action takes no identity code."
        }
        Invoke-Compose -Arguments $adminArguments
    }
    "web" {
        $webAction = if ($AdminAction -eq "list") { "status" } else { $AdminAction }
        if ($webAction -notin @("account", "status", "revoke-all")) { throw "Use web account/status/revoke-all." }
        if ($IdentityCode) { throw "Web administration actions do not accept extra arguments." }
        if ($webAction -eq "account") {
            $webUsername = (Read-Host "Web admin username / 网页后台用户名").Trim()
            $webPassword = Read-HiddenText -Prompt "Web admin password (10-128 chars) / 网页后台密码（10-128 位）"
            $webConfirmation = Read-HiddenText -Prompt "Confirm password / 再输入一次密码"
            if ($webPassword -cne $webConfirmation) { throw "The two passwords do not match / 两次输入的密码不一致。" }
            $previousOutputEncoding = $OutputEncoding
            try {
                $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
                "$webUsername`n$webPassword" | & $script:dockerExe compose --project-directory $projectDir --file $composeFile exec -T qqbot python -m bot_tools.web_admin account
                if ($LASTEXITCODE -ne 0) { throw "Unable to set the web administrator account." }
            }
            finally {
                $OutputEncoding = $previousOutputEncoding
                $webPassword = ""
                $webConfirmation = ""
            }
        }
        else {
            Invoke-Compose -Arguments @("exec", "-T", "qqbot", "python", "-m", "bot_tools.web_admin", $webAction)
        }
    }
    { $_ -in @("start", "restart") } {
        Import-PackagedImageIfNeeded
        Invoke-Compose -Arguments @("up", "--detach", "--force-recreate", "qqbot")
        Wait-BotHealthy
    }
    "build" {
        Invoke-Compose -Arguments @("build", "qqbot")
        Invoke-Compose -Arguments @("up", "--detach", "--force-recreate", "qqbot")
        Wait-BotHealthy
    }
    "stop" {
        Invoke-Compose -Arguments @("down")
    }
    "status" {
        Invoke-Compose -Arguments @("ps")
    }
    "logs" {
        & $script:dockerExe compose --project-directory $projectDir --file $composeFile logs --follow --tail 100 qqbot
    }
}
