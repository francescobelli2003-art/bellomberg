@echo off
REM BELLOMBERG - Auto Consigliere Run (Lunedi + Giovedi 7:30 AM)
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
for %%I in ("%~dp0..\..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
if not exist data mkdir data
echo [%date% %time%] Starting Bellomberg consigliere... >> data\scheduler.log
python consigliere_multi.py >> data\scheduler.log 2>&1
echo [%date% %time%] Done. >> data\scheduler.log
exit /b %ERRORLEVEL%
