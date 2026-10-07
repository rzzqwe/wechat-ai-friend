# Common startup preflight. Downloads are version-pinned, cached, and serialized.
# A parent PowerShell 7 process can pass its module paths to Windows PowerShell;
# use the child shell's own built-in modules for hashing and ZIP extraction.
$setupModuleRoot = Join-Path $PSHOME 'Modules'
$env:PSModulePath = $setupModuleRoot + ';' + $env:PSModulePath
Import-Module (Join-Path $setupModuleRoot 'Microsoft.PowerShell.Utility') -Force
. (Join-Path $PSScriptRoot 'startup_progress.ps1')
$script:SetupNodeVersion = '24.16.0'
$script:SetupPythonVersion = '3.13.16'
$script:SetupOpenClawVersion = '2026.9.6'

function Get-SetupCacheRoot {
    if ($env:WECHAT_AI_RUNTIME_CACHE) { return [IO.Path]::GetFullPath($env:WECHAT_AI_RUNTIME_CACHE) }
    # USERPROFILE is stable across desktop and packaged-app processes; LOCALAPPDATA
    # can be virtualized into a package's private cache and cause repeat downloads.
    return Join-Path $env:USERPROFILE '.openclaw/wechat-runtime'
}

function Import-SetupLegacyCache([string]$CacheRoot, [string[]]$LegacyRoots = @()) {
    if ($env:WECHAT_AI_RUNTIME_CACHE -and -not $LegacyRoots.Count) { return }
    $marker = Join-Path $CacheRoot 'legacy-cache-migrated.json'
    if (Test-Path -LiteralPath $marker) { return }
    if (-not $LegacyRoots.Count) {
        $LegacyRoots = @(Join-Path $env:LOCALAPPDATA 'WeChatAI/runtime')
        $packages = Join-Path $env:USERPROFILE 'AppData/Local/Packages'
        if (Test-Path -LiteralPath $packages) {
            $LegacyRoots += @(Get-ChildItem -LiteralPath $packages -Directory -Filter 'OpenAI.Codex_*' |
                ForEach-Object { Join-Path $_.FullName 'LocalCache/Local/WeChatAI/runtime' })
        }
    }
    $names = @('downloads', 'pip-cache', 'npm-cache', 'python-3.13.16-x64', 'python-3.13.16-arm64',
               'node-v24.16.0-win-x64', 'node-v24.16.0-win-arm64', 'openclaw-2026.9.6')
    $prefix = [IO.Path]::GetFullPath($CacheRoot).TrimEnd('\') + '\'
    foreach ($legacy in @($LegacyRoots | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $legacy -PathType Container)) { continue }
        $legacyPrefix = [IO.Path]::GetFullPath($legacy).TrimEnd('\') + '\'
        $legacyLock = $null
        try {
            try { $legacyLock = [IO.File]::Open((Join-Path $legacy 'environment.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
            catch [IO.IOException] { throw '旧缓存仍在安装中，未重复下载；请等待该窗口完成后重试。' }
            foreach ($name in $names) {
                $source = [IO.Path]::GetFullPath((Join-Path $legacy $name))
                $target = [IO.Path]::GetFullPath((Join-Path $CacheRoot $name))
                if (-not $source.StartsWith($legacyPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                    -not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw '旧缓存路径异常，未迁移。' }
                if (-not (Test-Path -LiteralPath $source -PathType Container) -or (Test-Path -LiteralPath $target)) { continue }
                if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                $staging = Join-Path $CacheRoot ('.staging-' + [guid]::NewGuid().ToString('N'))
                try {
                    Copy-Item -LiteralPath $source -Destination $staging -Recurse
                    $resolvedStaging = [IO.Path]::GetFullPath($staging)
                    if (-not $resolvedStaging.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw '迁移临时路径异常。' }
                    Move-Item -LiteralPath $resolvedStaging -Destination $target
                } finally { Remove-SetupStaging $staging $CacheRoot }
            }
        } finally { if ($legacyLock) { $legacyLock.Dispose() } }
    }
    Write-SetupJson $marker @{ migrated = $true }
}

function Write-SetupJson($Path, $Value) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Read-SetupJson($Path) {
    if (Test-Path -LiteralPath $Path) {
        try {
            $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($value -isnot [pscustomobject]) { throw 'Expected an environment metadata object.' }
            return $value
        }
        catch { Write-Host ('环境路径记录无法读取，正在检查已安装文件：' + (Split-Path $Path -Leaf)) -ForegroundColor Yellow }
    }
    return $null
}

function Invoke-SetupProcess([string]$Executable, [string[]]$Arguments, [switch]$Live, [switch]$Pulse) {
    if ($Live) { return Invoke-StartupProcess $Executable $Arguments }
    if ($Pulse -or ($script:StartupState -and -not $script:StartupState.Done)) {
        return Invoke-StartupProcess $Executable $Arguments -Quiet
    }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = -1
        try { $lines = @(& $Executable @Arguments 2>&1) }
        catch { return [pscustomobject]@{ Code = -1; Text = $_.Exception.Message } }
        $code = $LASTEXITCODE
        return [pscustomobject]@{ Code = $code; Text = (($lines | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) }
    } finally { $ErrorActionPreference = $previous }
}

function Get-SetupArchitecture {
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -eq 'ARM64') { return 'arm64' }
    if ($architecture -eq 'AMD64') { return 'x64' }
    throw '运行环境需要 64 位 Windows（x64 或 ARM64）。'
}

function Get-SetupDownloadSpec([string]$Component, [string]$Architecture) {
    # Hashes published by nodejs.org and python.org, linked in THIRD_PARTY_NOTICES.md.
    if ($Component -eq 'node') {
        $file = 'node-v24.16.0-win-' + $Architecture + '.zip'
        $hashes = @{ x64 = 'edaca9bd58ec8e92037dac4e877d52f6b8f430b81c18b57e264b4e2fb111cd56';
                     arm64 = '14834611d4c6b3c06054e7007732b90474c16e0b32f395e05b55a571ef71c6d2' }
        return @{ File = $file; Url = 'https://nodejs.org/dist/v24.16.0/' + $file; Hash = $hashes[$Architecture] }
    }
    $suffix = if ($Architecture -eq 'x64') { 'amd64' } else { 'arm64' }
    $file = 'python-3.13.16-' + $suffix + '.zip'
    $hashes = @{ x64 = 'bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159';
                 arm64 = '49b24a002efcf7451fe47a52ad9bc57a012a0c987eb6a635973ede200c421044' }
    return @{ File = $file; Url = 'https://www.python.org/ftp/python/3.13.16/' + $file; Hash = $hashes[$Architecture] }
}

function Request-SetupDownload([string]$Url, [string]$Destination) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.Timeout=600000
    $request.ReadWriteTimeout=600000
    $request.UserAgent='WeChatAI-Runtime-Setup'
    $response=$null; $stream=$null; $file=$null
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $name=[IO.Path]::GetFileName(([uri]$Url).AbsolutePath)
    $lastLog=-5.0
    try {
        $responseTask=$request.GetResponseAsync()
        while (-not $responseTask.Wait(250)) {
            if ($clock.Elapsed.TotalSeconds -gt 600) { throw '下载连接超时。' }
            Update-StartupDetail '正在连接下载服务器……' -Operation $name
            if ($clock.Elapsed.TotalSeconds - $lastLog -ge 5) {
                Write-StartupMessage ('正在连接下载服务器，已等待 {0:N0} 秒……' -f $clock.Elapsed.TotalSeconds)
                $lastLog=$clock.Elapsed.TotalSeconds
            }
        }
        $response=$responseTask.GetAwaiter().GetResult()
        $total=$response.ContentLength
        $stream=$response.GetResponseStream()
        $file=[IO.File]::Open($Destination, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $buffer=New-Object byte[] 65536
        [long]$received=0
        while ($true) {
            $read=$stream.ReadAsync($buffer, 0, $buffer.Length)
            while (-not $read.Wait(250)) {
                if ($clock.Elapsed.TotalSeconds -gt 600) { throw '下载超时。' }
                $status=Show-StartupDownloadProgress $received $total $clock $name
                if ($clock.Elapsed.TotalSeconds - $lastLog -ge 1) { Write-StartupMessage ($name + '：' + $status); $lastLog=$clock.Elapsed.TotalSeconds }
            }
            $count=$read.GetAwaiter().GetResult()
            if ($count -eq 0) { break }
            $file.Write($buffer, 0, $count)
            $received += $count
            $status=Show-StartupDownloadProgress $received $total $clock $name
            if ($clock.Elapsed.TotalSeconds - $lastLog -ge 1) { Write-StartupMessage ($name + '：' + $status); $lastLog=$clock.Elapsed.TotalSeconds }
        }
        $file.Flush()
        Write-StartupMessage ('下载完成：{0}，{1:N1} MB' -f $name, ($received / 1MB))
    } catch { $request.Abort(); throw }
    finally {
        if ($file) { $file.Dispose() }
        if ($stream) { $stream.Dispose() }
        if ($response) { $response.Close() }
    }
}

function Get-SetupArchive($Spec, [string]$CacheRoot) {
    $downloads = Join-Path $CacheRoot 'downloads'
    [IO.Directory]::CreateDirectory($downloads) | Out-Null
    $archive = Join-Path $downloads $Spec.File
    if ((Test-Path -LiteralPath $archive) -and (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ieq $Spec.Hash) {
        Write-Host ('复用已下载的安装包：' + $Spec.File) -ForegroundColor Green
        return $archive
    }
    $partial = $archive + '.partial'
    Write-Host ('正在下载运行组件：' + $Spec.File) -ForegroundColor Cyan
    Request-SetupDownload $Spec.Url $partial
    Write-StartupMessage ('正在校验安装包：' + $Spec.File)
    Update-StartupDetail '正在校验 SHA-256……' -Operation $Spec.File
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ine $Spec.Hash) {
        throw ('安装包校验失败，未安装：' + $Spec.File)
    }
    Move-Item -LiteralPath $partial -Destination $archive -Force
    return $archive
}

function Remove-SetupStaging([string]$Path, [string]$CacheRoot) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::GetFullPath($CacheRoot).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $resolved -Leaf) -notlike '.staging-*') { throw '临时目录位置异常，未清理。' }
    if (Test-Path -LiteralPath $resolved) {
        if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '临时目录是链接，未清理。' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Install-SetupArchive($Spec, [string]$Target, [string]$CacheRoot, [string]$Component) {
    $archive = Get-SetupArchive $Spec $CacheRoot
    $staging = Join-Path $CacheRoot ('.staging-' + [guid]::NewGuid().ToString('N'))
    try {
        Write-StartupMessage ('正在解压：' + $Spec.File)
        Update-StartupDetail '正在解压安装文件……' -Operation $Spec.File
        Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
        $source = if ($Component -eq 'node') { Join-Path $staging ([IO.Path]::GetFileNameWithoutExtension($Spec.File)) } else { $staging }
        $executable = Join-Path $source ($Component + '.exe')
        $valid = if ($Component -eq 'node') { Test-SetupNode $executable } else { Test-SetupPython $executable }
        if (-not $valid) { throw ($Component + ' 解压后的运行检查失败，未标记为安装完成。') }
        $targetPath = [IO.Path]::GetFullPath($Target)
        $prefix = [IO.Path]::GetFullPath($CacheRoot).TrimEnd('\') + '\'
        if (-not $targetPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([IO.Path]::GetFullPath($source)).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw '安装目录位置异常。' }
        if (Test-Path -LiteralPath $targetPath) {
            if ((Get-Item -LiteralPath $targetPath).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '安装目录是链接，未替换。' }
            Move-Item -LiteralPath $targetPath -Destination ($targetPath + '.incomplete-' + [guid]::NewGuid().ToString('N'))
        }
        Move-Item -LiteralPath $source -Destination $targetPath
    } finally { Remove-SetupStaging $staging $CacheRoot }
}

function Test-SetupPython([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    $result = Invoke-SetupProcess $Executable @('-B', '-c', 'import sys; assert sys.version_info >= (3,12); import tkinter, _tkinter, venv, ensurepip; tkinter.Tcl()')
    return $result.Code -eq 0
}

function Test-SetupDependencies([string]$Executable, [string]$Requirements) {
    $code = 'import sys,importlib.metadata as m; from pathlib import Path; import PIL.Image; lines=[s.strip() for s in Path(sys.argv[1]).read_text().splitlines() if s.strip() and not s.lstrip().startswith(''#'')]; assert all(''=='' in s and m.version(s.split(''=='')[0]) == s.split(''=='')[1] for s in lines)'
    $result = Invoke-SetupProcess $Executable @('-B', '-c', $code, $Requirements)
    return $result.Code -eq 0
}

function Get-SetupPythonCandidates([string]$ProjectRoot, [string]$CacheRoot) {
    $paths = @((Join-Path $ProjectRoot '.venv/Scripts/python.exe'))
    $saved = Read-SetupJson (Join-Path $ProjectRoot 'data/python-runtime.json')
    if ($saved.python_exe) { $paths += [string]$saved.python_exe }
    $paths += Join-Path $CacheRoot ('python-' + $script:SetupPythonVersion + '-' + (Get-SetupArchitecture) + '/python.exe')
    $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\*') { $paths += $pythonCommand.Source }
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher -and $launcher.Source -notlike '*\WindowsApps\*') {
        $result = Invoke-SetupProcess $launcher.Source @('-3', '-c', 'import sys; print(sys.executable)')
        if ($result.Code -eq 0) { $paths += $result.Text.Trim() }
    }
    return @($paths | Select-Object -Unique)
}

function Ensure-SetupPython([string]$ProjectRoot, [string]$CacheRoot) {
    Set-StartupStage 2 '检查 Python 和 Tkinter'
    $basePython = $null
    foreach ($candidate in (Get-SetupPythonCandidates $ProjectRoot $CacheRoot)) {
        if (Test-SetupPython $candidate) { $basePython = $candidate; break }
    }
    if (-not $basePython) {
        $architecture = Get-SetupArchitecture
        $target = Join-Path $CacheRoot ('python-' + $script:SetupPythonVersion + '-' + $architecture)
        Install-SetupArchive (Get-SetupDownloadSpec 'python' $architecture) $target $CacheRoot 'python'
        $basePython = Join-Path $target 'python.exe'
    }
    $environment = Join-Path $ProjectRoot '.venv'
    $python = Join-Path $environment 'Scripts/python.exe'
    if (-not (Test-SetupPython $python)) {
        if (Test-Path -LiteralPath $environment) {
            $resolved = [IO.Path]::GetFullPath($environment)
            if ((Split-Path $resolved -Parent) -ne [IO.Path]::GetFullPath($ProjectRoot) -or
                (Get-Item -LiteralPath $environment).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '项目 Python 环境目录位置异常。' }
            Move-Item -LiteralPath $environment -Destination ($environment + '.incomplete-' + [guid]::NewGuid().ToString('N'))
        }
        Write-Host '正在创建项目 Python 环境……' -ForegroundColor Cyan
        $result = Invoke-SetupProcess $basePython @('-m', 'venv', $environment) -Live
        if ($result.Code -ne 0 -or -not (Test-SetupPython $python)) { throw ('Python 环境创建失败：' + $result.Text) }
    }
    Write-StartupMessage 'Python 和 Tkinter 已就绪。'
    Set-StartupStage 3 '检查 Python 图片依赖'
    $requirements = Join-Path $ProjectRoot 'requirements.txt'
    if (-not (Test-SetupDependencies $python $requirements)) {
        Write-Host '正在补齐 Python 依赖……' -ForegroundColor Cyan
        $wheelCache = Join-Path $CacheRoot 'pip-cache'
        $result = Invoke-SetupProcess $python @('-u', '-m', 'pip', 'install', '--disable-pip-version-check', '--cache-dir', $wheelCache, '-r', $requirements) -Live
        if ($result.Code -ne 0 -or -not (Test-SetupDependencies $python $requirements)) { throw ('Python 依赖安装未完成：' + $result.Text) }
    }
    Write-StartupMessage 'Python 依赖已就绪。'
    Write-SetupJson (Join-Path $ProjectRoot 'data/python-runtime.json') @{ python_exe = $python; requirements_sha256 = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash }
    return $python
}

function Test-SetupNode([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Executable -Parent) 'npm.cmd') -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Executable -Parent) 'node_modules/npm/bin/npm-cli.js') -PathType Leaf)) { return $false }
    $result = Invoke-SetupProcess $Executable @('--version')
    if ($result.Code -ne 0 -or $result.Text.Trim() -notmatch '^v([0-9]+[.][0-9]+[.][0-9]+)$') { return $false }
    $version = [version]$Matches[1]
    return (($version.Major -eq 24 -and $version -ge [version]'24.16.0') -or $version -ge [version]'26.1.0')
}

