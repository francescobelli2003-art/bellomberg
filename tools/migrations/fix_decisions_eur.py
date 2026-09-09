"""
fix_decisions_eur.py — Corregge gli importi EUR delle decisioni gia' salvate
parsate male (es. "60k" diventato 60 invece di 60000).
Ri-parsa l'ACTION TABLE dei memo recenti col parser robusto e aggiorna eur_amount.

USO:
  python fix_decisions_eur.py            # DRY-RUN: mostra solo cosa cambierebbe
  python fix_decisions_eur.py --apply    # applica le correzioni al DB
"""
import sys, re, sqlite3, os

os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bellomberg.core.paths import SQLITE_PATH
DB = str(SQLITE_PATH)


# audit/11 §5: parser UNICO — la copia locale poteva divergere da quella canonica di
# memory_db (e gia' divergeva sulla regola '\dk\b'): uno script che CORREGGE il DB deve
# usare lo STESSO parser del sistema. Import senza costi (chromadb e' lazy in memory_db).
from bellomberg.storage.memory_db import _parse_eur_amount


def main(apply=False):
    if not os.path.exists(DB):
        print("DB non trovato:", DB); return
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    memos = conn.execute("SELECT id, full_markdown FROM memos ORDER BY id DESC LIMIT 6").fetchall()
    changes = []
    for memo in memos:
        md = memo["full_markdown"] or ""
        m = re.search(r"##\s*ACTION TABLE.*?\n(\|.*?\|.*?\n)+", md, re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        rows = []
        for line in m.group(0).split("\n"):
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5 or "---" in cells[0] or cells[0].lower() == "action":
                continue
            rows.append((cells[0], cells[1], _parse_eur_amount(cells[2])))
        decs = conn.execute(
            "SELECT id, action, ticker, eur_amount FROM decisions WHERE memo_id=? ORDER BY id",
            (memo["id"],)).fetchall()
        # audit/11 §4: lo zip posizionale senza verifica d'identita' poteva scrivere
        # l'importo sulla decisione SBAGLIATA (basta una decisione cancellata a mano).
        if len(decs) != len(rows):
            print(f"  [!] memo {memo['id']}: {len(decs)} decisioni vs {len(rows)} righe "
                  "ACTION TABLE — disallineate, memo SALTATO")
            continue
        for dec, row in zip(decs, rows):
            if ((dec["ticker"] or "").strip().upper() != (row[1] or "").strip().upper()
                    or (dec["action"] or "").strip().upper() != (row[0] or "").strip().upper()):
                print(f"  [!] memo {memo['id']}: coppia disallineata "
                      f"(#{dec['id']} {dec['action']}/{dec['ticker']} vs {row[0]}/{row[1]}), saltata")
                continue
            new_eur = row[2]
            old = dec["eur_amount"]
            if new_eur is not None and (old is None or abs((old or 0) - new_eur) > 0.5):
                changes.append((dec["id"], dec["action"], dec["ticker"], old, new_eur))

    if not changes:
        print("Nessuna correzione necessaria."); conn.close(); return
    print("Correzioni proposte (decisione: vecchio -> nuovo):")
    for (did, act, tk, old, new) in changes:
        print(f"  #{did:>3} {act:<14} {tk:<16} {old} -> {new:,.2f}")
    if apply:
        for (did, _, _, _, new) in changes:
            conn.execute("UPDATE decisions SET eur_amount=? WHERE id=?", (new, did))
        conn.commit()
        print(f"\nAPPLICATO: {len(changes)} decisioni aggiornate.")
    else:
        print(f"\nDRY-RUN: {len(changes)} decisioni da correggere. Rilancia con --apply per applicare.")
    conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
