$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'environment_setup.ps1')
    Start-StartupProgress '安装依赖' 7
    $ready = Invoke-ProjectEnvironment -Python -OpenClaw -Weixin
    Set-StartupStage 7 '确认运行环境'
    Complete-StartupProgress '依赖和运行环境已准备好，可以启动微信机器人。'
    exit 0
} catch {
    Fail-StartupProgress $_.Exception.Message
    exit 1
}
