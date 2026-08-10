# ny_window.ps1 - shared DST gate for the scheduled stages. Dot-sourced by draft_only.ps1,
# followup_only.ps1 and send_only.ps1.
#
# WHY THIS EXISTS
# The send time is specified in NEW YORK local time (recipients are Eastern), but Windows
# Task Scheduler fires on THIS machine's clock, which is Arabian Standard Time - UTC+4 with
# no daylight saving. New York observes DST, so the Gulf clock time that corresponds to a
# fixed New York time MOVES BY AN HOUR twice a year:
#
#     Mar-Nov  New York is EDT (UTC-4), 8h behind Gulf  ->  6:03am NY = 2:03pm Gulf
#     Nov-Mar  New York is EST (UTC-5), 9h behind Gulf  ->  6:03am NY = 3:03pm Gulf
#
# Hardcoding one Gulf time would silently send an hour off target for ~4 months of the year,
# and "remember to shift the tasks every November" is exactly the kind of maintenance that
# gets forgotten (see SYSTEM.md section 8 - every outage this system has had was silent).
#
# So each task carries TWO daily triggers, one per DST regime, and every runner calls
# Test-NewYorkWindow first. The trigger that lands on the intended New York time runs; the
# other one exits in under a second having done nothing. Correct on both sides of the
# transition, and on the transition day itself, with no annual edit.

function Get-NewYorkNow {
    # 'Eastern Standard Time' is the Windows ID for the whole US Eastern zone; the
    # conversion applies EDT/EST automatically according to the date.
    $ny = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    return [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $ny)
}

function Test-NewYorkWindow {
    <#
      Returns $true only if New York local time is currently within $ToleranceMinutes of
      $TargetHHmm. The two candidate triggers are an hour apart, so a 20 minute tolerance
      absorbs Task Scheduler's start delay while never letting both triggers pass.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$TargetHHmm,
        [int]$ToleranceMinutes = 20,
        [string]$CronLog,
        [string]$Stage = 'stage'
    )
    $now = Get-NewYorkNow
    $p = $TargetHHmm.Split(':')
    $target = $now.Date.AddHours([int]$p[0]).AddMinutes([int]$p[1])

    # Wrap the difference around the day so a target near midnight can't read as ~24h of
    # drift. Irrelevant at the current early-morning targets, wrong the day someone moves
    # the send to 00:15 and can't work out why the gate never opens.
    $diff = [math]::Abs(($now - $target).TotalMinutes) % 1440
    $drift = [math]::Min($diff, 1440 - $diff)

    $nyLabel = $now.ToString('HH:mm')
    $tz = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    $regime = if ($tz.IsDaylightSavingTime([DateTime]::UtcNow)) { 'EDT' } else { 'EST' }

    if ($drift -le $ToleranceMinutes) {
        if ($CronLog) {
            Add-Content -Path $CronLog -Value "$(Get-Date -Format o) $Stage gate OK: New York $nyLabel $regime (target $TargetHHmm)."
        }
        return $true
    }
    # Distinguish the two reasons a gate closes. ~60 min off is the OTHER DST trigger doing
    # exactly its job, and should read as routine. Anything else means this stage fired at a
    # time it was never scheduled for - almost always Task Scheduler's -StartWhenAvailable
    # catching up a run the machine slept through - and that day's stage will NOT run. Say so
    # loudly: an unnoticed missed day is this system's signature failure (SYSTEM.md section 8).
    if ($CronLog) {
        $off = [math]::Round($drift)
        if ($drift -ge 45 -and $drift -le 75) {
            Add-Content -Path $CronLog -Value "$(Get-Date -Format o) $Stage gate SKIP: New York $nyLabel $regime, target $TargetHHmm (off by $off min). Routine: this is the off-season DST trigger, its twin fires today."
        } else {
            Add-Content -Path $CronLog -Value "$(Get-Date -Format o) ** $Stage MISSED WINDOW: fired at New York $nyLabel $regime, $off min from the $TargetHHmm target. Nothing ran. Machine likely asleep at trigger time (a late catch-up is suppressed on purpose - the whole point is landing at $TargetHHmm New York). Re-run by hand with -Force if today still matters."
        }
    }
    return $false
}
