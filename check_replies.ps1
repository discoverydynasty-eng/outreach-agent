# check_replies.ps1 - refresh tracker.csv and scan Gmail for replies.
# Reuses GMAIL_APP_PASSWORD from .env (same one the send guard uses). No extra login.
#
#   powershell -ExecutionPolicy Bypass -File .\check_replies.ps1

$ErrorActionPreference = 'Stop'
$ProjectDir = 'C:\Users\Lenovo\hvac-outreach'
Set-Location $ProjectDir
$env:PYTHONUTF8 = '1'

# Load .env so GMAIL_APP_PASSWORD reaches the script.
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
$ErrorActionPreference = 'Continue'
& $venvPython (Join-Path $ProjectDir 'check_replies.py')
$ErrorActionPreference = 'Stop'
