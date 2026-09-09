@echo off
REM ============================================================
REM   BELLOMBERG - Daily Market Briefing Generator
REM   Called by Windows Task Scheduler 4x/day:
REM     07:30 (morning), 11:30 (midday), 15:30 (afternoon), 19:30 (evening)
REM   Mon-Fri. Output: data\briefing_cache.json (consumed by /news/briefing/current)
REM ============================================================

setlocal
for %%I in ("%~dp0..\..\..") do set "ROOT=%%~fI"
REM Repository root, three levels above tools/ops/windows.
set "LOG=%ROOT%\data\briefing.log"
set "TS=%date% %time%"

if not exist "%ROOT%\data" mkdir "%ROOT%\data"

echo. >> "%LOG%"
echo [%TS%] === briefing slot start === >> "%LOG%"

REM --- Try backend first (fast path, backend has API key already loaded) ---
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/news/briefing/refresh' -Method POST -TimeoutSec 90 -ErrorAction Stop; Write-Host ('OK period=' + $r.period + ' chars=' + $r.briefing_md.Length + ' tokens_in=' + $r.tokens_in + ' tokens_out=' + $r.tokens_out) } catch { Write-Host ('BACKEND_DOWN: ' + $_.Exception.Message); exit 1 }" >> "%LOG%" 2>&1

if %ERRORLEVEL% EQU 0 (
    echo [%TS%] backend path OK >> "%LOG%"
    goto :END
)

REM --- Fallback: direct Python invocation ---
echo [%TS%] backend unreachable, calling python directly... >> "%LOG%"
cd /d "%ROOT%"
python -c "from bellomberg.cli.briefing_engine import generate_briefing; r = generate_briefing(); print('direct OK', r.get('period'), 'chars=', len(r.get('briefing_md','')))" >> "%LOG%" 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [%TS%] *** ERROR: python fallback failed *** >> "%LOG%"
)

:END
echo [%TS%] === done === >> "%LOG%"
endlocal
exit /b 0
