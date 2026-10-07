param([string]$Library, [string]$Root, [string]$Case, [string]$ProjectName = 'project')
$ErrorActionPreference = 'Stop'
$env:WECHAT_AI_RUNTIME_CACHE = Join-Path $Root 'cache'
$env:OPENCLAW_STATE_DIR = Join-Path $Root 'state'
$script:FixtureRoot = $Root
$script:FixtureCase = $Case
$script:FaultActive = $false
$project = Join-Path $Root $ProjectName
[IO.Directory]::CreateDirectory($project) | Out-Null
[IO.File]::WriteAllText((Join-Path $project 'requirements.txt'), 'Pillow==12.3.0')
. $Library
function Get-SetupArchitecture { return 'x64' }
function Add-FixtureCall([string]$Text) { Add-Content -LiteralPath (Join-Path $script:FixtureRoot 'calls.txt') -Value $Text -Encoding UTF8 }
function Fixture-Marker([string]$Name) { return Join-Path $script:FixtureRoot $Name }
function New-FixtureOpenClaw([string]$Prefix, [switch]$Incomplete) {
    $package = Join-Path $Prefix 'node_modules/openclaw'
    [IO.Directory]::CreateDirectory($package) | Out-Null
    [IO.File]::WriteAllText((Join-Path $package 'package.json'), '{"name":"openclaw","version":"2026.9.6"}')
    if ($Incomplete) { return }
    [IO.Directory]::CreateDirectory((Join-Path $package 'dist')) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $Prefix 'node_modules/.bin')) | Out-Null
    [IO.File]::WriteAllText((Join-Path $package 'openclaw.mjs'), '// fixture')
    [IO.File]::WriteAllText((Join-Path $package 'dist/index.js'), '// fixture')
    [IO.File]::WriteAllText((Join-Path $Prefix 'node_modules/.bin/openclaw.cmd'), '@echo off')
}
function Invoke-SetupProcess([string]$Executable, [string[]]$Arguments) {
    $result = [pscustomobject]@{Code=0; Text=''}
    if ((Split-Path $Executable -Leaf) -eq 'python.exe') {
        if ($Arguments[0] -eq '-m' -and $Arguments[1] -eq 'venv') {
            Add-FixtureCall 'venv'
            $directory = Join-Path $Arguments[2] 'Scripts'
            [IO.Directory]::CreateDirectory($directory) | Out-Null
            [IO.File]::WriteAllText((Join-Path $directory 'python.exe'), 'fixture')
        } elseif ($Arguments -contains 'install') {
            Add-FixtureCall 'pip-install'
            [IO.File]::WriteAllText((Join-Path (Split-Path $Executable -Parent) 'dependencies.ready'), 'ready')
        } elseif (($Arguments -join ' ') -like '*import PIL.Image*') {
            if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Executable -Parent) 'dependencies.ready'))) { $result.Code=1 }
        }
    } elseif ((Split-Path $Executable -Leaf) -eq 'py.exe') {
        $result.Code=1
    } elseif ((Split-Path $Executable -Leaf) -eq 'npm.cmd' -or
              ((Split-Path $Executable -Leaf) -eq 'node.exe' -and $Arguments[0] -like '*npm-cli.js')) {
        Add-FixtureCall 'npm-install'
        $prefix = $Arguments[[Array]::IndexOf($Arguments, '--prefix') + 1]
        if ($script:FixtureCase -eq 'npm-failure' -and -not (Test-Path (Fixture-Marker 'npm.failed.once'))) {
            [IO.File]::WriteAllText((Fixture-Marker 'npm.failed.once'), 'failed')
            New-FixtureOpenClaw $prefix -Incomplete
            $result.Code=1; $result.Text='fixture interrupted npm install'
        } else { New-FixtureOpenClaw $prefix }
    } elseif ((Split-Path $Executable -Leaf) -eq 'node.exe') {
        if ($Arguments -contains 'plugins' -and $Arguments -contains 'install') {
            Add-FixtureCall 'plugin-install'
            [IO.File]::WriteAllText((Fixture-Marker 'plugin.ready'), 'ready')
        } elseif ($Arguments[0] -like '*openclaw.mjs') {
            if ($script:FaultActive -and $script:FixtureCase -eq 'openclaw-probe-error') {
                $result.Code=2; $result.Text='fixture runtime probe temporarily unavailable'
            } elseif ($script:FaultActive -and $script:FixtureCase -eq 'version-unreadable') {
                $result.Text='version temporarily unavailable'
            } elseif ($script:FixtureCase -eq 'version-preamble') {
                $result.Text='Node.js v24.16.0 checking environment.' + [Environment]::NewLine + 'OpenClaw 2026.9.6 (fixture)'
            } else { $result.Text='OpenClaw 2026.9.6 (fixture)' }
        } else { $result.Text='v24.16.0' }
    } else { $result.Code=1 }
    return $result
}
function Test-WeixinPluginInstalled {
    if ($script:FaultActive -and $script:FixtureCase -eq 'plugin-query-error') { throw 'fixture plugin registry temporarily unavailable' }
    $script:WeixinPluginCheckReason = 'fixture missing plugin'
    return Test-Path -LiteralPath (Fixture-Marker 'plugin.ready')
}
$script:GetPythonCandidates = (Get-Command Get-SetupPythonCandidates).ScriptBlock
$script:GetNodeCandidates = (Get-Command Get-SetupNodeCandidates).ScriptBlock
$script:GetOpenClawCandidates = (Get-Command Get-SetupOpenClawCandidates).ScriptBlock
function Keep-FixturePaths($Paths) { return @($Paths | Where-Object { $_ -and ([IO.Path]::GetFullPath($_)).StartsWith($script:FixtureRoot + '\', [StringComparison]::OrdinalIgnoreCase) }) }
function Get-SetupPythonCandidates($ProjectRoot, $CacheRoot) { Keep-FixturePaths (& $script:GetPythonCandidates $ProjectRoot $CacheRoot) }
function Get-SetupNodeCandidates($ProjectRoot, $CacheRoot) { Keep-FixturePaths (& $script:GetNodeCandidates $ProjectRoot $CacheRoot) }
function Get-SetupOpenClawCandidates($ProjectRoot, $CacheRoot) { Keep-FixturePaths (& $script:GetOpenClawCandidates $ProjectRoot $CacheRoot) }
function Get-SetupDownloadSpec($Component, $Architecture) {
    $name = if ($Component -eq 'node') { 'node-v24.16.0-win-x64.zip' } else { 'python-3.13.16-amd64.zip' }
    $source = Join-Path $script:FixtureRoot ('payloads/' + $name)
    return @{File=$name; Url=('https://fixture.invalid/' + $name); Hash=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash}
}
function Request-SetupDownload($Url, $Destination) {
    $name = Split-Path $Url -Leaf
    Add-FixtureCall ('download ' + $name)
    if ($script:FixtureCase -eq 'concurrent') { [Threading.Thread]::Sleep(250) }
    $failure = Fixture-Marker 'download.failed.once'
    if ($script:FixtureCase -eq 'download-failure' -and -not (Test-Path $failure)) {
        [IO.File]::WriteAllText($failure, 'failed')
        [IO.File]::WriteAllText($Destination, 'partial download')
        throw 'fixture download interrupted'
    }
    if ($script:FixtureCase -eq 'hash-failure' -and -not (Test-Path $failure)) {
        [IO.File]::WriteAllText($failure, 'failed')
        [IO.File]::WriteAllText($Destination, 'invalid archive')
        return
    }
    Copy-Item -LiteralPath (Join-Path $script:FixtureRoot ('payloads/' + $name)) -Destination $Destination
}
$failures = 0
try {
    if ($Case -eq 'legacy-migration') {
        $legacy = Join-Path $Root 'legacy-cache'
        [IO.Directory]::CreateDirectory((Join-Path $legacy 'downloads')) | Out-Null
        Copy-Item -LiteralPath (Join-Path $Root 'payloads/python-3.13.16-amd64.zip') -Destination (Join-Path $legacy 'downloads')
        Copy-Item -LiteralPath (Join-Path $Root 'payloads/node-v24.16.0-win-x64.zip') -Destination (Join-Path $legacy 'downloads')
        [IO.Directory]::CreateDirectory($env:WECHAT_AI_RUNTIME_CACHE) | Out-Null
        Import-SetupLegacyCache $env:WECHAT_AI_RUNTIME_CACHE @($legacy)
    }
    try { $ready = Invoke-ProjectEnvironment -ProjectRoot $project -Python -OpenClaw -Weixin }
    catch {
        if ($Case -notin @('download-failure','hash-failure','npm-failure')) { throw }
        $failures++
        $ready = Invoke-ProjectEnvironment -ProjectRoot $project -Python -OpenClaw -Weixin
    }
    if ($Case -eq 'metadata-lost') {
        foreach ($name in @('runtime.json','python-runtime.json')) {
            [IO.File]::WriteAllText((Join-Path $project ('data/' + $name)), '{corrupt metadata')
        }
    }
    if ($Case -eq 'cache-repair') {
        Remove-Item -LiteralPath (Join-Path $env:WECHAT_AI_RUNTIME_CACHE 'node-v24.16.0-win-x64/node.exe')
    }
    if ($Case -in @('openclaw-probe-error','plugin-query-error','version-unreadable')) { $script:FaultActive=$true }
    if ($Case -ne 'concurrent') {
        try { $ready = Invoke-ProjectEnvironment -ProjectRoot $project -Python -OpenClaw -Weixin }
        catch {
            if ($Case -notin @('openclaw-probe-error','plugin-query-error','version-unreadable')) { throw }
            $failures++
        }
    }
    $calls = [IO.File]::ReadAllLines((Join-Path $Root 'calls.txt'))
    @{ok=$true; failures=$failures; calls=$calls; python=$ready.Python; node=$ready.Runtime.Node} | ConvertTo-Json -Compress -Depth 5
    exit 0
} catch {
    Write-Host $_.Exception.Message
    exit 1
}
