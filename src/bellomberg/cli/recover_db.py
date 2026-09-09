"""
BELLOMBERG - DB Recovery Script

Database SQLite corrotto. Questo script:
1. Backup automatico del DB corrotto
2. Tenta recovery via sqlite3 .iterdump (legge ciò che può)
3. Se fallisce completamente, ricrea DB pulito da schema
4. Preserva trade_history, decisions, memos, chat_sessions, news_feed

USO (da qualunque cartella: il percorso del DB e' ancorato a memory_db):
    python recover_db.py
"""
import os
import sys
import shutil
import sqlite3
from datetime import datetime

from bellomberg.storage.memory_db import SQLITE_PATH as DB_PATH, DB_DIR   # B4 (02/09): ancorati, non relativi alla cwd


def log(msg, color=None):
    """Print colorato basic per Windows."""
    print(f"[RECOVERY] {msg}", flush=True)


def step_0_cleanup_partial():
    """Pulisci eventuali file parziali da tentativi precedenti."""
    log("STEP 0: Cleanup file parziali")
    for f in ["consigliere.db", "consigliere.db-journal", "consigliere.db-shm", "consigliere.db-wal"]:
        path = os.path.join(DB_DIR, f)
        if os.path.exists(path):
            sz = os.path.getsize(path)
            # Se il DB principale è < 50KB probabilmente è un parziale
            if f == "consigliere.db" and sz < 50_000:
                bad_path = path + ".partial_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                os.rename(path, bad_path)
                log(f"  Rinominato {f} ({sz}B parziale) -> {os.path.basename(bad_path)}")


def step_1_find_source():
    """Trova il DB sorgente da cui recuperare (corrupted o bad)."""
    log("STEP 1: Trovo il DB sorgente")
    candidates = []
    for f in os.listdir(DB_DIR):
        if f.startswith("consigliere.db") and (f.endswith(".bad") or ".corrupted_" in f or f == "consigliere.db"):
            full = os.path.join(DB_DIR, f)
            sz = os.path.getsize(full)
            if sz > 100_000:  # > 100KB plausibile
                candidates.append((sz, full))
    candidates.sort(reverse=True)
    if not candidates:
        log("  Nessun DB sorgente trovato! Verra' creato DB pulito.")
        return None
    src = candidates[0][1]
    log(f"  Sorgente: {src} ({candidates[0][0]} bytes)")
    return src


def step_2_backup(src):
    """Backup ulteriore prima di toccare nulla."""
    log("STEP 2: Backup di sicurezza")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(DB_DIR, f"consigliere.db.RECOVERY_BACKUP_{ts}")
    shutil.copy2(src, backup)
    log(f"  Backup -> {backup}")
    return backup


def step_3_try_recovery(src):
    """Tenta recovery: legge tabelle e copia in DB nuovo."""
    log("STEP 3: Tentativo recovery dati")
    recovered = {}
    try:
        # Apri sorgente in READ-ONLY per evitare ulteriori danni
        conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        conn.text_factory = bytes
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [r[0].decode() if isinstance(r[0], bytes) else r[0] for r in cur.fetchall()]
        log(f"  Trovate {len(tables)} tabelle: {tables}")
        for t in tables:
            try:
                cur.execute(f"SELECT * FROM {t}")
                # riallineamento 23/07 (audit/20 review F1): cattura i NOMI delle
                # colonne della sorgente — il reinserimento posizionale scambiava
                # i dati quando l'ordine colonne sorgente != destinazione (caso
                # reale: favorite_companies del DB vivo ha note DOPO added_at, il
                # canonico prima -> le note del PM finivano in added_at, zitte).
                src_cols = [d[0] for d in (cur.description or [])]
                rows = []
                fails = 0
                while True:
                    try:
                        r = cur.fetchone()
                        if r is None:
                            break
                        rows.append(r)
                        fails = 0
                    except sqlite3.DatabaseError:
                        # audit/11 §4: su cursor corrotto fetchone() rilancia la STESSA
                        # eccezione per sempre -> il vecchio `continue` era un loop
                        # infinito. Dopo 3 errori consecutivi si abbandona la tabella.
                        fails += 1
                        if fails >= 3:
                            log(f"  {t}: stop dopo {fails} errori consecutivi "
                                f"({len(rows)} righe salvate fin qui)")
                            break
                        continue
                recovered[t] = {"cols": src_cols, "rows": rows}
                log(f"  {t}: recuperate {len(rows)} righe")
            except Exception as e:
                log(f"  {t}: FAIL - {type(e).__name__}: {str(e)[:80]}")
                recovered[t] = {"cols": [], "rows": []}
        conn.close()
    except Exception as e:
        log(f"  Sorgente non leggibile: {type(e).__name__}: {e}")
    return recovered


def step_4_recreate_fresh():
    """Ricrea DB pulito usando lo schema di memory_db.py."""
    log("STEP 4: Creo DB pulito da schema")
    # MemoryDB.__init__ crea automaticamente lo schema
    try:
        from bellomberg.storage.memory_db import MemoryDB
        db = MemoryDB()
        log("  DB pulito creato OK")
        return True
    except Exception as e:
        log(f"  FAIL creazione DB pulito: {e}")
        return False


