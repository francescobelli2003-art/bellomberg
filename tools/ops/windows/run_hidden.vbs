' run_hidden.vbs - lancia un .bat SENZA finestra e restituisce il suo exit code.
' Bellomberg, 12/09/2026: azione dei 5 task del Task Scheduler (v. task_senza_finestra.ps1
' e install_all_schedulers.ps1). cmd.exe e' un programma a console e sul desktop interattivo
' Windows gli apre una finestra visibile (l'impostazione Hidden del task nasconde il task
' dalla lista, non la finestra); questo lanciatore la nasconde e non cambia nient'altro.
'
' Uso (azione del task, WorkingDirectory = cartella REALE del repo):
'   wscript.exe //B //Nologo "<repo>\tools\ops\windows\run_hidden.vbs" "<repo>\tools\ops\windows\run_X.bat"
' - 0    = finestra nascosta (nessuna console, nessun flash: la console del figlio nasce nascosta)
' - True = attende la fine: l'istanza del task resta in esecuzione finche' il .bat finisce
'          (MultipleInstances IgnoreNew invariato)
' - WScript.Quit rc = l'exit code del .bat diventa l'exit code di wscript.exe = LastTaskResult
' - //B  = batch mode: nessun popup di WSH nemmeno su errore di script
' Misurato 12/09/2026: exit 7->7, 0->0, 42->42, 255->255 (catena AutoBackup vera); 2 senza
'   argomenti; cwd, variabili d'ambiente, %TEMP% e PATH ereditati intatti dal task.
' Vincoli: CRLF (.gitattributes: *.vbs text eol=crlf), niente BOM, solo ASCII (WSH legge ANSI);
'   guardiano tests/test_bat_crlf.py. Riserva se VBScript sparisse da Windows: pythonw + CREATE_NO_WINDOW.
Option Explicit
Dim sh, rc, bat
If WScript.Arguments.Count <> 1 Then
    WScript.Quit 2
End If
bat = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
rc = sh.Run("cmd.exe /c """ & bat & """", 0, True)
WScript.Quit rc
