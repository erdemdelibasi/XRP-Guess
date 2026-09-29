# Triggers the Quarter-Hourly XRP Prediction GitHub Actions workflow via the
# API (gh workflow run) instead of relying on GitHub Actions' own native
# `schedule:` event to fire it.
#
# Measured 2026-09-27..29: the native 15-minute schedule silently degraded
# from a true ~15-minute cadence to firing roughly every 5 hours, for 40+
# hours and still ongoing when this was written. Every run that DID fire
# succeeded -- this is not a bug in predict.py or the workflow file, it is
# GitHub silently deprioritising a very-frequent `schedule:` trigger. It
# mirrors a previously-documented 32-hour "account lock" from
# 2026-09-19..21 (see daily_report.py's MAX_RUN_GAP comment): the same class
# of problem, not a one-off.
#
# workflow_dispatch (what `gh workflow run` sends) is a normal API call, not
# a `schedule:` event, so it is not subject to that same low-priority
# scheduling delay -- it queues like a push-triggered run. The actual
# computation still runs on GitHub's runners, on the free public-repo Actions
# minutes, exactly as before; only the TRIGGER moved local. This machine
# does nothing but make one API call every 15 minutes.
#
# predict.yml keeps its own much-reduced `schedule:` (hourly) as a fallback
# for whenever THIS machine is asleep at a Task Scheduler trigger -- a
# skipped trigger is silently dropped, not queued (same caveat as
# run_kanal_finans.ps1). predict.py's own unique(symbol, target_time) guard
# (see predict.py) makes the two triggers safe to overlap: whichever fires
# second for the same 15-minute slot just skips as a duplicate.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("trigger_predict_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

# Absolute path, not a bare `gh` -- Task Scheduler's process environment does
# not reliably carry the interactive shell's PATH (same reasoning as this
# folder's .venv\Scripts\python.exe references elsewhere).
$gh = "C:\Program Files\GitHub CLI\gh.exe"

$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    & $gh workflow run predict.yml --repo erdemdelibasi/XRP-Guess --ref main 2>&1 |
        ForEach-Object { "$timestamp $_" } | Out-File -FilePath $logFile -Append -Encoding utf8
    "$timestamp exit=$LASTEXITCODE" | Out-File -FilePath $logFile -Append -Encoding utf8
} finally {
    [Console]::OutputEncoding = $previousOutputEncoding
}
