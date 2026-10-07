param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$AccountId,
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$LocalId=''
)

$ErrorActionPreference = "Stop"
$helper=Join-Path $PSScriptRoot 'unbind_account.py'
if(Test-Path -LiteralPath $helper){
    $python=$env:WECHAT_AI_PYTHON
    if(-not $python){$python=Join-Path $PSScriptRoot '../.venv/Scripts/python.exe'}
    if(-not (Test-Path -LiteralPath $python)){
        $command=Get-Command python -ErrorAction SilentlyContinue
        if(-not $command){throw '没有找到工作台的 Python 运行环境。'}
        $python=$command.Source
    }
    $arguments=@('-B','-X','utf8',$helper,'--provider-id',$AccountId)
    if($LocalId){$arguments+=@('--local-id',$LocalId)}
    & $python @arguments
    exit $LASTEXITCODE
}
$runtimeSetup = Join-Path $PSScriptRoot 'use_project_runtime.ps1'
if (Test-Path -LiteralPath $runtimeSetup) { . $runtimeSetup }

function Has-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Invoke-OpenClaw {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($script:ProjectRuntimeRequiresStopForce -and $Arguments.Count -ge 2 -and
        $Arguments[0] -eq 'gateway' -and $Arguments[1] -eq 'stop' -and $Arguments -notcontains '--force') {
        $Arguments += '--force'
    }
    & openclaw @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "OpenClaw 命令失败（退出码 $LASTEXITCODE）：$($Arguments -join ' ')"
    }
}

if (-not (Has-Command "openclaw")) {
    throw "没有找到 OpenClaw。"
}

$stateRoot = $env:OPENCLAW_STATE_DIR
if ([string]::IsNullOrWhiteSpace($stateRoot)) { $stateRoot = Join-Path $env:USERPROFILE ".openclaw" }
$weixinRoot = Join-Path $stateRoot "openclaw-weixin"

# 先停止网关，避免删除凭证时仍有轮询进程占用旧账号。
try { Invoke-OpenClaw gateway stop } catch { Write-Host "网关当前没有运行，继续清理。" -ForegroundColor DarkYellow }

# 微信插件的账号凭证、同步游标和上下文令牌都在这个目录中。
$accountDir = Join-Path $weixinRoot "accounts"
foreach ($suffix in @(".json", ".sync.json", ".context-tokens.json")) {
    Remove-Item -LiteralPath (Join-Path $accountDir "$AccountId$suffix") -Force -ErrorAction SilentlyContinue
}

# 从插件维护的账号索引中移除真实 accountId。
$indexPath = Join-Path $weixinRoot "accounts.json"
if (Test-Path -LiteralPath $indexPath) {
    try {
        $parsed = Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
        # Windows PowerShell 5.1 可能把 JSON 数组包装成 {value, Count} 对象。
        if ($parsed.PSObject.Properties.Name -contains "value") { $ids = @($parsed.value) } else { $ids = @($parsed) }
        $remaining = @($ids | ForEach-Object { [string]$_ } | Where-Object { $_ -ne $AccountId })
        $json = "[" + (($remaining | ForEach-Object { '"' + $_ + '"' }) -join ",") + "]"
        Set-Content -LiteralPath $indexPath -Value $json -Encoding UTF8
    } catch { Write-Host "账号索引清理跳过：$($_.Exception.Message)" -ForegroundColor DarkYellow }
}

# 清除该账号的可选配对授权文件和配置项。
$allowFrom = Join-Path $stateRoot "credentials\openclaw-weixin-$AccountId-allowFrom.json"
Remove-Item -LiteralPath $allowFrom -Force -ErrorAction SilentlyContinue
try { Invoke-OpenClaw config unset "channels.openclaw-weixin.accounts.$AccountId" } catch { Write-Host "配置项清理跳过：$($_.Exception.Message)" -ForegroundColor DarkYellow }
try { Invoke-OpenClaw gateway start } catch { Write-Host "网关启动跳过：$($_.Exception.Message)" -ForegroundColor DarkYellow }

Write-Host "账号 $AccountId 的绑定凭证已请求清除。" -ForegroundColor Green
