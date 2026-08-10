# send_only.ps1 - STAGE 2 ONLY: send whatever draft_only.ps1 already queued in outbox.json.
#
# Scheduled to fire at a precise clock time (6:03 AM New York). Because the drafts are
# already prepared by the earlier draft stage, the send happens exactly on time - it does
# not drift with how long sourcing took. send_guard.py enforces every rule (cap, dedup,
# MX, opt-out, placeholder) and, in AUTO mode, sends via Gmail SMTP.
#
# -Force skips the New York time gate, for hand-running a batch off-schedule.

param([switch]$Force)

$ErrorActionPreference = 'Stop'
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
Set-Location $ProjectDir
$logDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$cronLog = Join-Path $logDir 'cron.log'
$env:PYTHONUTF8 = '1'

# Two daily triggers exist (2:03pm and 3:03pm Gulf), one per New York DST regime. Exit
# unless this is the one that lands on 6:03am New York. See ny_window.ps1.
. (Join-Path $ProjectDir 'ny_window.ps1')
if (-not $Force -and -not (Test-NewYorkWindow -TargetHHmm '06:03' -CronLog $cronLog -Stage 'send')) { exit 0 }

# Load .env so GMAIL_APP_PASSWORD reaches the guard.
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
$logFile = Join-Path $logDir "send_$stamp.log"
Add-Content -Path $cronLog -Value "$(Get-Date -Format o) === send stage firing (target 6:03am New York) ==="

$venvPython = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$ErrorActionPreference = 'Continue'
& $venvPython (Join-Path $ProjectDir 'send_guard.py') *> $logFile
$ErrorActionPreference = 'Stop'

Add-Content -Path $cronLog -Value (Get-Content $logFile -Tail 3 -ErrorAction SilentlyContinue)
