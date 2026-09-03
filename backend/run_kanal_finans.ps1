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
# *>> yerine Out-File -Encoding utf8 kullaniliyor: Windows PowerShell 5.1'de
# redirection operatorleri (*>>, >>) varsayilan olarak UTF-16LE yaziyor, bu da
# UTF-8 bekleyen araclarla (Read tool, grep, vs.) acilinca Turkce karakterlerin
# arasina bosluk giren okunmaz bir log uretiyordu.
#
# Bu satir isin OTEKI yarisi: kanal_finans.py artik stdout'u UTF-8'e zorluyor
# (bkz. oradaki yorum), ama PowerShell yerel bir programin ciktisini
# [Console]::OutputEncoding ile COZUYOR -- o da cp1252 kaldigi surece UTF-8
# baytlar mojibake'e donusurdu. Iki taraf da UTF-8 olmali.
$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    & $python (Join-Path $PSScriptRoot "kanal_finans.py") 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
} finally {
    [Console]::OutputEncoding = $previousOutputEncoding
}
