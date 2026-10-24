[CmdletBinding()]
param(
    [datetime]$Since = (Get-Date).AddDays(-1),
    [datetime]$Until = (Get-Date),
    [ValidateRange(1, 10000)][int]$MaxEvents = 10000
)
$ErrorActionPreference = 'Stop'
if ($Since -gt $Until) { throw 'Since must not exceed Until.' }
$archiveLogName = 'Microsoft-Windows-TaskScheduler/Operational'
$archiveLog = Get-WinEvent -ListLog $archiveLogName
if (-not $archiveLog.IsEnabled) { throw 'Task history is disabled. This script never enables it.' }
# Read existing events only. Never register, trigger, restart or modify a task.
$archiveEvents = @()
try {
    $archiveEvents = @(Get-WinEvent -FilterHashtable @{
        LogName = $archiveLogName; StartTime = $Since; EndTime = $Until
    } -MaxEvents $MaxEvents)
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
}
Write-Output '<Events>'
foreach ($archiveEvent in $archiveEvents) {
    [xml]$archiveXml = $archiveEvent.ToXml()
    $archiveTaskFields = @($archiveXml.Event.EventData.Data | Where-Object Name -eq 'TaskName')
    if ($archiveTaskFields.Count -eq 1 -and $archiveTaskFields[0].InnerText -eq '\quant-lab-archive-observer') {
        Write-Output $archiveEvent.ToXml()
    }
}
Write-Output '</Events>'
