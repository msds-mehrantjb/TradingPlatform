$ErrorActionPreference = "Continue"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$log = Join-Path $root "market-data-8021.out.log"
$err = Join-Path $root "market-data-8021.err.log"
$python = Join-Path $root "backend\.venv\Scripts\python.exe"

Set-Location $root

if (-not (Test-Path $python)) {
  throw "Backend Python was not found at $python"
}

$env:PYTHONPATH = $root
$env:PYTHONPYCACHEPREFIX = Join-Path $root "backend\.runtime_pycache"

$existingBackend = @(netstat -ano -p tcp |
  Select-String "LISTENING" |
  Where-Object { $_.Line -match "127\.0\.0\.1:8021\s+0\.0\.0\.0:0\s+LISTENING" } |
  ForEach-Object {
    $parts = $_.Line.Trim() -split "\s+"
    $parts[-1]
  } |
  Sort-Object -Unique
)

if ($existingBackend.Count -gt 0) {
  Write-Host "Market-data backend already appears to be listening on 127.0.0.1:8021. Existing PID(s): $($existingBackend -join ', ')."
  Write-Host "Run scripts\stop-app.ps1 first if you want to restart it."
  exit 0
}

& $python -m uvicorn backend.app.market_data_app:app --host 127.0.0.1 --port 8021 1> $log 2> $err
