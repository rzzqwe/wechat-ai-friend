# Visible progress shared by the three batch launchers; percentages describe steps.
$script:StartupState = $null

function Start-StartupProgress([string]$Title, [int]$Total = 7) {
    $script:StartupState = @{ Title=$Title; Total=$Total; Step=0; Name='准备启动'; Clock=[Diagnostics.Stopwatch]::StartNew(); Done=$false }
    Write-Host ('========== ' + $Title + ' · 实时进度 ==========') -ForegroundColor Cyan
}

function Set-StartupStage([int]$Step, [string]$Name) {
    if (-not $script:StartupState) { return }
    $script:StartupState.Step=$Step
    $script:StartupState.Name=$Name
    Write-Host ('[{0:HH:mm:ss}] [{1}/{2}] {3}' -f [DateTime]::Now, $Step, $script:StartupState.Total, $Name) -ForegroundColor Cyan
    Update-StartupDetail $Name
}

function Update-StartupDetail([string]$Status, [int]$Percent = -1, [string]$Operation = '') {
    if (-not $script:StartupState -or $script:StartupState.Done) { return }
    $completed = [Math]::Max(0, $script:StartupState.Step - 1)
    $overall = [int](100 * $completed / $script:StartupState.Total)
    Write-Progress -Id 1 -Activity $script:StartupState.Title -Status ('步骤 {0}/{1}：{2}' -f $script:StartupState.Step, $script:StartupState.Total, $script:StartupState.Name) -PercentComplete $overall
    Write-Progress -Id 2 -ParentId 1 -Activity $script:StartupState.Name -Status $Status -PercentComplete $Percent -CurrentOperation ($Operation + '  已用时 ' + [int]$script:StartupState.Clock.Elapsed.TotalSeconds + ' 秒')
}

function Write-StartupMessage([string]$Message) {
    Write-Host ('[{0:HH:mm:ss}] {1}' -f [DateTime]::Now, $Message)
}

function Complete-StartupProgress([string]$Message) {
    if ($script:StartupState) {
        $script:StartupState.Done=$true
        Write-Progress -Id 2 -Completed -Activity $script:StartupState.Name
        Write-Progress -Id 1 -Completed -Activity $script:StartupState.Title
        Write-Host ('[{0:HH:mm:ss}] [完成] {1}（共 {2:N1} 秒）' -f [DateTime]::Now, $Message, $script:StartupState.Clock.Elapsed.TotalSeconds) -ForegroundColor Green
    } else { Write-Host $Message -ForegroundColor Green }
}

function Fail-StartupProgress([string]$Message) {
    if ($script:StartupState) {
        Write-Progress -Id 2 -Completed -Activity $script:StartupState.Name
        Write-Progress -Id 1 -Completed -Activity $script:StartupState.Title
        $Message = ('步骤 {0}/{1}「{2}」失败：{3}' -f $script:StartupState.Step, $script:StartupState.Total, $script:StartupState.Name, $Message)
    }
    Write-Host $Message -ForegroundColor Red
}

function Protect-StartupOutput([string]$Text) {
    foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -match '(API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD)$' })) {
        if ($item.Value.Length -ge 6) { $Text=$Text.Replace($item.Value, '[已隐藏]') }
    }
    return $Text
}

function ConvertTo-StartupArgument([string]$Value) {
    if ($Value -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-StartupProcess([string]$Executable, [string[]]$Arguments, [string]$ReadyPattern = '', [switch]$Quiet, [scriptblock]$LineHandler, [switch]$KeepConsole) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$Executable
    $info.Arguments=(@($Arguments | ForEach-Object { ConvertTo-StartupArgument $_ }) -join ' ')
    $info.UseShellExecute=$false
    $info.CreateNoWindow=(-not $KeepConsole)
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $info.StandardOutputEncoding=New-Object Text.UTF8Encoding($false)
    $info.StandardErrorEncoding=New-Object Text.UTF8Encoding($false)
    $info.EnvironmentVariables['PYTHONUTF8']='1'
    $info.EnvironmentVariables['PYTHONUNBUFFERED']='1'
    if ($ReadyPattern) { $info.EnvironmentVariables['WECHAT_AI_STARTUP_READY']='1' }
    $process = New-Object Diagnostics.Process
    $process.StartInfo=$info
    $tail = New-Object Text.StringBuilder
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $nextHeartbeat=5.0
    try {
        if (-not $process.Start()) { throw '无法启动子进程。' }
        $outputTask=$process.StandardOutput.ReadLineAsync()
        $errorTask=$process.StandardError.ReadLineAsync()
        while ($null -ne $outputTask -or $null -ne $errorTask -or -not $process.HasExited) {
            foreach ($kind in @('output','error')) {
                $task = if ($kind -eq 'output') { $outputTask } else { $errorTask }
                while ($null -ne $task -and $task.IsCompleted) {
                $line=$task.GetAwaiter().GetResult()
                if ($null -eq $line) {
                    if ($kind -eq 'output') { $outputTask=$null } else { $errorTask=$null }
                    break
                }
                if ($LineHandler -and (& $LineHandler $line)) {
                    # Internal stage markers are consumed by the caller.
                } elseif ($ReadyPattern -and $line -ceq $ReadyPattern) {
                    Complete-StartupProgress '工作台已打开，可以开始配置和使用。'
                } else {
                    $safe=Protect-StartupOutput $line
                    [void]$tail.AppendLine($safe)
                    if (-not $Quiet -and $tail.Length -gt 65536) { [void]$tail.Remove(0, $tail.Length - 65536) }
                    if (-not $Quiet) { Write-Host $safe }
                }
                if ($kind -eq 'output') { $outputTask=$process.StandardOutput.ReadLineAsync() }
                else { $errorTask=$process.StandardError.ReadLineAsync() }
                $task = if ($kind -eq 'output') { $outputTask } else { $errorTask }
                }
            }
            if ($clock.Elapsed.TotalSeconds -ge $nextHeartbeat) {
                if (-not $script:StartupState -or -not $script:StartupState.Done) {
                    Write-StartupMessage ('仍在处理，已等待 {0:N0} 秒……' -f $clock.Elapsed.TotalSeconds)
                    Update-StartupDetail ('正在处理，已等待 {0:N0} 秒' -f $clock.Elapsed.TotalSeconds)
                }
                $nextHeartbeat=$clock.Elapsed.TotalSeconds+5
            }
            [Threading.Thread]::Sleep(50)
        }
        $process.WaitForExit()
        return [pscustomobject]@{Code=$process.ExitCode; Text=$tail.ToString()}
    } finally { $process.Dispose() }
}

function Show-StartupDownloadProgress([long]$Received, [long]$Total, $Clock, [string]$Name) {
    $megabytes=$Received / 1MB
    $speed=$megabytes / [Math]::Max(0.1, $Clock.Elapsed.TotalSeconds)
    if ($Total -gt 0) {
        $percent=[Math]::Min(100, [int](100.0 * $Received / $Total))
        $status='{0}% · {1:N1}/{2:N1} MB · {3:N1} MB/s' -f $percent, $megabytes, ($Total / 1MB), $speed
    } else { $percent=-1; $status='{0:N1} MB · {1:N1} MB/s' -f $megabytes, $speed }
    Update-StartupDetail $status $percent $Name
    return $status
}
