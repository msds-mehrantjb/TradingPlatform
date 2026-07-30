$ErrorActionPreference = "Continue"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$log = Join-Path $root "wca-research-worker.out.log"
$err = Join-Path $root "wca-research-worker.err.log"
$pidFile = Join-Path $root ".tmp\wca-research-worker.pid"
$python = Join-Path $root "backend\.venv\Scripts\python.exe"

Set-Location $root

if (-not (Test-Path $python)) {
  throw "Backend Python was not found at $python"
}

New-Item -ItemType Directory -Force -Path (Split-Path $pidFile) | Out-Null

$env:PYTHONPATH = $root
$env:PYTHONPYCACHEPREFIX = Join-Path $root "backend\.runtime_pycache"

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $python
$startInfo.Arguments = "-m backend.app.algorithms.wca.research_worker_main --owner-id wca-research-worker-app --poll-seconds 2"
$startInfo.WorkingDirectory = $root
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.EnvironmentVariables["PYTHONPATH"] = $root
$startInfo.EnvironmentVariables["PYTHONPYCACHEPREFIX"] = $env:PYTHONPYCACHEPREFIX

$process = [System.Diagnostics.Process]::Start($startInfo)
Set-Content -LiteralPath $pidFile -Value $process.Id
$stdout = $process.StandardOutput.ReadToEndAsync()
$stderr = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
$stdout.Result | Set-Content -LiteralPath $log
$stderr.Result | Set-Content -LiteralPath $err
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