function Get-SetupNodeCandidates([string]$ProjectRoot, [string]$CacheRoot) {
    $paths = @()
    $saved = Read-SetupJson (Join-Path $ProjectRoot 'data/runtime.json')
    if ($saved.enabled -eq $true -and $saved.node_dir) { $paths += Join-Path ([string]$saved.node_dir) 'node.exe' }
    $paths += Join-Path $CacheRoot ('node-v' + $script:SetupNodeVersion + '-win-' + (Get-SetupArchitecture) + '/node.exe')
    $state = if ($env:OPENCLAW_STATE_DIR) { $env:OPENCLAW_STATE_DIR } else { Join-Path $env:USERPROFILE '.openclaw' }
    $managed = Join-Path $state 'wechat-runtime'
    if (Test-Path -LiteralPath $managed) {
        $paths += @(Get-ChildItem -LiteralPath $managed -Directory -Filter 'node-v*-win-*' | ForEach-Object { Join-Path $_.FullName 'node.exe' })
    }
    $paths += @(Get-Command node.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    return @($paths | Select-Object -Unique)
}

function Get-SetupOpenClawEntry([string]$BinDirectory) {
    foreach ($root in @((Join-Path $BinDirectory '../openclaw'), (Join-Path $BinDirectory 'node_modules/openclaw'))) {
        $package = Read-SetupJson (Join-Path $root 'package.json')
        $packageVersion = $null
        if ($package.name -ceq 'openclaw' -and [version]::TryParse([string]$package.version, [ref]$packageVersion) -and
            $packageVersion -ge [version]$script:SetupOpenClawVersion) {
            $entry = Join-Path $root 'openclaw.mjs'
            if ((Test-Path -LiteralPath $entry -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $root 'dist/index.js') -PathType Leaf)) { return [IO.Path]::GetFullPath($entry) }
        }
    }
    return $null
}

