function Test-WeixinPluginInstalled {
    # Use discovery's selected package, including isolated npm generations.
    # A leftover dependency declaration does not prove that a plugin is usable.
    $script:WeixinPluginCheckReason = ''
    $script:WeixinPluginVerified = $false
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $fastResult=$null
        $fastScript=Join-Path $PSScriptRoot 'weixin_status_fast.mjs'
        if ($env:WECHAT_AI_NODE -and $env:WECHAT_AI_OPENCLAW_ENTRY -and
            (Test-Path -LiteralPath $fastScript) -and (Get-Command Invoke-SetupProcess -ErrorAction SilentlyContinue)) {
            $fastProbe=Invoke-SetupProcess $env:WECHAT_AI_NODE @($fastScript, (Split-Path $env:WECHAT_AI_OPENCLAW_ENTRY -Parent))
            if ($fastProbe.Code -eq 0) {
                try { $parsed=$fastProbe.Text | ConvertFrom-Json -ErrorAction Stop; if ($parsed.ok -eq $true) { $fastResult=$parsed } } catch { }
            }
        }
        if ($fastResult) {
            $infoLines=@($fastResult | ConvertTo-Json -Depth 8 -Compress)
            $infoExitCode=0
        } elseif ($script:StartupState -and $env:WECHAT_AI_NODE -and (Get-Command Invoke-SetupProcess -ErrorAction SilentlyContinue)) {
            $probe = Invoke-SetupProcess $env:WECHAT_AI_NODE @($env:WECHAT_AI_OPENCLAW_ENTRY, 'plugins', 'info', 'openclaw-weixin', '--json') -Pulse
            $infoLines = @($probe.Text)
            $infoExitCode = $probe.Code
        } else {
            $infoLines = @(& openclaw plugins info openclaw-weixin --json 2>$null)
            $infoExitCode = $LASTEXITCODE
        }
    } finally { $ErrorActionPreference = $previousPreference }
    $infoText = ($infoLines -join [Environment]::NewLine).Trim()
    if ($infoExitCode -ne 0) {
        $failureMessage = $infoText
        try {
            $failure = $infoText | ConvertFrom-Json -ErrorAction Stop
            if ($failure.error -is [string]) { $failureMessage = $failure.error }
            elseif ($failure.error.message) { $failureMessage = $failure.error.message }
        } catch { }
        if ($failureMessage -match '^Plugin not found: openclaw-weixin([.]|$)') {
            $script:WeixinPluginCheckReason = '运行端尚未登记微信渠道插件'
            return $false
        }
        throw '无法查询微信插件状态，已停止自动重装。请稍后重试或检查 OpenClaw 运行状态。'
    }
    try { $info = $infoText | ConvertFrom-Json -ErrorAction Stop }
    catch { throw '微信插件检测返回了无效信息，已停止自动重装。' }
    $plugin = if ($info.PSObject.Properties.Name -contains 'plugin') { $info.plugin } else { $info }
    if ($null -eq $plugin -or $plugin.id -cne 'openclaw-weixin') {
        throw '微信插件检测结果与目标插件不一致，已停止自动重装。'
    }
    $pluginRoot = [string]$plugin.rootDir
    if ([string]::IsNullOrWhiteSpace($pluginRoot)) { $pluginRoot = [string]$info.install.installPath }
    if ([string]::IsNullOrWhiteSpace($pluginRoot) -or -not [IO.Path]::IsPathRooted($pluginRoot)) {
        throw '微信插件检测未返回有效安装目录，已停止自动重装。'
    }
    try {
        $package = Get-Content -LiteralPath (Join-Path $pluginRoot 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $manifest = Get-Content -LiteralPath (Join-Path $pluginRoot 'openclaw.plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($package.name -cne '@tencent-weixin/openclaw-weixin' -or $manifest.id -cne 'openclaw-weixin') {
            $script:WeixinPluginCheckReason = '已登记的插件文件身份不匹配'
            return $false
        }
        $version = [version]$package.version
        if ($version -lt [version]'2.4.9') {
            $script:WeixinPluginCheckReason = '已安装版本低于所需的 2.4.9'
            return $false
        }
        $entryFile = [string]$plugin.source
        if ([string]::IsNullOrWhiteSpace($entryFile)) { $entryFile = Join-Path $pluginRoot 'dist/index.js' }
        $rootPrefix = [IO.Path]::GetFullPath($pluginRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if (-not [IO.Path]::IsPathRooted($entryFile) -or
            -not [IO.Path]::GetFullPath($entryFile).StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $entryFile -PathType Leaf)) {
            $script:WeixinPluginCheckReason = '已登记的插件入口文件缺失或异常'
            return $false
        }
        if ($plugin.dependencyStatus.requiredInstalled -eq $false) {
            $script:WeixinPluginCheckReason = '微信插件的必要依赖不完整'
            return $false
        }
    } catch {
        $script:WeixinPluginCheckReason = '已登记的插件包或清单文件缺失、损坏'
        return $false
    }
    if ($plugin.status -eq 'error') {
        throw '运行端报告微信插件异常，但安装文件完整。已停止自动重装，请先检查插件诊断。'
    }
    $script:WeixinPluginVerified = $true
    return $true
}
