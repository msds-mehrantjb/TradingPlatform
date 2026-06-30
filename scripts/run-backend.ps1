$ErrorActionPreference = "Continue"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$log = Join-Path $root "backend-8020.out.log"
$err = Join-Path $root "backend-8020.err.log"
$python = Join-Path $root "backend\.venv\Scripts\python.exe"

Set-Location $root

if (-not (Test-Path $python)) {
  throw "Backend Python was not found at $python"
}

& $python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8020 1> $log 2> $err
