[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectDir = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$releaseDir = [IO.Path]::GetFullPath((Join-Path $projectDir "release"))
$composeFile = Join-Path $projectDir "compose.yaml"
$dockerfile = Join-Path $projectDir "Dockerfile"
$dockerignoreFile = Join-Path $projectDir ".dockerignore"
$imageName = "local/nonebot-qq:1.7.2"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Assert-ReleaseTarget {
    $expected = [IO.Path]::GetFullPath([IO.Path]::Combine($projectDir, "release"))
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetDirectoryName($releaseDir))
    $comparison = [StringComparison]::OrdinalIgnoreCase

    if (-not [string]::Equals($releaseDir, $expected, $comparison) -or
        -not [string]::Equals($parent, $projectDir, $comparison) -or
        [IO.Path]::GetFileName($releaseDir) -cne "release") {
        throw "Refusing to refresh anything except the project's exact release directory."
    }

    if (Test-Path -LiteralPath $releaseDir) {
        $item = Get-Item -Force -LiteralPath $releaseDir
        if (-not $item.PSIsContainer) {
            throw "Release target exists but is not a directory: $releaseDir"
        }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to replace a release directory that is a symlink or junction."
        }
        $resolved = [IO.Path]::GetFullPath($item.FullName)
        if (-not [string]::Equals($resolved, $expected, $comparison)) {
            throw "Resolved release directory is not the expected project release directory."
        }
    }
}

function Assert-StagingTarget {
    param([Parameter(Mandatory = $true)][string]$Target)

    $fullTarget = [IO.Path]::GetFullPath($Target)
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetDirectoryName($fullTarget))
    $name = [IO.Path]::GetFileName($fullTarget)
    $comparison = [StringComparison]::OrdinalIgnoreCase

    if (-not [string]::Equals($parent, $projectDir, $comparison) -or
        -not $name.StartsWith(".release-staging-", [StringComparison]::Ordinal)) {
        throw "Refusing to use a staging directory outside the project."
    }

    if (Test-Path -LiteralPath $fullTarget) {
        $item = Get-Item -Force -LiteralPath $fullTarget
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release staging target must be a normal directory inside the project."
        }
        if (-not [string]::Equals([IO.Path]::GetFullPath($item.FullName), $fullTarget, $comparison)) {
            throw "Resolved release staging directory does not match its expected path."
        }
    }
}

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

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $script:dockerExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE."
    }
}

$releaseFiles = @(
    [pscustomobject]@{ Source = ".env.example"; Destination = ".env.example" },
    [pscustomobject]@{ Source = "bot.cmd"; Destination = "bot.cmd" },
    [pscustomobject]@{ Source = "bot.ps1"; Destination = "bot.ps1" },
    [pscustomobject]@{ Source = "bot.sh"; Destination = "bot.sh" },
    [pscustomobject]@{ Source = "menu.cmd"; Destination = "menu.cmd" },
    [pscustomobject]@{ Source = "menu.ps1"; Destination = "menu.ps1" },
    [pscustomobject]@{ Source = "menu.sh"; Destination = "menu.sh" },
    [pscustomobject]@{ Source = "qq-menu.json"; Destination = "qq-menu.json" },
    [pscustomobject]@{ Source = "compose.runtime.yaml"; Destination = "compose.yaml" },
    [pscustomobject]@{ Source = "START.txt"; Destination = "START.txt" }
    [pscustomobject]@{ Source = "TOOLBOX.md"; Destination = "TOOLBOX.md" }
    [pscustomobject]@{ Source = "AI.md"; Destination = "AI.md" }
    [pscustomobject]@{ Source = "LEARNING_CHAT.md"; Destination = "LEARNING_CHAT.md" }
    [pscustomobject]@{ Source = "WEB_ADMIN.md"; Destination = "WEB_ADMIN.md" }
    [pscustomobject]@{ Source = "TRICKCAL.md"; Destination = "TRICKCAL.md" }
    [pscustomobject]@{ Source = "OPTIMIZATIONS.md"; Destination = "OPTIMIZATIONS.md" }
    [pscustomobject]@{ Source = "LICENSES\AGPL-3.0-only.txt"; Destination = "AGPL-3.0-only.txt" }
)

Assert-ReleaseTarget

