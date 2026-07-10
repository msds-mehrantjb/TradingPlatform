$ErrorActionPreference = "Continue"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontend = Join-Path $root "frontend"
$dist = Join-Path $frontend "dist"
$log = Join-Path $root "frontend-5173.out.log"
$err = Join-Path $root "frontend-5173.err.log"

Set-Location $root

if (-not (Test-Path (Join-Path $dist "index.html"))) {
  Set-Location $frontend
  & npm.cmd run build 1> $log 2> $err
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

Set-Location $dist
& python -m http.server 5173 --bind 127.0.0.1 1> $log 2> $err
