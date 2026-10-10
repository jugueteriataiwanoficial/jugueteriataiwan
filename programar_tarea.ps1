# ==================================================================
# programar_tarea.ps1 — registra el CRON de Windows (Task Scheduler)
# que sincroniza el TOP 300 de POSGold con el index.html y lo publica.
#
# Uso (una sola vez, como administrador opcional):
#   powershell -ExecutionPolicy Bypass -File .\programar_tarea.ps1
#
# Para quitar la tarea:
#   Unregister-ScheduledTask -TaskName "JugueteriaTaiwan_SYNC_TOP300" -Confirm:$false
#
# La tarea corre a diario a las 06:00 y 18:00 (ajustar abajo). El
# registro completo queda en reportes\sync_log.txt (NO publicar: el
# .gitignore ya excluye reportes\).
# ==================================================================

$ErrorActionPreference = "Stop"
$carpeta = Split-Path -Parent $MyInvocation.MyCommand.Path
$python  = "python"
$script  = Join-Path $carpeta "sync_posgold.py"
$logDir  = Join-Path $carpeta "reportes"
$log     = Join-Path $logDir "sync_log.txt"
$accion  = "cmd /c cd /d ""$carpeta"" && $python ""$script"" --publicar >> ""$log"" 2>&1"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$tarea = "JugueteriaTaiwan_SYNC_TOP300"
$existente = Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
if ($existente) {
    Unregister-ScheduledTask -TaskName $tarea -Confirm:$false
    Write-Output "Tarea anterior eliminada."
}

$trigger = New-ScheduledTaskTrigger -Daily -At 06:00
$trigger2 = New-ScheduledTaskTrigger -Daily -At 18:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $tarea `
    -Trigger $trigger, $trigger2 `
    -Action (New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$carpeta`" && $python `"$script`" --publicar >> `"$log`" 2>&1") `
    -Settings $settings `
    -Description "Sincroniza el TOP 300 de POSGold al index.html y publica en GitHub Pages" | Out-Null

Write-Output "Tarea registrada: $tarea"
Write-Output "Horarios: todos los dias 06:00 y 18:00"
Write-Output "Registro: $log"
Write-Output ""
Write-Output "Prueba manual inmediata:"
Write-Output "  Start-ScheduledTask -TaskName '$tarea'"
