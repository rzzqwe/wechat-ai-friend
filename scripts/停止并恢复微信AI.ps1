$ErrorActionPreference = "Stop"
$runtimeSetup = Join-Path $PSScriptRoot 'use_project_runtime.ps1'
if (Test-Path -LiteralPath $runtimeSetup) { . $runtimeSetup }

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " 停用微信 AI 并恢复原设置" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

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

if (Has-Command "openclaw") {
    try {
        Invoke-OpenClaw channels stop --channel openclaw-weixin
    } catch {
        Write-Host "微信通道当前没有运行，继续恢复文件。" -ForegroundColor DarkYellow
    }
    try {
        Invoke-OpenClaw plugins disable openclaw-weixin
        Write-Host "微信 AI 通道已停用。" -ForegroundColor Green
    } catch {
        Write-Host "没有找到可停用的微信插件，继续恢复文件。" -ForegroundColor DarkYellow
    }
}

$workspace = Join-Path $env:USERPROFILE ".openclaw\workspace"
$soul = Join-Path $workspace "SOUL.md"
$backup = Join-Path $workspace "SOUL.md.before-wechat-ai"
$marker = Join-Path $workspace "SOUL.md.wechat-ai-managed"

if (Test-Path -LiteralPath $backup) {
    Copy-Item -LiteralPath $backup -Destination $soul -Force
    Write-Host "已恢复生成 AI 人格前的 SOUL.md。" -ForegroundColor Green
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
} elseif (Test-Path -LiteralPath $marker) {
    Remove-Item -LiteralPath $soul -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    Write-Host "生成 AI 人格前没有 SOUL.md，已删除本工具生成的文件。" -ForegroundColor Green
} else {
    Write-Host "没有发现备份文件，未改动 SOUL.md。" -ForegroundColor DarkYellow
}

Write-Host ""
$remove = Read-Host "只停用以后可能再次使用？直接回车；连微信绑定和插件也删除？输入 Y"
if ($remove -match "^[Yy]$") {
    $pythonExe = $env:WECHAT_AI_PYTHON
    if ([string]::IsNullOrWhiteSpace($pythonExe)) { $pythonExe = (Get-Command python -ErrorAction Stop).Source }
    & $pythonExe -B -X utf8 (Join-Path $PSScriptRoot 'route_wechat_account.py') --detach-all
    if ($LASTEXITCODE -ne 0) { throw '人格路由清理失败，账号列表未删除。' }
    if (Has-Command "openclaw") {
        try { Invoke-OpenClaw channels logout --channel openclaw-weixin } catch { Write-Host "清除微信绑定凭证时跳过：$($_.Exception.Message)" -ForegroundColor DarkYellow }
        try { Invoke-OpenClaw plugins uninstall "@tencent-weixin/openclaw-weixin" } catch { Write-Host "清除插件时跳过：$($_.Exception.Message)" -ForegroundColor DarkYellow }
    }
    $projectData = Join-Path (Split-Path -Parent $PSScriptRoot) "data"
    Remove-Item -LiteralPath (Join-Path $projectData "accounts.json") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $projectData "account-bindings.json") -Force -ErrorAction SilentlyContinue
    Write-Host "微信绑定凭证和微信插件已请求清除。" -ForegroundColor Green
} else {
    Write-Host "完成。微信里的 ClawBot 不会再调用这个 AI。" -ForegroundColor Green
    Write-Host "OpenClaw 和模型凭证仍保留，之后可以重新启用。"
}
Read-Host "按回车关闭此窗口"
