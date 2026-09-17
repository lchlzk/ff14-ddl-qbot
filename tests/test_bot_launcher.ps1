[CmdletBinding()]
param()

# Run using Windows PowerShell 5.1, matching bot.cmd on a fresh Windows install.
# Compile a native docker.exe double; no daemon, real images or .env are touched.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$testRepo = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$testRoot = Join-Path $testRepo ('.codex-tmp-launcher-' + [Guid]::NewGuid().ToString('N'))
$testEnvNames = @('QQBOT_TEST_DOCKER_LOG','QQBOT_TEST_DOCKER_STATE','QQBOT_TEST_DOCKER_MODE','QQBOT_TEST_DOCKER_LOAD_ID')
$testSavedEnv = @{}
foreach ($name in $testEnvNames) { $testSavedEnv[$name] = [Environment]::GetEnvironmentVariable($name) }
$testSavedPath = $env:PATH
$testEncoding = New-Object Text.UTF8Encoding($false)
$testExpected = 'sha256:' + ('a' * 64)
$testOld = 'sha256:' + ('b' * 64)

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-LauncherProcess([string]$ScriptPath, [string]$WorkingDirectory) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $info.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $ScriptPath + '" start'
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $outTask = $process.StandardOutput.ReadToEndAsync()
        $errTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            throw 'Launcher fixture timed out.'
        }
        return @{ ExitCode = $process.ExitCode; Output = $outTask.Result + $errTask.Result }
    } finally { $process.Dispose() }
}

