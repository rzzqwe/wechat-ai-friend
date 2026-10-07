param(
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$AccountId = "default",
    [string]$ResultPath = '',
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$runtimeSetup = Join-Path $PSScriptRoot 'use_project_runtime.ps1'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'environment_setup.ps1')) -and
    (Test-Path -LiteralPath $runtimeSetup)) { . $runtimeSetup }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$script:BindingStage = '检查陪伴设定'
$script:BindingClock=[Diagnostics.Stopwatch]::StartNew()
$script:BindingStageStarted=0.0
$script:BindingSaved=$false
$script:BoundProviderId=''
$script:GatewayState='unknown'
$script:HotChannelPaused=$false

function Invoke-WeixinRuntimeMode([string]$Mode,[string]$Provider='') {
    $helper=Join-Path $PSScriptRoot 'gateway_status.mjs'
    if(-not $env:WECHAT_AI_NODE -or -not (Test-Path -LiteralPath $helper)){return $false}
    $probe=Invoke-StartupProcess $env:WECHAT_AI_NODE @($helper,(Split-Path $PSScriptRoot -Parent),$env:OPENCLAW_CONFIG_PATH,$Mode,$Provider) -Quiet
    try { $status=$probe.Text.Trim() | ConvertFrom-Json; return $probe.Code -eq 0 -and $status.ok -eq $true }
    catch { return $false }
}

function Resume-BindingChannel {
    if(-not $script:HotChannelPaused){return $true}
    $helper=Join-Path $PSScriptRoot 'binding_channel.py'
    $resumed=Invoke-StartupProcess $pythonExe @('-B','-X','utf8',$helper,'resume',[string]$PID)
    if($resumed.Code -ne 0){throw '微信渠道恢复未完成，暂停记录仍保留。'}
    $script:HotChannelPaused=$false
    Remove-Item Env:WECHAT_AI_CHANNEL_PAUSED -ErrorAction SilentlyContinue
    return Invoke-WeixinRuntimeMode '--weixin-resume' $script:BoundProviderId
}

function Write-BindingResult([string]$Status, [string]$Message) {
    if ([string]::IsNullOrWhiteSpace($ResultPath)) { return }
    if (-not [string]::IsNullOrWhiteSpace($env:WECHAT_AI_API_KEY)) {
        $Message = $Message.Replace($env:WECHAT_AI_API_KEY, '[已隐藏 Key]')
    }
    $payload = @{ local_id = $AccountId; status = $Status; message = $Message; stage = $script:BindingStage;
                  elapsed_seconds=[Math]::Round($script:BindingClock.Elapsed.TotalSeconds,1);
                  binding_saved=$script:BindingSaved; provider_id=$script:BoundProviderId; gateway_state=$script:GatewayState }
    $folder = Split-Path -Parent $ResultPath
    [System.IO.Directory]::CreateDirectory($folder) | Out-Null
    $temporary = $ResultPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [System.IO.File]::WriteAllText($temporary, ($payload | ConvertTo-Json -Compress), $utf8)
    Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
}

function Set-BindingStage([string]$Message) {
    if ($script:BindingClock.Elapsed.TotalSeconds -gt $script:BindingStageStarted) {
        Write-Host ('上一阶段耗时 {0:N1} 秒；现在：{1}' -f ($script:BindingClock.Elapsed.TotalSeconds-$script:BindingStageStarted), $Message) -ForegroundColor Cyan
    }
    $script:BindingStageStarted=$script:BindingClock.Elapsed.TotalSeconds
    $script:BindingStage = $Message
    Write-BindingResult 'running' $Message
}

trap {
    $failure = '绑定失败（' + $script:BindingStage + '）：' + $_.Exception.Message
    if (-not [string]::IsNullOrWhiteSpace($env:WECHAT_AI_API_KEY)) {
        $failure = $failure.Replace($env:WECHAT_AI_API_KEY, '[已隐藏 Key]')
    }
    if($script:HotChannelPaused){
        try { [void](Resume-BindingChannel) }
        catch { Write-Host '微信接收尚未恢复；暂停记录已保留，下一次启动会安全恢复。' -ForegroundColor Yellow }
    }
    if ($script:BindingSaved) {
        $script:GatewayState='failed'
        $failure='微信绑定及专属人格已保存；后台尚未就绪，可点击启动账号，无需重新扫码。' + $failure
        try { Write-BindingResult 'succeeded' $failure } catch { Write-Host '无法保存错误状态。' -ForegroundColor Red }
    } else {
        try { Write-BindingResult 'failed' $failure } catch { Write-Host '无法保存错误状态。' -ForegroundColor Red }
    }
    Write-Host $failure -ForegroundColor Red
    if (-not $NoPause) {
        if ($script:BindingSaved) { Read-Host '按回车关闭窗口，工作台可查看后台状态或点击启动账号' }
        else { Read-Host '按回车关闭窗口，然后在工作台点击重新扫码' }
    }
    exit 1
}

