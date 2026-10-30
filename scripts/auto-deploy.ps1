# Unattended deploy for People Modelling.AI on TNBDEVIHDAAPP. Polls GitHub; when the
# branch this copy is on has moved, it pulls, rebuilds the frontend, restarts the app if
# the backend changed, and checks the app answers ON THE NEW COMMIT -- rolling back if it
# does not.
#
# A commit it rolled back is not tried again. The rollback records it in
# logs\deploy.failed.json and every later run holds off on it -- still reviving the app if
# it is down -- for as long as the branch is still at that commit. A different commit (a
# fix, a revert) deploys as usual; -Force, or deleting the file, tries the held one again.
#
# Run by the scheduled task PeopleModelling-AutoDeploy. Safe to run by hand too:
#
#   powershell -ExecutionPolicy Bypass -File scripts\auto-deploy.ps1 -WhatIf
#   powershell -ExecutionPolicy Bypass -File scripts\auto-deploy.ps1
#
# Nothing here is hardcoded that can drift. The two bugs that made FazuraGPT's old
# update.ps1 stale were both hardcoded values -- a port that .env.local disagreed with,
# and "origin/main" when the checkout was on another branch -- so the branch is read from
# the checkout and the port from .env.local.
#
# Only ever touches: this folder, the PeopleModelling task, and its own log. The box is
# shared -- FazuraGPT (5174), Talent Portal (8011), an IAR app (8001), another team's
# backend (9000), coalrating, PI Data Extraction, SOA Ageing and PostgreSQL all live here
# and are never in scope. Nothing here kills a process by port: several apps are Python,
# and a stale copy is a human's call, not a script's. Nothing here touches IIS.

[CmdletBinding()]
param(
    # Report what would happen and change nothing.
    [switch]$WhatIf,
    # Deploy even when the hash has not moved -- for recovering a broken dist\. Also
    # deploys a commit being held after a failed deploy (logs\deploy.failed.json).
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repo     = Split-Path $PSScriptRoot -Parent
$taskName = "PeopleModelling"
$appName  = "people-modelling"          # what GET /api/health calls itself
$logFile    = Join-Path $repo "logs\deploy.log"
$stateFile  = Join-Path $repo "logs\deploy.state.json"
$failedFile = Join-Path $repo "logs\deploy.failed.json"
$lockFile   = Join-Path $repo "logs\deploy.lock"

# The Python the app runs under: its OWN virtual environment, never C:\Python314.
# That interpreter is shared with FazuraGPT, the Talent Portal and three other apps,
# and this app's requirements disagree with theirs. An unattended deploy that can
# upgrade a package four other teams depend on is a bad idea however careful the flags
# are, so it cannot reach them at all.
#
# ALWAYS the full path: a bare "python" is not on SYSTEM's PATH, and the task then
# fails with result 1 and no log at all.
$pythonExe = Join-Path $repo ".venv\Scripts\python.exe"

# Where IIS serves the app. The frontend is built with this as its base, so assets and
# API calls resolve under the application rather than at the site root.
$basePath = "/peoplemodelling/"

# The longest to wait for the app to answer after a restart, and how often to ask.
# Generous on purpose: a wait that is too short writes off an app that was merely still
# starting, and that is exactly how FazuraGPT rolled back three good deploys in a row on
# 17 Oct 2026. A process that has ended is noticed at once instead.
$startSeconds = 120
$pollMs       = 2000

$script:outcome = "unknown"
$script:detail  = ""
$script:startedAt = Get-Date
# When the app was last restarted, so a health wait can say how long the start took.
# Initialised here because Wait-Healthy may run without a restart before it (a quiet run
# checking an app that is already up), and subtracting from $null throws.
$script:restartedAt = Get-Date

# --- plumbing ---------------------------------------------------------------

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    try {
        $dir = Split-Path $logFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    } catch {
        # A log that cannot be written must never stop a deploy.
    }
}

function Set-Outcome {
    param([string]$Outcome, [string]$Detail = "")
    $script:outcome = $Outcome
    $script:detail  = $Detail
}

