param(
  [switch]$NoBrowser,
  [switch]$SkipStop
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendUrl = "http://127.0.0.1:8020/api/health"
$frontendUrl = "http://127.0.0.1:5173/"

function Wait-ForUrl {
  param(
    [string]$Url,
    [int]$Seconds = 45
  )

  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $Url
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        return $true
      }
    } catch {
      Start-Sleep -Milliseconds 750
    }
  } while ((Get-Date) -lt $deadline)

  return $false
}

if (-not $SkipStop) {
  & (Join-Path $PSScriptRoot "stop-app.ps1")
}

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$backendRunner = Join-Path $PSScriptRoot "run-backend.ps1"
$frontendRunner = Join-Path $PSScriptRoot "run-frontend.ps1"

Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$backendRunner`"" -WorkingDirectory $root -WindowStyle Hidden
Start-Sleep -Seconds 2
Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$frontendRunner`"" -WorkingDirectory $root -WindowStyle Hidden

$backendReady = Wait-ForUrl $backendUrl 45
$frontendReady = Wait-ForUrl $frontendUrl 60

if (-not $backendReady) {
  Write-Warning "Backend did not respond at $backendUrl. Check backend-8020.err.log."
}

if (-not $frontendReady) {
  Write-Warning "Frontend did not respond at $frontendUrl. Check frontend-5173.err.log."
}

if ($backendReady -and $frontendReady -and -not $NoBrowser) {
  Start-Process $frontendUrl
}

Write-Host "Backend:  $backendUrl"
Write-Host "Frontend: $frontendUrl"