def step_5_reinsert(recovered):
    """Reinserisce dati recuperati nel DB pulito."""
    log("STEP 5: Reinserimento dati recuperati")
    if not recovered:
        log("  Nessun dato da reinserire (DB era illeggibile)")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
    cur = conn.cursor()
    total_inserted = 0
    for t, payload in recovered.items():
        # riallineamento 23/07: recovered[t] = {"cols": [...], "rows": [...]}
        src_cols = payload.get("cols") or []
        rows = payload.get("rows") or []
        if not rows:
            continue
        try:
            # Trova le colonne del nuovo schema
            cur.execute(f"PRAGMA table_info({t})")
            cols = cur.fetchall()
            if not cols:
                log(f"  {t}: tabella non esiste in schema nuovo (vecchia), skip")
                continue
            dst_cols = [c[1] for c in cols]
            n_cols_new = len(dst_cols)
            # riallineamento 23/07 (audit/20 review F1): reinserimento PER NOME —
            # intersezione sorgente/destinazione nell'ordine della destinazione.
            # Colonne solo-sorgente scartate DICHIARATE; colonne solo-destinazione
            # restano ai loro default/NULL. Il vecchio posizionale resta SOLO come
            # fallback dichiarato quando la sorgente non ha description (corrotta).
            by_name = bool(src_cols)
            if by_name:
                common = [c for c in dst_cols if c in src_cols]
                dropped = [c for c in src_cols if c not in dst_cols]
                if not common:
                    log(f"  {t}: nessuna colonna in comune con lo schema nuovo, skip dichiarato")
                    continue
                if dropped:
                    log(f"  {t}: colonne sorgente scartate (non nello schema nuovo): {dropped}")
                src_idx = {c: i for i, c in enumerate(src_cols)}
                insert_sql = (f"INSERT OR IGNORE INTO {t} ({', '.join(common)}) "
                              f"VALUES ({','.join(['?'] * len(common))})")
            else:
                log(f"  {t}: sorgente senza nomi colonna -> reinserimento POSIZIONALE "
                    "(fallback dichiarato: possibile disallineamento se gli schemi divergono)")
                insert_sql = f"INSERT OR IGNORE INTO {t} VALUES ({','.join(['?'] * n_cols_new)})"
            inserted = 0
            skipped_rows = 0
            for row in rows:
                # Decodifica bytes
                clean = []
                for v in row:
                    if isinstance(v, bytes):
                        try:
                            v = v.decode("utf-8", errors="replace")
                        except Exception:
                            v = str(v)
                    clean.append(v)
                if by_name:
                    values = [clean[src_idx[c]] if src_idx[c] < len(clean) else None
                              for c in common]
                else:
                    # Aggiusta dimensione riga (solo fallback posizionale)
                    values = (clean[:n_cols_new] if len(clean) > n_cols_new
                              else clean + [None] * (n_cols_new - len(clean)))
                try:
                    cur.execute(insert_sql, values)
                    # audit/11 §4: con OR IGNORE la riga scartata (vincolo UNIQUE/CHECK)
                    # non solleva: rowcount 0 = ignorata, non 'inserita'.
                    if cur.rowcount > 0:
                        inserted += 1
                    else:
                        skipped_rows += 1
                except Exception:
                    continue
            conn.commit()
            log(f"  {t}: reinserite {inserted}/{len(rows)}"
                + (f" ({skipped_rows} scartate da vincoli)" if skipped_rows else ""))
            total_inserted += inserted
        except Exception as e:
            log(f"  {t}: errore - {e}")
    conn.close()
    log(f"  TOTALE reinserito: {total_inserted} righe")


def step_6_verify():
    """Verifica integrità del DB nuovo."""
    log("STEP 6: Verifica DB nuovo")
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        res = cur.fetchall()
        if res and res[0][0] == "ok":
            log("  Integrity: OK ✓")
        else:
            log(f"  Integrity issues: {res[:3]}")
        # Mostra counts
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (t,) in cur.fetchall():
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                log(f"  {t}: {cur.fetchone()[0]} rows")
            except Exception as e:
                log(f"  {t}: error {e}")
        conn.close()
        return True
    except Exception as e:
        log(f"  Verify FAIL: {e}")
        return False


def main():
    log("=" * 60)
    log("BELLOMBERG DB RECOVERY")
    log("=" * 60)

    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)

    step_0_cleanup_partial()
    src = step_1_find_source()
    if src:
        step_2_backup(src)
        recovered = step_3_try_recovery(src)
        # Sposta sorgente da parte
        bad_renamed = src + ".processed_" + datetime.now().strftime("%H%M%S")
        if os.path.exists(src) and src != DB_PATH:
            os.rename(src, bad_renamed)
            log(f"Sorgente spostato -> {bad_renamed}")
    else:
        recovered = {}

    # Se DB_PATH ancora esiste e e' la stessa cosa, eliminalo prima di ricreare
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    if not step_4_recreate_fresh():
        log("ABORT: impossibile creare DB pulito. Verifica permessi su data/")
        sys.exit(1)

    step_5_reinsert(recovered)
    step_6_verify()

    log("=" * 60)
    log("RECOVERY COMPLETATO. Riavvia il backend Python.")
    log("=" * 60)


if __name__ == "__main__":
    main()
