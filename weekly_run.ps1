# weekly_run.ps1 - Windows runner for the Monday digest (weekly_summary.py).
#
# Windows equivalent of the com.fadbranding.hvacsummary launchd job. Emails Fad a
# once-a-week snapshot (sent / replies / bounces). Read access uses its own token
# (token_reader.json), so the send guard stays send-only.
#
# Registered to run every Monday morning by register_tasks.ps1.

$ErrorActionPreference = 'Stop'

# --- EDIT THIS if you move the folder ---
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
# ----------------------------------------

Set-Location $ProjectDir
$logDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# UTF-8 so accented names / unicode in the digest round-trip correctly.
$env:PYTHONUTF8 = '1'

$venvPython = Join-Path $ProjectDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "venv Python not found at $venvPython. Run SETUP_WINDOWS.md step 3 to create it."
}

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$logFile = Join-Path $logDir "summary_$stamp.log"

& $venvPython (Join-Path $ProjectDir 'weekly_summary.py') *> $logFile