Set-BindingStage '检查文本模型配置'
$baseUrl = $env:WECHAT_AI_BASE_URL
$model = $env:WECHAT_AI_MODEL
$apiKey = $env:WECHAT_AI_API_KEY
if ([string]::IsNullOrWhiteSpace($baseUrl) -or [string]::IsNullOrWhiteSpace($model) -or [string]::IsNullOrWhiteSpace($apiKey)) {
    throw '请先自行配置文本模型，在工作台填写服务地址、模型名称和 API Key；本工具不会安装文本模型。'
}
Set-BindingStage '检查陪伴设定'

$pythonExe = $env:WECHAT_AI_PYTHON
if ([string]::IsNullOrWhiteSpace($pythonExe)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $pythonExe = $pythonCommand.Source
}
$routeScript = Join-Path $PSScriptRoot 'route_wechat_account.py'
& $pythonExe -B -X utf8 $routeScript --local-id $AccountId --check
if ($LASTEXITCODE -ne 0) { throw '请先在工作台为这个用户分配专属人格。' }


Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " 微信 AI 朋友：检查环境并绑定微信" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

function Has-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Read-OpenClawVersionOutput {
    # 先完整等待原生命令退出；管道中的 Select-Object -First 会提前终止 npm 启动器。
    $versionLines = @(& openclaw --version 2>$null)
    $versionExitCode = $LASTEXITCODE
    if ($versionExitCode -ne 0) {
        throw "OpenClaw 版本命令失败（退出码 $versionExitCode），未继续扫码。"
    }
    return ($versionLines -join [Environment]::NewLine)
}

function Invoke-OpenClaw {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($script:ProjectRuntimeRequiresStopForce -and $Arguments.Count -ge 2 -and
        $Arguments[0] -eq 'gateway' -and $Arguments[1] -eq 'stop' -and $Arguments -notcontains '--force') {
        $Arguments += '--force'
    }
    $operationExitCode=0
    if ($ready.Runtime -and $Arguments[0] -ne 'channels' -and
        (Get-Command Invoke-StartupProcess -ErrorAction SilentlyContinue)) {
        $operationResult=Invoke-StartupProcess $env:WECHAT_AI_NODE (@($env:WECHAT_AI_OPENCLAW_ENTRY) + $Arguments)
        $operationExitCode=$operationResult.Code
    } else {
        & openclaw @Arguments
        $operationExitCode=$LASTEXITCODE
    }
    if ($operationExitCode -ne 0) {
        $operation = ($Arguments | Select-Object -First 2) -join ' '
        throw "OpenClaw 命令失败（退出码 $operationExitCode）：$operation。具体输出见扫码窗口。"
    }
}

function Get-OpenClawStateRoot {
    $stateRoot = $env:OPENCLAW_STATE_DIR
    if ([string]::IsNullOrWhiteSpace($stateRoot)) { $stateRoot = Join-Path $env:USERPROFILE ".openclaw" }
    return $stateRoot
}

