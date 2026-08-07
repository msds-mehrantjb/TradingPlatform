param(
  [switch]$NoBrowser,
  [switch]$SkipStop
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendUrl = "http://127.0.0.1:8020/api/health"
$marketDataUrl = "http://127.0.0.1:8021/api/health"
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
$marketDataRunner = Join-Path $PSScriptRoot "run-market-data-backend.ps1"
$frontendRunner = Join-Path $PSScriptRoot "run-frontend.ps1"
$wcaRuntimeRunner = Join-Path $PSScriptRoot "run-wca-runtime.ps1"
$wcaResearchWorkerRunner = Join-Path $PSScriptRoot "run-wca-research-worker.ps1"

Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$marketDataRunner`"" -WorkingDirectory $root -WindowStyle Hidden
Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$backendRunner`"" -WorkingDirectory $root -WindowStyle Hidden
Start-Sleep -Seconds 2
Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$wcaRuntimeRunner`"" -WorkingDirectory $root -WindowStyle Hidden
Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$wcaResearchWorkerRunner`"" -WorkingDirectory $root -WindowStyle Hidden
Start-Process -FilePath $powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$frontendRunner`"" -WorkingDirectory $root -WindowStyle Hidden

$backendReady = Wait-ForUrl $backendUrl 45
$marketDataReady = Wait-ForUrl $marketDataUrl 45
$frontendReady = Wait-ForUrl $frontendUrl 60

if (-not $backendReady) {
  Write-Warning "Backend did not respond at $backendUrl. Check backend-8020.err.log."
}

if (-not $marketDataReady) {
  Write-Warning "Market-data backend did not respond at $marketDataUrl. Check market-data-8021.err.log."
}

if (-not $frontendReady) {
  Write-Warning "Frontend did not respond at $frontendUrl. Check frontend-5173.err.log."
}

if ($backendReady -and $frontendReady -and -not $NoBrowser) {
  Start-Process $frontendUrl
}

Write-Host "Backend:  $backendUrl"
Write-Host "Market data: $marketDataUrl"
Write-Host "Frontend: $frontendUrl"
Write-Host "WCA runtime: background process (wca-runtime.out.log / wca-runtime.err.log)"
Write-Host "WCA research: background process (wca-research-worker.out.log / wca-research-worker.err.log)"