function Test-SetupOpenClaw([string]$BinDirectory, [string]$Node) {
    if (-not (Test-Path -LiteralPath (Join-Path $BinDirectory 'openclaw.cmd'))) { return $false }
    $entry = Get-SetupOpenClawEntry $BinDirectory
    if (-not $entry) { return $false }
    $result = Invoke-SetupProcess $Node @($entry, '--version')
    if ($result.Code -ne 0) { throw ('已安装 OpenClaw 的版本检查失败，未重复下载。请检查运行诊断：' + $result.Text) }
    $version = Get-SetupOpenClawVersion $result.Text
    if (-not $version) { throw '无法确认已安装 OpenClaw 的版本，未重复下载。请检查运行诊断。' }
    return [version]$version -ge [version]$script:SetupOpenClawVersion
}

function Get-SetupOpenClawVersion([string]$Text) {
    $plain = [regex]::Replace($Text, ([string][char]27 + '\[[0-9;]*m'), '')
    $match = [regex]::Match($plain, '(?im)^\s*(?:OpenClaw\s+)?([0-9]{4}[.][0-9]+[.][0-9]+)(?:\s|\(|$)')
    if ($match.Success) { return $match.Groups[1].Value }
    return $null
}

function Ensure-SetupOpenClaw([string]$ProjectRoot, [string]$CacheRoot) {
    Set-StartupStage 4 '检查 Node.js'
    $node = $null
    foreach ($candidate in (Get-SetupNodeCandidates $ProjectRoot $CacheRoot)) {
        if (Test-SetupNode $candidate) { $node = $candidate; break }
    }
    if (-not $node) {
        $architecture = Get-SetupArchitecture
        $target = Join-Path $CacheRoot ('node-v' + $script:SetupNodeVersion + '-win-' + $architecture)
        Install-SetupArchive (Get-SetupDownloadSpec 'node' $architecture) $target $CacheRoot 'node'
        $node = Join-Path $target 'node.exe'
    }
    Write-StartupMessage 'Node.js 已就绪。'
    Set-StartupStage 5 '检查 OpenClaw'
    $nodeDirectory = Split-Path $node -Parent
    $env:PATH = $nodeDirectory + ';' + $env:PATH
    $bins = Get-SetupOpenClawCandidates $ProjectRoot $CacheRoot
    $prefix = Join-Path $CacheRoot ('openclaw-' + $script:SetupOpenClawVersion)
    $binary = $null
    foreach ($candidate in $bins) {
        if (Test-SetupOpenClaw $candidate $node) { $binary = $candidate; break }
    }
    if (-not $binary) {
        $npm = Join-Path $nodeDirectory 'node_modules/npm/bin/npm-cli.js'
        if (-not (Test-Path -LiteralPath $npm)) { throw '所选 Node.js 缺少 npm，请配置完整的运行环境。' }
        Write-Host '正在安装 OpenClaw 2026.9.6，已有下载缓存会复用……' -ForegroundColor Cyan
        $result = Invoke-SetupProcess $node @($npm, 'install', '--prefix', $prefix, '--save-exact', '--no-audit', '--no-fund', '--prefer-offline', '--cache', (Join-Path $CacheRoot 'npm-cache'), ('openclaw@' + $script:SetupOpenClawVersion)) -Live
        $binary = Join-Path $prefix 'node_modules/.bin'
        if ($result.Code -ne 0 -or -not (Test-SetupOpenClaw $binary $node)) { throw ('OpenClaw 安装未完成：' + $result.Text) }
    }
    $entry = Get-SetupOpenClawEntry $binary
    $versionOutput = Invoke-SetupProcess $node @($entry, '--version')
    if ($versionOutput.Code -ne 0) { throw ('已安装 OpenClaw 的版本检查失败，未重复下载：' + $versionOutput.Text) }
    $version = Get-SetupOpenClawVersion $versionOutput.Text
    if (-not $version) { throw '无法确认 OpenClaw 版本，未保存环境路径。' }
    $manifest = @{ enabled = $true; node_dir = $nodeDirectory; bin_dir = [IO.Path]::GetFullPath($binary);
                   node_version = (Invoke-SetupProcess $node @('--version')).Text.Trim().TrimStart('v');
                   openclaw_version = $version; reason = 'Startup preflight verified installed executables.' }
    Write-SetupJson (Join-Path $ProjectRoot 'data/runtime.json') $manifest
    $env:WECHAT_AI_NODE = $node
    $env:WECHAT_AI_OPENCLAW_ENTRY = $entry
    $env:PATH = $nodeDirectory + ';' + $binary + ';' + $env:PATH
    # npm global shims may prefer an adjacent obsolete node.exe; invoke the
    # verified executable directly for every command in this PowerShell process.
    function global:openclaw { & $env:WECHAT_AI_NODE $env:WECHAT_AI_OPENCLAW_ENTRY @args; $global:LASTEXITCODE = $LASTEXITCODE }
    $script:ProjectRuntimeRequiresStopForce = [version]$version -ge [version]'2026.9.0'
    Write-StartupMessage ('OpenClaw ' + $version + ' 已就绪。')
    return [pscustomobject]@{ Node = $node; Bin = $binary; Version = $version }
}

