# bake_xlsx_values.ps1 — P0 17/07 "valori dentro i fogli": openpyxl scrive le formule
# SENZA valori cached, quindi il workbook APPARE vuoto se Excel non ricalcola
# all'apertura (o in un'anteprima). Questo script apre il file via Excel COM,
# forza il ricalcolo completo e risalva: le formule restano VIVE, il ricalcolo
# aggiunge solo i valori cached. Stesso pattern COM di xls_to_csv.ps1 (provato
# su questa macchina); nessuna dipendenza pip.
# USO: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bake_xlsx_values.ps1 -Path report\VAL_X.xlsx
# EXIT: 0 = OK · 2 = file non trovato · 3 = Excel COM non disponibile · 1 = ricalcolo/salvataggio fallito
param(
    [Parameter(Mandatory = $true)][string]$Path
)
# -LiteralPath: Test-Path/Resolve-Path nudi fanno glob-matching (review 17/07 B3:
# un nome con [parentesi] risulterebbe "non trovato")
if (-not (Test-Path -LiteralPath $Path)) { Write-Output "KO file non trovato: $Path"; exit 2 }
$full = (Resolve-Path -LiteralPath $Path).Path

try {
    $xl = New-Object -ComObject Excel.Application
} catch {
    Write-Output "KO Excel COM non disponibile: $($_.Exception.Message)"; exit 3
}
$xl.Visible = $false
$xl.DisplayAlerts = $false
try { $xl.AskToUpdateLinks = $false } catch {}

$exitCode = 1
try {
    # Open(path, UpdateLinks=0, ReadOnly=$false)
    $wb = $xl.Workbooks.Open($full, 0, $false)
    try {
        # -4105 = xlCalculationAutomatic; settabile solo a workbook aperto
        try { $xl.Calculation = -4105 } catch {}
        $xl.CalculateFullRebuild()
        $wb.Save()
        Write-Output "OK ricalcolato e salvato: $full"
        $exitCode = 0
    } finally {
        # review 17/07 B5: una Close fallita DOPO una Save riuscita non deve produrre
        # un "KO" col file gia' a posto — warning dichiarato, l'esito lo decide la Save
        try { $wb.Close($false) } catch { Write-Output "WARN Close fallita (file gia' salvato): $($_.Exception.Message)" }
    }
} catch {
    Write-Output "KO ricalcolo fallito: $($_.Exception.Message)"
} finally {
    $xl.Quit()
    [Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
}
exit $exitCode
