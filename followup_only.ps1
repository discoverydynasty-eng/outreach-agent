# followup_only.ps1 - runs the 3-step follow-up sequence (followup_guard.py). Added
# 2026-07-18. Threads onto each contact's original email; never a fresh cold email.
#
# Scheduled to fire before the cold-send stage each day, so follow-ups (time-sensitive,
# already-overdue contacts) go out first, ahead of that day's new cold outreach.
#
# -Force skips the New York time gate, for hand-running the pass off-schedule.

param([switch]$Force)

$ErrorActionPreference = 'Stop'
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
Set-Location $ProjectDir
$logDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$cronLog = Join-Path $logDir 'cron.log'
$env:PYTHONUTF8 = '1'

# Two daily triggers exist (1:58pm and 2:58pm Gulf), one per New York DST regime. Exit
# unless this is the one that lands on 5:58am New York, five minutes ahead of the send.
. (Join-Path $ProjectDir 'ny_window.ps1')
if (-not $Force -and -not (Test-NewYorkWindow -TargetHHmm '05:58' -CronLog $cronLog -Stage 'followup')) { exit 0 }

$envFile = Join-Path $ProjectDir '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
            $i = $t.IndexOf('='); $k = $t.Substring(0, $i).Trim(); $v = $t.Substring($i + 1).Trim()
            if ($k) { Set-Item -Path "Env:$k" -Value $v }
        }
    }
}

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$logFile = Join-Path $logDir "followup_$stamp.log"
Add-Content -Path $cronLog -Value "$(Get-Date -Format o) === followup stage firing ==="

$venvPython = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$ErrorActionPreference = 'Continue'
# Suppress hard-bounced addresses BEFORE the follow-up pass, so a mailbox that already
# 5xx'd on the cold email never gets emails 2 and 3. Mailing known-dead addresses
# repeatedly is a top-tier negative reputation signal, and follow-ups triple the exposure.
& $venvPython (Join-Path $ProjectDir 'bounce_scan.py') *> $logFile
& $venvPython (Join-Path $ProjectDir 'followup_guard.py') *>> $logFile
$ErrorActionPreference = 'Stop'

# Tail 12 (not 3) so the whole QUEUE HEALTH block and any ** WARNING lines land in
# cron.log - that's the file you actually skim, and a warning nobody sees is useless.
Add-Content -Path $cronLog -Value (Get-Content $logFile -Tail 12 -ErrorAction SilentlyContinue)
