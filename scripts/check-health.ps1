<#
.SYNOPSIS
    Is People Modelling.AI answering, and on which commit?

.DESCRIPTION
    Read-only. Changes nothing, touches nothing but this app's own port and task.

    Uses a raw socket on purpose. Invoke-WebRequest, curl.exe and .NET HttpClient ALL
    hang on this box, even against 127.0.0.1 -- a hand-written request answers in about
    100ms. Anyone "fixing" this to use Invoke-WebRequest will reintroduce the hang.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check-health.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$TaskName = "PeopleModelling"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent

# Read the port from .env.local rather than typing it. A gate aimed at the wrong port
# reports a healthy app as dead and a dead one as healthy.
if ($Port -eq 0) {
    $Port = 8021
    $envFile = Join-Path $repo ".env.local"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*PORT\s*=\s*(\d+)') { $Port = [int]$Matches[1]; break }
        }
    }
}

Write-Host "People Modelling.AI health  (port $Port)"
Write-Host ""

# --- the task ---
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "  task        $TaskName : $($task.State)"
    # 267009 means RUNNING. It reads like an error code and is not one.
    $result = if ($info.LastTaskResult -eq 267009) { "267009 (RUNNING -- this is success)" } else { $info.LastTaskResult }
    Write-Host "  last result $result"
    Write-Host "  last run    $($info.LastRunTime)"
} catch {
    Write-Host "  task        $TaskName : NOT FOUND"
}

# --- the port ---
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    foreach ($c in $listening) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        Write-Host "  listening   $($c.LocalAddress):$($c.LocalPort) by pid $($c.OwningProcess) ($($p.ProcessName))"
    }
} else {
    Write-Host "  listening   nothing on $Port"
}

# --- the app ---
$client = New-Object System.Net.Sockets.TcpClient
$received = New-Object System.IO.MemoryStream
try {
    $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne(3000)) { throw "connect timed out" }
    $client.EndConnect($connect)
    $client.ReceiveTimeout = 5000
    $stream = $client.GetStream()
    $bytes = [Text.Encoding]::ASCII.GetBytes("GET /api/health HTTP/1.1`r`nHost: 127.0.0.1:$Port`r`nConnection: close`r`n`r`n")
    $stream.Write($bytes, 0, $bytes.Length); $stream.Flush()
    $buffer = New-Object byte[] 8192
    while ($received.Length -lt 65536) {
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -le 0) { break }
        $received.Write($buffer, 0, $read)
    }
} catch {
    Write-Host "  http        no answer: $($_.Exception.Message)"
} finally { $client.Close() }

if ($received.Length -gt 0) {
    $reply = [Text.Encoding]::UTF8.GetString($received.ToArray())
    $statusLine = ($reply -split "`r`n")[0]
    Write-Host "  http        $statusLine"
    $split = $reply.IndexOf("`r`n`r`n")
    $body = if ($split -ge 0) { $reply.Substring($split + 4) } else { "" }
    if ($body -match '"app"\s*:\s*"([^"]*)"') {
        $app = $Matches[1]
        if ($app -eq "people-modelling") {
            Write-Host "  identity    people-modelling (this IS our app)"
        } else {
            # The box is shared and ports get reused. Another project's dev server
            # answering here is not our app and not a healthy deploy.
            Write-Host "  identity    '$app' -- THIS IS NOT OUR APP. Something else holds port $Port."
        }
    } else {
        Write-Host "  identity    the reply has no 'app' field -- not our health route"
    }
    if ($body -match '"commit"\s*:\s*"([^"]*)"') { Write-Host "  commit      $($Matches[1])" }
    if ($body -match '"uptime_seconds"\s*:\s*(\d+)') { Write-Host "  uptime      $($Matches[1])s" }
}

# --- what the last deploy did ---
$stateFile = Join-Path $repo "logs\deploy.state.json"
if (Test-Path $stateFile) {
    try {
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
        Write-Host ""
        Write-Host "  last deploy $($state.outcome) at $($state.finishedAt)"
        Write-Host "              $($state.detail)"
    } catch { }
}
$failedFile = Join-Path $repo "logs\deploy.failed.json"
if (Test-Path $failedFile) {
    try {
        $failed = Get-Content $failedFile -Raw | ConvertFrom-Json
        Write-Host ""
        Write-Host "  HELD        $($failed.commit.Substring(0,7)) failed at $($failed.failedAt): $($failed.reason)"
        Write-Host "              Deploys of that commit are held. Push a fix, or delete logs\deploy.failed.json."
    } catch { }
}
Write-Host ""
