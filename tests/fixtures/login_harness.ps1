param([string]$ScriptPath, [string]$ResultPath)
$ErrorActionPreference = 'Stop'
$global:FixtureVersion = if ($env:LOGIN_CASE -like 'upgrade_*') { '2026.5.12' } else { '2026.9.6' }
$global:FixturePluginRepaired = $false
$global:FixtureNodeVersion = switch ($env:LOGIN_CASE) {
    'node_upgrade_ok' { 'v22.16.0' }
    'node_24_stale' { 'v24.15.0' }
    'node_25_stale' { 'v25.0.0' }
    'node_26_stale' { 'v26.0.0' }
    default { 'v24.16.0' }
}
function Get-FixturePluginRoot {
    if ($env:LOGIN_CASE -like 'setup_plugin_isolated*') {
        return Join-Path $env:OPENCLAW_STATE_DIR 'npm/projects/fixture generation/node_modules/@tencent-weixin/openclaw-weixin'
    }
    return Join-Path $env:OPENCLAW_STATE_DIR 'npm/node_modules/@tencent-weixin/openclaw-weixin'
}
function Write-FixturePlugin {
    $root = Get-FixturePluginRoot
    New-Item -ItemType Directory -Path (Join-Path $root 'dist') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $root 'package.json') -Value '{"name":"@tencent-weixin/openclaw-weixin","version":"2.4.9"}' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $root 'openclaw.plugin.json') -Value '{"id":"openclaw-weixin"}' -Encoding UTF8
    if ($env:LOGIN_CASE -ne 'setup_plugin_postcheck_failed') {
        Set-Content -LiteralPath (Join-Path $root 'dist/index.js') -Value '// repaired fixture' -Encoding UTF8
    }
    $global:FixturePluginRepaired = $true
}
function global:node {
    $global:LASTEXITCODE = 0
    if ($env:LOGIN_CASE -eq 'node_invalid') { 'invalid version'; return }
    if ($env:LOGIN_CASE -eq 'node_exit_fail') { $global:LASTEXITCODE = 2 }
    $global:FixtureNodeVersion
}
function global:winget {
    $global:LASTEXITCODE = 0
    if ($env:LOGIN_CASE -like 'node_*_stale') { $global:LASTEXITCODE = 1 }
    else { $global:FixtureNodeVersion = 'v24.16.0' }
}
function global:npm.cmd {
    $global:LASTEXITCODE = 0
    if ($env:LOGIN_CASE -eq 'upgrade_fail') { $global:LASTEXITCODE = 1 }
    elseif ($env:LOGIN_CASE -ne 'upgrade_stale') { $global:FixtureVersion = '2026.9.6' }
}
function global:openclaw {
    $global:LASTEXITCODE = 0
    Add-Content -LiteralPath $env:LOGIN_CALLS -Value ($args -join ' ') -Encoding UTF8
    if ($args[0] -eq '--version') {
        if ($env:LOGIN_CASE -eq 'version_native') {
            & $env:WECHAT_AI_PYTHON $env:LOGIN_VERSION_SCRIPT $env:LOGIN_VERSION_DONE
            return
        }
        if ($env:LOGIN_CASE -eq 'version_preamble') { 'Checking installation...' }
        if ($env:LOGIN_CASE -eq 'version_invalid') { 'version unavailable'; return }
        $global:FixtureVersion
        if ($env:LOGIN_CASE -eq 'version_exit_fail') { $global:LASTEXITCODE = 2 }
        return
    }
    if ($args[0] -eq 'plugins' -and $args[1] -eq 'info') {
        if ($env:LOGIN_CASE -eq 'setup_plugin_query_failed') {
            '{"ok":false,"error":"registry temporarily unavailable"}'
            $global:LASTEXITCODE = 2
            return
        }
        if ($env:LOGIN_CASE -eq 'setup_plugin_invalid_info') { 'not JSON'; return }
        $root = Get-FixturePluginRoot
        $packagePath = Join-Path $root 'package.json'
        if (((-not (Test-Path -LiteralPath $packagePath)) -and $env:LOGIN_CASE -ne 'setup_plugin_missing_package') -or
            ($env:LOGIN_CASE -eq 'setup_plugin_unregistered' -and -not $global:FixturePluginRepaired)) {
            '{"ok":false,"error":"Plugin not found: openclaw-weixin. Run openclaw plugins list to see installed plugins."}'
            $global:LASTEXITCODE = 1
            return
        }
        $version = '2.4.9'
        if (Test-Path -LiteralPath $packagePath) {
            $version = (Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 | ConvertFrom-Json).version
        }
        $source = Join-Path $root 'dist/index.js'
        if ($env:LOGIN_CASE -eq 'setup_plugin_alt_entry') { $source = Join-Path $root 'lib/channel.js' }
        $plugin = @{id='openclaw-weixin'; rootDir=$root; source=$source; version=$version; status='loaded';
                    dependencyStatus=@{requiredInstalled=$true}}
        if ($env:LOGIN_CASE -eq 'setup_plugin_disabled') { $plugin.status='disabled' }
        if ($env:LOGIN_CASE -eq 'setup_plugin_missing_dependency' -and -not $global:FixturePluginRepaired) {
            $plugin.dependencyStatus.requiredInstalled=$false
        }
        if ($env:LOGIN_CASE -eq 'setup_plugin_runtime_error') { $plugin.status='error' }
        if ($env:LOGIN_CASE -eq 'setup_plugin_wrong_id') { $plugin.id='another-plugin' }
        $report = @{plugin=$plugin; install=@{installPath=$root}}
        if ($env:LOGIN_CASE -in @('setup_plugin_install_path','setup_plugin_no_root')) { $plugin.Remove('rootDir') }
        if ($env:LOGIN_CASE -eq 'setup_plugin_no_root') { $report.Remove('install') }
        if ($env:LOGIN_CASE -eq 'setup_plugin_flat_info') { $report=$plugin }
        $report | ConvertTo-Json -Depth 8 -Compress
        if ($env:LOGIN_CASE -eq 'setup_plugin_query_nonzero') { $global:LASTEXITCODE=2 }
        return
    }
    if ($args[0] -eq 'plugins' -and $args[1] -eq 'install') {
        if ($env:LOGIN_CASE -eq 'plugin_fail') { $global:LASTEXITCODE = 1; return }
        Write-FixturePlugin
        return
    }
    if ($args[0] -eq 'onboard') {
        if ($args -contains '--agent-name') { throw 'unknown option --agent-name' }
        if ($env:LOGIN_CASE -eq 'model_fail') { throw ('fixture failure ' + $env:WECHAT_AI_API_KEY) }
    }
    if ($args[0] -eq 'doctor') {
        if ($env:LOGIN_CASE -eq 'setup_migration_fail') { $global:LASTEXITCODE = 1 }
    }
    if ($args[0] -eq 'channels' -and $args[1] -eq 'login') {
        $directory = Join-Path $env:OPENCLAW_STATE_DIR 'openclaw-weixin'
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $directory 'accounts.json') -Value '["fixture-wechat"]' -Encoding UTF8
    }
    if ($args[0] -eq 'gateway' -and $args[1] -eq 'start' -and $env:LOGIN_CASE -eq 'gateway_fail') {
        $global:LASTEXITCODE = 1
    }
}
& $ScriptPath -AccountId user -ResultPath $ResultPath -NoPause
exit $LASTEXITCODE
