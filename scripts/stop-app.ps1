$ErrorActionPreference = "Continue"

$ports = @(8020, 5173)
$processIds = @()

try {
  $processIds = Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction Stop |
    Select-Object -ExpandProperty OwningProcess -Unique
} catch {
  $portPattern = ($ports | ForEach-Object { [regex]::Escape(":$_") }) -join "|"
  $processIds = netstat -ano -p tcp |
    Select-String "LISTENING" |
    Where-Object { $_.Line -match $portPattern } |
    ForEach-Object {
      $parts = $_.Line.Trim() -split "\s+"
      $parts[-1]
    } |
    Sort-Object -Unique
}

foreach ($processId in $processIds) {
  if ($processId) {
    Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
  }
}

Start-Sleep -Seconds 1
