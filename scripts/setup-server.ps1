<#
.SYNOPSIS
    One-time provisioning for People Modelling.AI on TNBDEVIHDAAPP (10.250.53.90).

.DESCRIPTION
    DRY RUN BY DEFAULT. Without -Apply this checks everything, prints exactly what it
    would do, and changes NOTHING.

    WHAT -Apply DOES (and nothing else):
      1. Creates D:\EASA\PeopleModelling\.app_state and \logs .
      2. Writes the live launcher run-peoplemodelling.cmd from scripts\run-peoplemodelling.cmd.
         It is written at the repo root and is deliberately UNTRACKED: a pull refuses to
         overwrite an untracked file, and that would wedge the auto-deploy.
      3. Installs backend\requirements.txt MACHINE-WIDE (this must run elevated).
      4. Runs npm install and builds the frontend with APP_BASE_PATH=/peoplemodelling/ .
      5. Creates two scheduled tasks, both as SYSTEM:
           PeopleModelling            - the app; at startup, restarts on failure
           PeopleModelling-AutoDeploy - scripts\auto-deploy.ps1 every 5 minutes

    WHAT IT NEVER TOUCHES: IIS (no site, no application, no pool, no binding, no cert),
    any other team's task or service, any other port. Giving the app its public address
    is a separate, reviewed step -- scripts\setup-hana-path.ps1 in the FazuraGPT repo,
    which needs the OK of the owner of Default Web Site. See docs\DEPLOYMENT.md.

    The box is shared with about twenty teams. Ports known to be taken: 5173, 5174
    (FazuraGPT), 8001 (IAR -- NEVER touch), 8011 (Talent Portal), 8443, 9000 (another
    team's backend), 5432 (PostgreSQL). This refuses to run if the port it is given is
    already listening.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup-server.ps1           # dry run
    powershell -ExecutionPolicy Bypass -File scripts\setup-server.ps1 -Apply    # do it
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$AppRoot   = "D:\EASA\PeopleModelling",
    [int]$Port         = 8021,
    [string]$BasePath  = "/peoplemodelling/",
    [string]$PythonExe = "C:\Python314\python.exe",
    [string]$TaskName  = "PeopleModelling",
    # How often the auto-deploy polls GitHub.
    [int]$DeployEveryMinutes = 5
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$deployTask = "$TaskName-AutoDeploy"
$mode = if ($Apply) { "APPLY" } else { "DRY RUN (nothing will be changed)" }
$script:problems = New-Object System.Collections.ArrayList

function Say([string]$t = "") { Write-Host $t }
function Heading([string]$t) { Say ""; Say "== $t" }
function Check([bool]$ok, [string]$pass, [string]$fail) {
    if ($ok) { Say "  ok    $pass" } else { Say "  FAIL  $fail"; [void]$script:problems.Add($fail) }
    return $ok
}
function Would([string]$t) { if ($Apply) { Say "  do    $t" } else { Say "  would $t" } }

Say "People Modelling.AI -- server provisioning"
Say "Mode: $mode"
Say "Repo: $repo"
Say "App root: $AppRoot   Port: $Port   Base path: $BasePath"

# --- checks -----------------------------------------------------------------

Heading "Prerequisites"

[void](Check (Test-Path -LiteralPath $PythonExe) "python at $PythonExe" `
    "python NOT at $PythonExe -- the task runs as SYSTEM and a bare 'python' is not on its PATH")

$npm = @("C:\Program Files\nodejs\npm.cmd", "C:\Program Files (x86)\nodejs\npm.cmd") |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
[void](Check ([bool]$npm) "npm at $npm" "npm.cmd not found -- Node.js must be installed machine-wide to build the frontend")

$git = Get-Command git -ErrorAction SilentlyContinue
[void](Check ([bool]$git) "git at $($git.Source)" "git not found on PATH")

# Elevation. The app runs as SYSTEM, and a pip install from a non-elevated shell
# silently lands in per-user site-packages that SYSTEM cannot see -- the app then fails
# imports while every standalone test passes. That cost a multi-hour outage once.
$elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
[void](Check $elevated "running elevated" `
    "NOT elevated -- pip would install per-user where SYSTEM cannot see it, and the tasks cannot be created")

Heading "Port $Port"
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    $owners = @($listening | ForEach-Object {
        $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        "pid $($_.OwningProcess) ($($p.ProcessName))"
    } | Select-Object -Unique) -join ", "
    # Never kill it. Several apps here are Python; a stale copy is a human's call.
    [void](Check $false "" "port $Port is ALREADY LISTENING ($owners). Pick another port -- do NOT kill the owner, the box is shared")
} else {
    Say "  ok    port $Port is free"
}

