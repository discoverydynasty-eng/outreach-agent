# register_tasks.ps1 - registers the Windows Task Scheduler jobs for the whole pipeline.
#
#   HVAC Draft      -> draft_only.ps1     daily 12:35pm + 1:35pm Gulf  (= 4:35am New York)
#   HVAC Followups  -> followup_only.ps1  daily  1:58pm + 2:58pm Gulf  (= 5:58am New York)
#   HVAC Send       -> send_only.ps1      daily  2:03pm + 3:03pm Gulf  (= 6:03am New York)
#   HVAC Weekly Summary -> weekly_run.ps1 Monday 8:30am                (inbox digest)
#
# Draft/Followups/Send are 3 separate tasks (not one script) so the SEND always fires at an
# exact clock minute regardless of how long sourcing or the follow-up pass took that day.
# Followups run first since they're already-overdue contacts; new cold leads follow 5 min
# later. The ~83 min gap before the send covers a slow sourcing run (bounded to 25 min).
#
# WHY TWO TRIGGERS PER TASK
# The times that matter are NEW YORK times, but Task Scheduler fires on this machine's
# clock (Arabian Standard Time, UTC+4, no DST). New York shifts an hour twice a year, so
# one fixed Gulf time cannot mean one fixed New York time. Each task therefore carries a
# trigger for each regime - the EDT one and the EST one, an hour apart - and every runner
# calls Test-NewYorkWindow (ny_window.ps1) first, exiting immediately unless it is the
# trigger that lands on the intended New York time. Self-correcting across the November and
# March transitions with no annual edit.
#
# Run this ONCE, in a normal (non-elevated is fine) PowerShell window, only AFTER a clean
# hand-run of each stage (SETUP_WINDOWS.md). Re-running it re-registers (updates) safely.
#
#   powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1

$ErrorActionPreference = 'Stop'

$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
$draft     = Join-Path $ProjectDir 'draft_only.ps1'
$followup  = Join-Path $ProjectDir 'followup_only.ps1'
$send      = Join-Path $ProjectDir 'send_only.ps1'
$weekly    = Join-Path $ProjectDir 'weekly_run.ps1'

foreach ($f in @($draft, $followup, $send, $weekly)) {
    if (-not (Test-Path $f)) { throw "Missing runner: $f" }
}

# -StartWhenAvailable catches up a run the PC missed while off (like launchd).
# -WakeToRun wakes the machine from sleep to run it.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
# NOTE: registered WITHOUT -RunLevel Highest on purpose - that needs an elevated shell to
# register and throws Access Denied otherwise. Not elevated works fine for these tasks.

# One trigger per New York DST regime; the runner's gate picks the right one each day.
function New-DualTrigger([string]$EdtGulf, [string]$EstGulf) {
    @((New-ScheduledTaskTrigger -Daily -At ([datetime]$EdtGulf)),
      (New-ScheduledTaskTrigger -Daily -At ([datetime]$EstGulf)))
}

$draftAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$draft`""
Register-ScheduledTask -TaskName 'HVAC Draft' -Force `
    -Action $draftAction -Trigger (New-DualTrigger '12:35' '13:35') -Settings $settings `
    -Description 'HVAC outreach: source + draft new cold leads into outbox.json (draft_only.ps1). Does not send. Two triggers, one per New York DST regime; the runner gates on 4:35am New York.' | Out-Null
Write-Output "Registered: 'HVAC Draft' (12:35pm + 1:35pm Gulf = 4:35am New York)"

$followupAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$followup`""
Register-ScheduledTask -TaskName 'HVAC Followups' -Force `
    -Action $followupAction -Trigger (New-DualTrigger '13:58' '14:58') -Settings $settings `
    -Description 'HVAC outreach: 3-step follow-up sequence, threaded onto each original email (followup_guard.py). Two triggers, one per New York DST regime; the runner gates on 5:58am New York.' | Out-Null
Write-Output "Registered: 'HVAC Followups' (1:58pm + 2:58pm Gulf = 5:58am New York)"

$sendAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$send`""
Register-ScheduledTask -TaskName 'HVAC Send' -Force `
    -Action $sendAction -Trigger (New-DualTrigger '14:03' '15:03') -Settings $settings `
    -Description 'HVAC outreach: send whatever HVAC Draft queued (send_guard.py). Two triggers, one per New York DST regime; the runner gates on 6:03am New York.' | Out-Null
Write-Output "Registered: 'HVAC Send' (2:03pm + 3:03pm Gulf = 6:03am New York)"

$weeklyAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$weekly`""
Register-ScheduledTask -TaskName 'HVAC Weekly Summary' -Force `
    -Action $weeklyAction -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 8:30am) -Settings $settings `
    -Description 'HVAC outreach: weekly digest email (weekly_summary.py).' | Out-Null
Write-Output "Registered: 'HVAC Weekly Summary' (Monday 8:30am)"

Write-Output ""
Write-Output "All 4 tasks registered. Verify with:  Get-ScheduledTask -TaskName 'HVAC*'"
Write-Output "Kill switch (pause everything that sends): Disable-ScheduledTask -TaskName 'HVAC Send','HVAC Followups'"
