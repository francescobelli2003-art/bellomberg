@echo off
REM ============================================================
REM   BELLOMBERG - Daily DB Backup
REM   Triggered by Windows Task Scheduler ogni giorno alle 23:00.
REM   Chiama POST /db/backup sul backend FastAPI.
REM   Se il backend non risponde, fallback a Python diretto.
REM   Output: DATA_DIR\backups\bellomberg_backup_YYYYMMDD_HHMMSS.zip
REM
REM   AutoBackup P1 (12/08): l'esito del task ora e' una MISURA.
REM   exit 0 SOLO se uno zip nuovo di QUESTO giro esiste su disco (>1 MB);
REM   qualunque fallimento esce 1 e lo dichiara nel log. Prima: i fine-riga
REM   LF rompevano i goto (task rosso 1/255 su backup riusciti) e il ramo
REM   di errore del fallback usciva 0 (fallimento vero = task verde).
REM   Fine-riga: CRLF OBBLIGATORI (.gitattributes: *.bat text eol=crlf).
REM ============================================================

setlocal
for %%I in ("%~dp0..\..\..") do set "ROOT=%%~fI"
REM Repository root, three levels above tools/ops/windows.
if not defined BELLOMBERG_PROJECT_ROOT set "BELLOMBERG_PROJECT_ROOT=%ROOT%"
cd /d "%ROOT%"
set "DATA_DIR="
set "DATA_DIR_FILE=%TEMP%\bellomberg_data_dir_%RANDOM%_%RANDOM%.tmp"
set "DATA_DIR_ERR=%DATA_DIR_FILE%.err"
call python -c "from bellomberg.core.paths import DATA_DIR; print(DATA_DIR)" > "%DATA_DIR_FILE%" 2> "%DATA_DIR_ERR%"
if %ERRORLEVEL% NEQ 0 goto :DATA_DIR_FAIL
set /p "DATA_DIR="<"%DATA_DIR_FILE%"
del /q "%DATA_DIR_FILE%" > nul 2>&1
if not defined DATA_DIR goto :DATA_DIR_EMPTY
set "LOG=%DATA_DIR%\backup.log"
set "TS=%date% %time%"
set "MARKER=%TEMP%\bellomberg_backup_start.tmp"

if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
if not exist "%DATA_DIR%\backups" mkdir "%DATA_DIR%\backups"
if exist "%DATA_DIR_ERR%" for %%I in ("%DATA_DIR_ERR%") do if %%~zI GTR 0 (
    echo [%TS%] avvisi durante la risoluzione di DATA_DIR: >> "%LOG%"
    type "%DATA_DIR_ERR%" >> "%LOG%"
)
del /q "%DATA_DIR_ERR%" > nul 2>&1

echo. >> "%LOG%"
echo [%TS%] === auto-backup start === >> "%LOG%"
type nul > "%MARKER%"

