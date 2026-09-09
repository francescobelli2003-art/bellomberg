"""bonifica_error_round2.py — Voce 2 §9-quattuortrigies (01/08/2026, Fable 5).

RIMUOVE dal DB i placeholder d'errore salvati come report FINALI (round_n=2):
righe che memory_db.get_recent_specialist_reports leggeva come memoria del desk
e che la garanzia scritta in specialists/base.py:469 prometteva di non creare.
Il fix nel codice (guardia sul ramo round_n==2) impedisce le NUOVE; questo
script bonifica le 9 storiche misurate il 01/08 (memo 48 quant/options + 7
righe del guasto 23/07 su crypto/fundamentals/macro/news/options/politics/quant).

Il predicato di selezione RIPRODUCE quello di base.py (non un'approssimazione):
  report.startswith("[ERROR")  OR  "No output produced in round" in report[:120]
limitato a round_n=2 e ai soli specialisti (niente righe "_*": il red team
persiste incondizionato per decisione P1 storica).

USO:
  python bonifica_error_round2.py            # DRY-RUN: mostra cosa farebbe, non scrive
  python bonifica_error_round2.py --apply    # backup + DELETE + verifica contata

Cinture: rifiuta di scrivere se la porta 8765 e' occupata; backup con l'API
sqlite PRIMA del DELETE; DELETE per id espliciti in transazione; conteggio
prima/dopo VERIFICATO (delta != atteso -> rollback ed exit 1).
"""
import socket
import sqlite3
import sys
from datetime import datetime

# 03/09: era il percorso assoluto della macchina del PM. L'unica fonte del percorso e'
# memory_db.SQLITE_PATH (lo pretende tests/test_data_dir_ancorato.py: un modulo che se lo
# costruisce da solo ignora BELLOMBERG_DATA_DIR e apre un DB vuoto altrove, in silenzio)
from bellomberg.storage import memory_db  # noqa: E402

DB = memory_db.SQLITE_PATH

PREDICATO = ("round_n = 2 AND specialist NOT LIKE '\\_%' ESCAPE '\\' AND ("
             "content LIKE '[ERROR%' OR "
             "instr(substr(content, 1, 120), 'No output produced in round') > 0)")


def porta_8765_occupata() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", 8765)) == 0
    finally:
        s.close()


def main() -> int:
    apply = "--apply" in sys.argv

    con = sqlite3.connect(DB if apply else "file:" + DB.replace("\\", "/") + "?mode=ro",
                          uri=not apply)
    con.row_factory = sqlite3.Row

    righe = con.execute(
        "SELECT id, memo_id, specialist, round_n, substr(content,1,80) AS testa, timestamp "
        "FROM specialist_reports WHERE " + PREDICATO + " ORDER BY id").fetchall()
    totale_prima = con.execute("SELECT COUNT(*) FROM specialist_reports").fetchone()[0]

    print(f"Placeholder d'errore in round 2 (specialisti): {len(righe)} "
          f"su {totale_prima} righe totali")
    for r in righe:
        print(f"  id={r['id']:>4}  memo={r['memo_id']:>3}  {r['specialist']:<13} "
              f"{r['timestamp']}  {r['testa']!r}")

    if not apply:
        print("\nDRY-RUN: nessuna scrittura. Rilancia con --apply per bonificare.")
        con.close()
        return 0

    if porta_8765_occupata():
        print("\nKO: porta 8765 occupata (backend attivo). Niente scritture: "
              "spegni il backend e rilancia.")
        con.close()
        return 1

    if not righe:
        print("\nNiente da bonificare.")
        con.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB.replace("consigliere.db",
                             f"consigliere_backup_{stamp}_pre_bonifica_r2.db")
    dest = sqlite3.connect(backup_path)
    con.backup(dest)
    dest.close()
    print(f"\nBackup scritto: {backup_path}")

    ids = [r["id"] for r in righe]
    try:
        con.execute("BEGIN")
        ph = ",".join("?" * len(ids))
        cur = con.execute("DELETE FROM specialist_reports WHERE id IN (" + ph + ")", ids)
        cancellate = cur.rowcount
        totale_dopo = con.execute("SELECT COUNT(*) FROM specialist_reports").fetchone()[0]
        if cancellate != len(ids) or totale_prima - totale_dopo != len(ids):
            con.execute("ROLLBACK")
            print(f"KO: delta inatteso (cancellate={cancellate}, attese={len(ids)}, "
                  f"totale {totale_prima}->{totale_dopo}). ROLLBACK eseguito.")
            return 1
        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        print(f"KO: {e}. ROLLBACK eseguito.")
        return 1
    finally:
        con.close()

    print(f"OK: cancellate {cancellate} righe ({totale_prima} -> {totale_dopo}), "
          f"conteggio VERIFICATO. Backup in {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