foreach ($entry in $releaseFiles) {
    $sourcePath = Join-Path $projectDir $entry.Source
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required release source is missing: $($entry.Source)"
    }
    $sourceItem = Get-Item -Force -LiteralPath $sourcePath
    if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Required release source must not be a symlink: $($entry.Source)"
    }
}

foreach ($buildSource in @($composeFile, $dockerfile, $dockerignoreFile)) {
    if (-not (Test-Path -LiteralPath $buildSource -PathType Leaf)) {
        throw "Required build source is missing: $buildSource"
    }
}

$ignorePatterns = [IO.File]::ReadAllLines($dockerignoreFile) | ForEach-Object { $_.Trim() }
if ($ignorePatterns -notcontains ".env" -or $ignorePatterns -notcontains "release/") {
    throw ".dockerignore must exclude both .env and release/ before packaging."
}

$exampleContent = [IO.File]::ReadAllText((Join-Path $projectDir ".env.example"))
if ($exampleContent -match '(?m)^\s*(QQ_APP_SECRET|QQ_BOTS)\s*=') {
    throw ".env.example must not contain QQ bot credentials; configure them in the web admin."
}
$exampleContent = $null

$script:dockerExe = Resolve-DockerExe
Invoke-Docker -Arguments @("version", "--format", "{{.Server.Version}}")
Invoke-Docker -Arguments @("compose", "version")

Write-Host "Building $imageName ..."
Invoke-Docker -Arguments @(
    "build",
    "--file", $dockerfile,
    "--tag", $imageName,
    $projectDir
)

$stagingName = ".release-staging-$PID-$([Guid]::NewGuid().ToString('N'))"
$stagingDir = [IO.Path]::GetFullPath((Join-Path $projectDir $stagingName))
Assert-StagingTarget -Target $stagingDir
[void](New-Item -ItemType Directory -Path $stagingDir)

try {
    Assert-StagingTarget -Target $stagingDir
    foreach ($entry in $releaseFiles) {
        $sourcePath = Join-Path $projectDir $entry.Source
        $destinationPath = Join-Path $stagingDir $entry.Destination
        Copy-Item -Force -LiteralPath $sourcePath -Destination $destinationPath
    }

    $stagedArchive = Join-Path $stagingDir "nonebot-qq.tar"
    Write-Host "Saving $imageName for offline use ..."
    Invoke-Docker -Arguments @("save", "--output", $stagedArchive, $imageName)

    $imageIdOutput = & $script:dockerExe image inspect --format "{{.Id}}" $imageName
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the packaged image ID."
    }
    $imageId = (@($imageIdOutput) -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($imageId)) {
        throw "Docker returned an empty image ID."
    }
    [IO.File]::WriteAllText(
        (Join-Path $stagingDir "image-id.txt"),
        ($imageId + "`n"),
        $utf8NoBom
    )

    if (Test-Path -LiteralPath (Join-Path $stagingDir ".env")) {
        throw "Safety check failed: release must never contain .env."
    }
    $archiveSize = (Get-Item -LiteralPath $stagedArchive).Length
    if ($archiveSize -le 0) {
        throw "Docker produced an empty image archive."
    }

    # Use the built image's Python; publishers do not need Python or ZIP tools.
    Invoke-Docker -Arguments @(
        "run", "--rm", "--network", "none", "--read-only", "--user", "0:0",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--volume", "${stagingDir}:/package",
        "--volume", "${projectDir}/tools:/build:ro",
        "--entrypoint", "python", $imageName, "/build/pack_release.py", "/package"
    )

    Assert-ReleaseTarget
    if (Test-Path -LiteralPath $releaseDir) {
        Remove-Item -Force -Recurse -LiteralPath $releaseDir
    }
    Move-Item -LiteralPath $stagingDir -Destination $releaseDir
    $zipPath = Join-Path $projectDir "QQbot-one-click.zip"
    Move-Item -LiteralPath (Join-Path $releaseDir "QQbot-one-click.zip") -Destination $zipPath -Force
}
finally {
    if (Test-Path -LiteralPath $stagingDir) {
        Assert-StagingTarget -Target $stagingDir
        Remove-Item -Force -Recurse -LiteralPath $stagingDir
    }
}

Write-Host "Offline package created at $releaseDir ($archiveSize image bytes)." -ForegroundColor Green
Write-Host "Distribute: $zipPath" -ForegroundColor Green