# What this run did, rewritten at the end whatever happened. The log answers "what
# happened at 06:19?"; this answers "what is the state now?", which is the question
# somebody actually has when a push has not appeared.
function Write-RunState {
    param([string]$Branch = "", [string]$Local = "", [string]$Remote = "")
    $state = [ordered]@{
        outcome     = $script:outcome
        detail      = $script:detail
        branch      = $Branch
        localCommit = $Local
        remoteCommit= $Remote
        ranAt       = $script:startedAt.ToString("yyyy-MM-dd HH:mm:ss")
        finishedAt  = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        whatIf      = [bool]$WhatIf
    }
    try {
        $dir = Split-Path $stateFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        [IO.File]::WriteAllText($stateFile, ($state | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    } catch {
        Write-Log "could not write $stateFile : $($_.Exception.Message)" "WARN"
    }
}

# --- running other programs without deadlocking -----------------------------

# Git over WinRM hangs FOREVER through PowerShell's ordinary native-command invocation.
# It is not credentials and not the network: git writes progress to stderr, remoting does
# not drain that pipe, the buffer fills, and git blocks on the write. Reading both pipes
# asynchronously -- BEFORE waiting -- turns a hang into a one-second fetch.
#
# System.Diagnostics.Process rather than Start-Process: the latter hangs outright in a
# WinRM logon for want of a window station, and -PassThru hands back an object whose
# ExitCode reads as $null, so every call looks like a failure even when it worked.
function Invoke-Program {
    param(
        [string]$FilePath,
        [string]$Arguments,
        [string]$WorkingDirectory = $repo,
        [int]$TimeoutSeconds = 300
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $FilePath
    $psi.Arguments              = $Arguments
    $psi.WorkingDirectory       = $WorkingDirectory
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true

    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEndAsync()
    $stderr = $proc.StandardError.ReadToEndAsync()

    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        try { $proc.Kill() } catch { }
        return [pscustomobject]@{ ExitCode = -1; Output = "timed out after ${TimeoutSeconds}s"; TimedOut = $true }
    }

    $text = ($stdout.Result + "`n" + $stderr.Result)
    # A PAT can live inside a .git/config key NAME, so it can surface in git's own error
    # text. It must never reach the log.
    $text = $text -replace 'github_pat_[A-Za-z0-9_]+', '[REDACTED]'
    $text = $text -replace 'ghp_[A-Za-z0-9]+', '[REDACTED]'
    $text = $text -replace 'https://[^@/\s]+:[^@/\s]+@', 'https://[REDACTED]@'
    return [pscustomobject]@{ ExitCode = $proc.ExitCode; Output = $text.Trim(); TimedOut = $false }
}

function Invoke-Git {
    param([string[]]$Arguments, [int]$TimeoutSeconds = 120)
    # Quote arguments containing spaces: a repo path like "People Modelling.AI" would
    # otherwise arrive at git as two arguments and fail.
    $argLine = ((@("-C", $repo) + $Arguments) | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }) -join ' '
    return Invoke-Program -FilePath "git" -Arguments $argLine -TimeoutSeconds $TimeoutSeconds
}

# npm is a .cmd shim, which ProcessStartInfo will not run without a shell; and SYSTEM's
# PATH may not have it. Resolve to a real path once, the same lesson as $pythonExe.
function Get-NpmPath {
    $candidates = @(
        "C:\Program Files\nodejs\npm.cmd",
        "C:\Program Files (x86)\nodejs\npm.cmd",
        "$env:ProgramFiles\nodejs\npm.cmd"
    )
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
    $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Invoke-Npm {
    param([string]$Arguments, [int]$TimeoutSeconds = 900, [hashtable]$Env = @{})
    $npm = Get-NpmPath
    if (-not $npm) { return [pscustomobject]@{ ExitCode = -1; Output = "npm.cmd not found"; TimedOut = $false } }
    $saved = @{}
    foreach ($k in $Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $Env[$k]) }
    try {
        # cmd.exe /c: npm.cmd is a batch shim and will not start directly.
        return Invoke-Program -FilePath "cmd.exe" -Arguments "/c `"`"$npm`" $Arguments`"" `
            -WorkingDirectory (Join-Path $repo "frontend") -TimeoutSeconds $TimeoutSeconds
    } finally {
        foreach ($k in $Env.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

# --- asking the app how it is ------------------------------------------------

# Read PORT from .env.local. Hardcoding it is what left FazuraGPT's update.ps1 checking
# an empty port for weeks, so a broken deploy looked healthy and a healthy one broken.
function Get-AppPort {
    $envFile = Join-Path $repo ".env.local"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*PORT\s*=\s*(\d+)') { return [int]$Matches[1] }
        }
    }
    return 8021
}

# Invoke-WebRequest, curl.exe and .NET HttpClient ALL hang on this box, even against
# 127.0.0.1. A hand-written request over a raw socket answers in about 100ms, so that is
# what the gate uses. Read to close, not a single Read(): the first packet is often the
# headers alone, and the commit we need is in the body.
function Get-AppReply {
    param([int]$Port, [string]$Path = "/api/health", [int]$TimeoutMs = 5000)
    $client = New-Object System.Net.Sockets.TcpClient
    $received = New-Object System.IO.MemoryStream
    try {
        $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(3000)) { return $null }
        $client.EndConnect($connect)
        $client.ReceiveTimeout = $TimeoutMs
        $client.SendTimeout    = $TimeoutMs
        $stream = $client.GetStream()
        $request = "GET $Path HTTP/1.1`r`nHost: 127.0.0.1:$Port`r`nConnection: close`r`n`r`n"
        $bytes = [Text.Encoding]::ASCII.GetBytes($request)
        $stream.Write($bytes, 0, $bytes.Length); $stream.Flush()
        $buffer = New-Object byte[] 8192
        while ($received.Length -lt 262144) {
            $read = $stream.Read($buffer, 0, $buffer.Length)
            if ($read -le 0) { break }
            $received.Write($buffer, 0, $read)
        }
    } catch {
        # Refused, or a read that timed out: whatever arrived is still the reply.
    } finally {
        $client.Close()
    }
    if ($received.Length -eq 0) { return $null }
    return [Text.Encoding]::UTF8.GetString($received.ToArray())
}

# What the app says it is: status line, and the app name and commit from the body.
# $null when nothing answered.
#
# The identity check matters because the box is shared and ports get reused. FazuraGPT
# spent a minute after every start being answered on its own port by another project's
# node dev server on the wildcard -- 404 on every route, which is neither the app nor a
# failure. An answer that is not OURS is not an answer.
function Get-AppStatus {
    param([int]$Port)
    $reply = Get-AppReply -Port $Port
    if (-not $reply) { return $null }
    $statusLine = ($reply -split "`r`n")[0]
    $body = ""
    $split = $reply.IndexOf("`r`n`r`n")
    if ($split -ge 0) { $body = $reply.Substring($split + 4) }
    $app = ""; $commit = ""
    if ($body -match '"app"\s*:\s*"([^"]*)"')    { $app = $Matches[1] }
    if ($body -match '"commit"\s*:\s*"([^"]*)"') { $commit = $Matches[1] }
    return [pscustomobject]@{
        StatusLine = $statusLine
        Ok         = ($statusLine -match '\s200\s')
        IsOurs     = ($app -eq $appName)
        App        = $app
        Commit     = $commit
    }
}

# Wait for the app to answer as ITSELF. Ends as soon as it does, so a quick start still
# returns in a few seconds; $startSeconds is the ceiling.
function Wait-Healthy {
    param([int]$Port, [int]$Seconds = $startSeconds, [string]$ExpectCommit = "")
    $deadline = (Get-Date).AddSeconds($Seconds)
    $last = "nothing answered"
    while ((Get-Date) -lt $deadline) {
        $status = Get-AppStatus -Port $Port
        if ($status) {
            if (-not $status.IsOurs) {
                $last = "something else is answering on $Port (app='$($status.App)'): $($status.StatusLine)"
            } elseif (-not $status.Ok) {
                $last = "the app answered $($status.StatusLine)"
            } elseif ($ExpectCommit -and $status.Commit -and $status.Commit -ne $ExpectCommit) {
                $last = "the app is still on $($status.Commit.Substring(0,7)), not $($ExpectCommit.Substring(0,7))"
            } else {
                $waited = [int]((Get-Date) - $script:restartedAt).TotalSeconds
                return [pscustomobject]@{ Healthy = $true; Status = $status; Message = "app answered 200 on $Port, ${waited}s after the start" }
            }
        }
        Start-Sleep -Milliseconds $pollMs
    }
    return [pscustomobject]@{ Healthy = $false; Status = $null; Message = "no healthy answer on $Port within ${Seconds}s -- $last" }
}

function Get-AppTaskState {
    try { return (Get-ScheduledTask -TaskName $taskName -ErrorAction Stop).State } catch { return "missing" }
}

function Restart-App {
    param([int]$Port)
    $script:restartedAt = Get-Date
    if ($WhatIf) { Write-Log "WHATIF would restart the task $taskName"; return $true }
    try {
        Write-Log "restarting $taskName"
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        return $true
    } catch {
        Write-Log "could not restart $taskName : $($_.Exception.Message)" "ERROR"
        return $false
    }
}

# --- a commit that failed to deploy -----------------------------------------
#
# Without this, a rollback puts the checkout back on the old commit while origin still
# holds the new one, so the next run sees "the branch has moved", pulls, builds, restarts
# and rolls back again -- taking the app down each time. FazuraGPT did exactly that three
# times in ten minutes before this existed. An app restarted every five minutes is worse
# off than one left on the old commit.

function Get-FailedDeploy {
    if (-not (Test-Path -LiteralPath $failedFile)) { return $null }
    try {
        $record = [IO.File]::ReadAllText($failedFile) | ConvertFrom-Json
        $commit = [string]$record.commit
        if ($commit -notmatch '^[0-9a-f]{40}$') { throw "it does not name a commit" }
        return [pscustomobject]@{
            Commit   = $commit
            FailedAt = if ($record.failedAt) { [string]$record.failedAt } else { "an unknown time" }
            Reason   = if ($record.reason)   { [string]$record.reason }   else { "no reason was recorded" }
        }
    } catch {
        # Never fatal: holding off on the strength of a file this script cannot
        # understand would stop deploys with nothing to say which commit.
        Write-Log "ignoring $failedFile -- it could not be read: $($_.Exception.Message)" "WARN"
        return $null
    }
}

function Get-DeployHold {
    param([string]$Local, [string]$Remote)
    if ($Force -or -not $Remote -or $Local -eq $Remote) { return $null }
    $failed = Get-FailedDeploy
    if ($null -eq $failed -or $failed.Commit -ne $Remote) { return $null }
    $short = $failed.Commit.Substring(0, 7)
    return [pscustomobject]@{
        Commit  = $failed.Commit
        Message = "not retrying ${short}: it failed at $($failed.FailedAt) ($($failed.Reason)); push a new commit, or delete logs\deploy.failed.json, or run with -Force"
    }
}

# Called BEFORE the rollback undoes the commit, so a run killed half way through the
# rollback still leaves the hold behind. Never throws: the rollback matters more.
function Save-FailedDeploy {
    param([string]$Commit, [string]$Reason, [string]$RolledBackTo, [string]$Branch)
    if (-not $Commit -or $Commit -eq $RolledBackTo) { return }
    $record = [ordered]@{
        commit = $Commit; failedAt = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        reason = $Reason; rolledBackTo = $RolledBackTo; branch = $Branch
    }
    try {
        [IO.File]::WriteAllText($failedFile, ($record | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
        Write-Log "recorded $($Commit.Substring(0,7)) as failed; later runs will not retry it while the branch is still there"
    } catch {
        Write-Log "could not record the failure in $failedFile : $($_.Exception.Message); a later run may try it again" "WARN"
    }
}

function Clear-FailedDeploy {
    if (-not (Test-Path -LiteralPath $failedFile)) { return }
    try { [IO.File]::Delete($failedFile); Write-Log "cleared logs\deploy.failed.json; this run deploys, and records itself again if it fails" }
    catch { Write-Log "could not remove $failedFile : $($_.Exception.Message)" "WARN" }
}

# --- the deploy --------------------------------------------------------------

function Get-ChangedFiles {
    param([string]$From, [string]$To)
    $diff = Invoke-Git @("diff", "--name-only", "$From..$To")
    if ($diff.ExitCode -ne 0) {
        # Unknown is treated as "everything changed" -- the safe direction.
        Write-Log "could not list changed files; assuming both frontend and backend changed" "WARN"
        return $null
    }
    return @($diff.Output -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Invoke-Build {
    param([string]$ExpectCommit)
    # Confirm the checkout really moved before building. A build after a failed pull
    # "succeeds" by rebuilding the old code -- twice that produced a convincing fake
    # deploy on FazuraGPT.
    $head = Invoke-Git @("rev-parse", "HEAD")
    if ($head.ExitCode -ne 0 -or $head.Output.Trim() -ne $ExpectCommit) {
        Write-Log "refusing to build: HEAD is '$($head.Output.Trim())', expected '$ExpectCommit'" "ERROR"
        return $false
    }
    if ($WhatIf) { Write-Log "WHATIF would run npm ci + npm run build with APP_BASE_PATH=$basePath"; return $true }

    if (-not (Test-Path (Join-Path $repo "frontend\node_modules"))) {
        Write-Log "frontend\node_modules missing; running npm install"
        $install = Invoke-Npm -Arguments "install --include=dev"
        if ($install.ExitCode -ne 0) {
            Write-Log "npm install failed: $($install.Output)" "ERROR"
            return $false
        }
    }

    Write-Log "building frontend with APP_BASE_PATH=$basePath"
    $build = Invoke-Npm -Arguments "run build" -Env @{ APP_BASE_PATH = $basePath }
    if ($build.ExitCode -ne 0) {
        Write-Log "npm run build failed: $($build.Output)" "ERROR"
        return $false
    }
    $indexPath = Join-Path $repo "frontend\dist\index.html"
    if (-not (Test-Path $indexPath)) {
        Write-Log "build reported success but frontend\dist\index.html is missing" "ERROR"
        return $false
    }
    # Prove the base path reached the output. A build with the wrong base SUCCEEDS and
    # then serves a page whose assets 404 behind IIS -- a blank screen that looks like a
    # server fault and is not. This is the gate that would have caught the deploy which
    # restored git's root-path dist and skipped the rebuild.
    $indexHtml = Get-Content $indexPath -Raw
    if ($indexHtml -notmatch [regex]::Escape("$basePath" + "assets/")) {
        Write-Log "built index.html does not reference ${basePath}assets/ -- the base path did not reach the build; the app would serve a blank page" "ERROR"
        return $false
    }
    Write-Log "frontend built; assets referenced under $basePath"
    return $true
}

function Invoke-PipInstall {
    # Installs into the app's OWN virtual environment ($pythonExe above), so nothing
    # here can reach the shared C:\Python314 that FazuraGPT, the Talent Portal and three
    # other apps run on. That isolation is the point: an unattended deploy must not be
    # able to change another team's dependencies.
    #
    # Still no --ignore-installed. Even inside a venv it forces a pointless full
    # reinstall every deploy, and it is exactly the flag that failed with "[WinError 5]
    # Access is denied" when it tried to replace a .pyd the running app had mapped.
    if ($WhatIf) { Write-Log "WHATIF would run pip install -r backend\requirements.txt in .venv"; return $true }
    $req = Join-Path $repo "backend\requirements.txt"

    # No venv, no install. Falling back to the system interpreter here would quietly
    # undo the isolation and start writing into the shared one again.
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        Write-Log "the virtual environment is missing ($pythonExe). Run scripts\setup-server.ps1 -Apply; NOT falling back to the shared interpreter." "ERROR"
        return $false
    }

    # Look before touching anything shared. --dry-run names what would change; an
    # upgrade of an already-installed package is the dangerous case, so it is logged
    # loudly rather than done quietly.
    $plan = Invoke-Program -FilePath $pythonExe -Arguments "-m pip install --dry-run -r `"$req`"" -TimeoutSeconds 300
    if ($plan.ExitCode -eq 0) {
        $would = ($plan.Output -split "`n" | Where-Object { $_ -match '^\s*Would install' }) -join " "
        if ($would) { Write-Log "pip plan: $would" } else { Write-Log "pip: nothing to install, every requirement is already present"; return $true }
    }

    Write-Log "installing backend requirements machine-wide"
    $pip = Invoke-Program -FilePath $pythonExe -Arguments "-m pip install -r `"$req`"" -TimeoutSeconds 900
    if ($pip.ExitCode -ne 0) {
        # A locked .pyd means another app on this box is using that package right now.
        # Rolling the deploy back is right; forcing the install is not.
        if ($pip.Output -match "WinError 5|Access is denied") {
            Write-Log "pip hit a locked file -- another app on this shared interpreter has it loaded. NOT forcing it: $($pip.Output)" "ERROR"
        } else {
            Write-Log "pip install failed: $($pip.Output)" "ERROR"
        }
        return $false
    }
    if ($pip.Output -match "Defaulting to user installation") {
        # Should be impossible inside a venv; if it ever happens the venv is broken and
        # packages went somewhere the app will not find them.
        Write-Log "pip defaulted to a USER install -- the virtual environment looks broken." "ERROR"
        return $false
    }
    Write-Log "backend requirements installed"
    return $true
}

# --- run ---------------------------------------------------------------------

$lockStream = $null
try {
    $logDir = Join-Path $repo "logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

    # One run at a time. A deploy that overlaps another would restart the app under the
    # other's feet and read its half-written build as a failure.
    try {
        $lockStream = [IO.File]::Open($lockFile, 'OpenOrCreate', 'Write', 'None')
    } catch {
        Write-Log "another deploy is running (logs\deploy.lock is held); leaving it alone"
        Set-Outcome "skipped" "another run holds the lock"
        Write-RunState
        exit 0
    }

    $port = Get-AppPort
    Write-Log "=== deploy run started (port $port$(if ($WhatIf) { ', WHATIF' })$(if ($Force) { ', FORCE' })) ==="

    # Read the branch, never type it. Deploying "main" into a checkout that is on another
    # branch is a no-op that looks like success.
    $branchResult = Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")
    if ($branchResult.ExitCode -ne 0) { throw "could not read the branch: $($branchResult.Output)" }
    $branch = $branchResult.Output.Trim()

    # Retry the fetch. TNB egress is Zscaler-intercepted, and it intermittently closes a
    # connection without a reply -- git reports "Empty reply from server" and the run
    # fails. Observed on 30 Oct 2026 at 22:00, with the runs either side at 21:55 and
    # 22:05 both fine. The next run five minutes later would recover anyway, but a
    # transient blip should not be logged as a failed deploy: that is how a real failure
    # stops being noticed.
    #
    # A genuine problem -- the repo gone, DNS broken, the network down -- fails all three
    # attempts and is reported as before.
    $fetch = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $fetch = Invoke-Git @("fetch", "origin", $branch)
        if ($fetch.ExitCode -eq 0) { break }
        if ($attempt -lt 3) {
            Write-Log "git fetch attempt $attempt failed ($($fetch.Output -replace "`n", ' ')); retrying" "WARN"
            Start-Sleep -Seconds (3 * $attempt)
        }
    }
    if ($fetch.ExitCode -ne 0) { throw "git fetch failed after 3 attempts: $($fetch.Output)" }

    $localResult  = Invoke-Git @("rev-parse", "HEAD")
    $remoteResult = Invoke-Git @("rev-parse", "origin/$branch")
    $local  = $localResult.Output.Trim()
    $remote = $remoteResult.Output.Trim()
    # ${branch}, not $branch: a colon straight after a variable name reads as a scope
    # qualifier ($script:x) and is a parse error, not a runtime one.
    Write-Log "branch ${branch}: local $($local.Substring(0,7)), origin $($remote.Substring(0,7))"

    $hold = Get-DeployHold -Local $local -Remote $remote
    $nothingToDo = ($local -eq $remote -and -not $Force)

    if ($hold) {
        Write-Log $hold.Message "WARN"
        Set-Outcome "holding-off" $hold.Message
    } elseif ($nothingToDo) {
        Set-Outcome "up-to-date" "already on $($local.Substring(0,7))"
    }

    # A quiet run -- nothing to deploy, or a held commit -- still revives an app that is
    # down. A hold that left the app dead would be worse than the loop it replaces.
    if ($hold -or $nothingToDo) {
        $status = Get-AppStatus -Port $port
        if ($status -and $status.IsOurs -and $status.Ok) {
            Write-Log "app is answering on $port, on $(if ($status.Commit) { $status.Commit.Substring(0,7) } else { 'an unknown commit' })"
        } else {
            $taskState = Get-AppTaskState
            Write-Log "app is not answering on $port (task state: $taskState); reviving it" "WARN"
            if (Restart-App -Port $port) {
                $health = Wait-Healthy -Port $port
                Write-Log $health.Message $(if ($health.Healthy) { "INFO" } else { "ERROR" })
                Set-Outcome $(if ($health.Healthy) { "revived" } else { "failed" }) $health.Message
            } else {
                Set-Outcome "failed" "could not restart the task"
            }
        }
        Write-RunState -Branch $branch -Local $local -Remote $remote
        Write-Log "=== deploy run finished: $($script:outcome) ==="
        exit $(if ($script:outcome -eq "failed") { 1 } else { 0 })
    }

    # --- there is something to deploy ---------------------------------------
    Write-Log "deploying $($local.Substring(0,7)) -> $($remote.Substring(0,7))"
    Clear-FailedDeploy

    $changed = Get-ChangedFiles -From $local -To $remote
    $frontendChanged = $true; $backendChanged = $true; $requirementsChanged = $true
    if ($null -ne $changed) {
        $frontendChanged     = [bool]($changed | Where-Object { $_ -like "frontend/*" })
        $backendChanged      = [bool]($changed | Where-Object { $_ -like "backend/*" -or $_ -eq "run_server.py" })
        $requirementsChanged = [bool]($changed | Where-Object { $_ -eq "backend/requirements.txt" })
        Write-Log "$($changed.Count) file(s) changed: frontend=$frontendChanged backend=$backendChanged requirements=$requirementsChanged"
    }

    if ($WhatIf) {
        # build is always true: the pull restores git's root-path dist, so it must be
        # rebuilt whether or not frontend/ changed.
        Write-Log "WHATIF would pull, then build=True (always), pip=$requirementsChanged, restart=$backendChanged"
        Set-Outcome "whatif" "would deploy $($remote.Substring(0,7))"
        Write-RunState -Branch $branch -Local $local -Remote $remote
        exit 0
    }

    # 1. Pull.
    #
    # First put the build output back as git has it. The repo TRACKS frontend\dist, and
    # every build on this server rewrites those files -- so by the second deploy the
    # working tree is dirty and `git pull --ff-only` refuses outright with "your local
    # changes would be overwritten", failing every run from then on. dist is pure build
    # output, regenerated below, so discarding it costs nothing.
    #
    # (The cleaner fix is to untrack frontend\dist altogether, but app.yaml's Databricks
    # Apps fallback -- `test -f dist/index.html` when npm build fails -- relies on it
    # being committed. This keeps both deployments working.)
    $restore = Invoke-Git @("checkout", "--", "frontend/dist")
    if ($restore.ExitCode -ne 0) { Write-Log "could not restore frontend/dist: $($restore.Output)" "WARN" }
    # Stale hashed assets from earlier builds, which are untracked and would otherwise
    # pile up in dist forever.
    [void](Invoke-Git @("clean", "-fdq", "frontend/dist"))

    $pull = Invoke-Git @("pull", "--ff-only", "origin", $branch)
    if ($pull.ExitCode -ne 0) { throw "git pull failed: $($pull.Output)" }
    $afterPull = (Invoke-Git @("rev-parse", "HEAD")).Output.Trim()
    if ($afterPull -ne $remote) { throw "pull did not land on $remote (HEAD is $afterPull)" }
    Write-Log "pulled; HEAD is now $($afterPull.Substring(0,7))"

    $rollback = {
        param([string]$Reason)
        Write-Log "rolling back to $($local.Substring(0,7)): $Reason" "ERROR"
        Save-FailedDeploy -Commit $remote -Reason $Reason -RolledBackTo $local -Branch $branch
        $reset = Invoke-Git @("reset", "--hard", $local)
        if ($reset.ExitCode -ne 0) { Write-Log "ROLLBACK FAILED: $($reset.Output)" "ERROR"; return }
        # Always rebuild on the way back too: the reset restored git's root-path dist,
        # which does not work behind the /peoplemodelling application.
        [void](Invoke-Build -ExpectCommit $local)
        if ($backendChanged)  { [void](Restart-App -Port $port); [void](Wait-Healthy -Port $port) }
        Write-Log "rolled back to $($local.Substring(0,7))"
    }

    # 2. Dependencies, before the build that may need them.
    if ($requirementsChanged -and -not (Invoke-PipInstall)) {
        & $rollback "pip install failed"
        Set-Outcome "failed" "pip install failed; rolled back to $($local.Substring(0,7))"
        Write-RunState -Branch $branch -Local $local -Remote $remote
        exit 1
    }

    # 3. Build -- ALWAYS, not only when frontend/ changed.
    #
    # Step 1 restores frontend\dist to what git has, because it is tracked and a dirty
    # copy makes the pull refuse. What git has is the ORIGINAL root-path build: assets
    # at /assets/..., which under /peoplemodelling resolve to the shared site root and
    # 404. So a deploy that restored dist and then skipped the build because only
    # scripts/ changed left the app serving a blank page -- which is exactly what
    # happened on 30 Oct 2026 after three script-only deploys in a row.
    #
    # Restoring and not rebuilding is never correct. The build costs about 20 seconds
    # and only runs when there is actually a commit to deploy, not on every poll.
    if (-not (Invoke-Build -ExpectCommit $remote)) {
        & $rollback "frontend build failed"
        Set-Outcome "failed" "build failed; rolled back to $($local.Substring(0,7))"
        Write-RunState -Branch $branch -Local $local -Remote $remote
        exit 1
    }

    # 4. Restart -- only when the backend changed. A frontend-only change needs none:
    #    the app serves frontend\dist off disk, so a rebuilt asset is live the moment it
    #    is written, with no interruption at all.
    if ($backendChanged) {
        if (-not (Restart-App -Port $port)) {
            & $rollback "could not restart the task"
            Set-Outcome "failed" "restart failed; rolled back to $($local.Substring(0,7))"
            Write-RunState -Branch $branch -Local $local -Remote $remote
            exit 1
        }
        # 5. Verify -- and verify it is on the NEW commit, not merely that something
        #    answered. An app that failed to restart would otherwise pass this gate.
        $health = Wait-Healthy -Port $port -ExpectCommit $remote
        if (-not $health.Healthy) {
            & $rollback $health.Message
            Set-Outcome "failed" "$($health.Message); rolled back to $($local.Substring(0,7))"
            Write-RunState -Branch $branch -Local $local -Remote $remote
            exit 1
        }
        Write-Log $health.Message
    } else {
        Write-Log "backend unchanged; no restart needed -- the new frontend is served off disk at once"
        $status = Get-AppStatus -Port $port
        if (-not ($status -and $status.IsOurs -and $status.Ok)) {
            Write-Log "app is not answering after a frontend-only deploy; reviving it" "WARN"
            [void](Restart-App -Port $port)
            [void](Wait-Healthy -Port $port)
        }
    }

    Set-Outcome "deployed" "deployed $($remote.Substring(0,7)) on $branch"
    Write-RunState -Branch $branch -Local $local -Remote $remote
    Write-Log "=== deploy run finished: deployed $($remote.Substring(0,7)) ==="
    exit 0

} catch {
    Write-Log "deploy run failed: $($_.Exception.Message)" "ERROR"
    Set-Outcome "failed" $_.Exception.Message
    Write-RunState
    exit 1
} finally {
    if ($lockStream) { $lockStream.Close(); try { Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue } catch { } }
}
