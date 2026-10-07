$ErrorActionPreference = 'Stop'

try {
    . (Join-Path $PSScriptRoot 'environment_setup.ps1')
    Start-StartupProgress '启动 OpenClaw' 8
    $ready = Invoke-ProjectEnvironment -Python -OpenClaw -Weixin
    $openclaw = Get-Command openclaw.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $openclaw) {
        throw '没有找到 OpenClaw，请先通过微信机器人工作台完成安装，或检查 data/runtime.json 中的运行环境路径。'
    }

    Set-StartupStage 7 '启动 OpenClaw 网关'
    . (Join-Path $PSScriptRoot 'gateway_environment.ps1')
    $native=Initialize-ProjectGateway
    $result = if($native){Invoke-ProjectGateway start}else{Invoke-StartupProcess $env:WECHAT_AI_NODE @($env:WECHAT_AI_OPENCLAW_ENTRY, 'gateway', 'start')}
    if ($result.Code -eq 3) { Complete-StartupProgress '后台已启动，仍在初始化；工作台会显示实际运行状态。'; exit 0 }
    if ($result.Code -ne 0) {
        throw ('OpenClaw 网关启动失败（退出码 ' + $result.Code + '），请查看上方输出。')
    }

    Set-StartupStage 8 '确认 OpenClaw 网关状态'
    if($native){Complete-StartupProgress 'OpenClaw 网关已通过健康和就绪检查。';exit 0}
    $result = Invoke-StartupProcess $env:WECHAT_AI_NODE @($env:WECHAT_AI_OPENCLAW_ENTRY, 'gateway', 'status')
    if ($result.Code -ne 0) {
        throw ('启动命令已执行，但网关状态检查失败（退出码 ' + $result.Code + '），请查看上方输出。')
    }

    Complete-StartupProgress 'OpenClaw 网关在后台运行，可以关闭此窗口。'
    exit 0
} catch {
    Fail-StartupProgress $_.Exception.Message
    exit 1
}
