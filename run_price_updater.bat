@echo off
setlocal
set "ROOT="
for /f "usebackq delims=" %%I in (`python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "%~dp0."`) do set "ROOT=%%I"
if not defined ROOT (
    echo Bellomberg: impossibile risolvere il percorso reale del progetto. 1>&2
    endlocal
    exit /b 2
)
if not defined BELLOMBERG_PROJECT_ROOT set "BELLOMBERG_PROJECT_ROOT=%ROOT%"
call "%ROOT%\tools\ops\windows\run_price_updater.bat"
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
