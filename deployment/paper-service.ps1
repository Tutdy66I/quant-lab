[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Status', 'InstallTask', 'RemoveTask')]
    [string]$Action = 'Start',
    [string]$PythonExe = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root 'strategies\paper_trader.py'
$dataPath = Join-Path $root 'runtime\paper_v2'
$taskName = 'quant-lab-paper-observer'
if (-not $PythonExe) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $PythonExe = $bundled }
    else { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Provide a working -PythonExe path.' }
New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
$runnerPath = Join-Path $dataPath 'runner.json'
function Get-Observer {
    if (-not (Test-Path -LiteralPath $runnerPath)) { return $null }
    $runner = Get-Content -LiteralPath $runnerPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$runner.pid) -ErrorAction Stop
    if ($process -and $process.CommandLine -and $process.CommandLine.Contains($scriptPath) -and $process.CommandLine.Contains('--loop')) {
        return $process
    }
    return $null
}
switch ($Action) {
    'Start' {
        $existing = Get-Observer
        if ($existing) { Write-Output ('Observer already running: ' + $existing.ProcessId); break }
        $arguments = @('-u', ('"' + $scriptPath + '"'), '--loop', '--data-dir', ('"' + $dataPath + '"'))
        $env:PYTHONUTF8 = '1'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $process = Start-Process -FilePath $PythonExe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataPath 'runner.stdout.log') -RedirectStandardError (Join-Path $dataPath 'runner.stderr.log')
        Write-Output ('Observer launched: ' + $process.Id)
    }
    'Stop' { New-Item -ItemType File -Path (Join-Path $dataPath 'STOP') -Force | Out-Null; Write-Output 'Graceful stop requested.' }
    'Status' {
        $observer = Get-Observer
        [PSCustomObject]@{running=[bool]$observer; pid=$observer.ProcessId} | ConvertTo-Json -Compress
        & $PythonExe $scriptPath --data-dir $dataPath --status
        $healthExit = $LASTEXITCODE
        if (-not $observer) { exit 1 }
        exit $healthExit
    }
    'InstallTask' {
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $hostExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action Start -PythonExe "' + $PythonExe + '"'
        $actionSpec = New-ScheduledTaskAction -Execute $hostExe -Argument $arguments -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $actionSpec -Trigger $trigger -Principal $principal -Settings $settings -Description 'quant-lab public-data paper model observer; no exchange keys or real orders' -Force | Out-Null
        Write-Output ('Logon task registered: ' + $taskName)
    }
    'RemoveTask' { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue; Write-Output 'Logon task removed.' }
}