REM --- Try backend first ---
REM DB igiene 21/07: si controlla anche $r.ok (quick_check per-DB dell'endpoint):
REM ok=false = un DB e' stato SALTATO dal backup -> si tenta comunque il fallback
REM diretto e il motivo resta nel log (mai un backup a meta' loggato come OK).
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/db/backup' -Method POST -TimeoutSec 60 -ErrorAction Stop; if (-not $r.ok) { Write-Host ('QUICK_CHECK_KO: ' + ($r.db_quick_check | ConvertTo-Json -Compress)); exit 2 }; Write-Host ('OK ' + $r.size_mb + 'MB ' + $r.files_count + ' files - ' + $r.backup_path) } catch { Write-Host ('BACKEND_DOWN: ' + $_.Exception.Message); exit 1 }" >> "%LOG%" 2>&1

if %ERRORLEVEL% EQU 0 (
    echo [%TS%] backend backup OK >> "%LOG%"
    goto :VERIFY
)

REM --- Fallback: backup diretto via Python (no backend) ---
REM DB igiene 21/07: NIENTE piu' copia nuda del WAL vivo (il vecchio one-liner
REM zippava data/*.db live e perdeva il -wal): sqlite backup API + quick_check.
echo [%TS%] backend unreachable/KO, calling python db_backup_direct... >> "%LOG%"
python tools\ops\db_backup_direct.py >> "%LOG%" 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [%TS%] *** ERROR: python fallback failed or quick_check KO ^(v. righe sopra^) *** >> "%LOG%"
    goto :FAIL
)

:VERIFY
REM --- La garanzia e' una MISURA, non una frase (lezione 21/07): exit 0 solo se
REM --- in backups/ esiste uno zip scritto DOPO il marker di questo giro e >1MB.
powershell -NoProfile -Command ^
  "$dir=$env:DATA_DIR; $marker=$env:MARKER; if (-not (Test-Path -LiteralPath $marker)) { Write-Host 'VERIFY_KO: marker di inizio giro assente - impossibile misurare la freschezza'; exit 1 }; $m=(Get-Item -LiteralPath $marker).LastWriteTime.AddSeconds(-2); $z=Get-ChildItem -LiteralPath (Join-Path $dir 'backups') -Filter 'bellomberg_backup_*.zip' | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($null -eq $z) { Write-Host 'VERIFY_KO: nessuno zip in backups/'; exit 1 }; if ($z.LastWriteTime -lt $m) { Write-Host ('VERIFY_KO: zip piu recente ' + $z.Name + ' PRECEDENTE a questo giro (' + $z.LastWriteTime + ' vs marker ' + $m + ')'); exit 1 }; if ($z.Length -lt 1MB) { Write-Host ('VERIFY_KO: ' + $z.Name + ' sotto 1 MB: ' + $z.Length + ' byte'); exit 1 }; Write-Host ('VERIFY_OK: ' + $z.Name + ' ' + [math]::Round($z.Length/1MB,2) + ' MB')" >> "%LOG%" 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [%TS%] *** ERROR: VERIFICA FALLITA: il backup dichiarato non risulta su disco *** >> "%LOG%"
    goto :FAIL
)

:CLEANUP
REM --- Retention: tieni gli ultimi $keep backup, cancella i piu' vecchi ---
REM --- (gira SOLO dopo VERIFY_OK: mai potare il passato su un giro fallito) ---
powershell -NoProfile -Command ^
  "$dir=Join-Path $env:DATA_DIR 'backups'; $keep=30; $files=Get-ChildItem -LiteralPath $dir -Filter 'bellomberg_backup_*.zip' | Sort-Object LastWriteTime -Descending; if ($files.Count -gt $keep) { $files | Select-Object -Skip $keep | Remove-Item -Force; Write-Host ('cleanup: rimossi ' + ($files.Count - $keep) + ' backup vecchi') } else { Write-Host ('retention OK: ' + $files.Count + ' backup totali') }" >> "%LOG%" 2>&1

echo [%TS%] === done === >> "%LOG%"
endlocal
exit /b 0

:FAIL
echo [%TS%] === done (FALLITO, exit 1) === >> "%LOG%"
endlocal
exit /b 1

:DATA_DIR_FAIL
echo *** ERROR: impossibile risolvere DATA_DIR da bellomberg.core.paths *** 1>&2
type "%DATA_DIR_FILE%" 1>&2
type "%DATA_DIR_ERR%" 1>&2
del /q "%DATA_DIR_FILE%" > nul 2>&1
del /q "%DATA_DIR_ERR%" > nul 2>&1
endlocal
exit /b 1

:DATA_DIR_EMPTY
echo *** ERROR: bellomberg.core.paths.DATA_DIR vuota *** 1>&2
del /q "%DATA_DIR_FILE%" > nul 2>&1
del /q "%DATA_DIR_ERR%" > nul 2>&1
endlocal
exit /b 1
