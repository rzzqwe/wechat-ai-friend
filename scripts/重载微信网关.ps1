$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'environment_setup.ps1')
$ready = Invoke-ProjectEnvironment -Python -OpenClaw -Weixin
. (Join-Path $PSScriptRoot 'gateway_environment.ps1')
if (Initialize-ProjectGateway) {
    $stopped=Invoke-ProjectGateway stop
    if($stopped.Code -ne 0){throw '网关停止失败，未重复启动。'}
    $started=Invoke-ProjectGateway start
    if($started.Code -ne 0 -and $started.Code -ne 3){throw '网关启动失败。'}
    exit 0
}
if (-not $script:ProjectRuntimeRequiresStopForce) {
    $versionLines = @(& openclaw --version 2>$null)
    if ($LASTEXITCODE -ne 0) { throw '无法确认 OpenClaw 运行版本，未重载网关。' }
    $versionMatch = [regex]::Match(($versionLines -join [Environment]::NewLine), '([0-9]+)[.]([0-9]+)[.]([0-9]+)')
    if (-not $versionMatch.Success) { throw '无法确认 OpenClaw 运行版本，未重载网关。' }
    $version = [version]::new([int]$versionMatch.Groups[1].Value, [int]$versionMatch.Groups[2].Value, [int]$versionMatch.Groups[3].Value)
    $script:ProjectRuntimeRequiresStopForce = $version -ge [version]'2026.9.0'
}
# Newer Windows runtimes retain SQLite inspection children during startup.
# Stop the supervised generation completely before bringing up the next one.
if ($script:ProjectRuntimeRequiresStopForce) {
    & openclaw gateway stop --force
    if ($LASTEXITCODE -ne 0) { throw '网关停止失败，未重复启动。' }
    & openclaw gateway start
    if ($LASTEXITCODE -ne 0) { throw '网关启动失败。' }
    exit 0
}
& openclaw gateway restart
if ($LASTEXITCODE -ne 0) {
    & openclaw gateway start
    if ($LASTEXITCODE -ne 0) { throw '网关启动失败。' }
}
