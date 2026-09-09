# xls_to_csv.ps1 — converte i .xls binari Damodaran in CSV (un CSV per foglio)
# via Excel COM (nessuna dipendenza pip: xlrd non e' installato su questa macchina).
# USO: powershell -ExecutionPolicy Bypass -File scripts\xls_to_csv.ps1 -SrcDir C:\BellombergData\damodaran [-OutDir ...\csv]
# Chiamato da scripts\aggiorna_damodaran.py. Idempotente: sovrascrive i CSV.
param(
    [Parameter(Mandatory = $true)][string]$SrcDir,
    [string]$OutDir = ""
)
if (-not $OutDir) { $OutDir = Join-Path $SrcDir "csv" }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$files = Get-ChildItem -Path $SrcDir -Filter "*.xls" | Where-Object { $_.Extension -eq ".xls" }
if (-not $files) { Write-Output "Nessun .xls in $SrcDir"; exit 0 }

$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
try {
    foreach ($f in $files) {
        $wb = $xl.Workbooks.Open($f.FullName, 0, $true)
        try {
            foreach ($ws in $wb.Worksheets) {
                $safe = ($ws.Name -replace '[^A-Za-z0-9]+', '_').Trim('_')
                $dest = Join-Path $OutDir ("{0}__{1}.csv" -f $f.BaseName, $safe)
                if (Test-Path $dest) { Remove-Item $dest -Force }
                # 62 = xlCSVUTF8 (Excel >=2016); fallback 6 = xlCSV
                try { $ws.SaveAs($dest, 62) } catch { $ws.SaveAs($dest, 6) }
                Write-Output ("OK {0} -> {1}" -f $f.Name, (Split-Path $dest -Leaf))
            }
        } finally {
            $wb.Close($false)
        }
    }
} finally {
    $xl.Quit()
    [Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
}
