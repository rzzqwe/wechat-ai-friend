# The official installer owns the service definition and runtime pin.
function Set-ProjectGatewayEnvironment {
    $state=if($env:OPENCLAW_STATE_DIR){$env:OPENCLAW_STATE_DIR}else{Join-Path $env:USERPROFILE '.openclaw'}
    $config=if($env:OPENCLAW_CONFIG_PATH){$env:OPENCLAW_CONFIG_PATH}elseif($env:OPENCLAW_CONFIG){$env:OPENCLAW_CONFIG}else{Join-Path $state 'openclaw.json'}
    $env:OPENCLAW_STATE_DIR=[IO.Path]::GetFullPath($state)
    $env:OPENCLAW_CONFIG_PATH=[IO.Path]::GetFullPath($config)
}
function Initialize-ProjectGateway {
    Set-ProjectGatewayEnvironment
    $helper=Join-Path $PSScriptRoot 'gateway_control.mjs'
    if (-not $env:WECHAT_AI_NODE -or -not $env:WECHAT_AI_OPENCLAW_ENTRY -or -not (Test-Path -LiteralPath $helper)) { return $false }
    $package=Split-Path -Parent $env:WECHAT_AI_OPENCLAW_ENTRY
    $metadata=Read-SetupJson (Join-Path $package 'package.json')
    if ($metadata.name -cne 'openclaw' -or $metadata.version -cne '2026.9.6') { return $false }
    $compat=Join-Path $PSScriptRoot 'apply_openclaw_windows_compat.py'
    if ($env:WECHAT_AI_PYTHON -and (Test-Path -LiteralPath $compat)) {
        $patched=Invoke-StartupProcess $env:WECHAT_AI_PYTHON @('-B','-X','utf8',$compat,$package) -Quiet
        if($patched.Code -ne 0){throw 'Windows 后台兼容修正失败，未继续启动。'}
    }
    $prepare=Join-Path $PSScriptRoot 'prepare_gateway_config.py'
    if ($env:WECHAT_AI_PYTHON -and (Test-Path -LiteralPath $prepare)) {
        $prepared=Invoke-StartupProcess $env:WECHAT_AI_PYTHON @('-B','-X','utf8',$prepare)
        if ($prepared.Code -ne 0) { throw '后台组件配置失败，未继续启动。' }
    }
    $check=Invoke-StartupProcess $env:WECHAT_AI_NODE @($helper,$package,'check',$env:OPENCLAW_STATE_DIR,$env:OPENCLAW_CONFIG_PATH) -Quiet
    if ($check.Code -eq 0) { return $true }
    if ($check.Code -ne 2) { throw '后台服务路径检查失败，未操作其他后台。' }
    $inspection=$check.Text.Trim() | ConvertFrom-Json
    if ($inspection.stopSafe -eq $true) {
        Write-StartupMessage '正在先暂停已确认归属的旧后台，避免修复时与运行中的数据库争用。'
        $stopped=Invoke-ProjectGateway stop
        if ($stopped.Code -ne 0) { throw '旧后台尚未完全停止，未重复安装服务。' }
    }
    Write-StartupMessage '正在一次性修复后台服务的数据和配置路径，后续启动会直接复用。'
    $repair=Invoke-StartupProcess $env:WECHAT_AI_NODE @($env:WECHAT_AI_OPENCLAW_ENTRY,'gateway','install','--force','--runtime-path',$env:WECHAT_AI_NODE)
    if ($repair.Code -ne 0) { throw '后台服务修复失败，请查看上方安装提示。' }
    $check=Invoke-StartupProcess $env:WECHAT_AI_NODE @($helper,$package,'check',$env:OPENCLAW_STATE_DIR,$env:OPENCLAW_CONFIG_PATH) -Quiet
    if ($check.Code -ne 0) { throw '后台服务修复后路径仍未匹配，未继续启动。' }
    return $true
}
function Invoke-ProjectGateway([ValidateSet('stop','start')][string]$Action) {
    $helper=Join-Path $PSScriptRoot 'gateway_control.mjs'
    $package=Split-Path -Parent $env:WECHAT_AI_OPENCLAW_ENTRY
    return Invoke-StartupProcess $env:WECHAT_AI_NODE @($helper,$package,$Action,$env:OPENCLAW_STATE_DIR,$env:OPENCLAW_CONFIG_PATH)
}
