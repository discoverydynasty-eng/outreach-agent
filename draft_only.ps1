# draft_only.ps1 - STAGE 1 ONLY: source + draft into outbox.json. Does NOT send.
#
# Runs earlier than the send task so drafting (which takes a variable few minutes) is
# finished before the send fires at a precise clock time. The send is handled separately
# by send_only.ps1, so the actual send minute is exact and not tied to sourcing time.
#
# -Force skips the New York time gate, for hand-running a draft off-schedule.

param([switch]$Force)

$ErrorActionPreference = 'Stop'
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
Set-Location $ProjectDir
$logDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$cronLog = Join-Path $logDir 'cron.log'
$env:PYTHONUTF8 = '1'

# Two daily triggers exist (12:35pm and 1:35pm Gulf), one per New York DST regime. Exit
# unless this is the one that lands on 4:35am New York. Gating BEFORE the lock is taken
# matters: the off-season trigger must not create .run.lock, or it would block the real
# run an hour later.
. (Join-Path $ProjectDir 'ny_window.ps1')
if (-not $Force -and -not (Test-NewYorkWindow -TargetHHmm '04:35' -CronLog $cronLog -Stage 'draft')) { exit 0 }

# Sourcing fans out into parallel background agents that can take >10 min. Claude Code's
# default 600s ceiling was TERMINATING the draft run before it wrote outbox.json (0 sent
# 2026-07-16). Raise to 25 min - well inside the ~88 min gap before the send stage, and
# bounded so a genuinely wedged run can't hang the task forever.
$env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = '1500000'

# Load .env (ANTHROPIC key is commented out; subscription auth is used).
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

$lock = Join-Path $ProjectDir '.run.lock'
if (Test-Path $lock) {
    # A lock this script itself always removes in `finally` should never survive - unless
    # the whole process got killed uncleanly (machine slept/crashed mid-run), which is
    # exactly what happened 2026-07-20: the lock outlived that run and silently blocked
    # every draft (0 sent/day) for 6 days with no error anywhere. A real run is bounded to
    # ~25 min by CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS above, so treat anything older than
    # 2 hours as orphaned and self-heal instead of blocking forever.
    $age = (Get-Date) - (Get-Item $lock).LastWriteTime
    if ($age.TotalHours -lt 2) {
        Add-Content -Path $cronLog -Value "$(Get-Date -Format o) draft: previous run still active ($([math]::Round($age.TotalMinutes,1)) min old). Aborting."
        exit 0
    }
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) draft: found a $([math]::Round($age.TotalHours,1))h-old lock (orphaned by an unclean exit) - clearing it and proceeding."
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType File -Path $lock -Force | Out-Null
try {
    $stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
    $logFile = Join-Path $logDir "draft_$stamp.log"
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) === draft stage: sourcing + drafting into outbox.json ==="

    $ccBase = Join-Path $env:APPDATA 'Claude\claude-code'
    $newest = Get-ChildItem $ccBase -Directory -ErrorAction Stop |
        Sort-Object -Property @{ Expression = { try { [version]$_.Name } catch { [version]'0.0.0' } } } -Descending |
        Select-Object -First 1
    $claudeExe = Join-Path $newest.FullName 'claude.exe'
    if (-not (Test-Path $claudeExe)) { throw "claude.exe not found at $claudeExe" }

    $prompt = "Read CLAUDE.md in this directory and run today's outreach job through Step 6, following every guardrail (one niche at a time, current style). Queue finished emails into outbox.json. Do NOT attempt to send anything yourself; a separate scheduled step sends."

    $ErrorActionPreference = 'Continue'
    $null | & $claudeExe -p $prompt --dangerously-skip-permissions *> $logFile
    $ErrorActionPreference = 'Stop'

    $pending = (Get-Content (Join-Path $ProjectDir 'outbox.json') -Raw | ConvertFrom-Json).pending.Count
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) draft stage done: $pending draft(s) queued, awaiting the send stage."

    # A draft run that queues nothing looks identical in cron.log whether the lead pool is
    # empty or the CLI could not start at all. That ambiguity cost four days: the Claude
    # subscription's OAuth expired on 2026-08-03 and every run failed in ~6 seconds, while
    # the zero sends were read as an exhausted pool. If nothing was queued, say WHY, loudly.
    if ($pending -eq 0) {
        $log = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
        $reason = switch -Regex ($log) {
            'OAuth session expired|Failed to authenticate|Invalid API key|Please run /login' {
                'CLI AUTH FAILED - run `claude` then /login to re-authenticate. Drafting is DEAD until then.' }
            'Credit balance is too low|insufficient_quota' {
                'CLI OUT OF CREDIT - top up billing or switch back to subscription auth.' }
            'API Error|ECONNRESET|ETIMEDOUT|fetch failed' {
                'CLI could not reach the API (network/transient). Should recover on the next run.' }
            'Background tasks still running' {
                'CLI hit the background-task ceiling before writing the outbox.' }
            default { $null }
        }
        if ($reason) {
            Add-Content -Path $cronLog -Value "$(Get-Date -Format o) ** DRAFT FAILED: $reason"
        } else {
            Add-Content -Path $cronLog -Value "$(Get-Date -Format o) ** DRAFT EMPTY: CLI ran without error but queued nothing - likely no confirmable leads left. Check leads.json."
        }
    }
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
