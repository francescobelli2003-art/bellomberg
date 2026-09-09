# -*- coding: utf-8 -*-
"""Quarantena reversibile degli xlsx di valutazione.

I veicoli vengono letti dal negozio corrente a ogni operazione. I file legacy e
lo storico oltre gli ultimi tre modelli restano gestiti come prima. Senza
``--apply`` viene mostrata soltanto l'anteprima.
"""
import argparse
import os
import re
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
KEEP_PER_TICKER = 3
TIPI_VEICOLO = {"cef", "dat", "etf", "etn", "commodity", "crypto", "holding"}
PAT = re.compile(r"^(VAL|DCF)_(.+?)_(\d{8}_\d{4})(_FLAGGED)?\.xlsx$")


def backend_alive() -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 8765))
        s.close()
        return True
    except Exception:
        return False


def _dentro(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _percorso_leggibile(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:  # Windows: report e progetto possono essere su dischi diversi.
        return str(path.resolve())


def _valida_piano(quarantena, root: Path, dest: Path, *, report_dir=None):
    origini = [Path(report_dir) if report_dir is not None else root / "report", root / "models"]
    for directory in origini:
        configurata = report_dir is not None and directory == Path(report_dir)
        if directory.exists() and not configurata and not _dentro(directory, root):
            return "radice sorgente fuori dal repository: %s" % directory
    if not _dentro(dest, root / "archive" / "private" / "attic") or not _dentro(root / "archive" / "private" / "attic", root):
        return "radice destinazione fuori da attic del repository: %s" % dest
    destinazioni = set()
    for sorgente, _ in quarantena:
        sorgente = Path(sorgente)
        if not any(_dentro(sorgente, directory) for directory in origini):
            return "sorgente fuori da report/models: %s" % sorgente
        arrivo = dest / sorgente.name
        chiave = os.path.normcase(str(arrivo.resolve()))
        if not _dentro(arrivo, dest):
            return "destinazione fuori quarantena: %s" % arrivo
        if chiave in destinazioni:
            return "due sorgenti hanno la stessa destinazione: %s" % arrivo.name
        if arrivo.exists():
            return "destinazione gia' esistente: %s" % arrivo
        destinazioni.add(chiave)
    return None


def main(argv=None, *, root=None, carica_negozio=None, oggi=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report_dir = None
    if root is None:
        from bellomberg.core.paths import PROJECT_ROOT, REPORT_DIR
        root = PROJECT_ROOT
        report_dir = REPORT_DIR
    root = Path(root).resolve()

    if carica_negozio is None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from bellomberg.storage.classificazione import carica_veicoli
        carica_negozio = carica_veicoli

    negozio = carica_negozio()
    if negozio.get("origine") in {"assente", "illeggibile"}:
        print("STOP: negozio dei veicoli %s: %s" %
              (negozio.get("origine"), negozio.get("motivo") or "causa non dichiarata"))
        return 2
    veicoli = {
        ticker.replace(".", "_")
        for ticker, voce in negozio.get("veicoli", {}).items()
        if isinstance(voce, dict) and voce.get("tipo") in TIPI_VEICOLO
    }

    # Si spostano solo xlsx; il backend acceso non rende incoerente il DB.
    if args.apply and backend_alive():
        print("NOTA: backend acceso - ok per questo script (solo spostamenti di file).")
    dest = root / "archive" / "private" / "attic" / ("val_quarantena_" + (oggi or datetime.now().strftime("%Y%m%d")))
    per_ticker = {}
    quarantena = []
    for base in (Path(report_dir) if report_dir is not None else root / "report", root / "models"):
        if not base.is_dir():
            continue
        for nome_file in sorted(os.listdir(base)):
            if not nome_file.lower().endswith(".xlsx"):
                continue
            path = base / nome_file
            match = PAT.match(nome_file)
            if not match:
                quarantena.append((path, "legacy/demo fuori pattern"))
                continue
            famiglia, nome, stamp = match.group(1), match.group(2).upper(), match.group(3)
            if " " in nome:
                quarantena.append((path, "legacy nome esteso (pre-standard ticker)"))
                continue
            if famiglia == "DCF" and nome in veicoli:
                quarantena.append((path, "DCF su veicolo dichiarato dal negozio"))
                continue
            per_ticker.setdefault(nome, []).append((stamp, path))
    for nome, files in per_ticker.items():
        files.sort(reverse=True)
        for _, path in files[KEEP_PER_TICKER:]:
            quarantena.append((path, "storico oltre gli ultimi %d di %s" % (KEEP_PER_TICKER, nome)))

    tenuti = sum(len(v[:KEEP_PER_TICKER]) for v in per_ticker.values())
    print("Tengo %d file (max %d per ticker, %d ticker); in QUARANTENA %d file -> %s"
          % (tenuti, KEEP_PER_TICKER, len(per_ticker), len(quarantena), dest))
    for path, motivo in quarantena:
        print("  - %s  [%s]" % (_percorso_leggibile(path, root), motivo))

    errore_piano = _valida_piano(quarantena, root, dest, report_dir=report_dir)
    if errore_piano:
        print("STOP: piano di quarantena non sicuro: %s" % errore_piano)
        return 2
    if not args.apply:
        print("\nANTEPRIMA: nessuno spostamento. Rilancia con --apply.")
        return 0

    os.makedirs(dest, exist_ok=True)
    manifest = [datetime.now().isoformat(timespec="seconds") +
                " - quarantena xlsx valutazione da negozio corrente (reversibile)"]
    mossi = 0
    for path, motivo in quarantena:
        try:
            shutil.move(path, dest / path.name)
            manifest.append("%s <- %s  [%s]" % (path.name, _percorso_leggibile(path, root), motivo))
            mossi += 1
        except Exception as exc:
            manifest.append("ERRORE su %s: %s" % (path, exc))
            print("  ERRORE su %s: %s" % (path, exc))
    with open(dest / "_MANIFEST.txt", "w", encoding="utf-8") as handle:
        handle.write("\n".join(manifest))
    completo = mossi == len(quarantena)
    print("%s: %d/%d file in quarantena. Manifest: %s" %
          ("FATTO" if completo else "KO parziale", mossi, len(quarantena), dest / "_MANIFEST.txt"))
    return 0 if completo else 2


if __name__ == "__main__":
    sys.exit(main())
