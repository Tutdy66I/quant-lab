[CmdletBinding()]
param(
    [ValidateSet('Plan', 'RunOnce', 'Health', 'Install', 'Disable', 'Status', 'Remove')]
    [string]$Action = 'Plan',
    [string]$PythonExe = '',
    [string]$DataRoot = '',
    [switch]$ConfirmEnable,
    [switch]$PreviousCollectorStopped
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'quant-lab-archive-observer'
if (-not $DataRoot) { $DataRoot = Join-Path $projectRoot 'runtime\archive_observer_v1' }
$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
if (-not $PythonExe) { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
$PythonExe = [System.IO.Path]::GetFullPath($PythonExe)
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Supply an existing Python 3.10+ executable.' }
# Command-line quotes cannot be embedded in a Windows path. Never construct a shell expression.
foreach ($value in @($PythonExe, $DataRoot, $projectRoot)) {
    if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Invalid path.' }
}
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
switch ($Action) {
    'RunOnce' {
        Push-Location $projectRoot
        try { & $PythonExe -m deployment.archive_service run --root $DataRoot; $code = $LASTEXITCODE }
        finally { Pop-Location }
        exit $code
    }
    'Health' {
        Push-Location $projectRoot
        try { & $PythonExe -m deployment.archive_service health --root $DataRoot; $code = $LASTEXITCODE }
        finally { Pop-Location }
        exit $code
    }
    'Disable' { Disable-ScheduledTask -TaskName $taskName | Out-Null; return }
    'Remove' { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; return }
    'Status' {
        Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State, Settings, Triggers
        Get-ScheduledTaskInfo -TaskName $taskName
        return
    }
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$hostExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "' + $PSCommandPath + '" -Action RunOnce -PythonExe "' + $PythonExe + '" -DataRoot "' + $DataRoot + '"'
function Escape-Xml([string]$value) { return [System.Security.SecurityElement]::Escape($value) }
$boundary = (Get-Date).AddMinutes(5).ToString('yyyy-MM-ddTHH:mm:ss')
# Indefinite hourly repetition plus logon; no password, elevation, wake timer or resident loop.
$definition = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Quant Lab public archive operations only. Hourly and logon. No trading.</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger><Repetition><Interval>PT1H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled></TimeTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>$(Escape-Xml $user)</UserId><Delay>PT1M</Delay></LogonTrigger>
  </Triggers>
  <Principals><Principal id="Observer"><UserId>$(Escape-Xml $user)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun><ExecutionTimeLimit>PT20M</ExecutionTimeLimit><Priority>7</Priority>
  </Settings>
  <Actions Context="Observer"><Exec><Command>$(Escape-Xml $hostExe)</Command><Arguments>$(Escape-Xml $arguments)</Arguments><WorkingDirectory>$(Escape-Xml $projectRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($Action -eq 'Plan') { $definition; return }
if (-not $ConfirmEnable -or -not $PreviousCollectorStopped) {
    throw 'Install requires user approval (-ConfirmEnable) and prior collector handover (-PreviousCollectorStopped). Plan does not register or enable any task.'
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Task exists: inspect it; never overwrite implicitly.' }
Push-Location $projectRoot
try {
    & $PythonExe -m deployment.archive_service health --root $DataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Initialize/repair the offline operations baseline first.' }
} finally { Pop-Location }
Register-ScheduledTask -TaskName $taskName -Xml $definition | Out-Null
Write-Output 'Task registered. First time trigger is in five minutes; logon also triggers it. Inspect Status and health files.'
