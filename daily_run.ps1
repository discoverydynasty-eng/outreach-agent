# daily_run.ps1 - Windows runner for the HVAC Daily Outreach job (hardened build).
#
# Two stages, matching the macOS daily_run.sh:
#   Stage 1 - Claude Code (headless) sources, enriches, and writes finished emails
#             into outbox.json. Claude NEVER sends.
#   Stage 2 - send_guard.py (in the venv) re-checks every hard rule in real code and
#             is the ONLY thing that puts mail on the wire.
#
# Called by Windows Task Scheduler each morning (see SETUP_WINDOWS.md).
# Loads .env, prevents overlapping runs with a lock file, and logs everything.
#
# SETUP (one time):
#   1. Confirm $ProjectDir below points at this folder.
#   2. Create the venv + install deps (SETUP_WINDOWS.md step 3).
#   3. Put credentials.json in this folder (SETUP_WINDOWS.md step 2) and authorize once.
#   4. (Optional) Put your Places API key in a file named .env:  PLACES_API_KEY=your_key
#   5. Test by hand FIRST:  powershell -ExecutionPolicy Bypass -File .\daily_run.ps1
#   6. Only after a clean hand-run, register the scheduled task (SETUP_WINDOWS.md).

$ErrorActionPreference = 'Stop'

# --- EDIT THIS if you move the folder ---
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
# ----------------------------------------

Set-Location $ProjectDir
$logDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$cronLog = Join-Path $logDir 'cron.log'

# Emoji / accented business names in outbox.json must round-trip as UTF-8, not the
# Windows locale codepage. This makes Python read/write JSON as UTF-8 in both stages.
$env:PYTHONUTF8 = '1'

# --- Load .env (KEY=VALUE lines) into this process's environment, e.g. PLACES_API_KEY ---
$envFile = Join-Path $ProjectDir '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
            $i = $t.IndexOf('=')
            $k = $t.Substring(0, $i).Trim()
            $v = $t.Substring($i + 1).Trim()
            if ($k) { Set-Item -Path "Env:$k" -Value $v }
        }
    }
}

# --- Prevent two runs at once (e.g. a stacked / catch-up trigger) ---
$lock = Join-Path $ProjectDir '.run.lock'
if (Test-Path $lock) {
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) run already in progress or previous run did not exit cleanly. Aborting."
    exit 0
}
New-Item -ItemType File -Path $lock -Force | Out-Null

try {
    $stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
    $logFile = Join-Path $logDir "run_$stamp.log"
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) === starting daily outreach run === -> $logFile"

    # --- Resolve the venv Python that owns sending (created in SETUP_WINDOWS.md step 3) ---
    $venvPython = Join-Path $ProjectDir 'venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        throw "venv Python not found at $venvPython. Run SETUP_WINDOWS.md step 3 to create it."
    }

    # --- Resolve the newest installed Claude Code CLI. The install path is
    #     version-pinned (...\claude-code\<version>\claude.exe) and changes on
    #     update, so pick the highest version folder at runtime. ---
    $ccBase = Join-Path $env:APPDATA 'Claude\claude-code'
    $newest = Get-ChildItem $ccBase -Directory -ErrorAction Stop |
        Sort-Object -Property @{ Expression = { try { [version]$_.Name } catch { [version]'0.0.0' } } } -Descending |
        Select-Object -First 1
    if (-not $newest) { throw "No Claude Code CLI found under $ccBase" }
    $claudeExe = Join-Path $newest.FullName 'claude.exe'
    if (-not (Test-Path $claudeExe)) { throw "claude.exe not found at $claudeExe" }

    # --- Stage 1: Claude Code fills the outbox (does NOT send) ---
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) stage 1: Claude Code drafting into outbox.json..."
    $prompt = "Read CLAUDE.md in this directory and run today's outreach job through Step 6, following every guardrail. Queue finished emails into outbox.json. Do NOT attempt to send anything yourself."

    # Headless print mode. --dangerously-skip-permissions runs tools without prompting
    # (required for an unattended scheduled run).
    # Two Windows-native-exe gotchas handled here:
    #  1. Pipe empty stdin ($null | ...) so claude.exe doesn't block waiting for input.
    #  2. Native warnings on stderr must NOT abort the script. Under the top-level
    #     ErrorActionPreference='Stop', PowerShell wraps a native command's stderr as a
    #     terminating NativeCommandError. Drop to 'Continue' around the external calls so
    #     stderr just lands in the log. *> captures all streams to $logFile.
    $ErrorActionPreference = 'Continue'
    $null | & $claudeExe -p $prompt --dangerously-skip-permissions *> $logFile

    # Read autonomy_mode for accurate logging. The guard itself re-reads config.json and
    # is the real decision-maker; this is only so cron.log says "held" vs "sent" correctly.
    $mode = 'REVIEW'
    try { $mode = ((Get-Content (Join-Path $ProjectDir 'config.json') -Raw | ConvertFrom-Json).autonomy_mode).ToUpper() } catch {}

    # --- Stage 2: the enforced guard. In AUTO it sends what passes; in REVIEW it HOLDS
    #     the drafts and sends nothing until you run approve.ps1. ---
    Add-Content -Path $cronLog -Value "$(Get-Date -Format o) stage 2: send guard ($mode mode)..."
    & $venvPython (Join-Path $ProjectDir 'send_guard.py') *>> $logFile
    $ErrorActionPreference = 'Stop'

    if ($mode -eq 'REVIEW') {
        Add-Content -Path $cronLog -Value "$(Get-Date -Format o) === REVIEW: drafts HELD for approval. Review review_pending.txt, then run approve.ps1 to send. ==="
    } else {
        Add-Content -Path $cronLog -Value "$(Get-Date -Format o) === finished daily outreach run. summary: ==="
    }
    $summary = Join-Path $ProjectDir 'run_summary.txt'
    if (Test-Path $summary) { Add-Content -Path $cronLog -Value (Get-Content $summary -Raw) }
    Add-Content -Path $cronLog -Value (Get-Content $logFile -Tail 6 -ErrorAction SilentlyContinue)
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
