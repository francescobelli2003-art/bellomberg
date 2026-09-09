# ==================================================================
#   BELLOMBERG - One-shot installer per TUTTI gli scheduler automatici
# ==================================================================
# Registra in Windows Task Scheduler:
#   1. Bellomberg-PriceUpdater  -> ogni 15 min (prezzi live + FX)
#   2. Bellomberg-NewsFeed      -> ogni 15 min Lun-Ven 07:00-23:00
#   3. Bellomberg-Consigliere   -> Lun + Gio 07:30 AM (riunione settimanale)
#   4. Bellomberg-Briefing      -> 4 slot Lun-Ven 07:30/11:30/15:30/19:30 (Haiku daily briefing)
#   5. Bellomberg-AutoBackup    -> ogni giorno 23:00 (zip DB + retention ultimi 30)
#
# USO:
#   1. Apri PowerShell COME AMMINISTRATORE (tasto destro -> "Esegui come amministratore")
#   2. Esegui:
#        powershell -ExecutionPolicy Bypass -File .\tools\ops\windows\install_all_schedulers.ps1   (dalla cartella REALE del repo, non da una junction: $PSScriptRoot non la risolve)
#
# Per RIMUOVERE tutti i task:
#   Unregister-ScheduledTask -TaskName "Bellomberg-PriceUpdater" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "Bellomberg-NewsFeed"     -Confirm:$false
#   Unregister-ScheduledTask -TaskName "Bellomberg-Consigliere"  -Confirm:$false
#   Unregister-ScheduledTask -TaskName "Bellomberg-Briefing"     -Confirm:$false
#   Unregister-ScheduledTask -TaskName "Bellomberg-AutoBackup"   -Confirm:$false
# ==================================================================

$ProjectPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
$LauncherPath = $PSScriptRoot
$PriceUpdaterBat = Join-Path $LauncherPath "run_price_updater.bat"
$NewsFeedBat     = Join-Path $LauncherPath "run_news_feed.bat"
$ConsigliereBat  = Join-Path $LauncherPath "run_consigliere_scheduled.bat"
$BriefingBat     = Join-Path $LauncherPath "run_briefing_v2.bat"
$DbBackupBat     = Join-Path $LauncherPath "run_db_backup.bat"

Write-Host ""
Write-Host "========================================================" -ForegroundColor Yellow
Write-Host " BELLOMBERG | Installazione TUTTI gli scheduler" -ForegroundColor Yellow
Write-Host "========================================================" -ForegroundColor Yellow
Write-Host ""

# --- Sanity check: tutti i .bat devono esistere ---
$missing = @()
foreach ($b in @($PriceUpdaterBat, $NewsFeedBat, $ConsigliereBat, $BriefingBat, $DbBackupBat)) {
    if (-not (Test-Path $b)) { $missing += $b }
}
if ($missing.Count -gt 0) {
    Write-Host "ERROR: i seguenti .bat non esistono:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

$me = "$env:USERDOMAIN\$env:USERNAME"

# Helper per rimuovere task esistente (idempotente)
function Remove-IfExists($name) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($t) {
        Write-Host "  Rimuovo task esistente: $name" -ForegroundColor DarkGray
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}

# ==================================================================
# 1) PRICE UPDATER - ogni 15 minuti, 24/7
# ==================================================================
Write-Host "[1/5] Registro: Bellomberg-PriceUpdater (ogni 15 min)" -ForegroundColor Cyan
Remove-IfExists "Bellomberg-PriceUpdater"

$action1  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$PriceUpdaterBat`"" -WorkingDirectory $ProjectPath
$trigger1 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 9999)
$settings1 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew
$principal1 = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Bellomberg-PriceUpdater" `
    -Description "Bellomberg - aggiorna prezzi live + FX ogni 15 min." `
    -Action $action1 -Trigger $trigger1 -Settings $settings1 -Principal $principal1 -ErrorAction Stop | Out-Null
Write-Host "  OK -> log: $ProjectPath\data\price_updater.log" -ForegroundColor Green

# ==================================================================
# 2) NEWS FEED - ogni 15 min, Lun-Ven 07:00-23:00
# ==================================================================
Write-Host ""
Write-Host "[2/5] Registro: Bellomberg-NewsFeed (Lun-Ven, ogni 15 min, 07-23)" -ForegroundColor Cyan
Remove-IfExists "Bellomberg-NewsFeed"

$action2 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$NewsFeedBat`"" -WorkingDirectory $ProjectPath

$triggerStart = (Get-Date "07:00:00")
$baseTrigger = New-ScheduledTaskTrigger -Weekly -At $triggerStart `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday
$repTrigger = New-ScheduledTaskTrigger -Once -At $triggerStart `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Hours 16)
$baseTrigger.Repetition = $repTrigger.Repetition

