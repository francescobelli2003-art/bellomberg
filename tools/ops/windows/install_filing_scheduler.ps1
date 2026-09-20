# Solo il task I-20. Nessuna modifica agli altri scheduler Bellomberg.
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$FilingProject = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$FilingPythonCommand = (Get-Command python.exe -ErrorAction Stop).Source
$FilingPython = (& $FilingPythonCommand -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $FilingPython)) { throw 'Interprete Python non verificato' }
$FilingPythonw = Join-Path (Split-Path $FilingPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $FilingPythonw)) { throw 'pythonw.exe assente: installazione annullata' }
$FilingName = 'Bellomberg-FilingDiff'
$FilingArgs = '-m bellomberg.market_data.filing_worker --due'
$FilingExisting = Get-ScheduledTask -TaskName $FilingName -ErrorAction SilentlyContinue
[pscustomobject]@{ Task = $FilingName; Python = $FilingPythonw; Arguments = $FilingArgs; Directory = $FilingProject; Schedule = 'Daily 08:10; profili scaduti soltanto'; Apply = [bool]$Apply }
if (-not $Apply) { return }
if ($FilingExisting) { throw 'Task gia presente: confrontare la configurazione prima di sostituirlo' }
$FilingAction = New-ScheduledTaskAction -Execute $FilingPythonw -Argument $FilingArgs -WorkingDirectory $FilingProject
$FilingTrigger = New-ScheduledTaskTrigger -Daily -At '08:10'
$FilingSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$FilingPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $FilingName -Action $FilingAction -Trigger $FilingTrigger -Settings $FilingSettings -Principal $FilingPrincipal -Description 'Confronti documentali dei profili abilitati e scaduti. Log: filing_worker.jsonl nella directory dati.' | Out-Null
Get-ScheduledTask -TaskName $FilingName | Select-Object TaskName, State, Actions, Triggers
