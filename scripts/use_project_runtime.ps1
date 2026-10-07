# Prefer this project's validated runtime without changing the system PATH.
$script:ProjectRuntimeRequiresStopForce = $false
$runtimeManifest = Join-Path $PSScriptRoot '../data/runtime.json'
if (-not (Test-Path -LiteralPath $runtimeManifest)) {
    # Reuse our already installed portable runtime before trying a system upgrade.
    # An explicit (including disabled) manifest always takes precedence.
    $stateRoot = $env:OPENCLAW_STATE_DIR
    if ([string]::IsNullOrWhiteSpace($stateRoot)) { $stateRoot = Join-Path $env:USERPROFILE '.openclaw' }
    $managedRoot = Join-Path $stateRoot 'wechat-runtime'
    $packageRoot = Join-Path $managedRoot 'openclaw-2026.9.6/node_modules/openclaw'
    $managedBin = Join-Path $managedRoot 'openclaw-2026.9.6/node_modules/.bin'
    $packagePath = Join-Path $packageRoot 'package.json'
    $canReuse = $false
    if ((Test-Path -LiteralPath $packagePath -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $managedBin 'openclaw.cmd') -PathType Leaf)) {
        try {
            $packageInfo = Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 | ConvertFrom-Json
            $canReuse = $packageInfo.name -ceq 'openclaw' -and $packageInfo.version -ceq '2026.9.6'
        } catch { $canReuse = $false }
    }
    if ($canReuse) {
        foreach ($candidate in @(Get-ChildItem -LiteralPath $managedRoot -Directory -Filter 'node-v*-win-*' | Sort-Object Name -Descending)) {
            $candidateNode = Join-Path $candidate.FullName 'node.exe'
            if (-not (Test-Path -LiteralPath $candidateNode -PathType Leaf)) { continue }
            try {
                $nodeLines = @(& $candidateNode --version 2>$null)
                if ($LASTEXITCODE -ne 0) { continue }
                $nodeText = ($nodeLines -join [Environment]::NewLine).Trim()
                if ($nodeText -notmatch '^v([0-9]+[.][0-9]+[.][0-9]+)$') { continue }
                $candidateVersion = [version]$Matches[1]
                if (-not (($candidateVersion.Major -eq 24 -and $candidateVersion -ge [version]'24.16.0') -or
                          $candidateVersion -ge [version]'26.1.0')) { continue }
            } catch { continue }
            $manifest = [ordered]@{
                enabled = $true; node_dir = $candidate.FullName; bin_dir = $managedBin;
                node_version = $candidateVersion.ToString(); openclaw_version = '2026.9.6';
                reason = 'Reused installed compatible runtime; system Node.js unchanged.'
            }
            [IO.Directory]::CreateDirectory((Split-Path -Parent $runtimeManifest)) | Out-Null
            $temporary = $runtimeManifest + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
            try {
                [IO.File]::WriteAllText($temporary, ($manifest | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
                Move-Item -LiteralPath $temporary -Destination $runtimeManifest -Force
            } finally {
                if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
            }
            Write-Host ('使用已安装的兼容运行环境：Node.js ' + $candidateVersion + ' / OpenClaw 2026.9.6。') -ForegroundColor Green
            break
        }
    }
}
if (Test-Path -LiteralPath $runtimeManifest) {
    $savedRuntime = Get-Content -LiteralPath $runtimeManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($savedRuntime.enabled -eq $true) {
        $nodeDir = [string]$savedRuntime.node_dir
        $binDir = [string]$savedRuntime.bin_dir
        if (-not [IO.Path]::IsPathRooted($nodeDir) -or -not [IO.Path]::IsPathRooted($binDir) -or
            -not (Test-Path -LiteralPath (Join-Path $nodeDir 'node.exe')) -or
            -not (Test-Path -LiteralPath (Join-Path $binDir 'openclaw.cmd'))) {
            throw 'Project runtime configuration is invalid: data/runtime.json'
        }
        $env:PATH = $nodeDir + ';' + $binDir + ';' + $env:PATH
        $runtimeVersion = $null
        if ([version]::TryParse([string]$savedRuntime.openclaw_version, [ref]$runtimeVersion)) {
            $script:ProjectRuntimeRequiresStopForce = $runtimeVersion -ge [version]'2026.9.0'
        }
    }
}