try {
    $testBin = Join-Path $testRoot 'bin'
    [void](New-Item -ItemType Directory -Path $testBin -Force)
    Add-Type -Path (Join-Path $PSScriptRoot 'fixtures\MockDocker.cs') -OutputAssembly (Join-Path $testBin 'docker.exe') -OutputType ConsoleApplication
    $env:PATH = $testBin + ';' + $testSavedPath
    $cases = @(
        @{ Name='fresh'; Current=''; Manifest=$testExpected; Archive=$true; Code=0; Loads=1 },
        @{ Name='current'; Current=$testExpected; Manifest=$testExpected; Archive=$true; Code=0; Loads=0 },
        @{ Name='upgrade'; Current=$testOld; Manifest=$testExpected; Archive=$true; Code=0; Loads=1 },
        @{ Name='legacy'; Current=''; Manifest=''; Archive=$true; Code=0; Loads=1 },
        @{ Name='no-archive'; Current=''; Manifest=$testExpected; Archive=$false; Code=1; Loads=0 },
        @{ Name='no-package'; Current=''; Manifest=''; Archive=$false; Code=1; Loads=0 },
        @{ Name='stale-missing-archive'; Current=$testOld; Manifest=$testExpected; Archive=$false; Code=1; Loads=0 },
        @{ Name='invalid-manifest'; Current=$testExpected; Manifest='invalid'; Archive=$true; Code=1; Loads=0 },
        @{ Name='load-failure'; Current=''; Manifest=$testExpected; Archive=$true; Mode='load-fail'; Code=1; Loads=1 },
        @{ Name='wrong-loaded-image'; Current=''; Manifest=$testExpected; Archive=$true; LoadId=$testOld; Code=1; Loads=1 },
        @{ Name='missing-loaded-tag'; Current=''; Manifest=''; Archive=$true; LoadId=''; Code=1; Loads=1 },
        @{ Name='daemon-failure'; Current=''; Manifest=$testExpected; Archive=$true; Mode='query-fail'; Code=1; Loads=0 },
        @{ Name='source-build'; Current=''; Manifest=''; Archive=$false; Source=$true; Code=0; Loads=0 },
        @{ Name='current-without-archive'; Current=$testExpected; Manifest=$testExpected; Archive=$false; Code=0; Loads=0 }
    )
    foreach ($case in $cases) {
        # Construct Unicode without depending on the host ANSI code page.
        $folderName = ([string][char]0x5B89) + ([char]0x88C5) + ' space ' + $case.Name
        $folder = Join-Path $testRoot $folderName
        [void](New-Item -ItemType Directory -Path $folder)
        Copy-Item -LiteralPath (Join-Path $testRepo 'bot.ps1') -Destination (Join-Path $folder 'bot.ps1')
        [IO.File]::WriteAllText((Join-Path $folder '.env'), "QQ_APP_ID=test`nQQ_APP_SECRET=test-only`n", $testEncoding)
        [IO.File]::WriteAllText((Join-Path $folder 'compose.yaml'), 'services: {}', $testEncoding)
        $before = (Get-FileHash -LiteralPath (Join-Path $folder '.env')).Hash
        if ($case.Archive) { [IO.File]::WriteAllBytes((Join-Path $folder 'nonebot-qq.tar'), [byte[]]@(0)) }
        if ($case.Manifest) { [IO.File]::WriteAllText((Join-Path $folder 'image-id.txt'), $case.Manifest + "`n", $testEncoding) }
        if ($case.ContainsKey('Source')) { [IO.File]::WriteAllText((Join-Path $folder 'Dockerfile'), 'FROM scratch', $testEncoding) }
        $env:QQBOT_TEST_DOCKER_LOG = Join-Path $folder 'calls.log'
        $env:QQBOT_TEST_DOCKER_STATE = Join-Path $folder 'state.txt'
        $env:QQBOT_TEST_DOCKER_MODE = if ($case.ContainsKey('Mode')) { $case.Mode } else { '' }
        $env:QQBOT_TEST_DOCKER_LOAD_ID = if ($case.ContainsKey('LoadId')) { $case.LoadId } else { $testExpected }
        [IO.File]::WriteAllText($env:QQBOT_TEST_DOCKER_STATE, $case.Current, $testEncoding)
        $result = Invoke-LauncherProcess (Join-Path $folder 'bot.ps1') $testRoot
        $calls = [IO.File]::ReadAllLines($env:QQBOT_TEST_DOCKER_LOG)
        Assert-True ($result.ExitCode -eq $case.Code) ($case.Name + ': unexpected result: ' + $result.Output)
        Assert-True (@($calls | Where-Object { $_ -match '^load --input ' }).Count -eq $case.Loads) ($case.Name + ': incorrect load count')
        Assert-True (@($calls | Where-Object { $_ -match ' up --detach --force-recreate qqbot$' }).Count -eq [int]($case.Code -eq 0)) ($case.Name + ': incorrect compose start')
        Assert-True (@($calls | Where-Object { $_ -match '^image inspect ' }).Count -eq 0) ($case.Name + ': unsafe missing-image probe')
        Assert-True ((Get-FileHash -LiteralPath (Join-Path $folder '.env')).Hash -eq $before) ($case.Name + ': credentials changed')
        if ($case.Code -eq 0) { Assert-True ($result.Output -match 'running and healthy') ($case.Name + ': no success confirmation') }
        Write-Host ('PASS ' + $case.Name)
    }
    Write-Host ('Passed ' + $cases.Count + ' native Windows PowerShell 5.1 launcher scenarios.')
} finally {
    $env:PATH = $testSavedPath
    foreach ($name in $testEnvNames) { [Environment]::SetEnvironmentVariable($name, $testSavedEnv[$name]) }
    if (Test-Path -LiteralPath $testRoot) {
        $item = Get-Item -LiteralPath $testRoot -Force
        Assert-True ([IO.Path]::GetDirectoryName($item.FullName) -eq $testRepo) 'Refusing unsafe test cleanup'
        Assert-True ($item.Name.StartsWith('.codex-tmp-launcher-')) 'Refusing unsafe test cleanup'
        Assert-True (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Refusing link cleanup'
        Remove-Item -LiteralPath $item.FullName -Recurse -Force
    }
}