function Get-SetupOpenClawCandidates([string]$ProjectRoot, [string]$CacheRoot) {
    $saved = Read-SetupJson (Join-Path $ProjectRoot 'data/runtime.json')
    $bins = @()
    if ($saved.enabled -eq $true -and $saved.bin_dir) { $bins += [string]$saved.bin_dir }
    $prefix = Join-Path $CacheRoot ('openclaw-' + $script:SetupOpenClawVersion)
    $bins += Join-Path $prefix 'node_modules/.bin'
    $state = if ($env:OPENCLAW_STATE_DIR) { $env:OPENCLAW_STATE_DIR } else { Join-Path $env:USERPROFILE '.openclaw' }
    $bins += Join-Path $state ('wechat-runtime/openclaw-' + $script:SetupOpenClawVersion + '/node_modules/.bin')
    $bins += @(Get-Command openclaw.cmd -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { Split-Path $_.Source -Parent })
    return @($bins | Select-Object -Unique)
}

function Ensure-SetupWeixin {
    Set-StartupStage 6 '检查微信渠道插件'
    if (-not (Test-WeixinPluginInstalled)) {
        Write-Host ('正在补齐微信插件：' + $script:WeixinPluginCheckReason) -ForegroundColor Cyan
        $result = Invoke-SetupProcess $env:WECHAT_AI_NODE @($env:WECHAT_AI_OPENCLAW_ENTRY, 'plugins', 'install', '@tencent-weixin/openclaw-weixin@2.4.9') -Live
        if ($result.Code -ne 0) { throw ('微信插件安装失败，未启动服务：' + $result.Text) }
        if (-not (Test-WeixinPluginInstalled)) { throw '微信插件安装后未通过检查，未标记为安装完成。' }
    }
    Write-StartupMessage '微信渠道插件已就绪。'
}

