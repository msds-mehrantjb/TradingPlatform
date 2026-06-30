$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$startScript = Join-Path $PSScriptRoot "start-app.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$taskName = "Trading App Auto Start"

function Install-StartupFolderLauncher {
  $startup = [Environment]::GetFolderPath("Startup")
  $launcher = Join-Path $startup "Trading App Auto Start.vbs"
  $command = "`"$powershell`" -NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
  $escapedCommand = $command.Replace('"', '""')
  $content = @"
Set shell = CreateObject("WScript.Shell")
shell.Run "$escapedCommand", 0, False
"@

  Set-Content -LiteralPath $launcher -Value $content -Encoding ASCII
  Write-Host "Installed Startup folder launcher: $launcher"
}

$action = New-ScheduledTaskAction `
  -Execute $powershell `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`"" `
  -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT30S"

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

try {
  Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Starts the Trading backend and frontend after Windows login." `
    -Force | Out-Null

  Write-Host "Installed scheduled task: $taskName"
} catch {
  Write-Warning "Scheduled Task install failed: $($_.Exception.Message)"
  Install-StartupFolderLauncher
}

Write-Host "Fresh start command: powershell -NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
