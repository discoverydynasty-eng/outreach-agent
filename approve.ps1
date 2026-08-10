# approve.ps1 - operator approval step for REVIEW mode.
#
# In REVIEW mode (config.json -> autonomy_mode = REVIEW), the daily run drafts emails
# into outbox.json and the send guard HOLDS them - nothing is sent. Review them in
# review_pending.txt (or outbox.json), then run this to send the ones that pass every
# guard check. To send only some, delete the unwanted items from outbox.json first.
#
#   powershell -ExecutionPolicy Bypass -File .\approve.ps1
#
# (In AUTO mode you never need this - the guard sends on the scheduled run.)

$ErrorActionPreference = 'Stop'

# --- EDIT THIS if you move the folder ---
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
# ----------------------------------------

Set-Location $ProjectDir
$env:PYTHONUTF8 = '1'

# Load .env so GMAIL_APP_PASSWORD (and any other keys) reach the guard's process.
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

$venvPython = Join-Path $ProjectDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "venv Python not found at $venvPython. Run SETUP_WINDOWS.md step 3 to create it."
}

$logDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$cronLog = Join-Path $logDir 'cron.log'
Add-Content -Path $cronLog -Value "$(Get-Date -Format o) operator approval: sending held drafts..."

# --approve tells the guard to bypass the REVIEW hold and send what passes.
# Native stderr from Python must not abort this script under ErrorActionPreference=Stop.
$ErrorActionPreference = 'Continue'
& $venvPython (Join-Path $ProjectDir 'send_guard.py') --approve
$ErrorActionPreference = 'Stop'

$summary = Join-Path $ProjectDir 'run_summary.txt'
if (Test-Path $summary) { Add-Content -Path $cronLog -Value (Get-Content $summary -Raw) }
