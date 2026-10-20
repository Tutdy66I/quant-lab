#Requires -RunAsAdministrator
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$statusPath = Join-Path $runtime 'prerequisites-status.json'
Start-Transcript -Path (Join-Path $runtime 'prerequisites-install.log') -Append | Out-Null
$restartNeeded = $false
function Save-InstallStatus([string]$State, [string]$Stage, [string]$Message='') {
    [PSCustomObject]@{status=$State; stage=$Stage; message=$Message; pid=$PID; observed_at=[DateTime]::UtcNow.ToString('o'); restart_required=$restartNeeded} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
try {
    Save-InstallStatus 'running' 'features'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Install Microsoft App Installer (winget) first.' }
    foreach ($feature in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
        $current = Get-WindowsOptionalFeature -Online -FeatureName $feature
        if ($current.State -ne 'Enabled') {
            $result = Enable-WindowsOptionalFeature -Online -FeatureName $feature -All -NoRestart
            $restartNeeded = $restartNeeded -or $result.RestartNeeded
        }
    }
    foreach ($package in @('Microsoft.WSL', 'Docker.DockerDesktop')) {
        Save-InstallStatus 'running' $package
        & winget install --exact --id $package --source winget --silent --disable-interactivity --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 3010) { $restartNeeded = $true }
        elseif ($LASTEXITCODE -ne 0) { throw ('Package installation needs attention: ' + $package + ', exit ' + $LASTEXITCODE) }
    }
    Save-InstallStatus 'completed' 'initialization_pending' 'Initialize Ubuntu and Docker Desktop after any required restart.'
    Write-Output ('Prerequisite installation finished. Restart required by feature installer: ' + $restartNeeded)
    Write-Output 'No reboot was issued. After arranging any required restart, initialize Ubuntu and Docker Desktop, then run deployment.preflight.'
} catch {
    Save-InstallStatus 'error' 'installation' $_.Exception.Message
    throw
} finally {
    Stop-Transcript | Out-Null
}
