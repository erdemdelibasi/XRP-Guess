# Kanal Finans TS'yi bu bilgisayardan calistirir -- GitHub Actions'in bulut
# IP'si YouTube tarafindan engellendigi icin (bkz. CLAUDE.md), bu is kalici
# olarak yerel bir Windows Task Scheduler gorevinden calisiyor. Windows
# Task Scheduler bu scripti dogrudan cagirir, kendi ortaminda .env
# okunmadigi icin degiskenleri burada elle yukluyoruz.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Error "backend\.env bulunamadi -- backend\.env.example'i .env olarak kopyalayip gercek degerleri gir."
    exit 1
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
    $name, $value = $_.Split('=', 2)
    [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
}

$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("kanal_finans_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $python (Join-Path $PSScriptRoot "kanal_finans.py") *>> $logFile
