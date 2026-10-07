param(
    [Parameter(Mandatory = $true)][string]$ExpectedUserSid,
    [Parameter(Mandatory = $true)][string]$ExpectedLauncher,
    [Parameter(Mandatory = $true)][string]$ReportDirectory
)
$ErrorActionPreference = 'Stop'
$reportPath = Join-Path $ReportDirectory 'task-permission-result.json'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows administrator consent is required for this one-time task ACL repair.'
    }
    if ($identity.User.Value -ne $ExpectedUserSid) { throw 'The elevated Windows identity changed.' }
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $task = $service.GetFolder('\').GetTask('OpenClaw Gateway')
    $definition = $task.Definition
    $taskUser = [Security.Principal.NTAccount]::new($definition.Principal.UserId)
    if ($taskUser.Translate([Security.Principal.SecurityIdentifier]).Value -ne $ExpectedUserSid) {
        throw 'This task belongs to a different Windows user.'
    }
    if ($definition.Principal.RunLevel -ne 0 -or $definition.Actions.Count -ne 1) {
        throw 'Only the existing, limited-privilege, single-action user task is supported.'
    }
    if ([IO.Path]::GetFullPath($definition.Actions.Item(1).Path) -ne [IO.Path]::GetFullPath($ExpectedLauncher)) {
        throw 'The gateway task launcher no longer matches the inspected path.'
    }
    [IO.Directory]::CreateDirectory($ReportDirectory) | Out-Null
    $original = $task.GetSecurityDescriptor(5)
    $backupPath = Join-Path $ReportDirectory 'task-original-sddl.txt'
    if (-not (Test-Path -LiteralPath $backupPath)) {
        [IO.File]::WriteAllText($backupPath, $original, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText((Join-Path $ReportDirectory 'task-original.xml'), $task.Xml, [Text.UTF8Encoding]::new($false))
    }
    $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($original)
    $sid = [Security.Principal.SecurityIdentifier]::new($ExpectedUserSid)
    # FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE.
    # No ownership, DACL administration, elevated run level, or other task changes.
    $requiredMask = 0x1201BF
    $alreadyGranted = $false
    foreach ($ace in $descriptor.DiscretionaryAcl) {
        if ($ace -is [Security.AccessControl.CommonAce] -and $ace.SecurityIdentifier -eq $sid -and
            $ace.AceQualifier -eq [Security.AccessControl.AceQualifier]::AccessAllowed -and
            ($ace.AccessMask -band $requiredMask) -eq $requiredMask) { $alreadyGranted = $true }
    }
    if (-not $alreadyGranted) {
        $ace = [Security.AccessControl.CommonAce]::new(
            [Security.AccessControl.AceFlags]::None,
            [Security.AccessControl.AceQualifier]::AccessAllowed,
            $requiredMask, $sid, $false, $null)
        $descriptor.DiscretionaryAcl.InsertAce(0, $ace)
        $task.SetSecurityDescriptor($descriptor.GetSddlForm([Security.AccessControl.AccessControlSections]::Access), 0x10)
    }
    $result = @{ok=$true;changed=(-not $alreadyGranted);task='OpenClaw Gateway';sid=$ExpectedUserSid;before=$original;after=$task.GetSecurityDescriptor(5);at=[DateTime]::Now.ToString('o')}
    [IO.File]::WriteAllText($reportPath, ($result | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    exit 0
} catch {
    [IO.Directory]::CreateDirectory($ReportDirectory) | Out-Null
    [IO.File]::WriteAllText($reportPath, (@{ok=$false;error=$_.Exception.Message;at=[DateTime]::Now.ToString('o')} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    exit 1
}
