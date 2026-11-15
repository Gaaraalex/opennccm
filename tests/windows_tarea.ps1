# Recolector de Windows con su tarea programada, contra el central falso (tests/recolector_central_falso.py).
# Solo para la CI (windows-2022, como administrador): crea y borra la tarea «OpenNCCM Recolector» de verdad.
#   pwsh tests/windows_tarea.ps1 -Exe dist\opennccm-recolector-X.exe
param([Parameter(Mandatory = $true)][string]$Exe)
$ErrorActionPreference = "Stop"
$Tarea = "OpenNCCM Recolector"
$Url = "http://127.0.0.1:8771"
$fallos = @()
function Check([string]$nombre, [bool]$ok, $extra = "") {
  if ($ok) { Write-Host "OK   $nombre" } else { Write-Host "FAIL $nombre  $extra"; $script:fallos += $nombre }
}
function Estado { Invoke-RestMethod "$Url/prueba/estado" }
function Procesos { @(Get-CimInstance Win32_Process -Filter "Name='opennccm-recolector.exe'") }
function Esperar([scriptblock]$cond, [int]$seg) {
  $fin = (Get-Date).AddSeconds($seg)
  while ((Get-Date) -lt $fin) { if (& $cond) { return $true }; Start-Sleep 2 }
  return $false
}

$tmp = Join-Path $env:RUNNER_TEMP "central-falso"
$central = Start-Process python -ArgumentList "tests/recolector_central_falso.py", "8771", "`"$tmp`"" -PassThru -NoNewWindow
try {
  if (-not (Esperar { Test-Path "$tmp\clave.txt" } 30)) { throw "El central falso no arrancó" }
  Start-Sleep 1
  $datos = Join-Path $env:ProgramData "OpenNCCM Recolector"
  New-Item -ItemType Directory -Force $datos | Out-Null
  Copy-Item "$tmp\opennccm.env" $datos   # SOLO PRUEBA: la clave pública con la que firma el central falso

  & $Exe "/central=$Url" "/clave=$(Get-Content "$tmp\clave.txt")"
  Check "instala sin /sin-tarea: alta hecha" ($LASTEXITCODE -eq 0 -and (Estado).altas -eq 1) $LASTEXITCODE

  $xml = [xml](schtasks /Query /TN $Tarea /XML | Out-String)
  Check "la tarea «$Tarea» existe y corre como SYSTEM" ($xml.Task.Principals.Principal.UserId -eq "S-1-5-18")
  Check "…arranca con Windows y se repite cada minuto (sin abrir otra copia)" ($null -ne $xml.Task.Triggers.BootTrigger `
    -and $xml.Task.Triggers.TimeTrigger.Repetition.Interval -eq "PT1M" -and $xml.Task.Settings.MultipleInstancesPolicy -eq "IgnoreNew")

  Check "el recolector late" (Esperar { (Estado).latidos -ge 2 } 60) (Estado | ConvertTo-Json -Compress)
  $duenos = Procesos | ForEach-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User } | Select-Object -Unique
  Check "…y su proceso es de SYSTEM" ($duenos -contains "SYSTEM" -and $duenos.Count -eq 1) ($duenos -join ",")

  $antes = @((Estado).pids).Count
  Procesos | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Check "al matar el proceso, la tarea lo vuelve a arrancar (otro pid late)" (Esperar { @((Estado).pids).Count -gt $antes } 150) `
    (Estado | ConvertTo-Json -Compress)

  Invoke-RestMethod -Method Post "$Url/prueba/revocar" | Out-Null
  Check "revocar lo detiene" (Esperar { (Procesos).Count -eq 0 } 60) ((Procesos).Count)
  $estadoTarea = (schtasks /Query /TN $Tarea /FO CSV /NH | ConvertFrom-Csv -Header a, b, Estado).Estado
  Start-Sleep 130
  Check "…y no vuelve (la tarea quedó deshabilitada; 2 minutos después sigue detenido)" ((Procesos).Count -eq 0 -and $estadoTarea -match "Disabled|Deshabilitado") `
    "$estadoTarea / $((Procesos).Count)"

  $instalado = Join-Path $env:ProgramFiles "OpenNCCM Recolector\opennccm-recolector.exe"
  & $instalado desinstalar /todo
  Start-Sleep 10
  schtasks /Query /TN $Tarea 2>$null | Out-Null
  $sinTarea = $LASTEXITCODE -ne 0
  Check "desinstalar /todo borra la tarea y las carpetas" ($sinTarea -and -not (Test-Path (Split-Path $instalado)) -and -not (Test-Path $datos))
} finally {
  Stop-Process -Id $central.Id -Force -ErrorAction SilentlyContinue
  schtasks /Delete /TN $Tarea /F 2>$null | Out-Null
}
if ($fallos.Count) { Write-Host "RESULTADO: $($fallos.Count) fallos: $($fallos -join '; ')"; exit 1 }
Write-Host "RESULTADO: TODO OK"
exit 0  # el último programa (schtasks /Delete de una tarea que ya no existe) no es el resultado