function Read-OpenClawConfig {
    $configPath = $env:OPENCLAW_CONFIG_PATH
    if ([string]::IsNullOrWhiteSpace($configPath)) { $configPath = $env:OPENCLAW_CONFIG }
    if ([string]::IsNullOrWhiteSpace($configPath)) {
        $configPath = Join-Path (Get-OpenClawStateRoot) 'openclaw.json'
    }
    if (-not (Test-Path -LiteralPath $configPath)) { return [pscustomobject]@{} }
    try {
        return (Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        throw '无法读取已有 OpenClaw 配置，已停止自动配置。请修复配置文件后重新扫码。'
    }
}

. (Join-Path $PSScriptRoot 'weixin_environment.ps1')


function Test-ModelConfiguration($Config, [string]$BaseUrl, [string]$Model, [string]$ApiKey) {
    $primary = $Config.agents.defaults.model
    if ($primary -isnot [string]) { $primary = $primary.primary }
    foreach ($entry in $Config.models.providers.PSObject.Properties) {
        $provider = $entry.Value
        if ($primary -cne ($entry.Name + '/' + $Model)) { continue }
        if (([string]$provider.baseUrl).Trim().TrimEnd('/') -cne $BaseUrl.Trim().TrimEnd('/')) { continue }
        if ($provider.api -cne 'openai-completions') { continue }
        if ($provider.apiKey -isnot [string] -or $provider.apiKey -cne $ApiKey) { continue }
        if (@($provider.models | Where-Object { $_.id -ceq $Model }).Count -eq 0) { continue }
        return $true
    }
    return $false
}

function Read-WeixinAccountIndex {
    $stateRoot = Get-OpenClawStateRoot
    $indexPath = Join-Path $stateRoot "openclaw-weixin\accounts.json"
    if (-not (Test-Path -LiteralPath $indexPath)) { return @() }
    try {
        $value = Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
        if ($null -ne $value) {
            if ($value.PSObject.Properties.Name -contains 'value') { $value = $value.value }
            return @($value | ForEach-Object { [string]$_ })
        }
    } catch { }
    return @()
}

Set-BindingStage '检查 Node.js 与 OpenClaw 运行环境'
$ready=$null
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'environment_setup.ps1')) {
    . (Join-Path $PSScriptRoot 'environment_setup.ps1')
    $ready = Invoke-ProjectEnvironment -Python -OpenClaw -Weixin
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'gateway_environment.ps1')) {
    . (Join-Path $PSScriptRoot 'gateway_environment.ps1')
    Set-ProjectGatewayEnvironment
}
if (-not $ready.Runtime) {
if (-not (Has-Command "node")) {
    if (Has-Command "winget") {
        Write-Host "没有检测到 Node.js，尝试自动安装 Node.js LTS..." -ForegroundColor Yellow
        winget install --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath"
    }
    if (-not (Has-Command "node")) {
        Write-Host "没有检测到 Node.js。请先安装 Node.js LTS，再重新点击绑定。" -ForegroundColor Yellow
        Start-Process "https://nodejs.org/zh-cn/download"
        throw '没有找到 Node.js，请安装后重新扫码。'
    }
}

function Test-CompatibleNode {
    $versionLines = @(& node --version 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 版本命令失败，未继续扫码。' }
    $match = [regex]::Match(($versionLines -join [Environment]::NewLine), '^v?([0-9]+)[.]([0-9]+)[.]([0-9]+)\s*$')
    if (-not $match.Success) { throw '无法确认 Node.js 版本，未继续扫码。' }
    $version = [version]::new([int]$match.Groups[1].Value, [int]$match.Groups[2].Value, [int]$match.Groups[3].Value)
    # OpenClaw 2026.9.6's package.json engines; Node 25 and 26.0 are unsupported.
    return (($version -ge [version]'24.16.0' -and $version.Major -eq 24) -or $version -ge [version]'26.1.0')
}
if (-not (Test-CompatibleNode)) {
    Write-Host 'OpenClaw 运行环境需要 Node.js 24.16+（24.x）或 26.1+，正在更新 Node.js LTS。' -ForegroundColor Yellow
    $nodeUpgradeExitCode = $null
    if (Has-Command "winget") {
        winget upgrade --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
        $nodeUpgradeExitCode = $LASTEXITCODE
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath"
    }
    if (-not (Test-CompatibleNode)) {
        if ($null -ne $nodeUpgradeExitCode -and $nodeUpgradeExitCode -ne 0) {
            throw ('Node.js 自动更新失败（winget 退出码 ' + $nodeUpgradeExitCode + '）；当前安装可能未由 winget 管理。请自行安装兼容的 Node.js，或通过 data/runtime.json 指定已有运行环境，再重新扫码。')
        }
        throw 'Node.js 更新尚未生效，请安装兼容的 Node.js LTS 并重启工作台后重新扫码。'
    }
}

if (-not (Has-Command "openclaw")) {
    Write-Host "第一次运行，需要安装兼容版本 OpenClaw 2026.9.6。" -ForegroundColor Yellow
    Write-Host "安装过程可能需要几分钟，请不要关闭这个窗口。"
    & npm.cmd install --global openclaw@2026.9.6
    if ($LASTEXITCODE -ne 0) { throw 'OpenClaw 安装失败，未继续扫码。请查看 npm 错误后重试。' }
    # 安装脚本可能刚刚修改了用户 PATH；在当前窗口刷新一次。
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

if (-not (Has-Command "openclaw")) {
    Write-Host "OpenClaw 安装后仍未出现在 PATH 中，请重启电脑或终端后再试。" -ForegroundColor Red
    throw 'OpenClaw 安装后仍不在 PATH 中，请重启工作台后重试。'
}

# 固定本流程核对过的最低兼容版本，不在扫码时追随最新依赖。
$versionText = Read-OpenClawVersionOutput
$versionMatch = [regex]::Match([string]$versionText, '(\d+)\.(\d+)\.(\d+)')
if ($versionMatch.Success) {
    $installedVersion = [version]::new([int]$versionMatch.Groups[1].Value, [int]$versionMatch.Groups[2].Value, [int]$versionMatch.Groups[3].Value)
    if ($installedVersion -lt [version]::new(2026, 9, 6)) {
        Write-Host "当前 OpenClaw 版本 $installedVersion 较旧，正在升级到 2026.9.6 以支持陪伴语音……" -ForegroundColor Yellow
        Set-BindingStage '正在升级 OpenClaw 到 2026.9.6，请等待安装完成'
        & npm.cmd install --global openclaw@2026.9.6
        if ($LASTEXITCODE -ne 0) { throw 'OpenClaw 升级失败，未使用旧版继续安装微信插件。请查看 npm 错误后重试。' }
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath"
    }
}

$versionText = Read-OpenClawVersionOutput
$versionMatch = [regex]::Match([string]$versionText, '([0-9]+)[.]([0-9]+)[.]([0-9]+)')
if (-not $versionMatch.Success) { throw '无法确认 OpenClaw 版本，未继续扫码。' }
$verifiedVersion = [version]::new([int]$versionMatch.Groups[1].Value, [int]$versionMatch.Groups[2].Value, [int]$versionMatch.Groups[3].Value)
if ($verifiedVersion -lt [version]::new(2026, 9, 6)) { throw '升级后的 OpenClaw 仍低于 2026.9.6，请检查 PATH 中是否存在旧安装。' }
$script:ProjectRuntimeRequiresStopForce = $verifiedVersion -ge [version]'2026.9.0'
} else {
    $verifiedVersion=[version]$ready.Runtime.Version
    $script:ProjectRuntimeRequiresStopForce=$verifiedVersion -ge [version]'2026.9.0'
    Write-Host '运行环境预检已通过，跳过同一次绑定中的重复检查。' -ForegroundColor Green
}

# This release's Windows task audit needs the already verified defaults fix.
# Locate the selected npm shim, covering both managed .bin and global installs.
$compatScript = Join-Path $PSScriptRoot 'apply_openclaw_windows_compat.py'
if ($verifiedVersion -eq [version]'2026.9.6' -and (Test-Path -LiteralPath $compatScript)) {
    Set-BindingStage '正在配置 Windows 运行兼容性'
    $shim = Get-Command openclaw.cmd -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $shimDirectory = Split-Path -Parent $shim.Source
    $packageRoots = @()
    foreach ($candidate in @((Join-Path $shimDirectory 'node_modules/openclaw'), (Join-Path $shimDirectory '../openclaw'))) {
        $metadataPath = Join-Path $candidate 'package.json'
        if (Test-Path -LiteralPath $metadataPath) {
            $metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($metadata.name -ceq 'openclaw' -and $metadata.version -ceq '2026.9.6') { $packageRoots += $candidate }
        }
    }
    if ($packageRoots.Count -ne 1) { throw '无法唯一确认 OpenClaw 安装目录，未应用 Windows 兼容修正。' }
    & $pythonExe -B -X utf8 $compatScript $packageRoots[0]
    if ($LASTEXITCODE -ne 0) { throw 'Windows 运行兼容修正失败，未继续扫码。' }
}

# The workbench can provision an account before OpenClaw is installed. Use the
# official migration for its legacy agent list before querying/installing plugins.
$configBeforeMigration = Read-OpenClawConfig
$configMigrated=$false
if ($configBeforeMigration.agents.PSObject.Properties.Name -contains 'list' -or
    $configBeforeMigration.agents.defaults.PSObject.Properties.Name -contains 'memorySearch') {
    Set-BindingStage '正在迁移 OpenClaw 配置'
    Invoke-OpenClaw doctor --fix --non-interactive
    $configMigrated=$true
}

Set-BindingStage '正在准备微信渠道插件'
if (($ready.WeixinVerified -eq $true -and -not $configMigrated) -or (Test-WeixinPluginInstalled)) {
    Write-Host '微信渠道插件已安装，跳过重复安装。' -ForegroundColor Green
} else {
    Write-Host ('微信渠道插件需要安装或修复：' + $script:WeixinPluginCheckReason) -ForegroundColor Yellow
    try {
        Invoke-OpenClaw plugins install "@tencent-weixin/openclaw-weixin@2.4.9" --force
    } catch {
        throw "插件安装失败：$($_.Exception.Message)"
    }
    if (-not (Test-WeixinPluginInstalled)) {
        throw ('插件安装后仍未通过检查：' + $script:WeixinPluginCheckReason + '。未继续扫码，请先检查安装结果。')
    }
}
$existingConfig = Read-OpenClawConfig
if ($existingConfig.plugins.entries.'openclaw-weixin'.enabled -ne $true) {
    Invoke-OpenClaw config set plugins.entries.openclaw-weixin.enabled true
}
if ($existingConfig.session.dmScope -cne 'per-account-channel-peer') {
    Invoke-OpenClaw config set session.dmScope per-account-channel-peer
}

if (-not [string]::IsNullOrWhiteSpace($apiKey) -and (Test-ModelConfiguration $existingConfig $baseUrl $model $apiKey)) {
    Write-Host '模型配置未变化，复用已保存的配置。' -ForegroundColor Green
} elseif (-not [string]::IsNullOrWhiteSpace($apiKey)) {
    Set-BindingStage '正在配置模型服务'
    $modelConfigured=$false
    $fastModelScript=Join-Path $PSScriptRoot 'configure_text_model.py'
    if (Test-Path -LiteralPath $fastModelScript) {
        Write-Host '正在保存文本模型设置，检查是否可复用现有后台……' -ForegroundColor Green
        & $pythonExe -B -X utf8 $fastModelScript
        $modelResult=$LASTEXITCODE
        if ($modelResult -eq 0) { $modelConfigured=$true }
        elseif ($modelResult -ne 2) { throw '文本模型保存失败，未继续扫码；原配置仍保留。' }
    }
    if (-not $modelConfigured) {
    Set-BindingStage '首次初始化模型配置与后台服务'
    Write-Host '正在首次初始化模型配置与后台服务，这一步包含后台服务安装。' -ForegroundColor Green
    Invoke-OpenClaw onboard --non-interactive --accept-risk --skip-health --install-daemon `
        --skip-channels --skip-skills --skip-ui --skip-hooks --skip-bootstrap `
        --auth-choice custom-api-key `
        --custom-base-url $baseUrl `
        --custom-model-id $model `
        --custom-api-key $apiKey `
        --secret-input-mode plaintext `
        --custom-compatibility openai
    }
} else {
    Write-Host "没有填写 API Key，跳过模型配置。请先在 OpenClaw 中配置模型。" -ForegroundColor Yellow
}

# 扫码期间停止轮询，避免新账号在路由准备前落到默认人格。
Write-Host '绑定新用户期间，微信接收会短暂暂停，完成后一起恢复。' -ForegroundColor Yellow
Set-BindingStage '正在暂停微信接收以准备独立绑定'
$nativeGateway=$false
if (Get-Command Initialize-ProjectGateway -ErrorAction SilentlyContinue) { $nativeGateway=Initialize-ProjectGateway }
$pauseChannel=Join-Path $PSScriptRoot 'binding_channel.py'
if ($nativeGateway -and (Test-Path -LiteralPath $pauseChannel) -and (Invoke-WeixinRuntimeMode '--weixin-check')) {
    $paused=Invoke-StartupProcess $pythonExe @('-B','-X','utf8',$pauseChannel,'pause',[string]$PID)
    if($paused.Code -ne 0){throw '微信接收暂停失败，未开始扫码。'}
    $script:HotChannelPaused=$true
    if(Invoke-WeixinRuntimeMode '--weixin-pause') {
        $env:WECHAT_AI_CHANNEL_PAUSED='1'
        Write-Host '微信接收已暂停，后台保持运行，扫码后直接恢复。' -ForegroundColor Green
    } else {
        [void](Resume-BindingChannel)
        Write-Host '未能确认渠道暂停，改用完整暂停以保证专属路由先保存。' -ForegroundColor Yellow
    }
}
if ($nativeGateway -and -not $script:HotChannelPaused) {
    $stopped=Invoke-ProjectGateway stop
    if ($stopped.Code -ne 0) { throw '后台未能完全暂停，未开始扫码。' }
} elseif (-not $nativeGateway) { Invoke-OpenClaw gateway stop }

$accountIdsBeforeLogin = @(Read-WeixinAccountIndex)
Write-Host "正在请求微信官方二维码，显示后请用绑定者的手机微信扫码并确认。" -ForegroundColor Cyan
Set-BindingStage '正在准备扫码组件并请求二维码'
$fastLogin=Join-Path $PSScriptRoot 'weixin_login_fast.mjs'
$usedFastLogin=$false
if ($nativeGateway -and (Test-Path -LiteralPath $fastLogin)) {
    $env:WECHAT_AI_GATEWAY_PAUSED='1'
    $env:WECHAT_AI_LOGIN_PARENT_PID=[string]$PID
    $receiptFolder=Join-Path $PSScriptRoot '../data/login-receipts'
    [void][IO.Directory]::CreateDirectory($receiptFolder)
    $loginAttempt=[guid]::NewGuid().ToString('N')
    $loginReceipt=Join-Path $receiptFolder ($loginAttempt+'.json')
    $env:WECHAT_AI_LOGIN_ATTEMPT=$loginAttempt
    $env:WECHAT_AI_LOGIN_RECEIPT=$loginReceipt
    $personaBindings=Get-Content -LiteralPath (Join-Path $PSScriptRoot '../data/persona-bindings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $env:WECHAT_AI_BINDING_AGENT=[string]$personaBindings.PSObject.Properties[$AccountId].Value.agent_id
    $script:FastProviderId=''
    $bindingFile=Join-Path $PSScriptRoot '../data/account-bindings.json'
    $env:WECHAT_AI_EXISTING_PROVIDER=''
    if (Test-Path -LiteralPath $bindingFile) {
        $knownBindings=Get-Content -LiteralPath $bindingFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $knownBinding=$knownBindings.PSObject.Properties[$AccountId]
        if ($knownBinding) { $env:WECHAT_AI_EXISTING_PROVIDER=[string]$knownBinding.Value }
    }
    $package=Split-Path -Parent $env:WECHAT_AI_OPENCLAW_ENTRY
    $login=Invoke-StartupProcess $env:WECHAT_AI_NODE @($fastLogin,$package,$AccountId) -KeepConsole -LineHandler {
        param($line)
        if ($line -ceq '__WECHAT_QR_READY__') { Set-BindingStage '等待扫码，请在二维码窗口中完成微信授权'; return $true }
        if ($line -ceq '__WECHAT_LOGIN_SAVED__') { Set-BindingStage '微信授权已保存，正在准备专属人格'; return $true }
        if ($line -cmatch '^__WECHAT_PROVIDER_ID__([A-Za-z0-9_-]+)$') { $script:FastProviderId=$Matches[1]; return $true }
        return $false
    }
    Remove-Item Env:WECHAT_AI_GATEWAY_PAUSED -ErrorAction SilentlyContinue
    Remove-Item Env:WECHAT_AI_EXISTING_PROVIDER -ErrorAction SilentlyContinue
    Remove-Item Env:WECHAT_AI_LOGIN_PARENT_PID -ErrorAction SilentlyContinue
    $receiptVerified=$false
    if(Test-Path -LiteralPath $loginReceipt){
        $verified=@(& $pythonExe -B -X utf8 (Join-Path $PSScriptRoot 'verify_login_receipt.py') $loginReceipt $loginAttempt $AccountId)
        if($LASTEXITCODE -eq 0 -and $verified.Count -eq 1){
            $script:FastProviderId=[string]$verified[0]
            $receiptVerified=$true
        }
    }
    Remove-Item Env:WECHAT_AI_LOGIN_ATTEMPT -ErrorAction SilentlyContinue
    Remove-Item Env:WECHAT_AI_LOGIN_RECEIPT -ErrorAction SilentlyContinue
    Remove-Item Env:WECHAT_AI_BINDING_AGENT -ErrorAction SilentlyContinue
    if ($receiptVerified) {
        $usedFastLogin=$true
        if($login.Code -ne 0){Write-Host '微信授权及凭证已核验保存，继续配置专属人格。' -ForegroundColor Green}
    } elseif ($login.Code -eq 0) { throw '扫码进程已结束，但本次授权回执未完整保存，未猜测账号归属。' }
    elseif ($login.Code -ne 2) { throw ('微信授权回执未通过核验（扫码进程退出码 '+$login.Code+'），未猜测账号或启动后台。') }
}
if (-not $usedFastLogin) {
if ($AccountId -eq "default") {
    Invoke-OpenClaw channels login --channel openclaw-weixin
} else {
    Invoke-OpenClaw channels login --channel openclaw-weixin --account $AccountId
}
}

# 插件会把微信服务返回的真实 ID 保存到 openclaw-weixin/accounts.json。
# 记录别名到真实 ID 的映射，管理界面才能精准启停和筛选记录。
$accountIdsAfterLogin = @(Read-WeixinAccountIndex)
$newAccountIds = @($accountIdsAfterLogin | Where-Object { $_ -notin $accountIdsBeforeLogin })
$actualAccountId = $null
if ($usedFastLogin -and $script:FastProviderId -in $accountIdsAfterLogin) {
    $actualAccountId=$script:FastProviderId
} elseif ($newAccountIds.Count -eq 1) {
    $actualAccountId = [string]$newAccountIds[0]
} elseif ($newAccountIds.Count -eq 0) {
    $bindingFile = Join-Path $PSScriptRoot '../data/account-bindings.json'
    if (Test-Path -LiteralPath $bindingFile) {
        $savedBindings = Get-Content -LiteralPath $bindingFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $known = $savedBindings.PSObject.Properties[$AccountId]
        if ($known -and [string]$known.Value -in $accountIdsAfterLogin) { $actualAccountId = [string]$known.Value }
    }
    if (-not $actualAccountId -and $accountIdsAfterLogin.Count -eq 1) { $actualAccountId = [string]$accountIdsAfterLogin[0] }
}
if ([string]::IsNullOrWhiteSpace($actualAccountId)) {
    throw '不能唯一确认扫码账号，已保持网关停止，未猜测或覆盖账号人格映射。请一次只绑定一个账号。'
}

Set-BindingStage '正在保存专属人格与账号路由'
& $pythonExe -B -X utf8 $routeScript --local-id $AccountId --provider-id $actualAccountId
if ($LASTEXITCODE -ne 0) { throw '专属人格路由未准备好，已保持网关停止。请修复绑定后再启动账号。' }
$script:BindingSaved=$true
$script:BoundProviderId=$actualAccountId
if($loginReceipt -and (Test-Path -LiteralPath $loginReceipt)){Remove-Item -LiteralPath $loginReceipt -Force}
Write-Host '专属人格和独立记忆已绑定，正在启动网关。' -ForegroundColor Green
Set-BindingStage '正在启动微信网关'
$script:GatewayState='starting'
if ($script:HotChannelPaused) {
    Set-BindingStage '正在恢复微信接收，复用已运行的后台'
    if (-not (Resume-BindingChannel)) {
        Write-BindingResult 'succeeded' '微信绑定与专属人格已保存；微信接收正在恢复，工作台会显示实际运行状态。'
        if(-not $NoPause){Read-Host '绑定已完成，按回车关闭窗口'}
        exit 0
    }
} elseif ($nativeGateway) {
    $started=Invoke-ProjectGateway start
    if ($started.Code -eq 3) {
        Write-BindingResult 'succeeded' '微信绑定与专属人格已保存；后台启动中，工作台会显示实际运行状态。'
        if (-not $NoPause) { Read-Host '绑定已完成，按回车关闭窗口' }
        exit 0
    }
    if ($started.Code -eq 2) { Invoke-OpenClaw gateway start }
    elseif ($started.Code -ne 0) { throw '后台启动失败，微信绑定仍保留。' }
} else { Invoke-OpenClaw gateway start }
$script:GatewayState='ready'
Write-BindingResult 'succeeded' '扫码及专属人格配置完成'

Write-Host ""
Write-Host "账号 $AccountId 绑定完成后，直接在该微信里的 ClawBot 对话中发消息即可。" -ForegroundColor Green
Write-Host "如需重试，请在工作台选中对应陪伴或账号，点击重新扫码。"
if (-not $NoPause) { Read-Host "按回车关闭此窗口" }
