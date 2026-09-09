@echo off
REM BELLOMBERG - Auto Price Updater
REM Schedula questo file in Windows Task Scheduler ogni 15 minuti.
REM
REM Setup Task Scheduler:
REM   1. Apri Task Scheduler (Win+R, taskschd.msc)
REM   2. Crea attivita': nome = "Bellomberg Price Updater"
REM   3. Trigger: ripeti ogni 15 minuti, H24 (verificato 25/07: il task VERO
REM      gira con repeat infinito, non "8 ore" come diceva questo commento;
REM      la foto IV giornaliera ha la sua guardia oraria in iv_history.py)
REM   4. Azione: avvia programma = path completo a questo .bat
REM   5. Spunta "Esegui con privilegi piu' alti"
REM
REM Per loop continuo dalla shell:
REM   python price_updater.py --loop 60

chcp 65001 > nul
set PYTHONIOENCODING=utf-8

for %%I in ("%~dp0..\..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
if not exist data mkdir data
python price_updater.py --no-ibkr --quiet >> data\price_updater.log 2>&1
exit /b %ERRORLEVEL%