$settings2 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
$principal2 = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Bellomberg-NewsFeed" `
    -Description "Bellomberg - news auto-feed (Marketaux + NewsAPI + RSS + Haiku classifier)." `
    -Action $action2 -Trigger $baseTrigger -Settings $settings2 -Principal $principal2 | Out-Null
Write-Host "  OK -> log: $ProjectPath\data\news_feed.log" -ForegroundColor Green

# ==================================================================
# 3) CONSIGLIERE - Lun + Gio 07:30 AM
# ==================================================================
Write-Host ""
Write-Host "[3/5] Registro: Bellomberg-Consigliere (Lun + Gio 07:30)" -ForegroundColor Cyan
Remove-IfExists "Bellomberg-Consigliere"

$action3  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$ConsigliereBat`"" -WorkingDirectory $ProjectPath
$trigger3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Thursday -At "07:30AM"
$settings3 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal3 = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName "Bellomberg-Consigliere" `
    -Description "Bellomberg - riunione consigliere multi-agente (7 specialisti + Capo) Lun e Gio 07:30." `
    -Action $action3 -Trigger $trigger3 -Settings $settings3 -Principal $principal3 -ErrorAction Stop | Out-Null
Write-Host "  OK -> log: $ProjectPath\data\scheduler.log" -ForegroundColor Green

# ==================================================================
# 4) BRIEFING - 4x/giorno Lun-Ven (07:30 / 11:30 / 15:30 / 19:30)
# ==================================================================
Write-Host ""
Write-Host "[4/5] Registro: Bellomberg-Briefing (Lun-Ven, 4 slot 07:30/11:30/15:30/19:30)" -ForegroundColor Cyan
Remove-IfExists "Bellomberg-Briefing"

$action4 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BriefingBat`"" -WorkingDirectory $ProjectPath

$slots = @("07:30AM", "11:30AM", "03:30PM", "07:30PM")
$triggers4 = @()
foreach ($slot in $slots) {
    $triggers4 += New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $slot
}

$settings4 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal4 = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Bellomberg-Briefing" `
    -Description "Bellomberg - Daily Market Briefing 4x/day via Haiku (overnight + EU open + US open + close)." `
    -Action $action4 -Trigger $triggers4 -Settings $settings4 -Principal $principal4 -ErrorAction Stop | Out-Null
Write-Host "  OK -> log: $ProjectPath\data\briefing.log" -ForegroundColor Green
Write-Host "         cache: $ProjectPath\data\briefing_cache.json" -ForegroundColor DarkGray

# ==================================================================
# 5) DB AUTO-BACKUP - ogni giorno alle 23:00 (con retention 30 backup)
# ==================================================================
Write-Host ""
Write-Host "[5/5] Registro: Bellomberg-AutoBackup (ogni giorno 23:00)" -ForegroundColor Cyan
Remove-IfExists "Bellomberg-AutoBackup"

$action5 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$DbBackupBat`"" -WorkingDirectory $ProjectPath
$trigger5 = New-ScheduledTaskTrigger -Daily -At "11:00PM"
$settings5 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal5 = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Bellomberg-AutoBackup" `
    -Description "Bellomberg - daily DB backup 23:00 + retention ultimi 30 backup in data/backups/" `
    -Action $action5 -Trigger $trigger5 -Settings $settings5 -Principal $principal5 -ErrorAction Stop | Out-Null
Write-Host "  OK -> log: $ProjectPath\data\backup.log" -ForegroundColor Green
Write-Host "         backups: $ProjectPath\data\backups\" -ForegroundColor DarkGray

# ==================================================================
# RIEPILOGO
# ==================================================================
Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host " TUTTI E 5 GLI SCHEDULER REGISTRATI E ATTIVI" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Verifica stato:" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTask -TaskName Bellomberg-* | Get-ScheduledTaskInfo | Format-Table TaskName,LastRunTime,NextRunTime,LastTaskResult" -ForegroundColor White
Write-Host ""
Write-Host "Test manuale (esegui subito):" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName Bellomberg-PriceUpdater" -ForegroundColor White
Write-Host "  Start-ScheduledTask -TaskName Bellomberg-AutoBackup" -ForegroundColor White
Write-Host ""
Write-Host "Per disattivarli TUTTI in futuro:" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTask Bellomberg-* | Unregister-ScheduledTask -Confirm:`$false" -ForegroundColor White
Write-Host ""