function Invoke-ProjectEnvironment {
    param([string]$ProjectRoot = ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))),
          [switch]$Python, [switch]$OpenClaw, [switch]$Weixin)
    $cache = Get-SetupCacheRoot
    Set-StartupStage 1 '检查安装缓存与安装锁'
    [IO.Directory]::CreateDirectory($cache) | Out-Null
    $lockPath = Join-Path $cache 'environment.lock'
    $lock = $null
    $deadline = [DateTime]::UtcNow.AddMinutes(30)
    $lastWaitLog=[DateTime]::MinValue
    while ($null -eq $lock) {
        try { $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw '另一窗口仍在准备环境，请等待它完成后重试。' }
            if (([DateTime]::UtcNow - $lastWaitLog).TotalSeconds -ge 5) {
                Write-StartupMessage '另一窗口正在准备运行环境，正在等待，完成后会直接复用……'
                Update-StartupDetail '等待另一窗口完成安装……'
                $lastWaitLog=[DateTime]::UtcNow
            }
            [Threading.Thread]::Sleep(250)
        }
    }
    try {
        Import-SetupLegacyCache $cache
        $env:npm_config_cache = Join-Path $cache 'npm-cache'
        $result = [ordered]@{}
        if ($Python) {
            $result.Python = Ensure-SetupPython $ProjectRoot $cache
            $env:WECHAT_AI_PYTHON=$result.Python
        }
        if ($OpenClaw -or $Weixin) { $result.Runtime = Ensure-SetupOpenClaw $ProjectRoot $cache }
        if ($Weixin) {
            if (-not (Get-Command Test-WeixinPluginInstalled -ErrorAction SilentlyContinue)) {
                . (Join-Path $PSScriptRoot 'weixin_environment.ps1')
            }
            Ensure-SetupWeixin
            $result.WeixinVerified = $true
        }
        Write-Host '运行环境检查通过，已安装且完整的组件直接复用。' -ForegroundColor Green
        return [pscustomobject]$result
    } finally { $lock.Dispose() }
}
