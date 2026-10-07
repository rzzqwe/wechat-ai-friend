param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$AccountId,
    [ValidateSet('start', 'stop')]
    [string]$Action = 'start'
)

$ErrorActionPreference = "Stop"
$runtimeSetup = Join-Path $PSScriptRoot 'use_project_runtime.ps1'
if ($Action -eq 'start') {
    . (Join-Path $PSScriptRoot 'environment_setup.ps1')
    $ready = Invoke-ProjectEnvironment -Python -OpenClaw -Weixin
} elseif (Test-Path -LiteralPath $runtimeSetup) {
    . $runtimeSetup
}

function Has-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Invoke-OpenClaw {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & openclaw @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "OpenClaw 命令失败（退出码 $LASTEXITCODE）：$($Arguments -join ' ')"
    }
}

if (-not (Has-Command "openclaw")) {
    throw "没有找到 OpenClaw，请先点击安装并绑定微信。"
}

$fastAccount=Join-Path $PSScriptRoot 'set_account_enabled.py'
if ((Test-Path -LiteralPath $fastAccount) -and $env:WECHAT_AI_PYTHON) {
    & $env:WECHAT_AI_PYTHON -B -X utf8 $fastAccount $AccountId $Action
    if ($LASTEXITCODE -ne 0) { throw '账号设置失败。' }
    if ($Action -eq 'start') {
        . (Join-Path $PSScriptRoot 'gateway_environment.ps1')
        if (Initialize-ProjectGateway) {
            $started=Invoke-ProjectGateway start
            if($started.Code -ne 0 -and $started.Code -ne 3){throw '网关启动失败。'}
            Write-Host "账号 $AccountId 已启用，后台会自动应用账号配置。" -ForegroundColor Green
            exit 0
        }
    } else {
        Write-Host "账号 $AccountId 已停用，后台会自动停止这个账号。" -ForegroundColor Green
        exit 0
    }
}

# 让同一网关上的多个微信账号按账号、渠道和对端隔离会话。
Invoke-OpenClaw config set session.dmScope per-account-channel-peer

$enabled = if ($Action -eq 'start') { 'true' } else { 'false' }
Invoke-OpenClaw config set "channels.openclaw-weixin.accounts.$AccountId.enabled" $enabled

try {
    Invoke-OpenClaw gateway restart
} catch {
    Invoke-OpenClaw gateway start
}

Write-Host "账号 $AccountId 已设置为 $Action。" -ForegroundColor Green
