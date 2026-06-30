$ErrorActionPreference = "Continue"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontend = Join-Path $root "frontend"
$log = Join-Path $root "frontend-5173.out.log"
$err = Join-Path $root "frontend-5173.err.log"

Set-Location $frontend

& npm.cmd run dev -- --port 5173 --force 1> $log 2> $err