Heading "Repo"
[void](Check (Test-Path (Join-Path $repo "backend\requirements.txt")) "backend\requirements.txt present" "backend\requirements.txt missing")
[void](Check (Test-Path (Join-Path $repo "run_server.py")) "run_server.py present" "run_server.py missing")
$branch = (& git -C $repo rev-parse --abbrev-ref HEAD 2>$null)
Say "  info  checkout is on branch '$branch' -- auto-deploy follows THIS branch, not main"

$envFile = Join-Path $repo ".env.local"
if (Test-Path $envFile) {
    $portLine = (Get-Content $envFile | Where-Object { $_ -match '^\s*PORT\s*=\s*(\d+)' })
    $envPort = if ($portLine -match '(\d+)') { [int]$Matches[1] } else { 0 }
    [void](Check ($envPort -eq $Port) ".env.local PORT=$envPort matches" `
        ".env.local says PORT=$envPort but this run uses $Port -- auto-deploy health-checks the .env.local port, so they must agree")
    $hasToken = [bool](Get-Content $envFile | Where-Object { $_ -match '^\s*DATABRICKS_TOKEN\s*=\s*\S' })
    if ($hasToken) { Say "  ok    DATABRICKS_TOKEN is set (value not read)" }
    else { Say "  info  DATABRICKS_TOKEN is empty -- everything works except the AI readiness column" }
} else {
    [void](Check $false "" ".env.local missing. Copy .env.local.example to .env.local and fill it in. Note Notepad's Save As writes .env.local.txt -- check with: dir .env*")
}

if ($script:problems.Count -gt 0) {
    Say ""
    Say "STOPPING: $($script:problems.Count) problem(s) above must be fixed first."
    exit 1
}

# --- the plan ---------------------------------------------------------------

Heading "Plan"
Would "create $AppRoot\.app_state and $AppRoot\logs"
Would "write $repo\run-peoplemodelling.cmd (untracked, from scripts\run-peoplemodelling.cmd)"
Would "pip install --ignore-installed -r backend\requirements.txt  (machine-wide)"
Would "npm install + npm run build  with APP_BASE_PATH=$BasePath"
Would "create scheduled task '$TaskName' (SYSTEM, at startup, restart on failure)"
Would "create scheduled task '$deployTask' (SYSTEM, every $DeployEveryMinutes minutes)"
Say ""
Say "  NOT done here: anything in IIS. The public address is a separate reviewed step --"
Say "  see docs\DEPLOYMENT.md step 7."

if (-not $Apply) {
    Say ""
    Say "Dry run complete. Nothing was changed. Re-run with -Apply to do it."
    exit 0
}

# --- do it ------------------------------------------------------------------

Heading "Applying"

foreach ($dir in @("$AppRoot\.app_state", "$AppRoot\logs")) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null; Say "  made $dir" }
    else { Say "  have $dir" }
}

$liveLauncher = Join-Path $repo "run-peoplemodelling.cmd"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run-peoplemodelling.cmd") -Destination $liveLauncher -Force
Say "  wrote $liveLauncher"

Say "  installing backend requirements machine-wide..."
# NO --ignore-installed. C:\Python314 is SHARED -- FazuraGPT, the Talent Portal and
# others run on it. --ignore-installed forces a reinstall of EVERY requirement,
# including packages those apps have loaded right now, and Windows will not let pip
# replace a .pyd a live process has mapped: it fails with "[WinError 5] Access is
# denied" on, say, numpy, while already elevated. Worse, where it succeeds it has
# silently swapped another team's dependency underneath them.
#
# Plain install adds only what is missing and leaves everything else alone. The
# per-user-shadowing problem --ignore-installed exists to solve is handled below, by
# asking the interpreter where each module actually resolves from.
& $PythonExe -m pip install -r (Join-Path $repo "backend\requirements.txt") 2>&1 |
    Select-Object -Last 6 | ForEach-Object { Say "    $_" }

# Where does each import ACTUALLY come from? Test-Path on site-packages cannot tell a
# machine-wide install from a per-user one that shadows it, and that distinction is the
# whole point: the app runs as SYSTEM, which cannot see per-user packages, so a package
# resolving under AppData is an import error waiting for the first request that needs it.
Say "  checking what the app's own interpreter resolves..."
$probe = @'
import importlib, os, sys
mods = ["fastapi", "uvicorn", "pandas", "openpyxl", "PyPDF2", "sqlalchemy",
        "pydantic", "tenacity", "multipart", "databricks.sdk"]
roaming = os.environ.get("APPDATA", "").lower()
for name in mods:
    try:
        m = importlib.import_module(name)
    except Exception as exc:
        print(f"MISSING {name}: {type(exc).__name__}")
        continue
    path = (getattr(m, "__file__", "") or "").lower()
    if roaming and roaming in path:
        print(f"PERUSER {name}: {path}")
    else:
        print(f"OK      {name}")
'@
$probeFile = Join-Path $env:TEMP "pm-probe-imports.py"
[IO.File]::WriteAllText($probeFile, $probe, (New-Object Text.UTF8Encoding($false)))
& $PythonExe $probeFile 2>&1 | ForEach-Object {
    if ($_ -match '^OK') { Say "  ok    $_" }
    else { Say "  FAIL  $_" }
}
Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue

Say "  building the frontend..."
Push-Location (Join-Path $repo "frontend")
try {
    & $npm install --include=dev 2>&1 | Select-Object -Last 3 | ForEach-Object { Say "    $_" }
    $env:APP_BASE_PATH = $BasePath
    & $npm run build 2>&1 | Select-Object -Last 5 | ForEach-Object { Say "    $_" }
    Remove-Item Env:\APP_BASE_PATH -ErrorAction SilentlyContinue
} finally { Pop-Location }
if (Test-Path (Join-Path $repo "frontend\dist\index.html")) { Say "  ok    frontend\dist built" }
else { Say "  FAIL  frontend\dist\index.html missing after the build" }

# The app task. At startup so a reboot brings it back, and restarting on failure so a
# crash does not need a human. SYSTEM because that is what can bind and what the
# machine-wide packages are installed for.
Say "  creating scheduled task '$TaskName'..."
$appAction  = New-ScheduledTaskAction -Execute $liveLauncher -WorkingDirectory $repo
$appTrigger = New-ScheduledTaskTrigger -AtStartup
$appSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Hours 0)
Register-ScheduledTask -TaskName $TaskName -Action $appAction -Trigger $appTrigger -Settings $appSettings `
    -User "SYSTEM" -RunLevel Highest -Force | Out-Null
Say "  ok    task '$TaskName' created"

# The auto-deploy task. Every $DeployEveryMinutes minutes, indefinitely. It holds a lock
# so overlapping runs cannot fight, and holds off on a commit that failed.
Say "  creating scheduled task '$deployTask'..."
$deployAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'auto-deploy.ps1')`"" `
    -WorkingDirectory $repo
$deployTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $DeployEveryMinutes)
$deploySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $deployTask -Action $deployAction -Trigger $deployTrigger `
    -Settings $deploySettings -User "SYSTEM" -RunLevel Highest -Force | Out-Null
Say "  ok    task '$deployTask' created (every $DeployEveryMinutes min)"

Say "  starting '$TaskName'..."
Start-ScheduledTask -TaskName $TaskName
Say ""
Say "Done. Next:"
Say "  1. Wait ~20s, then check it answers:   powershell -File scripts\check-health.ps1"
Say "  2. Give it its public address:         docs\DEPLOYMENT.md step 7 (needs the site owner's OK)"
