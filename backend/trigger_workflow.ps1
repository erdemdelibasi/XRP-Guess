# Starts a GitHub Actions workflow from THIS machine at its intended hour
# (Task Scheduler), instead of waiting for GitHub's own `schedule:` event.
# Copied from XAU-Guess/backend/trigger_workflow.ps1 (the repos share no code);
# a fix to the guard logic belongs in both. The task passes the workflow file
# and the stand-down window:
#
#   Task                          Workflow             When (TR)          Window
#   XRP-Guess Retrain Trigger     daily_retrain.yml    daily 06:30        12h
#   XRP-Guess Report Trigger      daily_report.yml     daily 18:10        12h
#
# Each hour is its workflow's cron converted to TR (fixed UTC+3).
# predict.yml is NOT here: it fires every 15 minutes and has its own, simpler
# trigger (trigger_predict.ps1) -- predict.py's unique(symbol, target_time)
# makes overlapping runs harmless, so it needs no guard.
#
# Why: the crons stopped keeping time. Measured 2026-09-28..10-05:
# daily_retrain.yml's 03:30Z started at 09:17-10:40Z, daily_report.yml's
# 15:10Z (18:10 TR) at 18:37-21:12Z -- the mail arrived 3.5-6 hours late.
# Every run that started succeeded; nothing in the scripts was wrong.
#
# A workflow_dispatch is an ordinary API call and queues like a push; the job
# itself still runs on GitHub's runners with the repo's secrets, on the free
# public-repo minutes, exactly as before. This machine only makes the call.
#
# Two guards keep it at ONE run per slot, and the cron stays as the fallback
# for a slot this machine sleeps through:
#   - here: no dispatch if a run of the workflow from the last $WindowHours
#     is running or succeeded (the task has StartWhenAvailable, so a machine
#     that wakes hours late must not follow a late cron run that already did
#     the work);
#   - the workflow: a SCHEDULED run stands down if a dispatched run exists in
#     the same window (its gate job, which must use the same number).
# For the report the second run would be a second mail; for retrain it would
# be harmless but wasted, and a second commit.
#
# The window must be longer than the cron's delay (up to ~7 hours measured)
# and shorter than a day minus that delay, or yesterday's late cron would
# count for today. 12 hours sits inside both.
#
# If GitHub cannot be asked, nothing is dispatched: the cron fallback still
# runs (late), whereas a blind dispatch could run twice.
param(
    [Parameter(Mandatory = $true)][string]$Workflow,
    [int]$WindowHours = 12,
    # Log the decision without dispatching -- for checking a new task by hand.
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$repo = "erdemdelibasi/XRP-Guess"

$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$name = [IO.Path]::GetFileNameWithoutExtension($Workflow)
$logFile = Join-Path $logDir ("trigger_{0}_{1}.log" -f $name, (Get-Date -Format "yyyy-MM-dd"))

function Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    $line | Out-File -FilePath $logFile -Append -Encoding utf8
    if ($DryRun) { Write-Output $line }
}

# Absolute path, not a bare `gh`: Task Scheduler does not reliably carry the
# interactive shell's PATH (same as trigger_predict.ps1).
$gh = "C:\Program Files\GitHub CLI\gh.exe"

$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    $raw = & $gh run list --repo $repo --workflow $Workflow --limit 20 --json "createdAt,startedAt,event,status,conclusion" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Log "gh run list failed (exit=$LASTEXITCODE): $raw -- not dispatching, the cron fallback stays"
        exit 1
    }
    $cutoff = (Get-Date).ToUniversalTime().AddHours(-$WindowHours)
    # Parsed here rather than with --jq: Windows PowerShell 5.1 strips the
    # double quotes a jq string literal needs on its way to a native program.
    # Two steps on purpose: 5.1's ConvertFrom-Json writes a JSON array as ONE
    # object, so @(... | ConvertFrom-Json) is an array holding the array.
    $parsed = $raw | Out-String | ConvertFrom-Json
    $runs = @($parsed | Where-Object { $_ })
    # A scheduled run that found a dispatch in ITS OWN window stood down and
    # did nothing, yet still concludes "success". Counting it here would skip
    # a slot that never ran -- XAU-Guess lost a morning's mail exactly that
    # way on 10-02. Same rule as the workflow's gate, applied at the time the
    # gate ran (startedAt).
    $dispatched = @($runs | Where-Object {
        $_.event -eq "workflow_dispatch" -and $_.conclusion -notin @("failure", "cancelled")
    } | ForEach-Object { ([datetime]$_.createdAt).ToUniversalTime() })
    function StoodDown($run) {
        if ($run.event -ne "schedule") { return $false }
        $at = if ($run.startedAt) { $run.startedAt } else { $run.createdAt }
        $t = ([datetime]$at).ToUniversalTime()
        return @($dispatched | Where-Object { $_ -le $t -and $_ -ge $t.AddHours(-$WindowHours) }).Count -gt 0
    }
    $recent = @($runs | Where-Object {
        ([datetime]$_.createdAt).ToUniversalTime() -ge $cutoff -and
        $_.conclusion -notin @("failure", "cancelled", "startup_failure", "timed_out") -and
        -not (StoodDown $_)
    })
    if ($recent.Count -gt 0) {
        $r = $recent[0]
        Log "skip: a $Workflow run already exists in the last ${WindowHours}h ($($r.createdAt) $($r.event) $($r.status) $($r.conclusion))"
        exit 0
    }
    if ($DryRun) {
        Log "dry run: would dispatch $Workflow (no run in the last ${WindowHours}h)"
        exit 0
    }
    $out = & $gh workflow run $Workflow --repo $repo --ref main 2>&1
    Log "dispatched ${Workflow}: $out exit=$LASTEXITCODE"
    exit $LASTEXITCODE
} catch {
    # Under Windows PowerShell 5.1 with Stop, a native program's stderr line
    # arriving through 2>&1 is itself a terminating error -- without this the
    # run would die before writing a word to the log.
    Log "error: $_ -- not dispatching, the cron fallback stays"
    exit 1
} finally {
    [Console]::OutputEncoding = $previousOutputEncoding
}
