@echo off
REM ============================================================
REM   BELLOMBERG - News Auto-Feed Puller
REM   Called by Windows Task Scheduler every 15 min during
REM   market hours. Hits the FastAPI backend if it's running,
REM   else falls back to direct Python invocation.
REM ============================================================

setlocal
for %%I in ("%~dp0..\..\..") do set "ROOT=%%~fI"
REM Repository root, three levels above tools/ops/windows.
set "LOG=%ROOT%\data\news_feed.log"
set "TS=%date% %time%"

REM Ensure data folder exists
if not exist "%ROOT%\data" mkdir "%ROOT%\data"

echo. >> "%LOG%"
echo [%TS%] === news feed refresh start === >> "%LOG%"

REM --- Try backend first (fast path, uses already-loaded models) ---
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/news/feed/refresh?days=1&classify=true' -Method POST -TimeoutSec 180 -ErrorAction Stop; $st = 'OK'; $bl = ''; if ($null -eq $r.PSObject.Properties['degraded']) { $st = 'STATO_IGNOTO'; $bl = ' (backend pre-P0: non riporta lo stato provider - riavviarlo)' } elseif ($r.degraded) { $st = 'DEGRADATO'; $bl = ' provider_muti=' + (($r.providers_blocked.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value }) -join ',') }; Write-Host ($st + ' fetched=' + $r.fetched + ' classified=' + $r.classified + ' saved=' + $r.saved + ' dupes=' + $r.skipped_duplicates + $bl) } catch { Write-Host ('BACKEND_DOWN: ' + $_.Exception.Message); exit 1 }" >> "%LOG%" 2>&1

if %ERRORLEVEL% EQU 0 (
    echo [%TS%] backend path OK >> "%LOG%"
    goto :END
)

REM --- Fallback: direct Python invocation (backend not running) ---
echo [%TS%] backend unreachable, calling python directly... >> "%LOG%"
cd /d "%ROOT%"
python -c "from bellomberg.market_data.news_aggregator import auto_pull_feed; r = auto_pull_feed(days=1, classify=True); print('direct DEGRADATO' if r.get('degraded') else 'direct OK', r)" >> "%LOG%" 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [%TS%] *** ERROR: python fallback failed *** >> "%LOG%"
)

:END
echo [%TS%] === done === >> "%LOG%"
endlocal
exit /b 0
