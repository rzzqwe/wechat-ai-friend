$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'environment_setup.ps1')
    Start-StartupProgress '启动微信机器人' 7
    $ready = Invoke-ProjectEnvironment -Python -OpenClaw -Weixin
    $projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    Set-StartupStage 7 '打开微信机器人工作台'
    $result = Invoke-StartupProcess $ready.Python @('-u', '-B', '-X', 'utf8', (Join-Path $projectRoot 'wechat_bot_app.py')) '__WECHAT_WORKBENCH_READY__'
    if ($result.Code -ne 0) { throw ('工作台退出码：' + $result.Code + '。' + $result.Text) }
    exit 0
} catch {
    Fail-StartupProgress $_.Exception.Message
    exit 1
}
