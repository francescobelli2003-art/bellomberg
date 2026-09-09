# ==================================================================
#   BELLOMBERG - Fix per registrare SOLO il PriceUpdater
# ==================================================================
# Usare quando install_all_schedulers.ps1 ha fallito su PriceUpdater
# (PowerShell rifiuta [TimeSpan]::MaxValue come RepetitionDuration).
#
# USO (PowerShell come Amministratore):
#   powershell -ExecutionPolicy Bypass -File .ix_price_updater.ps1   (dalla cartella REALE del repo, non da una junction)
# ==================================================================

$ProjectPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
$PriceUpdaterBat = Join-Path $PSScriptRoot "run_price_updater.bat"

if (-not (Test-Path $PriceUpdaterBat)) {
    Write-Host "ERROR: $PriceUpdaterBat non esiste" -ForegroundColor Red
    exit 1
}

$me = "$env:USERDOMAIN\$env:USERNAME"
$TaskName = "Bellomberg-PriceUpdater"

# Rimuovi se gia' esiste
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Rimuovo task esistente: $TaskName" -ForegroundColor DarkGray
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Write-Host "Registro: $TaskName (ogni 15 min, 24/7)" -ForegroundColor Cyan

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$PriceUpdaterBat`"" -WorkingDirectory $ProjectPath

# Trigger: parte tra 2 minuti, si ripete ogni 15 min per 9999 giorni (~27 anni = praticamente infinito)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 9999)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName `
        -Description "Bellomberg - aggiorna prezzi live + FX ogni 15 min." `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -ErrorAction Stop | Out-Null
    Write-Host ""
    Write-Host "OK -> task registrato. Prima esecuzione tra 2 min." -ForegroundColor Green
    Write-Host "Log: $ProjectPath\data\price_updater.log" -ForegroundColor Green
    Write-Host ""
    Write-Host "Per testarlo subito:" -ForegroundColor Yellow
    Write-Host "  Start-ScheduledTask -TaskName $TaskName" -ForegroundColor White
    Write-Host ""
    Write-Host "Per vedere lo stato di tutti i task Bellomberg:" -ForegroundColor Yellow
    Write-Host "  Get-ScheduledTask -TaskName Bellomberg-* | Get-ScheduledTaskInfo | Format-Table TaskName,LastRunTime,NextRunTime,LastTaskResult" -ForegroundColor White
} catch {
    Write-Host ""
    Write-Host "ERROR: registrazione fallita" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
